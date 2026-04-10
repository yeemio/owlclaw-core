"""Template registry — loads and indexes .md.j2 templates."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from owlclaw.templates.skills.exceptions import TemplateNotFoundError
from owlclaw.templates.skills.models import (
    TemplateCategory,
    TemplateMetadata,
    TemplateParameter,
)

logger = logging.getLogger(__name__)

# Pattern for Jinja2 comment block {# ... #}
_METADATA_BLOCK = re.compile(r"\{#(.*?)#\}", re.DOTALL)


class TemplateRegistry:
    """Registry of SKILL.md templates loaded from a directory tree."""

    def __init__(self, templates_dir: Path | str) -> None:
        """Initialize registry and load templates.

        Args:
            templates_dir: Path to the templates root (containing category subdirs).
        """
        self.templates_dir = Path(templates_dir)
        self._templates: dict[str, TemplateMetadata] = {}
        self._load_templates()
        if self._templates:
            logger.info("Loaded %d templates from %s", len(self._templates), self.templates_dir)

    def _load_templates(self) -> None:
        """Recursively scan template directory and load all .md.j2 files."""
        if not self.templates_dir.exists() or not self.templates_dir.is_dir():
            logger.warning("Templates directory does not exist: %s", self.templates_dir)
            return

        for category_dir in sorted(self.templates_dir.iterdir()):
            if not category_dir.is_dir():
                continue

            try:
                category = TemplateCategory(category_dir.name)
            except ValueError:
                logger.debug("Skipping non-category directory: %s", category_dir.name)
                continue

            for template_file in sorted(category_dir.glob("*.md.j2")):
                try:
                    metadata = self._parse_template_metadata(template_file, category)
                    if metadata.id in self._templates:
                        logger.warning(
                            "Duplicate template id '%s' in %s (already loaded from %s); skipping",
                            metadata.id,
                            template_file,
                            self._templates[metadata.id].file_path,
                        )
                        continue
                    self._templates[metadata.id] = metadata
                except Exception as e:
                    logger.warning(
                        "Failed to load template %s: %s",
                        template_file,
                        e,
                        exc_info=False,
                    )

    def _parse_template_metadata(
        self,
        template_file: Path,
        category: TemplateCategory,
    ) -> TemplateMetadata:
        """Extract metadata from template file {# ... #} comment block."""
        content = template_file.read_text(encoding="utf-8")
        match = _METADATA_BLOCK.search(content)
        if not match:
            raise ValueError("Template missing metadata comment block {# ... #}")

        raw = match.group(1).strip()
        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            raise ValueError("Metadata block must be valid YAML mapping")

        raw_name = data.get("name") or template_file.stem.replace("-", " ").title()
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValueError("Template metadata 'name' must be a non-empty string")
        name = raw_name.strip()
        raw_description = data.get("description", "")
        if not isinstance(raw_description, str):
            raise ValueError("Template metadata 'description' must be a string")
        description = raw_description.strip()
        tags = data.get("tags")
        if not isinstance(tags, list):
            tags = [t for t in tags.split(",")] if isinstance(tags, str) else []
        tags = [str(t).strip() for t in tags if t]

        parameters: list[TemplateParameter] = []
        seen_param_names: set[str] = set()
        for p in data.get("parameters") or []:
            if isinstance(p, dict):
                param_name = str(p.get("name", "")).strip()
                if not param_name or param_name in seen_param_names:
                    continue
                seen_param_names.add(param_name)
                raw_required = p.get("required", True)
                if isinstance(raw_required, str):
                    required = raw_required.strip().lower() not in ("0", "false", "no", "off")
                else:
                    required = bool(raw_required)
                raw_choices = p.get("choices")
                if isinstance(raw_choices, list):
                    choices = raw_choices
                elif isinstance(raw_choices, str):
                    choices = [c.strip() for c in raw_choices.split(",") if c.strip()]
                else:
                    choices = None
                parameters.append(
                    TemplateParameter(
                        name=param_name,
                        type=str(p.get("type", "str")),
                        description=str(p.get("description", "")),
                        required=required,
                        default=p.get("default"),
                        choices=choices,
                    )
                )

        examples = data.get("examples") or []
        if isinstance(examples, str):
            examples = [examples]
        examples = [str(e) for e in examples]

        # "health-check.md.j2" -> "health-check" (stem of "health-check.md.j2" is "health-check.md")
        base_name = template_file.name.removesuffix(".md.j2")
        template_id = f"{category.value}/{base_name}"
        return TemplateMetadata(
            id=template_id,
            name=name,
            category=category,
            description=description,
            tags=tags,
            parameters=parameters,
            examples=examples,
            file_path=template_file,
        )

    def get_template(self, template_id: str) -> TemplateMetadata | None:
        """Get template by ID. Returns None if not found."""
        return self._templates.get(template_id)

    def get_template_or_raise(self, template_id: str) -> TemplateMetadata:
        """Get template by ID. Raises TemplateNotFoundError if not found."""
        t = self._templates.get(template_id)
        if t is None:
            raise TemplateNotFoundError(f"Template not found: {template_id}")
        return t

    def list_templates(
        self,
        category: TemplateCategory | None = None,
        tags: list[str] | None = None,
    ) -> list[TemplateMetadata]:
        """List templates, optionally filtered by category and tags."""
        templates = list(self._templates.values())
        if category is not None:
            templates = [t for t in templates if t.category == category]
        if tags:
            normalized_tags = {tag.strip().lower() for tag in tags if tag and tag.strip()}
            templates = [
                t for t in templates
                if any(template_tag.lower() in normalized_tags for template_tag in t.tags)
            ]
        return templates

    def search_templates(self, query: str) -> list[TemplateMetadata]:
        """Search templates by name, description, or tags."""
        query_lower = query.lower().strip()
        if not query_lower:
            return []
        return [
            t
            for t in self._templates.values()
            if (
                query_lower in t.name.lower()
                or query_lower in t.description.lower()
                or any(query_lower in tag.lower() for tag in t.tags)
            )
        ]
