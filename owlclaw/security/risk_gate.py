"""Risk gate: evaluate and control high-risk operation execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4


class RiskDecision(str, Enum):
    """Risk decision outputs."""

    EXECUTE = "execute"
    PAUSE = "pause"
    REJECT = "reject"


@dataclass
class PendingRiskOperation:
    """Operation pending confirmation."""

    operation_id: UUID
    created_at: datetime
    capability_name: str
    risk_level: str
    reason: str


class RiskGate:
    """Simple risk policy engine with confirmation workflow."""

    def __init__(
        self,
        confirmation_timeout_seconds: int = 300,
        audit_log: Any | None = None,
        audit_source: str = "risk_gate",
    ) -> None:
        self._timeout = max(1, confirmation_timeout_seconds)
        self._pending: dict[UUID, PendingRiskOperation] = {}
        self._audit_log = audit_log
        self._audit_source = audit_source

    def evaluate(
        self,
        capability_name: str,
        *,
        risk_level: str = "low",
        requires_confirmation: bool = False,
        budget_ratio: float = 0.0,
    ) -> tuple[RiskDecision, UUID | None]:
        """Return risk decision and optional pending operation id."""
        normalized = (risk_level or "low").strip().lower()
        if normalized == "critical":
            return self._pause(capability_name, normalized, "critical capability")
        if requires_confirmation:
            return self._pause(capability_name, normalized, "manual confirmation required")
        if normalized == "high" and budget_ratio >= 0.8:
            return self._pause(capability_name, normalized, "budget ratio exceeds threshold")
        if normalized not in {"low", "medium", "high", "critical"}:
            self._audit(
                "risk_rejected",
                capability_name=capability_name,
                risk_level=normalized,
                reason="unsupported_risk_level",
            )
            return RiskDecision.REJECT, None
        self._audit(
            "risk_executed",
            capability_name=capability_name,
            risk_level=normalized,
            requires_confirmation=requires_confirmation,
            budget_ratio=budget_ratio,
        )
        return RiskDecision.EXECUTE, None

    def _pause(self, capability_name: str, risk_level: str, reason: str) -> tuple[RiskDecision, UUID]:
        op_id = uuid4()
        self._pending[op_id] = PendingRiskOperation(
            operation_id=op_id,
            created_at=datetime.now(timezone.utc),
            capability_name=capability_name,
            risk_level=risk_level,
            reason=reason,
        )
        self._audit(
            "risk_paused",
            operation_id=str(op_id),
            capability_name=capability_name,
            risk_level=risk_level,
            reason=reason,
        )
        return RiskDecision.PAUSE, op_id

    def confirm(self, operation_id: UUID) -> bool:
        """Confirm a pending operation and remove it from queue."""
        self._expire_pending()
        success = self._pending.pop(operation_id, None) is not None
        self._audit(
            "risk_confirmed" if success else "risk_confirm_missing",
            operation_id=str(operation_id),
        )
        return success

    def reject(self, operation_id: UUID) -> bool:
        """Reject a pending operation and remove it from queue."""
        self._expire_pending()
        success = self._pending.pop(operation_id, None) is not None
        self._audit(
            "risk_rejected_manual" if success else "risk_reject_missing",
            operation_id=str(operation_id),
        )
        return success

    def _expire_pending(self) -> int:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=self._timeout)
        expired = [op_id for op_id, op in self._pending.items() if op.created_at < cutoff]
        for op_id in expired:
            self._pending.pop(op_id, None)
            self._audit("risk_timeout", operation_id=str(op_id))
        return len(expired)

    def pending_count(self) -> int:
        """Return active pending operation count after expiration check."""
        self._expire_pending()
        return len(self._pending)

    def _audit(self, event_type: str, **details: Any) -> None:
        if self._audit_log is None:
            return
        record = getattr(self._audit_log, "record", None)
        if callable(record):
            record(event_type=event_type, source=self._audit_source, details=details)
