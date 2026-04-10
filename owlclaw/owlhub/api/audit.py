"""Audit logging for OwlHub write operations."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from owlclaw.owlhub.api.auth import Principal, get_current_principal

current_principal_type = Annotated[Principal, Depends(get_current_principal)]


class AuditLogger:
    """Append/query JSONL audit logs."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(os.getenv("OWLHUB_AUDIT_LOG", "./.owlhub/audit.log.jsonl")).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, *, event_type: str, principal: Principal, details: dict[str, Any]) -> None:
        payload = {
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_id": principal.user_id,
            "role": principal.role,
            "details": details,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def query(self, *, event_type: str = "", limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if event_type and str(payload.get("event_type", "")) != event_type:
                continue
            rows.append(payload)
        rows.sort(key=lambda item: str(item.get("timestamp", "")), reverse=True)
        return rows[:limit]


def create_audit_router(audit: AuditLogger) -> APIRouter:
    """Create admin-only audit query endpoint."""
    router = APIRouter(prefix="/api/v1/audit", tags=["audit"])

    @router.get("")
    def list_audit(
        principal: current_principal_type,
        event_type: str = "",
        limit: int = Query(100, ge=1, le=1000),
    ) -> list[dict[str, Any]]:
        if principal.role != "admin":
            raise HTTPException(status_code=403, detail="admin role required")
        return audit.query(event_type=event_type, limit=limit)

    return router
