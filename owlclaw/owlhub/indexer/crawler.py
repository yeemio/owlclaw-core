"""Repository crawler for discovering OwlHub skill manifests."""

from __future__ import annotations

from pathlib import Path

import yaml  # type: ignore[import-untyped]

from owlclaw.owlhub.schema import SkillManifest, VersionState


class SkillRepositoryCrawler:
    """Crawl repository paths and parse SKILL.md frontmatter into manifests."""

    def crawl_repository(self, repository: str) -> list[SkillManifest]:
        """Return all manifests found under the given repository path."""
        root = Path(repository)
        if not root.exists():
            return []
        manifests: list[SkillManifest] = []
        for skill_file in sorted(root.rglob("SKILL.md")):
            manifest = self._parse_skill(skill_file)
            if manifest is not None:
                manifests.append(manifest)
        return manifests

    def _parse_skill(self, skill_file: Path) -> SkillManifest | None:
        content = skill_file.read_text(encoding="utf-8").lstrip("\ufeff")
        if not content.startswith("---"):
            return None
        segments = content.split("---", 2)
        if len(segments) < 3:
            return None
        raw_frontmatter = segments[1]
        loaded = yaml.safe_load(raw_frontmatter) or {}
        if not isinstance(loaded, dict):
            return None

        name = loaded.get("name")
        description = loaded.get("description")
        metadata = loaded.get("metadata", {})
        if not isinstance(name, str) or not name.strip():
            return None
        if not isinstance(description, str) or not description.strip():
            return None
        version = "0.1.0"
        version_state = VersionState.RELEASED
        tags: list[str] = []
        industry: str | None = None
        dependencies: dict[str, str] = {}
        if isinstance(metadata, dict):
            meta_version = metadata.get("version")
            if isinstance(meta_version, str) and meta_version.strip():
                version = meta_version.strip()
            raw_state = str(metadata.get("state", metadata.get("version_state", "released"))).strip().lower()
            if raw_state == VersionState.DRAFT.value:
                version_state = VersionState.DRAFT
            elif raw_state == VersionState.DEPRECATED.value:
                version_state = VersionState.DEPRECATED
            meta_tags = metadata.get("tags", [])
            if isinstance(meta_tags, list):
                tags = [str(tag).strip() for tag in meta_tags if isinstance(tag, str) and str(tag).strip()]
            meta_industry = metadata.get("industry")
            if isinstance(meta_industry, str) and meta_industry.strip():
                industry = meta_industry.strip()
            meta_dependencies = metadata.get("dependencies", {})
            if isinstance(meta_dependencies, dict):
                dependencies = {
                    str(dep).strip(): str(constraint).strip()
                    for dep, constraint in meta_dependencies.items()
                    if isinstance(dep, str) and dep.strip() and isinstance(constraint, str) and constraint.strip()
                }

        raw_tags = loaded.get("tags", [])
        if isinstance(raw_tags, list):
            tags = [str(tag).strip() for tag in raw_tags if isinstance(tag, str) and str(tag).strip()]
        raw_industry = loaded.get("industry")
        if isinstance(raw_industry, str) and raw_industry.strip():
            industry = raw_industry.strip()
        raw_dependencies = loaded.get("dependencies", {})
        if isinstance(raw_dependencies, dict):
            dependencies = {
                str(dep).strip(): str(constraint).strip()
                for dep, constraint in raw_dependencies.items()
                if isinstance(dep, str) and dep.strip() and isinstance(constraint, str) and constraint.strip()
            }

        publisher = skill_file.parent.parent.name or "unknown"
        return SkillManifest(
            name=name.strip(),
            version=version,
            publisher=publisher,
            description=description.strip(),
            license="MIT",
            tags=tags,
            industry=industry,
            dependencies=dependencies,
            repository=str(skill_file.parent),
            version_state=version_state,
        )
