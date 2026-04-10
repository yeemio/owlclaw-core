"""Skills loading and management for OwlClaw.

This module implements the Skills Loader component, which discovers and loads
SKILL.md files from application directories following the Agent Skills specification.
"""

import logging
import os
import platform
import re
import shutil
import threading
import time
from collections.abc import Callable
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml  # type: ignore[import-untyped]

from owlclaw.capabilities.capability_matcher import CapabilityMatcher, extract_tool_intents, parse_available_tools
from owlclaw.capabilities.skill_nl_parser import detect_parse_mode, parse_natural_language_skill
from owlclaw.capabilities.tool_schema import extract_tools_schema
from owlclaw.config.loader import ConfigLoadError, YAMLConfigLoader

logger = logging.getLogger(__name__)
_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_FRONTMATTER_PATTERN = re.compile(r"^---\r?\n(.*?)\r?\n---(?:\r?\n(.*))?$", re.DOTALL)
_RAW_NAME_PATTERN = re.compile(r"^name:\s*(.+)$", re.MULTILINE)

if TYPE_CHECKING:
    from owlclaw.capabilities.registry import CapabilityRegistry
    from owlclaw.governance.ledger import Ledger


def _body_contains_trigger_hint(body_text: str) -> bool:
    normalized = body_text.lower()
    hints = (
        "每天",
        "每周",
        "每月",
        "当",
        "daily",
        "weekly",
        "every day",
        "every week",
        "when",
        "new order",
        "inventory change",
    )
    return any(token in body_text or token in normalized for token in hints)


def _contains_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or 127 <= ord(ch) <= 159 for ch in value)


def _has_valid_raw_skill_name(frontmatter_raw: str) -> bool:
    match = _RAW_NAME_PATTERN.search(frontmatter_raw)
    if match is None:
        return False
    raw_name = match.group(1)
    if raw_name != raw_name.strip() or _contains_control_chars(raw_name):
        return False
    # YAML may quote values (e.g. name: '0' or name: "my-skill").
    # Strip matching outer quotes before validation.
    if len(raw_name) >= 2 and (
        (raw_name[0] == "'" and raw_name[-1] == "'")
        or (raw_name[0] == '"' and raw_name[-1] == '"')
    ):
        raw_name = raw_name[1:-1]
    return _SKILL_NAME_PATTERN.match(raw_name) is not None


