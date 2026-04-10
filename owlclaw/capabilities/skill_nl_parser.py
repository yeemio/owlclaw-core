"""Natural-language SKILL.md parser with lightweight disk cache."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from owlclaw.capabilities.trigger_resolver import resolve_trigger_intent


@dataclass(frozen=True)
class SkillNLParseResult:
    """Parsed natural-language skill result."""

    parse_mode: str
    trigger_intent: str
    business_rules: list[str]
    trigger_config: dict[str, Any]
    confidence: float
    from_cache: bool = False


def detect_parse_mode(frontmatter: dict[str, Any]) -> str:
    """Detect parser mode from frontmatter shape."""
    raw_owlclaw = frontmatter.get("owlclaw")
    if isinstance(raw_owlclaw, dict) and raw_owlclaw:
        return "structured"
    return "natural_language"


def parse_natural_language_skill(
    *,
    skill_name: str,
    frontmatter: dict[str, Any],
    body: str,
    cache_root: Path | None = None,
) -> SkillNLParseResult:
    """Parse NL skill body into trigger config and business rules."""
    content_hash = _compute_hash(frontmatter=frontmatter, body=body)
    cache_file = _cache_file_path(skill_name=skill_name, cache_root=cache_root)
    cached = _load_cache(cache_file=cache_file, content_hash=content_hash)
    if cached is not None:
        return SkillNLParseResult(
            parse_mode="natural_language",
            trigger_intent=cached["trigger_intent"],
            business_rules=cached["business_rules"],
            trigger_config=cached["trigger_config"],
            confidence=float(cached["confidence"]),
            from_cache=True,
        )

    trigger_intent = _extract_trigger_intent(frontmatter=frontmatter, body=body)
    business_rules = _extract_business_rules(body=body)
    resolved = resolve_trigger_intent(trigger_intent)
    result = SkillNLParseResult(
        parse_mode="natural_language",
        trigger_intent=trigger_intent,
        business_rules=business_rules,
        trigger_config=resolved.trigger_config,
        confidence=resolved.confidence,
        from_cache=False,
    )
    _save_cache(
        cache_file=cache_file,
        content_hash=content_hash,
        result={
            "trigger_intent": result.trigger_intent,
            "business_rules": result.business_rules,
            "trigger_config": result.trigger_config,
            "confidence": result.confidence,
        },
    )
    return result


def _compute_hash(*, frontmatter: dict[str, Any], body: str) -> str:
    payload = json.dumps({"frontmatter": frontmatter, "body": body}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_file_path(*, skill_name: str, cache_root: Path | None) -> Path:
    root = cache_root or Path(".owlclaw") / "cache"
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "-", skill_name).strip("-") or "skill"
    return root / f"{safe_name}.parsed.json"


def _load_cache(*, cache_file: Path, content_hash: str) -> dict[str, Any] | None:
    if not cache_file.exists():
        return None
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("content_hash") != content_hash:
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    if not isinstance(result.get("trigger_intent"), str):
        return None
    if not isinstance(result.get("business_rules"), list):
        return None
    if not isinstance(result.get("trigger_config"), dict):
        return None
    return result


def _save_cache(*, cache_file: Path, content_hash: str, result: dict[str, Any]) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {"content_hash": content_hash, "result": result}
    cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_trigger_intent(*, frontmatter: dict[str, Any], body: str) -> str:
    description = frontmatter.get("description")
    if isinstance(description, str) and _looks_like_trigger_sentence(description):
        return description.strip()

    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _looks_like_trigger_sentence(stripped):
            return stripped
    return "每天 0 点执行"


def _extract_business_rules(*, body: str) -> list[str]:
    rules: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        rules.append(stripped)
    return rules


def _looks_like_trigger_sentence(text: str) -> bool:
    normalized = text.lower()
    keywords = (
        "每天",
        "每周",
        "每月",
        "当",
        "cron",
        "daily",
        "weekly",
        "every day",
        "every week",
        "when",
        "on monday",
        "new order",
    )
    return any(token in text or token in normalized for token in keywords)
