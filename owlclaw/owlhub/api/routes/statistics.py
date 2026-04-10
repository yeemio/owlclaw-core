"""Statistics export endpoints for OwlHub API."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from owlclaw.owlhub.api.auth import Principal, get_current_principal

router = APIRouter(prefix="/api/v1/statistics", tags=["statistics"])
current_principal_type = Annotated[Principal, Depends(get_current_principal)]


@router.get("/export")
def export_statistics(
    request: Request,
    principal: current_principal_type,
    format: str = Query("json", pattern="^(json|csv)$"),
) -> object:
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    tracker = request.app.state.statistics_tracker
    payload = tracker.export(format=format)
    if format == "csv":
        return PlainTextResponse(payload, media_type="text/csv")
    return json.loads(payload)