class Skill:
    """Represents a loaded Skill with metadata and optional full content.

    A Skill is a knowledge document (SKILL.md) that describes a capability's
    purpose, usage guidelines, and relationships with other capabilities.
    Skills follow the Agent Skills open specification (Anthropic, Dec 2025).

    Attributes:
        name: Unique identifier for the Skill
        description: Brief description of what the Skill does
        file_path: Path to the SKILL.md file
        metadata: Agent Skills standard metadata (author, version, tags)
        owlclaw_config: OwlClaw-specific extension fields
    """

    def __init__(
        self,
        name: str,
        description: str,
        file_path: Path,
        metadata: dict[str, Any],
        owlclaw_config: dict[str, Any] | None = None,
        full_content: str | None = None,
        parse_mode: str = "structured",
        trigger_config: dict[str, Any] | None = None,
        resolved_tools: list[str] | None = None,
    ):
        self.name = name
        self.description = description
        self.file_path = Path(file_path)
        self.metadata = metadata
        self.owlclaw_config = owlclaw_config or {}
        self._full_content = full_content
        self._is_loaded = full_content is not None
        self.parse_mode = parse_mode if parse_mode in {"structured", "natural_language", "hybrid"} else "structured"
        self.trigger_config = trigger_config if isinstance(trigger_config, dict) else {}
        self.resolved_tools = [item for item in (resolved_tools or []) if isinstance(item, str) and item.strip()]

    @property
    def task_type(self) -> str | None:
        """Get the task_type for AI routing (OwlClaw extension)."""
        raw = self.owlclaw_config.get("task_type")
        if not isinstance(raw, str):
            return None
        normalized = raw.strip()
        return normalized or None

    @property
    def constraints(self) -> dict[str, Any]:
        """Get the constraints for governance filtering (OwlClaw extension)."""
        raw = self.owlclaw_config.get("constraints", {})
        return raw if isinstance(raw, dict) else {}

    @property
    def trigger(self) -> str | None:
        """Get the trigger configuration (OwlClaw extension)."""
        return self.owlclaw_config.get("trigger")

    @property
    def focus(self) -> list[str]:
        """Get focus tags used for runtime skill selection (OwlClaw extension)."""
        raw = self.owlclaw_config.get("focus", [])
        if isinstance(raw, str):
            normalized = raw.strip()
            return [normalized] if normalized else []
        if isinstance(raw, list | tuple | set):
            out: list[str] = []
            seen: set[str] = set()
            for item in raw:
                if not isinstance(item, str):
                    continue
                normalized = item.strip()
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                out.append(normalized)
            return out
        return []

    @property
    def risk_level(self) -> str:
        """Get declared risk level (low/medium/high/critical), defaulting to low."""
        raw = self.owlclaw_config.get("risk_level", "low")
        if isinstance(raw, str):
            normalized = raw.strip().lower()
            if normalized in {"low", "medium", "high", "critical"}:
                return normalized
        return "low"

    @property
    def requires_confirmation(self) -> bool:
        """Whether this skill requires human confirmation before execution.

        For compatibility with architecture v4.1:
        - explicit owlclaw.requires_confirmation takes precedence;
        - high/critical risk defaults to True when not explicitly set.
        """
        raw = self.owlclaw_config.get("requires_confirmation")
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, int) and raw in {0, 1}:
            return bool(raw)
        if isinstance(raw, str):
            normalized = raw.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return self.risk_level in {"high", "critical"}

    def load_full_content(self) -> str:
        """Load full instruction text from SKILL.md (lazy loading).

        The full content is loaded only when needed and cached for subsequent
        access. This minimizes memory usage during startup.

        Returns:
            The instruction text (content after frontmatter)
        """
        if not self._is_loaded:
            content = self.file_path.read_text(encoding="utf-8")
            content = content.lstrip("\ufeff")
            match = _FRONTMATTER_PATTERN.match(content)
            self._full_content = (match.group(2) if match else "") or ""
            self._full_content = self._full_content.strip()
            self._is_loaded = True
        return self._full_content or ""

    def clear_full_content_cache(self) -> None:
        """Drop cached full content so subsequent reads re-load from file."""
        self._full_content = None
        self._is_loaded = False

    @property
    def references_dir(self) -> Path | None:
        """Path to references/ directory if it exists.

        The references/ directory contains supporting documentation
        referenced by the Skill (e.g., trading-rules.md).
        """
        ref_dir = self.file_path.parent / "references"
        return ref_dir if ref_dir.exists() else None

    @property
    def scripts_dir(self) -> Path | None:
        """Path to scripts/ directory if it exists.

        The scripts/ directory contains helper scripts used by the Skill
        (e.g., check_signals.py).
        """
        scripts_dir = self.file_path.parent / "scripts"
        return scripts_dir if scripts_dir.exists() else None

    @property
    def assets_dir(self) -> Path | None:
        """Path to assets/ directory if it exists."""
        assets_dir = self.file_path.parent / "assets"
        return assets_dir if assets_dir.exists() else None

    def to_dict(self) -> dict[str, Any]:
        """Serialize metadata to dict (excludes full content).

        Returns:
            Dictionary with Skill metadata suitable for JSON serialization
        """
        return {
            "name": self.name,
            "description": self.description,
            "file_path": str(self.file_path),
            "metadata": self.metadata,
            "parse_mode": self.parse_mode,
            "trigger_config": self.trigger_config,
            "resolved_tools": self.resolved_tools,
            "task_type": self.task_type,
            "constraints": self.constraints,
            "trigger": self.trigger,
            "focus": self.focus,
            "risk_level": self.risk_level,
            "requires_confirmation": self.requires_confirmation,
        }


