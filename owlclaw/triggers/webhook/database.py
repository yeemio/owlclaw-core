"""Database migration and seed management for webhook module."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class DatabaseVersionStatus:
    """Database migration status for webhook module."""

    current_version: str
    applied_versions: list[str]
    seeded_records: int
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class WebhookDatabaseManager:
    """Manage webhook migration execution and seed data loading."""

    def __init__(self, *, initial_version: str = "000") -> None:
        self._current_version = initial_version
        self._applied_versions: list[str] = [initial_version]
        self._seeded: list[dict[str, Any]] = []

    def run_migration(self, target_version: str) -> DatabaseVersionStatus:
        if not target_version.strip():
            raise ValueError("target_version is required")
        if target_version != self._current_version:
            self._current_version = target_version
            self._applied_versions.append(target_version)
        return self.status()

    def load_seed_data(self, records: list[dict[str, Any]]) -> int:
        self._seeded.extend(records)
        return len(records)

    def status(self) -> DatabaseVersionStatus:
        return DatabaseVersionStatus(
            current_version=self._current_version,
            applied_versions=list(self._applied_versions),
            seeded_records=len(self._seeded),
        )
