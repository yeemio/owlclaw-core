"""Governance integration adapter for webhook trigger pipeline."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from owlclaw.triggers.webhook.types import GovernanceContext, GovernanceDecision, ValidationError, ValidationResult


class GovernancePolicyProtocol(Protocol):
    """Protocol for governance policy evaluation backend."""

    async def check_permission(self, context: GovernanceContext) -> dict[str, Any]: ...

    async def check_rate_limit(self, context: GovernanceContext) -> dict[str, Any]: ...


class GovernanceAuditProtocol(Protocol):
    """Protocol for governance audit sink."""

    async def record(self, event: dict[str, Any]) -> None: ...


class GovernanceClient:
    """Adapter that enforces governance checks before webhook execution."""

    def __init__(
        self,
        policy: GovernancePolicyProtocol | None = None,
        *,
        audit_sink: GovernanceAuditProtocol | None = None,
        timeout_seconds: float = 1.0,
    ) -> None:
        self._policy = policy
        self._audit_sink = audit_sink
        self._timeout_seconds = timeout_seconds

    async def check_permission(self, context: GovernanceContext) -> GovernanceDecision:
        if self._policy is None:
            return GovernanceDecision(allowed=True)
        return await self._invoke_policy_call(self._policy.check_permission(context))

    async def check_rate_limit(self, context: GovernanceContext) -> GovernanceDecision:
        if self._policy is None:
            return GovernanceDecision(allowed=True)
        return await self._invoke_policy_call(self._policy.check_rate_limit(context))

    async def validate_execution(self, context: GovernanceContext) -> ValidationResult:
        permission = await self.check_permission(context)
        if not permission.allowed:
            await self.audit_log(context, permission)
            return self._to_validation(permission, default_code="GOVERNANCE_REJECTED")
        rate_limit = await self.check_rate_limit(context)
        if not rate_limit.allowed:
            await self.audit_log(context, rate_limit)
            return self._to_validation(rate_limit, default_code="RATE_LIMITED")
        await self.audit_log(context, GovernanceDecision(allowed=True, reason="allowed"))
        return ValidationResult(valid=True)

    async def audit_log(
        self,
        context: GovernanceContext,
        decision: GovernanceDecision,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self._audit_sink is None:
            return
        event: dict[str, Any] = {
            "tenant_id": context.tenant_id,
            "endpoint_id": context.endpoint_id,
            "agent_id": context.agent_id,
            "request_id": context.request_id,
            "source_ip": context.source_ip,
            "user_agent": context.user_agent,
            "allowed": decision.allowed,
            "status_code": decision.status_code,
            "reason": decision.reason,
            "policy_limits": decision.policy_limits,
            "timestamp": context.timestamp.isoformat(),
        }
        if details:
            event["details"] = details
        await self._audit_sink.record(event)

    async def _invoke_policy_call(self, call: Any) -> GovernanceDecision:
        try:
            raw = await asyncio.wait_for(call, timeout=self._timeout_seconds)
        except TimeoutError:
            return GovernanceDecision(allowed=False, status_code=503, reason="governance timeout")
        except Exception as exc:
            return GovernanceDecision(allowed=False, status_code=503, reason=f"governance unavailable: {exc}")
        return self._coerce_decision(raw)

    @staticmethod
    def _coerce_decision(raw: dict[str, Any]) -> GovernanceDecision:
        allowed = bool(raw.get("allowed", True))
        if allowed:
            return GovernanceDecision(allowed=True, status_code=200, reason=raw.get("reason"))
        status_code = int(raw.get("status_code", 403))
        reason = raw.get("reason", "governance rejected")
        limits = raw.get("policy_limits")
        policy_limits = limits if isinstance(limits, dict) else {}
        return GovernanceDecision(
            allowed=False,
            status_code=status_code,
            reason=str(reason),
            policy_limits=policy_limits,
        )

    @staticmethod
    def _to_validation(decision: GovernanceDecision, *, default_code: str) -> ValidationResult:
        return ValidationResult(
            valid=False,
            error=ValidationError(
                code=default_code,
                message=decision.reason or default_code.lower(),
                status_code=decision.status_code if decision.status_code >= 400 else 403,
                details={"policy_limits": decision.policy_limits} if decision.policy_limits else None,
            ),
        )