class SkillsLoader:
    """Discovers and loads SKILL.md files from application directories.

    At startup only frontmatter metadata is loaded; full instruction text
    is loaded on demand via Skill.load_full_content() (progressive loading).
    """

    def __init__(self, base_path: Path | str):
        if isinstance(base_path, str):
            normalized = base_path.strip()
            if not normalized:
                raise ValueError("base_path must be a non-empty path")
            self.base_path = Path(normalized)
        elif isinstance(base_path, Path):
            self.base_path = base_path
        else:
            raise ValueError("base_path must be a non-empty path")
        self.skills: dict[str, Skill] = {}
        self._skills_enabled_overrides: dict[str, bool] = {}

    def scan(self) -> list[Skill]:
        """Recursively scan for SKILL.md files and load metadata.

        Returns:
            List of loaded Skill objects. Invalid or missing files are
            logged and skipped.
        """
        self.skills.clear()
        self._skills_enabled_overrides = self._load_skill_enablement_overrides()
        if not self.base_path.exists() or not self.base_path.is_dir():
            logger.warning("Skills base path does not exist or is not a directory: %s", self.base_path)
            return []
        skill_files = sorted(self.base_path.rglob("SKILL.md"))
        for skill_file in skill_files:
            skill = self._parse_skill_file(skill_file)
            if skill is not None:
                if not self._is_skill_enabled(skill.name):
                    logger.warning("Skill '%s' disabled by config, skipping", skill.name)
                    continue
                if skill.name in self.skills:
                    existing = self.skills[skill.name]
                    existing_priority = self._skill_source_priority(existing.file_path)
                    new_priority = self._skill_source_priority(skill.file_path)
                    if new_priority > existing_priority:
                        logger.warning(
                            "Duplicate Skill name '%s' in %s overrides %s by source priority",
                            skill.name,
                            skill_file,
                            existing.file_path,
                        )
                        self.skills[skill.name] = skill
                        continue
                    logger.warning(
                        "Duplicate Skill name '%s' in %s (already loaded from %s); skipping",
                        skill.name,
                        skill_file,
                        existing.file_path,
                    )
                    continue
                self.skills[skill.name] = skill
        return list(self.skills.values())

    @staticmethod
    def _skill_source_priority(file_path: Path) -> int:
        """Return source priority for duplicate skill resolution.

        Higher value wins: workspace (2) > managed/installed (1) > bundled (0).
        """
        parts = {part.lower() for part in file_path.parts}
        if "bundled" in parts:
            return 0
        if "managed" in parts or "installed" in parts:
            return 1
        return 2

    def _parse_skill_file(self, file_path: Path) -> Skill | None:
        """Parse SKILL.md file and extract frontmatter metadata.

        On YAML error, missing required fields, or read error, logs a
        warning and returns None.
        """
        try:
            content = file_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("Failed to read Skill file %s: %s", file_path, e)
            return None
        content = content.lstrip("\ufeff")

        if not content.startswith("---"):
            logger.warning("Skill file %s missing frontmatter", file_path)
            return None

        match = _FRONTMATTER_PATTERN.match(content)
        if not match:
            logger.warning("Skill file %s invalid frontmatter format", file_path)
            return None
        frontmatter_raw, _body = match.groups()

        try:
            frontmatter = yaml.safe_load(frontmatter_raw)
        except yaml.YAMLError as e:
            logger.warning("Skill file %s YAML parse error: %s", file_path, e)
            return None
        if not _has_valid_raw_skill_name(frontmatter_raw):
            logger.warning("Skill file %s name must be kebab-case", file_path)
            return None

        if frontmatter is None:
            logger.warning("Skill file %s empty frontmatter", file_path)
            return None
        if not isinstance(frontmatter, dict):
            logger.warning("Skill file %s frontmatter must be a mapping", file_path)
            return None
        frontmatter_map: dict[str, Any] = frontmatter

        if "name" not in frontmatter_map or "description" not in frontmatter_map:
            logger.warning(
                "Skill file %s missing required fields (name, description)",
                file_path,
            )
            return None
        if not isinstance(frontmatter_map["name"], str) or not frontmatter_map["name"].strip():
            logger.warning("Skill file %s invalid name field", file_path)
            return None
        raw_name = frontmatter_map["name"]
        if raw_name != raw_name.strip() or _contains_control_chars(raw_name) or not _SKILL_NAME_PATTERN.match(raw_name):
            logger.warning("Skill file %s name must be kebab-case", file_path)
            return None
        if not isinstance(frontmatter_map["description"], str) or not frontmatter_map["description"].strip():
            logger.warning("Skill file %s invalid description field", file_path)
            return None

        metadata = frontmatter_map.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        metadata = dict(metadata)
        top_level_tags = frontmatter_map.get("tags")
        if isinstance(top_level_tags, list):
            normalized_tags = [str(tag).strip() for tag in top_level_tags if isinstance(tag, str) and str(tag).strip()]
            if normalized_tags:
                metadata["tags"] = normalized_tags
        top_level_industry = frontmatter_map.get("industry")
        if isinstance(top_level_industry, str) and top_level_industry.strip():
            metadata["industry"] = top_level_industry.strip()
        tools_schema, tool_errors = extract_tools_schema(frontmatter_map)
        if tool_errors:
            for error in tool_errors:
                logger.warning("Skill file %s invalid tool declaration: %s", file_path, error)
        metadata["tools_schema"] = tools_schema
        owlclaw_config = frontmatter_map.get("owlclaw", {})
        if not isinstance(owlclaw_config, dict):
            owlclaw_config = {}
        body_text = (_body or "").strip()
        parse_mode = detect_parse_mode(frontmatter_map)
        trigger_config: dict[str, Any] = {}
        resolved_tools: list[str] = []
        if parse_mode == "natural_language":
            nl_result = parse_natural_language_skill(
                skill_name=frontmatter_map["name"].strip(),
                frontmatter=frontmatter_map,
                body=body_text,
            )
            trigger_config = dict(nl_result.trigger_config)
            trigger_type = trigger_config.get("type")
            if trigger_type == "cron" and isinstance(trigger_config.get("expression"), str):
                owlclaw_config["trigger"] = f'cron("{trigger_config["expression"]}")'
            elif trigger_type == "webhook" and isinstance(trigger_config.get("event"), str):
                owlclaw_config["trigger"] = f'webhook("{trigger_config["event"]}")'
            elif trigger_type == "queue" and isinstance(trigger_config.get("topic"), str):
                owlclaw_config["trigger"] = f'queue("{trigger_config["topic"]}")'
            elif trigger_type == "db_change" and isinstance(trigger_config.get("table"), str):
                owlclaw_config["trigger"] = f'db_change("{trigger_config["table"]}")'
            owlclaw_config["parse_confidence"] = nl_result.confidence
        elif parse_mode == "structured" and "trigger" not in owlclaw_config and _body_contains_trigger_hint(body_text):
            nl_result = parse_natural_language_skill(
                skill_name=frontmatter_map["name"].strip(),
                frontmatter=frontmatter_map,
                body=body_text,
            )
            parse_mode = "hybrid"
            trigger_config = dict(nl_result.trigger_config)
            trigger_type = trigger_config.get("type")
            if trigger_type == "cron" and isinstance(trigger_config.get("expression"), str):
                owlclaw_config["trigger"] = f'cron("{trigger_config["expression"]}")'
            elif trigger_type == "webhook" and isinstance(trigger_config.get("event"), str):
                owlclaw_config["trigger"] = f'webhook("{trigger_config["event"]}")'
            elif trigger_type == "queue" and isinstance(trigger_config.get("topic"), str):
                owlclaw_config["trigger"] = f'queue("{trigger_config["topic"]}")'
            elif trigger_type == "db_change" and isinstance(trigger_config.get("table"), str):
                owlclaw_config["trigger"] = f'db_change("{trigger_config["table"]}")'
            owlclaw_config["parse_confidence"] = nl_result.confidence
        prerequisites = self._extract_prerequisites(frontmatter_map, owlclaw_config)
        ready, reasons = self._check_prerequisites(prerequisites)
        if not ready:
            logger.warning(
                "Skill file %s skipped due to unmet prerequisites: %s",
                file_path,
                "; ".join(reasons),
            )
            return None
        if tools_schema:
            resolved_tools = sorted(name for name in tools_schema if isinstance(name, str) and name.strip())
        else:
            available_tools = parse_available_tools()
            if available_tools:
                matcher = CapabilityMatcher()
                intents = extract_tool_intents(frontmatter=frontmatter_map, body=body_text)
                matches = matcher.resolve(tool_intents=intents, available_tools=available_tools)
                resolved_tools = sorted({item.tool_name for item in matches})

        return Skill(
            name=raw_name,
            description=frontmatter_map["description"].strip(),
            file_path=file_path,
            metadata=metadata,
            owlclaw_config=owlclaw_config,
            full_content=None,
            parse_mode=parse_mode,
            trigger_config=trigger_config,
            resolved_tools=resolved_tools,
        )

    @staticmethod
    def _extract_prerequisites(frontmatter: dict[str, Any], owlclaw_config: dict[str, Any]) -> dict[str, Any]:
        nested = owlclaw_config.get("prerequisites")
        if isinstance(nested, dict):
            return nested
        top_level = frontmatter.get("prerequisites")
        if isinstance(top_level, dict):
            return top_level
        return {}

    @staticmethod
    def _as_str_list(value: Any) -> list[str]:
        if isinstance(value, str):
            normalized = value.strip()
            return [normalized] if normalized else []
        if not isinstance(value, list):
            return []
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        return out

    @staticmethod
    def _get_by_path(data: dict[str, Any], dotted_path: str) -> tuple[bool, Any]:
        if not dotted_path:
            return False, None
        cursor: Any = data
        for part in dotted_path.split("."):
            key = part.strip()
            if not key or not isinstance(cursor, dict) or key not in cursor:
                return False, None
            cursor = cursor[key]
        return True, cursor

    @staticmethod
    def _normalize_os_name(raw: str) -> str:
        candidate = raw.strip().lower()
        aliases = {
            "win32": "windows",
            "windows": "windows",
            "linux": "linux",
            "darwin": "darwin",
            "mac": "darwin",
            "macos": "darwin",
            "osx": "darwin",
        }
        return aliases.get(candidate, candidate)

    def _load_runtime_config(self) -> dict[str, Any]:
        try:
            from owlclaw.config.manager import ConfigManager

            cfg = ConfigManager.instance().get()
            dumped = cfg.model_dump(mode="python")
            return dumped if isinstance(dumped, dict) else {}
        except Exception:
            return {}

    def _load_skill_enablement_overrides(self) -> dict[str, bool]:
        try:
            config_data = YAMLConfigLoader.load_dict()
        except (ConfigLoadError, OSError) as exc:
            logger.warning("Failed to load owlclaw.yaml for skills enablement: %s", exc)
            return {}
        skills_block = config_data.get("skills")
        if not isinstance(skills_block, dict):
            return {}
        entries = skills_block.get("entries")
        if not isinstance(entries, dict):
            return {}
        overrides: dict[str, bool] = {}
        for raw_name, raw_cfg in entries.items():
            if not isinstance(raw_name, str):
                continue
            name = raw_name.strip()
            if not name:
                continue
            if isinstance(raw_cfg, bool):
                overrides[name] = raw_cfg
                continue
            if isinstance(raw_cfg, dict):
                enabled = raw_cfg.get("enabled")
                if isinstance(enabled, bool):
                    overrides[name] = enabled
        return overrides

    def _is_skill_enabled(self, skill_name: str) -> bool:
        return self._skills_enabled_overrides.get(skill_name, True)

    def _check_prerequisites(self, prerequisites: dict[str, Any]) -> tuple[bool, list[str]]:
        if not prerequisites:
            return True, []
        reasons: list[str] = []

        for env_name in self._as_str_list(prerequisites.get("env")):
            if not os.getenv(env_name):
                reasons.append(f"missing env {env_name}")

        for bin_name in self._as_str_list(prerequisites.get("bins")):
            if shutil.which(bin_name) is None:
                reasons.append(f"missing binary {bin_name}")

        for package_name in self._as_str_list(prerequisites.get("python_packages")):
            if find_spec(package_name) is None:
                reasons.append(f"missing python package {package_name}")

        declared_os = {
            self._normalize_os_name(item)
            for item in self._as_str_list(prerequisites.get("os"))
            if self._normalize_os_name(item)
        }
        if declared_os:
            current_os = self._normalize_os_name(platform.system())
            if current_os not in declared_os:
                reasons.append(f"os mismatch {current_os} not in {sorted(declared_os)}")

        cfg_requirements = prerequisites.get("config")
        if isinstance(cfg_requirements, list):
            cfg = self._load_runtime_config()
            for path in self._as_str_list(cfg_requirements):
                found, value = self._get_by_path(cfg, path)
                if not found or value is None:
                    reasons.append(f"missing config {path}")
        elif isinstance(cfg_requirements, dict):
            cfg = self._load_runtime_config()
            for path, expected in cfg_requirements.items():
                if not isinstance(path, str) or not path.strip():
                    continue
                found, value = self._get_by_path(cfg, path.strip())
                if not found:
                    reasons.append(f"missing config {path.strip()}")
                    continue
                if value != expected:
                    reasons.append(f"config mismatch {path.strip()} expected={expected!r} actual={value!r}")

        return len(reasons) == 0, reasons

    def get_skill(self, name: str) -> Skill | None:
        """Retrieve a Skill by name."""
        if not isinstance(name, str):
            return None
        normalized = name.strip()
        if not normalized:
            return None
        return self.skills.get(normalized)

    def list_skills(self) -> list[Skill]:
        """List all loaded Skills."""
        return list(self.skills.values())

    def clear_all_full_content_cache(self) -> int:
        """Clear full content cache for all loaded skills.

        Returns:
            Number of skills whose cache was cleared.
        """
        cleared = 0
        for skill in self.skills.values():
            skill.clear_full_content_cache()
            cleared += 1
        return cleared


