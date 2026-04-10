"""Statistics tracking primitives for OwlHub."""

from __future__ import annotations

import csv
import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkillStatistics:
    """Aggregated statistics for one skill."""

    skill_name: str
    publisher: str
    total_downloads: int
    downloads_last_30d: int
    total_installs: int
    active_installs: int
    last_updated: datetime


@dataclass
class _CachedReleaseStats:
    total_downloads: int
    downloads_last_30d: int
    last_updated: datetime
    expires_at: datetime


class StatisticsTracker:
    """Track skill usage statistics using local events and GitHub release data."""

    def __init__(
        self,
        *,
        github_token: str | None = None,
        cache_ttl_seconds: int = 3600,
        storage_path: Path | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.github_token = github_token
        self.cache_ttl_seconds = cache_ttl_seconds
        self.storage_path = storage_path
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._download_events: dict[tuple[str, str], list[datetime]] = {}
        self._install_events: dict[tuple[str, str], list[tuple[str, datetime]]] = {}
        self._cache: dict[str, _CachedReleaseStats] = {}
        self._daily_aggregate: dict[str, dict[str, int]] = {}
        self._lock = threading.Lock()
        self._load_from_storage()

    def record_download(
        self,
        *,
        skill_name: str,
        publisher: str,
        version: str,
        occurred_at: datetime | None = None,
    ) -> None:
        """Record one local download event."""
        _ = version
        key = (publisher, skill_name)
        event_time = occurred_at or self._now_fn()
        with self._lock:
            self._download_events.setdefault(key, []).append(event_time)
            self._persist_locked()

    def record_install(
        self,
        *,
        skill_name: str,
        publisher: str,
        version: str,
        user_id: str,
        occurred_at: datetime | None = None,
    ) -> None:
        """Record one local install event."""
        _ = version
        key = (publisher, skill_name)
        event_time = occurred_at or self._now_fn()
        with self._lock:
            self._install_events.setdefault(key, []).append((user_id, event_time))
            self._persist_locked()

    def get_statistics(self, *, skill_name: str, publisher: str, repository: str | None = None) -> SkillStatistics:
        """Aggregate local events and optional GitHub release download metrics."""
        github_total = 0
        github_last_30d = 0
        github_updated = self._now_fn()
        if repository:
            github_total, github_last_30d, github_updated = self._get_github_release_stats(repository)

        now = self._now_fn()
        window_start = now - timedelta(days=30)
        key = (publisher, skill_name)

        with self._lock:
            downloads = list(self._download_events.get(key, []))
            installs = list(self._install_events.get(key, []))

        local_total_downloads = len(downloads)
        local_downloads_30d = sum(1 for event_time in downloads if event_time >= window_start)
        local_total_installs = len(installs)
        active_users = {user_id for user_id, event_time in installs if event_time >= window_start}

        return SkillStatistics(
            skill_name=skill_name,
            publisher=publisher,
            total_downloads=github_total + local_total_downloads,
            downloads_last_30d=github_last_30d + local_downloads_30d,
            total_installs=local_total_installs,
            active_installs=len(active_users),
            last_updated=max(github_updated, now),
        )

    def list_all_statistics(self) -> list[SkillStatistics]:
        """Return statistics for all tracked skills."""
        with self._lock:
            keys = set(self._download_events.keys()) | set(self._install_events.keys())
        results = [self.get_statistics(skill_name=skill_name, publisher=publisher) for publisher, skill_name in keys]
        results.sort(key=lambda item: (item.publisher, item.skill_name))
        return results

    def export(self, *, format: str = "json") -> str:
        """Export all statistics as JSON or CSV."""
        rows = self.list_all_statistics()
        if format == "json":
            payload = [
                {
                    "publisher": row.publisher,
                    "skill_name": row.skill_name,
                    "total_downloads": row.total_downloads,
                    "downloads_last_30d": row.downloads_last_30d,
                    "total_installs": row.total_installs,
                    "active_installs": row.active_installs,
                    "last_updated": row.last_updated.isoformat(),
                }
                for row in rows
            ]
            return json.dumps(payload, ensure_ascii=False, indent=2)
        if format == "csv":
            buffer = StringIO()
            writer = csv.DictWriter(
                buffer,
                fieldnames=[
                    "publisher",
                    "skill_name",
                    "total_downloads",
                    "downloads_last_30d",
                    "total_installs",
                    "active_installs",
                    "last_updated",
                ],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        "publisher": row.publisher,
                        "skill_name": row.skill_name,
                        "total_downloads": row.total_downloads,
                        "downloads_last_30d": row.downloads_last_30d,
                        "total_installs": row.total_installs,
                        "active_installs": row.active_installs,
                        "last_updated": row.last_updated.isoformat(),
                    }
                )
            return buffer.getvalue()
        raise ValueError("unsupported export format")

    def run_daily_aggregation(self, *, day: datetime | None = None) -> dict[str, dict[str, int]]:
        """Run one in-process daily aggregation pass."""
        target_day = (day or self._now_fn()).astimezone(timezone.utc).date().isoformat()
        with self._lock:
            for (publisher, skill_name), events in self._download_events.items():
                key = f"{target_day}:{publisher}:{skill_name}"
                current = self._daily_aggregate.setdefault(key, {"downloads": 0, "installs": 0})
                current["downloads"] = len(events)
            for (publisher, skill_name), install_events in self._install_events.items():
                key = f"{target_day}:{publisher}:{skill_name}"
                current = self._daily_aggregate.setdefault(key, {"downloads": 0, "installs": 0})
                current["installs"] = len(install_events)
            snapshot = dict(self._daily_aggregate)
            self._persist_locked()
            return snapshot

    def _get_github_release_stats(self, repository: str) -> tuple[int, int, datetime]:
        cached = self._cache.get(repository)
        now = self._now_fn()
        if cached and cached.expires_at > now:
            return (cached.total_downloads, cached.downloads_last_30d, cached.last_updated)

        owner_repo = self._normalize_repository(repository)
        if owner_repo is None:
            logger.warning("Unsupported repository format for statistics: %s", repository)
            return (0, 0, now)
        owner, repo = owner_repo
        url = f"https://api.github.com/repos/{owner}/{repo}/releases"
        request = urllib.request.Request(url, headers=self._build_headers())
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                logger.warning("GitHub API rate limited for %s/%s", owner, repo)
            else:
                logger.warning("GitHub API request failed for %s/%s: %s", owner, repo, exc)
            return (0, 0, now)
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            logger.warning("Failed to fetch GitHub statistics for %s/%s: %s", owner, repo, exc)
            return (0, 0, now)

        total_downloads = 0
        downloads_last_30d = 0
        window_start = now - timedelta(days=30)
        for release in payload if isinstance(payload, list) else []:
            if not isinstance(release, dict):
                continue
            published_at_text = str(release.get("published_at", "")).strip()
            published_at = _parse_datetime(published_at_text)
            release_downloads = 0
            for asset in release.get("assets", []):
                if isinstance(asset, dict):
                    count = asset.get("download_count", 0)
                    if isinstance(count, int):
                        release_downloads += count
            total_downloads += release_downloads
            if published_at and published_at >= window_start:
                downloads_last_30d += release_downloads

        cached_value = _CachedReleaseStats(
            total_downloads=total_downloads,
            downloads_last_30d=downloads_last_30d,
            last_updated=now,
            expires_at=now + timedelta(seconds=self.cache_ttl_seconds),
        )
        self._cache[repository] = cached_value
        return (total_downloads, downloads_last_30d, now)

    def _build_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "owlclaw-owlhub"}
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        return headers

    @staticmethod
    def _normalize_repository(repository: str) -> tuple[str, str] | None:
        normalized = repository.strip()
        if not normalized:
            return None
        if Path(normalized).exists():
            return None
        parsed = urllib.parse.urlparse(repository)
        if parsed.netloc != "github.com":
            return None
        parts = [segment for segment in parsed.path.split("/") if segment]
        if len(parts) < 2:
            return None
        return (parts[0], parts[1].removesuffix(".git"))

    def _load_from_storage(self) -> None:
        if self.storage_path is None or not self.storage_path.exists():
            return
        payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        downloads = payload.get("downloads", [])
        installs = payload.get("installs", [])
        aggregates = payload.get("daily_aggregate", {})

        if isinstance(downloads, list):
            for row in downloads:
                if not isinstance(row, dict):
                    continue
                publisher = str(row.get("publisher", ""))
                skill_name = str(row.get("skill_name", ""))
                timestamp = _parse_datetime(str(row.get("occurred_at", "")))
                if not publisher or not skill_name or timestamp is None:
                    continue
                self._download_events.setdefault((publisher, skill_name), []).append(timestamp)
        if isinstance(installs, list):
            for row in installs:
                if not isinstance(row, dict):
                    continue
                publisher = str(row.get("publisher", ""))
                skill_name = str(row.get("skill_name", ""))
                user_id = str(row.get("user_id", ""))
                timestamp = _parse_datetime(str(row.get("occurred_at", "")))
                if not publisher or not skill_name or not user_id or timestamp is None:
                    continue
                self._install_events.setdefault((publisher, skill_name), []).append((user_id, timestamp))
        if isinstance(aggregates, dict):
            clean: dict[str, dict[str, int]] = {}
            for key, row in aggregates.items():
                if isinstance(key, str) and isinstance(row, dict):
                    clean[key] = {
                        "downloads": int(row.get("downloads", 0)),
                        "installs": int(row.get("installs", 0)),
                    }
            self._daily_aggregate = clean

    def _persist_locked(self) -> None:
        if self.storage_path is None:
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        downloads = []
        installs = []
        for (publisher, skill_name), events in self._download_events.items():
            for occurred_at in events:
                downloads.append(
                    {
                        "publisher": publisher,
                        "skill_name": skill_name,
                        "occurred_at": occurred_at.isoformat(),
                    }
                )
        for (publisher, skill_name), install_events in self._install_events.items():
            for user_id, occurred_at in install_events:
                installs.append(
                    {
                        "publisher": publisher,
                        "skill_name": skill_name,
                        "user_id": user_id,
                        "occurred_at": occurred_at.isoformat(),
                    }
                )
        payload = {"downloads": downloads, "installs": installs, "daily_aggregate": self._daily_aggregate}
        self.storage_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
