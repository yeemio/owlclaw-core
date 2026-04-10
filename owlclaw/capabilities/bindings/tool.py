"""Binding tool wrapper that dispatches to executors and records Ledger events."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Protocol

from owlclaw.capabilities.bindings.executor import BindingExecutorRegistry
from owlclaw.capabilities.bindings.schema import BindingConfig, SQLBindingConfig
from owlclaw.security import DataMasker, InputSanitizer, RiskDecision, RiskGate

logger = logging.getLogger(__name__)

# Safe message for ledger when binding execution fails; do not persist raw exception text.
LEDGER_ERROR_MESSAGE = "Binding execution failed."


def _safe_ledger_error_message(exc: BaseException) -> str:
    """Return a safe, non-leaking message for ledger records."""
    return LEDGER_ERROR_MESSAGE


class LedgerProtocol(Protocol):
    """Protocol for governance ledger integration."""

    async def record_execution(
        self,
        tenant_id: str,
        agent_id: str,
        run_id: str,
        capability_name: str,
        task_type: str,
        input_params: dict[str, Any],
        output_result: dict[str, Any] | None,
        decision_reasoning: str | None,
        execution_time_ms: int,
        llm_model: str,
        llm_tokens_input: int,
        llm_tokens_output: int,
        estimated_cost: Decimal,
        status: str,
        error_message: str | None = None,
    ) -> None:
        """Record one invocation."""


class BindingTool:
    """Auto-generated callable tool for declarative binding execution."""

    def __init__(
        self,
        name: str,
        description: str,
        parameters_schema: dict[str, Any],
        binding_config: BindingConfig,
        executor_registry: BindingExecutorRegistry,
        ledger: LedgerProtocol | None = None,
        *,
        tenant_id: str = "default",
        agent_id: str = "binding-tool",
        risk_level: str = "low",
        requires_confirmation: bool = False,
        task_type: str | None = None,
        constraints: dict[str, Any] | None = None,
        focus: list[str] | None = None,
        sanitizer: InputSanitizer | None = None,
        masker: DataMasker | None = None,
        risk_gate: RiskGate | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters_schema = parameters_schema
        self.binding_config = binding_config
        self.executor_registry = executor_registry
        self.ledger = ledger
        self.tenant_id = tenant_id
        self.agent_id = agent_id
        self.risk_level = risk_level
        self.requires_confirmation = requires_confirmation
        self.task_type = task_type or ""
        self.constraints = constraints or {}
        self.focus = list(focus or [])
        self._sanitizer = sanitizer or InputSanitizer()
        self._masker = masker or DataMasker()
        self._risk_gate = risk_gate or RiskGate()

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        """Execute binding with timing and optional ledger recording."""
        start = time.monotonic()
        executor = self.executor_registry.get(self.binding_config.type)
        run_id = str(uuid.uuid4())
        sanitized_parameters = self._sanitize_parameters(kwargs)
        try:
            self._enforce_risk_policy()
            result = await executor.execute(self.binding_config, sanitized_parameters)
            result = self._mask_result(result)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            status = self._derive_status(result)
            await self._record_ledger(
                run_id=run_id,
                parameters=sanitized_parameters,
                result_summary=self._summarize(result),
                elapsed_ms=elapsed_ms,
                status=status,
                error_message=None,
            )
            return result
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            safe_message = _safe_ledger_error_message(exc)
            await self._record_ledger(
                run_id=run_id,
                parameters=sanitized_parameters,
                result_summary=self._summarize({"error": safe_message}),
                elapsed_ms=elapsed_ms,
                status="error",
                error_message=safe_message,
            )
            raise

    async def _record_ledger(
        self,
        *,
        run_id: str,
        parameters: dict[str, Any],
        result_summary: str,
        elapsed_ms: int,
        status: str,
        error_message: str | None,
    ) -> None:
        if self.ledger is None:
            return
        try:
            await self.ledger.record_execution(
                tenant_id=self.tenant_id,
                agent_id=self.agent_id,
                run_id=run_id,
                capability_name=self.name,
                task_type=f"binding:{self.binding_config.type}",
                input_params={
                    "tool_name": self.name,
                    "binding_type": self.binding_config.type,
                    "mode": self.binding_config.mode,
                    "parameters": parameters,
                },
                output_result={
                    "result_summary": result_summary,
                    "elapsed_ms": elapsed_ms,
                    "status": status,
                    "binding_type": self.binding_config.type,
                    "mode": self.binding_config.mode,
                },
                decision_reasoning=None,
                execution_time_ms=elapsed_ms,
                llm_model="binding-executor",
                llm_tokens_input=0,
                llm_tokens_output=0,
                estimated_cost=Decimal("0"),
                status=status,
                error_message=error_message,
            )
        except Exception:
            logger.exception("Failed to record binding execution for tool '%s'", self.name)

    @staticmethod
    def _summarize(result: dict[str, Any], max_length: int = 500) -> str:
        """Summarize invocation result payload for ledger storage."""
        text = json.dumps(result, ensure_ascii=False, default=str)
        if len(text) > max_length:
            return f"{text[:max_length]}...(truncated)"
        return text

    @staticmethod
    def _derive_status(result: dict[str, Any]) -> str:
        raw = result.get("status")
        if isinstance(raw, str) and raw.strip().lower() == "shadow":
            return "shadow"
        return "success"

    def _sanitize_parameters(self, parameters: dict[str, Any]) -> dict[str, Any]:
        return {key: self._sanitize_value(value) for key, value in parameters.items()}

    def _sanitize_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._sanitizer.sanitize(value, source=f"binding:{self.name}").sanitized
        if isinstance(value, list):
            return [self._sanitize_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._sanitize_value(item) for item in value)
        if isinstance(value, Mapping):
            return {str(k): self._sanitize_value(v) for k, v in value.items()}
        return value

    def _mask_result(self, result: dict[str, Any]) -> dict[str, Any]:
        masked = self._masker.mask(result)
        return masked if isinstance(masked, dict) else {"value": masked}

    def _enforce_risk_policy(self) -> None:
        requires_confirmation = self.requires_confirmation
        if isinstance(self.binding_config, SQLBindingConfig) and self._is_sql_write(self.binding_config.query):
            if self.risk_level not in {"high", "critical"}:
                raise PermissionError("SQL write bindings require risk_level high or critical")
            requires_confirmation = True
        decision, _op_id = self._risk_gate.evaluate(
            self.name,
            risk_level=self.risk_level,
            requires_confirmation=requires_confirmation,
        )
        if decision != RiskDecision.EXECUTE:
            raise PermissionError(f"Binding execution blocked by risk policy: {decision.value}")

    @staticmethod
    def _is_sql_write(query: str) -> bool:
        return not query.lstrip().lower().startswith("select")
