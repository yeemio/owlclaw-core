"""Signal router for unified dispatch."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Protocol

from owlclaw.triggers.signal.models import Signal, SignalResult, SignalType

logger = logging.getLogger(__name__)


class SignalHandler(Protocol):
    async def __call__(self, signal: Signal) -> SignalResult: ...


class SignalAuthorizer(Protocol):
    async def authorize(self, signal: Signal) -> tuple[bool, str | None]: ...


class SignalLedger(Protocol):
    async def record_execution(
        self,
        tenant_id: str,
        agent_id: str,
        run_id: str,
        capability_name: str,
        task_type: str,
        input_params: dict,
        output_result: dict | None,
        decision_reasoning: str | None,
        execution_time_ms: int,
        llm_model: str,
        llm_tokens_input: int,
        llm_tokens_output: int,
        estimated_cost: Decimal,
        status: str,
        error_message: str | None = None,
    ) -> None: ...


class SignalRouter:
    """Dispatch signal requests to registered handlers."""

    def __init__(
        self,
        handlers: dict[SignalType, Callable[[Signal], Awaitable[SignalResult]]],
        *,
        authorizer: SignalAuthorizer | None = None,
        ledger: SignalLedger | None = None,
    ) -> None:
        self._handlers = handlers
        self._authorizer = authorizer
        self._ledger = ledger

    async def dispatch(self, signal: Signal) -> SignalResult:
        allowed, reason = await self._authorize(signal)
        if not allowed:
            result = SignalResult(status="error", error_code="unauthorized", message=reason or "unauthorized")
            await self._record(signal, result, "blocked")
            return result

        handler = self._handlers.get(signal.type)
        if handler is None:
            result = SignalResult(status="error", error_code="bad_request", message="unsupported_signal")
            await self._record(signal, result, "failed")
            return result
        try:
            result = await handler(signal)
            await self._record(signal, result, "success")
            return result
        except Exception as exc:
            logger.warning("Signal handler failed: %s", exc)
            result = SignalResult(status="error", error_code="bad_request", message="Operation failed.")
            await self._record(signal, result, "failed")
            return result

    async def _authorize(self, signal: Signal) -> tuple[bool, str | None]:
        if self._authorizer is None:
            return True, None
        return await self._authorizer.authorize(signal)

    async def _record(self, signal: Signal, result: SignalResult, status: str) -> None:
        if self._ledger is None:
            return
        with contextlib.suppress(Exception):
            await self._ledger.record_execution(
                tenant_id=signal.tenant_id,
                agent_id=signal.agent_id,
                run_id=f"signal-{signal.id}",
                capability_name=f"signal.{signal.type.value}",
                task_type="signal",
                input_params={
                    "source": signal.source.value,
                    "operator": signal.operator,
                    "message": signal.message,
                    "focus": signal.focus,
                },
                output_result={
                    "status": result.status,
                    "message": result.message,
                    "run_id": result.run_id,
                    "error_code": result.error_code,
                },
                decision_reasoning="manual_signal_operation",
                execution_time_ms=0,
                llm_model="",
                llm_tokens_input=0,
                llm_tokens_output=0,
                estimated_cost=Decimal("0"),
                status=status,
                error_message=result.message if status == "failed" else None,
            )
