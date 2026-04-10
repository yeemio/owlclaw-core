"""Blacklist management endpoints for OwlHub API."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from owlclaw.owlhub.api.auth import Principal, get_current_principal

router = APIRouter(prefix="/api/v1/admin/blacklist", tags=["moderation"])
current_principal_type = Annotated[Principal, Depends(get_current_principal)]


class BlacklistRequest(BaseModel):
    publisher: str
    skill_name: str | None = None
    reason: str = ""


@router.get("")
def list_blacklist(request: Request, principal: current_principal_type) -> list[dict[str, str | None]]:
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    manager = request.app.state.blacklist_manager
    return [
        {
            "publisher": item.publisher,
            "skill_name": item.skill_name,
            "reason": item.reason,
            "created_at": item.created_at,
            "created_by": item.created_by,
        }
        for item in manager.list_entries()
    ]


@router.post("")
def add_blacklist_entry(
    payload: BlacklistRequest,
    request: Request,
    principal: current_principal_type,
) -> dict[str, str | None]:
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    manager = request.app.state.blacklist_manager
    entry = manager.add_entry(
        publisher=payload.publisher,
        skill_name=payload.skill_name,
        reason=payload.reason or "moderation decision",
        created_by=principal.user_id,
    )
    request.app.state.audit_logger.log(
        event_type="blacklist_add",
        principal=principal,
        details={"publisher": entry.publisher, "skill_name": entry.skill_name, "reason": entry.reason},
    )
    _set_index_blacklist_flag(publisher=entry.publisher, skill_name=entry.skill_name, flagged=True)
    return {
        "publisher": entry.publisher,
        "skill_name": entry.skill_name,
        "reason": entry.reason,
        "created_at": entry.created_at,
        "created_by": entry.created_by,
    }


@router.delete("")
def remove_blacklist_entry(
    request: Request,
    principal: current_principal_type,
    publisher: str = Query(..., min_length=1),
    skill_name: str = Query(""),
) -> dict[str, object]:
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    manager = request.app.state.blacklist_manager
    removed = manager.remove_entry(publisher=publisher, skill_name=skill_name or None)
    if removed:
        request.app.state.audit_logger.log(
            event_type="blacklist_remove",
            principal=principal,
            details={"publisher": publisher, "skill_name": skill_name or None},
        )
        _set_index_blacklist_flag(publisher=publisher, skill_name=skill_name or None, flagged=False)
    return {"removed": removed, "publisher": publisher, "skill_name": skill_name or None}


def _set_index_blacklist_flag(*, publisher: str, skill_name: str | None, flagged: bool) -> None:
    index_path = Path(os.getenv("OWLHUB_INDEX_PATH", "./index.json")).resolve()
    if not index_path.exists():
        return
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    skills = payload.get("skills", [])
    if not isinstance(skills, list):
        return
    for entry in skills:
        if not isinstance(entry, dict):
            continue
        manifest = entry.get("manifest", {})
        if not isinstance(manifest, dict):
            continue
        if str(manifest.get("publisher", "")) != publisher:
            continue
        if skill_name is not None and str(manifest.get("name", "")) != skill_name:
            continue
        entry["blacklisted"] = flagged
    payload["skills"] = skills
    index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    from owlclaw.owlhub.api.routes.skills import _load_index

    _load_index.cache_clear()
