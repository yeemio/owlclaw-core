"""Index builder for OwlHub index.json generation."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import timezone
from pathlib import Path
from typing import Any

from owlclaw.owlhub.indexer.crawler import SkillRepositoryCrawler
from owlclaw.owlhub.schema import IndexEntry, VersionState
from owlclaw.owlhub.schema.models import utc_now
from owlclaw.owlhub.statistics import StatisticsTracker


class IndexBuilder:
    """Build index payload from repository sources."""

    def __init__(
        self,
        crawler: SkillRepositoryCrawler | None = None,
        statistics_tracker: StatisticsTracker | None = None,
    ):
        self.crawler = crawler or SkillRepositoryCrawler()
        self.statistics_tracker = statistics_tracker or StatisticsTracker()

    def calculate_checksum(self, file_path: Path) -> str:
        """Calculate SHA256 checksum for a file."""
        digest = hashlib.sha256()
        with file_path.open("rb") as handle:
            while True:
                chunk = handle.read(8192)
                if not chunk:
                    break
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"

    def crawl_repository(self, repository: str) -> list[dict[str, Any]]:
        """Crawl one repository and return normalized index entries."""
        manifests = self.crawler.crawl_repository(repository)
        entries: list[dict[str, Any]] = []
        for manifest in manifests:
            published_at = utc_now()
            entry = IndexEntry(
                manifest=manifest,
                download_url=f"{repository.rstrip('/')}#{manifest.name}@{manifest.version}",
                checksum=self._manifest_checksum(manifest),
                published_at=published_at,
                updated_at=published_at,
                version_state=manifest.version_state if hasattr(manifest, "version_state") else VersionState.RELEASED,
            )
            payload = asdict(entry)
            payload["published_at"] = entry.published_at.astimezone(timezone.utc).isoformat()
            payload["updated_at"] = entry.updated_at.astimezone(timezone.utc).isoformat()
            stats = self.statistics_tracker.get_statistics(
                skill_name=manifest.name,
                publisher=manifest.publisher,
                repository=manifest.repository,
            )
            payload["statistics"] = {
                "total_downloads": stats.total_downloads,
                "downloads_last_30d": stats.downloads_last_30d,
                "last_updated": stats.last_updated.astimezone(timezone.utc).isoformat(),
            }
            entries.append(payload)
        return entries

    def build_index(self, repositories: list[str]) -> dict[str, Any]:
        """Build complete index payload from all repositories."""
        skills: list[dict[str, Any]] = []
        for repository in repositories:
            skills.extend(self.crawl_repository(repository))
        skills.sort(key=lambda item: (item["manifest"]["name"], item["manifest"]["version"]))
        search_index = self._build_search_index(skills)
        return {
            "version": "1.0",
            "generated_at": utc_now().astimezone(timezone.utc).isoformat(),
            "total_skills": len(skills),
            "skills": skills,
            "search_index": search_index,
        }

    @staticmethod
    def _manifest_checksum(manifest: Any) -> str:
        raw = f"{manifest.publisher}:{manifest.name}:{manifest.version}".encode()
        return f"sha256:{hashlib.sha256(raw).hexdigest()}"

    @staticmethod
    def _build_search_index(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
        index: list[dict[str, Any]] = []
        for item in skills:
            manifest = item.get("manifest", {})
            name = str(manifest.get("name", "")).strip()
            publisher = str(manifest.get("publisher", "")).strip()
            version = str(manifest.get("version", "")).strip()
            description = str(manifest.get("description", "")).strip()
            tags = [tag for tag in manifest.get("tags", []) if isinstance(tag, str)]
            industry = str(manifest.get("industry", "")).strip()
            search_text = " ".join(part for part in [name, description, " ".join(tags), industry] if part).strip().lower()
            index.append(
                {
                    "id": f"{publisher}/{name}@{version}",
                    "name": name,
                    "publisher": publisher,
                    "version": version,
                    "tags": tags,
                    "industry": industry,
                    "search_text": search_text,
                }
            )
        return index