def auto_register_binding_tools(
    skills_loader: SkillsLoader,
    registry: "CapabilityRegistry",
    ledger: "Ledger | None" = None,
) -> list[str]:
    """Auto-register binding-declared tools from Skill metadata."""
    from owlclaw.capabilities.bindings import (
        BindingExecutorRegistry,
        BindingTool,
        HTTPBindingExecutor,
        parse_binding_config,
    )

    executor_registry = BindingExecutorRegistry()
    executor_registry.register("http", HTTPBindingExecutor())

    registered: list[str] = []
    for skill in skills_loader.list_skills():
        tools_schema = skill.metadata.get("tools_schema", {})
        if not isinstance(tools_schema, dict):
            continue
        for tool_name, tool_def in tools_schema.items():
            if not isinstance(tool_name, str) or not tool_name.strip() or not isinstance(tool_def, dict):
                continue
            binding_data = tool_def.get("binding")
            if not isinstance(binding_data, dict):
                continue
            if tool_name in registry.handlers:
                continue
            try:
                config = parse_binding_config(binding_data)
            except ValueError as exc:
                logger.warning(
                    "Skip binding tool '%s' in skill '%s': %s",
                    tool_name,
                    skill.name,
                    exc,
                )
                continue
            tool = BindingTool(
                name=tool_name,
                description=str(tool_def.get("description", "")),
                parameters_schema=tool_def.get("parameters", {}) if isinstance(tool_def.get("parameters"), dict) else {},
                binding_config=config,
                executor_registry=executor_registry,
                ledger=ledger,
                risk_level=skill.risk_level,
                requires_confirmation=skill.requires_confirmation,
                task_type=skill.task_type,
                constraints=skill.constraints,
                focus=skill.focus,
            )
            registry.register_handler(tool_name, tool)
            registered.append(tool_name)
    return registered


