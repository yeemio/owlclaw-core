"""Security audit log model for sanitizer/risk/masking events."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SecurityAuditEvent:
    """Single security audit event."""

    event_type: str
    source: str
    details: dict[str, Any]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class FileSecurityAuditBackend:
    """Append-only JSONL audit backend."""

    def __init__(self, file_path: str | Path) -> None:
        self._path = Path(file_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: SecurityAuditEvent) -> None:
        payload = {
            "event_type": event.event_type,
            "source": event.source,
            "details": event.details,
            "created_at": event.created_at.isoformat(),
        }
        with self._path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(payload, ensure_ascii=False) + "\n")


class SecurityAuditLog:
    """In-memory audit sink for security-related events."""

    def __init__(self, backend: FileSecurityAuditBackend | None = None) -> None:
        if backend is None:
            backend = self._build_backend_from_env()
        self._events: list[SecurityAuditEvent] = []
        self._backend = backend

    @staticmethod
    def _build_backend_from_env() -> FileSecurityAuditBackend | None:
        backend_name = os.getenv("OWLCLAW_SECURITY_AUDIT_BACKEND", "").strip().lower()
        if backend_name != "file":
            return None
        file_path = os.getenv("OWLCLAW_SECURITY_AUDIT_FILE", "security_audit.log.jsonl")
        return FileSecurityAuditBackend(file_path)

    def record(self, event_type: str, source: str, details: dict[str, Any]) -> SecurityAuditEvent:
        """Record one event and emit debug log."""
        event = SecurityAuditEvent(event_type=event_type, source=source, details=dict(details))
        self._events.append(event)
        if self._backend is not None:
            self._backend.write(event)
        logger.info("security audit event type=%s source=%s", event_type, source)
        return event

    def list_events(self) -> list[SecurityAuditEvent]:
        """Return all recorded events."""
        return list(self._events)
