"""Review workflow endpoints for OwlHub API."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

logger = logging.getLogger(__name__)

from owlclaw.owlhub.api.auth import Principal, get_current_principal
from owlclaw.owlhub.api.schemas import AppealRequest, RejectRequest, ReviewRecordResponse

router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"])
current_principal_type = Annotated[Principal, Depends(get_current_principal)]


@router.get("/pending", response_model=list[ReviewRecordResponse])
def list_pending_reviews(request: Request, principal: current_principal_type) -> list[ReviewRecordResponse]:
    if principal.role not in {"reviewer", "admin"}:
        raise HTTPException(status_code=403, detail="reviewer role required")
    system = request.app.state.review_system
    records = system.list_pending_records()
    return [_to_response(item) for item in records]


@router.post("/{review_id}/approve", response_model=ReviewRecordResponse)
def approve_review(review_id: str, request: Request, principal: current_principal_type) -> ReviewRecordResponse:
    if principal.role not in {"reviewer", "admin"}:
        raise HTTPException(status_code=403, detail="reviewer role required")
    system = request.app.state.review_system
    try:
        system.assign_reviewer(review_id=review_id, reviewer=principal.user_id)
        record = system.approve(review_id=review_id, reviewer=principal.user_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="review not found") from exc
    except ValueError as exc:
        logger.debug("Approve review failed: %s", exc)
        raise HTTPException(status_code=409, detail="operation_failed") from exc
    return _to_response(record)


@router.post("/{review_id}/reject", response_model=ReviewRecordResponse)
def reject_review(
    review_id: str,
    payload: RejectRequest,
    request: Request,
    principal: current_principal_type,
) -> ReviewRecordResponse:
    if principal.role not in {"reviewer", "admin"}:
        raise HTTPException(status_code=403, detail="reviewer role required")
    system = request.app.state.review_system
    try:
        system.assign_reviewer(review_id=review_id, reviewer=principal.user_id)
        record = system.reject(review_id=review_id, reviewer=principal.user_id, reason=payload.reason)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="review not found") from exc
    except ValueError as exc:
        logger.debug("Reject review failed: %s", exc)
        raise HTTPException(status_code=409, detail="operation_failed") from exc
    return _to_response(record)


@router.post("/{review_id}/appeal")
def appeal_review(
    review_id: str,
    payload: AppealRequest,
    request: Request,
    principal: current_principal_type,
) -> dict[str, str]:
    system = request.app.state.review_system
    review = None
    try:
        review = next(item for item in system.list_records() if item.review_id == review_id)
    except StopIteration as exc:
        raise HTTPException(status_code=404, detail="review not found") from exc

    if principal.role != "admin" and not _principal_matches_publisher(principal.user_id, review.publisher):
        raise HTTPException(status_code=403, detail="publisher role required")

    try:
        appeal = system.appeal(review_id=review_id, publisher=review.publisher, reason=payload.reason)
    except ValueError as exc:
        logger.debug("Appeal review failed: %s", exc)
        raise HTTPException(status_code=409, detail="operation_failed") from exc
    except PermissionError as exc:
        logger.debug("Appeal permission denied: %s", exc)
        raise HTTPException(status_code=403, detail="operation_failed") from exc
    return {"review_id": appeal.review_id, "publisher": appeal.publisher, "reason": appeal.reason}


def _to_response(record) -> ReviewRecordResponse:
    return ReviewRecordResponse(
        review_id=record.review_id,
        skill_name=record.skill_name,
        version=record.version,
        publisher=record.publisher,
        status=record.status.value if hasattr(record.status, "value") else str(record.status),
        comments=record.comments,
        reviewed_at=record.reviewed_at,
    )


def _principal_matches_publisher(user_id: str, publisher: str) -> bool:
    identity = user_id.strip().lower()
    target = publisher.strip().lower()
    return identity == target or (":" in identity and identity.split(":", 1)[1] == target)