class SkillsWatcher:
    """Lightweight file watcher with polling + debounce for SKILL.md changes."""

    def __init__(
        self,
        skills_loader: SkillsLoader,
        *,
        poll_interval_seconds: float = 1.0,
        debounce_seconds: float = 0.5,
    ) -> None:
        self.skills_loader = skills_loader
        self.poll_interval_seconds = max(0.1, float(poll_interval_seconds))
        self.debounce_seconds = max(0.0, float(debounce_seconds))
        self._callbacks: list[Callable[[list[Skill]], None]] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_applied_fingerprint: tuple[tuple[str, int, int], ...] | None = None
        self._last_reload_monotonic: float = 0.0

    def watch(self, callback: Callable[[list[Skill]], None]) -> None:
        """Register callback called after successful reload."""
        if callable(callback):
            self._callbacks.append(callback)

    def start(self) -> None:
        """Start background polling thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="skills-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop background polling thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.poll_interval_seconds * 2))
        self._thread = None

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception:
                logger.debug("skills watcher poll failed", exc_info=True)
            self._stop_event.wait(self.poll_interval_seconds)

    def _collect_fingerprint(self) -> tuple[tuple[str, int, int], ...]:
        base = self.skills_loader.base_path
        if not base.exists() or not base.is_dir():
            return tuple()
        rows: list[tuple[str, int, int]] = []
        for file_path in sorted(base.rglob("SKILL.md")):
            try:
                stat = file_path.stat()
            except OSError:
                continue
            rows.append((str(file_path), int(stat.st_mtime_ns), int(stat.st_size)))
        return tuple(rows)

    def poll_once(self) -> bool:
        """Poll once; return True when reload occurred."""
        current = self._collect_fingerprint()
        if self._last_applied_fingerprint is None:
            self._last_applied_fingerprint = current
            return False
        if current == self._last_applied_fingerprint:
            return False
        now = time.monotonic()
        if now - self._last_reload_monotonic < self.debounce_seconds:
            return False
        skills = self.skills_loader.scan()
        self._last_applied_fingerprint = current
        self._last_reload_monotonic = now
        for callback in self._callbacks:
            try:
                callback(skills)
            except Exception:
                logger.debug("skills watcher callback failed", exc_info=True)
        return True
