"""Approval queue for progressive migration decisions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any


class ApprovalStatus(str, Enum):
    """Approval request lifecycle states."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"
    EXPIRED = "expired"


@dataclass
class ApprovalRequest:
    """One approval request for a migration decision."""

    id: str
    tenant_id: str
    agent_id: str
    skill_name: str
    suggestion: dict[str, Any]
    reasoning: str | None
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc) + timedelta(hours=24))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    approver: str | None = None
    approved_payload: dict[str, Any] | None = None


class InMemoryApprovalQueue:
    """In-memory approval queue for Lite Mode and tests."""

    def __init__(self, *, timeout_seconds: int = 24 * 60 * 60) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._timeout_seconds = timeout_seconds
        self._items: dict[str, ApprovalRequest] = {}

    async def create(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        skill_name: str,
        suggestion: dict[str, Any],
        reasoning: str | None = None,
    ) -> ApprovalRequest:
        now = datetime.now(timezone.utc)
        request = ApprovalRequest(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            agent_id=agent_id,
            skill_name=skill_name,
            suggestion=dict(suggestion),
            reasoning=reasoning,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=self._timeout_seconds),
        )
        self._items[request.id] = request
        return request

    async def list(self, *, tenant_id: str, status: ApprovalStatus | None = None) -> list[ApprovalRequest]:
        await self.expire_pending()
        rows = [row for row in self._items.values() if row.tenant_id == tenant_id]
        if status is not None:
            rows = [row for row in rows if row.status == status]
        rows.sort(key=lambda row: row.created_at)
        return rows

    async def approve(
        self,
        request_id: str,
        *,
        approver: str,
        modified_payload: dict[str, Any] | None = None,
    ) -> ApprovalRequest:
        request = self._require_active(request_id)
        request.updated_at = datetime.now(timezone.utc)
        request.approver = approver
        if modified_payload is None:
            request.status = ApprovalStatus.APPROVED
            request.approved_payload = dict(request.suggestion)
        else:
            request.status = ApprovalStatus.MODIFIED
            request.approved_payload = dict(modified_payload)
        return request

    async def reject(self, request_id: str, *, approver: str) -> ApprovalRequest:
        request = self._require_active(request_id)
        request.updated_at = datetime.now(timezone.utc)
        request.approver = approver
        request.status = ApprovalStatus.REJECTED
        request.approved_payload = None
        return request

    async def expire_pending(self) -> int:
        now = datetime.now(timezone.utc)
        count = 0
        for request in self._items.values():
            if request.status == ApprovalStatus.PENDING and request.expires_at <= now:
                request.status = ApprovalStatus.EXPIRED
                request.updated_at = now
                count += 1
        return count

    def _require_active(self, request_id: str) -> ApprovalRequest:
        if request_id not in self._items:
            raise KeyError(f"unknown approval request: {request_id}")
        request = self._items[request_id]
        if request.status != ApprovalStatus.PENDING:
            raise ValueError(f"approval request is not pending: {request.status.value}")
        if request.expires_at <= datetime.now(timezone.utc):
            request.status = ApprovalStatus.EXPIRED
            raise ValueError("approval request has expired")
        return request
