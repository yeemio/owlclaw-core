"""Governance proxy for wrapping direct LLM calls with runtime safeguards."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from owlclaw.config.loader import YAMLConfigLoader
from owlclaw.governance.ledger_inmemory import InMemoryLedger
from owlclaw.integrations import llm as llm_integration

logger = logging.getLogger(__name__)


class GovernanceRejectedError(RuntimeError):
    """Raised when a governance gate blocks an LLM call."""


@dataclass
class _CircuitState:
    state: str = "closed"  # closed | open | half_open
    consecutive_failures: int = 0
    opened_at: float = 0.0
    half_open_calls: int = 0


class GovernanceProxy:
    """Wrap LLM calls with budget, rate-limit, circuit-breaker, and audit."""

    def __init__(
        self,
        *,
        daily_limit_usd: Decimal = Decimal("10"),
        monthly_limit_usd: Decimal = Decimal("200"),
        default_qps: int = 10,
        per_service_qps: dict[str, int] | None = None,
        failure_threshold: int = 5,
        recovery_timeout_seconds: int = 60,
        half_open_max_calls: int = 3,
        tenant_id: str = "default",
        agent_id: str = "mionyee-governance-proxy",
        passthrough_on_error: bool = True,
        llm_call: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self.daily_limit_usd = daily_limit_usd
        self.monthly_limit_usd = monthly_limit_usd
        self.default_qps = max(1, int(default_qps))
        self.per_service_qps = per_service_qps or {}
        self.failure_threshold = max(1, int(failure_threshold))
        self.recovery_timeout_seconds = max(1, int(recovery_timeout_seconds))
        self.half_open_max_calls = max(1, int(half_open_max_calls))
        self.tenant_id = tenant_id
        self.agent_id = agent_id
        self.passthrough_on_error = bool(passthrough_on_error)
        self._llm_call = llm_call or llm_integration.acompletion

        self._daily_usage: dict[tuple[str, date], Decimal] = {}
        self._monthly_usage: dict[tuple[str, int, int], Decimal] = {}
        self._rate_windows: dict[str, deque[float]] = {}
        self._circuits: dict[str, _CircuitState] = {}
        self._lock = asyncio.Lock()
        self._ledger = InMemoryLedger(max_records=20_000)

    @property
    def ledger(self) -> InMemoryLedger:
        """Expose audit ledger for query/reporting."""
        return self._ledger

    @classmethod
    def from_config(cls, path: str) -> GovernanceProxy:
        """Create proxy from owlclaw.yaml-like config."""
        payload = YAMLConfigLoader.load_dict(path)
        governance = payload.get("governance", {}) if isinstance(payload, dict) else {}
        if not isinstance(governance, dict):
            governance = {}
        budget_cfg = governance.get("budget", {}) if isinstance(governance.get("budget"), dict) else {}
        rate_cfg = governance.get("rate_limit", {}) if isinstance(governance.get("rate_limit"), dict) else {}
        cb_cfg = governance.get("circuit_breaker", {}) if isinstance(governance.get("circuit_breaker"), dict) else {}
        proxy_cfg = governance.get("proxy", {}) if isinstance(governance.get("proxy"), dict) else {}

        per_service = rate_cfg.get("per_service", {})
        if not isinstance(per_service, dict):
            per_service = {}

        return cls(
            daily_limit_usd=Decimal(str(budget_cfg.get("daily_limit_usd", "10"))),
            monthly_limit_usd=Decimal(str(budget_cfg.get("monthly_limit_usd", "200"))),
            default_qps=int(rate_cfg.get("default_qps", 10)),
            per_service_qps={str(k): max(1, int(v)) for k, v in per_service.items()},
            failure_threshold=int(cb_cfg.get("failure_threshold", 5)),
            recovery_timeout_seconds=int(cb_cfg.get("recovery_timeout_seconds", 60)),
            half_open_max_calls=int(cb_cfg.get("half_open_max_calls", 3)),
            tenant_id=str(proxy_cfg.get("tenant_id", "default")),
            agent_id=str(proxy_cfg.get("agent_id", "mionyee-governance-proxy")),
            passthrough_on_error=bool(proxy_cfg.get("passthrough_on_error", True)),
        )

    async def acompletion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        caller: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Governed LLM call entrypoint compatible with litellm usage."""
        started = time.perf_counter()
        try:
            async with self._lock:
                self._check_budget(caller)
                self._check_rate_limit(caller)
                self._check_circuit(caller)
        except GovernanceRejectedError as exc:
            await self._record_event(
                caller=caller,
                model=model,
                status="blocked",
                reason=str(exc),
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )
            raise
        except Exception as exc:
            if not self.passthrough_on_error:
                logger.exception("GovernanceProxy gate error")
                raise RuntimeError("Governance proxy call failed.") from exc
            logger.warning("GovernanceProxy gate error, passthrough enabled: %s", exc)
            return await self._passthrough_call(model=model, messages=messages, **kwargs)

        try:
            raw_response = await self._llm_call(model=model, messages=messages, **kwargs)
            response_dict = self._normalize_response(raw_response)
            estimated_cost = self._estimate_cost(response_dict)
            async with self._lock:
                self._consume_budget(caller, estimated_cost)
                self._on_success(caller)
            await self._record_event(
                caller=caller,
                model=model,
                status="success",
                reason="",
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                output=response_dict,
                estimated_cost=estimated_cost,
            )
            return response_dict
        except Exception as exc:
            async with self._lock:
                self._on_failure(caller)
            safe_reason = self._safe_reason(exc)
            await self._record_event(
                caller=caller,
                model=model,
                status="failure",
                reason=safe_reason,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
            )
            logger.exception("GovernanceProxy LLM call failed")
            raise RuntimeError("Governance proxy call failed.") from exc

    async def _passthrough_call(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        raw_response = await self._llm_call(model=model, messages=messages, **kwargs)
        return self._normalize_response(raw_response)

    def _check_budget(self, caller: str) -> None:
        today = datetime.now(timezone.utc).date()
        month_key = (caller, today.year, today.month)
        day_key = (caller, today)
        day_used = self._daily_usage.get(day_key, Decimal("0"))
        month_used = self._monthly_usage.get(month_key, Decimal("0"))
        if day_used >= self.daily_limit_usd:
            raise GovernanceRejectedError("budget_exhausted_daily")
        if month_used >= self.monthly_limit_usd:
            raise GovernanceRejectedError("budget_exhausted_monthly")

    def _check_rate_limit(self, caller: str) -> None:
        now = time.monotonic()
        qps = self.per_service_qps.get(caller, self.default_qps)
        window = self._rate_windows.setdefault(caller, deque())
        while window and now - window[0] > 1.0:
            window.popleft()
        if len(window) >= qps:
            raise GovernanceRejectedError("rate_limited")
        window.append(now)

    def _check_circuit(self, caller: str) -> None:
        now = time.monotonic()
        state = self._circuits.setdefault(caller, _CircuitState())
        if state.state == "open":
            if now - state.opened_at >= self.recovery_timeout_seconds:
                state.state = "half_open"
                state.half_open_calls = 0
            else:
                raise GovernanceRejectedError("circuit_open")
        if state.state == "half_open" and state.half_open_calls >= self.half_open_max_calls:
            raise GovernanceRejectedError("circuit_half_open_limit")
        if state.state == "half_open":
            state.half_open_calls += 1

    def _on_success(self, caller: str) -> None:
        self._circuits[caller] = _CircuitState(state="closed")

    def _on_failure(self, caller: str) -> None:
        state = self._circuits.setdefault(caller, _CircuitState())
        state.consecutive_failures += 1
        if state.consecutive_failures >= self.failure_threshold:
            state.state = "open"
            state.opened_at = time.monotonic()
            state.half_open_calls = 0

    def _consume_budget(self, caller: str, cost: Decimal) -> None:
        today = datetime.now(timezone.utc).date()
        day_key = (caller, today)
        month_key = (caller, today.year, today.month)
        self._daily_usage[day_key] = self._daily_usage.get(day_key, Decimal("0")) + cost
        self._monthly_usage[month_key] = self._monthly_usage.get(month_key, Decimal("0")) + cost

    async def _record_event(
        self,
        *,
        caller: str,
        model: str,
        status: str,
        reason: str,
        elapsed_ms: int,
        output: dict[str, Any] | None = None,
        estimated_cost: Decimal = Decimal("0"),
    ) -> None:
        await self._ledger.record_execution(
            tenant_id=self.tenant_id,
            agent_id=self.agent_id,
            run_id=f"govproxy-{uuid.uuid4().hex}",
            capability_name=caller,
            task_type="llm_proxy",
            input_params={"caller": caller},
            output_result=output,
            decision_reasoning=reason or None,
            execution_time_ms=max(0, int(elapsed_ms)),
            llm_model=model,
            llm_tokens_input=0,
            llm_tokens_output=0,
            estimated_cost=estimated_cost,
            status=status,
            error_message=reason or None,
        )

    @staticmethod
    def _safe_reason(exc: Exception) -> str:
        """Return sanitized reason string for audit records."""
        return exc.__class__.__name__

    @staticmethod
    def _normalize_response(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if hasattr(raw, "model_dump") and callable(raw.model_dump):
            dumped = raw.model_dump()
            if isinstance(dumped, dict):
                return dumped
        if hasattr(raw, "dict") and callable(raw.dict):
            dumped = raw.dict()
            if isinstance(dumped, dict):
                return dumped
        return {"raw_response": str(raw)}

    @staticmethod
    def _estimate_cost(response: dict[str, Any]) -> Decimal:
        usage = response.get("usage", {})
        if not isinstance(usage, dict):
            return Decimal("0")
        total_tokens = usage.get("total_tokens")
        try:
            tokens = Decimal(str(total_tokens))
        except Exception:
            return Decimal("0")
        # Conservative default estimate when provider pricing metadata is unavailable.
        return (tokens / Decimal("1000")) * Decimal("0.002")
