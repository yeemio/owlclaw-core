"""Blacklist model and storage helpers for OwlHub moderation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class BlacklistEntry:
    """One blacklist entry keyed by publisher and optional skill name."""

    publisher: str
    skill_name: str | None
    reason: str
    created_at: str
    created_by: str


class BlacklistManager:
    """Persist and query blacklist entries."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: list[BlacklistEntry] = []
        self._load()

    def list_entries(self) -> list[BlacklistEntry]:
        return sorted(self._entries, key=lambda item: item.created_at, reverse=True)

    def add_entry(
        self,
        *,
        publisher: str,
        skill_name: str | None,
        reason: str,
        created_by: str,
    ) -> BlacklistEntry:
        normalized_publisher = publisher.strip()
        normalized_skill = skill_name.strip() if isinstance(skill_name, str) and skill_name.strip() else None
        existing = [item for item in self._entries if item.publisher == normalized_publisher and item.skill_name == normalized_skill]
        for item in existing:
            self._entries.remove(item)
        entry = BlacklistEntry(
            publisher=normalized_publisher,
            skill_name=normalized_skill,
            reason=reason.strip(),
            created_at=datetime.now(timezone.utc).isoformat(),
            created_by=created_by,
        )
        self._entries.append(entry)
        self._persist()
        return entry

    def remove_entry(self, *, publisher: str, skill_name: str | None = None) -> bool:
        normalized_publisher = publisher.strip()
        normalized_skill = skill_name.strip() if isinstance(skill_name, str) and skill_name.strip() else None
        before = len(self._entries)
        self._entries = [
            item
            for item in self._entries
            if not (item.publisher == normalized_publisher and item.skill_name == normalized_skill)
        ]
        changed = len(self._entries) != before
        if changed:
            self._persist()
        return changed

    def is_blocked(self, *, publisher: str, skill_name: str) -> bool:
        normalized_publisher = publisher.strip()
        normalized_skill = skill_name.strip()
        for item in self._entries:
            if item.publisher != normalized_publisher:
                continue
            if item.skill_name is None or item.skill_name == normalized_skill:
                return True
        return False

    def _load(self) -> None:
        if not self.path.exists():
            self._entries = []
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        rows = payload if isinstance(payload, list) else []
        entries: list[BlacklistEntry] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            entries.append(
                BlacklistEntry(
                    publisher=str(row.get("publisher", "")),
                    skill_name=str(row["skill_name"]) if row.get("skill_name") else None,
                    reason=str(row.get("reason", "")),
                    created_at=str(row.get("created_at", "")),
                    created_by=str(row.get("created_by", "")),
                )
            )
        self._entries = entries

    def _persist(self) -> None:
        rows = [asdict(item) for item in self._entries]
        self.path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
