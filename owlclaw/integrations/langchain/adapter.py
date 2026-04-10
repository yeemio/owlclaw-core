"""Adapter to register and execute LangChain runnables as OwlClaw capabilities."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, cast

from owlclaw.integrations.langchain.config import LangChainConfig
from owlclaw.integrations.langchain.errors import ErrorHandler
from owlclaw.integrations.langchain.metrics import MetricsCollector
from owlclaw.integrations.langchain.privacy import PrivacyMasker
from owlclaw.integrations.langchain.retry import RetryPolicy, calculate_backoff_delay, should_retry
from owlclaw.integrations.langchain.schema import SchemaBridge
from owlclaw.integrations.langchain.trace import TraceManager
from owlclaw.integrations.langchain.version import check_langchain_version

logger = logging.getLogger(__name__)


@dataclass
class RunnableConfig:
    """Configuration for one registered runnable."""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    input_transformer: Any | None = None
    output_transformer: Any | None = None
    fallback: str | None = None
    retry_policy: dict[str, Any] | None = None
    timeout_seconds: int | None = None
    enable_tracing: bool = True


class LangChainAdapter:
    """LangChain integration adapter."""

    def __init__(
        self,
        app: Any,
        config: LangChainConfig,
        *,
        schema_bridge: SchemaBridge | None = None,
        trace_manager: TraceManager | None = None,
        error_handler: ErrorHandler | None = None,
    ) -> None:
        self._app = app
        self._config = config
        self._schema_bridge = schema_bridge or SchemaBridge()
        self._trace_manager = trace_manager or TraceManager(config)
        self._error_handler = error_handler or ErrorHandler(fallback_executor=self._invoke_fallback)
        self._privacy_masker = PrivacyMasker(config.privacy.mask_patterns)
        self._metrics = MetricsCollector()

    def register_runnable(self, runnable: Any, config: RunnableConfig) -> None:
        """Register runnable as capability handler."""
        check_langchain_version(
            min_version=self._config.min_version,
            max_version=self._config.max_version,
        )
        self._validate_runnable(runnable)
        if not config.name.strip():
            raise ValueError("Runnable config name must be non-empty")
        if not config.description.strip():
            raise ValueError("Runnable config description must be non-empty")
        if not isinstance(config.input_schema, dict) or not config.input_schema:
            raise ValueError("Runnable config input_schema must be a non-empty dict")

        handler = self._create_handler(runnable, config)
        self._register_handler(config.name, handler)

    def health_status(self) -> dict[str, Any]:
        """Return health status for LangChain integration."""
        try:
            check_langchain_version(
                min_version=self._config.min_version,
                max_version=self._config.max_version,
            )
            langchain_available = True
            reason = ""
        except Exception as exc:
            langchain_available = False
            reason = str(exc)

        try:
            self._config.validate_semantics()
            config_valid = True
        except Exception:
            config_valid = False

        tracing_enabled = bool(self._config.tracing.enabled)
        langfuse_enabled = bool(self._config.tracing.langfuse_integration)
        status = "healthy" if (langchain_available and config_valid) else "degraded"
        return {
            "status": status,
            "langchain_available": langchain_available,
            "config_valid": config_valid,
            "tracing_enabled": tracing_enabled,
            "langfuse_enabled": langfuse_enabled,
            "reason": reason,
        }

    def _create_handler(self, runnable: Any, config: RunnableConfig):
        """Create wrapped capability handler."""

        async def handler(session: dict[str, Any]) -> dict[str, Any]:
            context = session.get("context") if isinstance(session, dict) else None
            payload = session.get("input", session) if isinstance(session, dict) else {}
            if not isinstance(payload, dict):
                payload = {"input": payload}
            return await self.execute(runnable, payload, context, config)

        return handler

    async def execute(
        self,
        runnable: Any,
        input_data: dict[str, Any],
        context: Any,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        """Execute runnable with validation, trace, and error mapping."""
        started_at = time.perf_counter()
        span = None
        if config.enable_tracing:
            span = self._trace_manager.create_span(
                name=f"langchain_{config.name}",
                input_data=input_data,
                context=context if isinstance(context, dict) else None,
            )

        retry_policy = self._build_retry_policy(config.retry_policy)
        last_error: Exception | None = None
        attempts = 0
        governance_result = await self._validate_governance(config.name, context)
        if not governance_result.get("allowed", True):
            status_code = int(governance_result.get("status_code", 403))
            reason = str(governance_result.get("reason", "capability denied by governance policy"))
            payload = self._error_handler.create_error_response(
                error_type="PolicyDeniedError" if status_code == 403 else "RateLimitError",
                message=reason,
                status_code=status_code,
                details={"capability": config.name},
            )
            headers = governance_result.get("headers")
            if isinstance(headers, dict):
                payload["headers"] = headers
            await self._record_execution(
                context=context,
                config=config,
                input_data=input_data,
                output_data=None,
                status="blocked",
                error_message=reason,
                started_at=started_at,
                span=span,
                attempts=attempts,
                governance_result=governance_result,
            )
            duration_ms = max(0, int((time.perf_counter() - started_at) * 1000))
            self._metrics.record_execution(
                capability=config.name,
                status="blocked",
                duration_ms=duration_ms,
            )
            return payload

        try:
            self._schema_bridge.validate_input(input_data, config.input_schema)
            transformed_input = self._schema_bridge.transform_input(input_data, config.input_transformer)

            for attempt in range(1, retry_policy.max_attempts + 1):
                attempts = attempt
                try:
                    result = await self._execute_with_timeout(runnable, transformed_input, config.timeout_seconds)
                    transformed_output = self._schema_bridge.transform_output(result, config.output_transformer)
                    if span is not None:
                        span.end(output=transformed_output)
                    await self._record_execution(
                        context=context,
                        config=config,
                        input_data=input_data,
                        output_data=transformed_output,
                        status="success",
                        error_message=None,
                        started_at=started_at,
                        span=span,
                        attempts=attempts,
                        governance_result=governance_result,
                    )
                    duration_ms = max(0, int((time.perf_counter() - started_at) * 1000))
                    self._metrics.record_execution(
                        capability=config.name,
                        status="success",
                        duration_ms=duration_ms,
                        retry_count=max(0, attempts - 1),
                    )
                    return transformed_output
                except Exception as exc:
                    last_error = exc
                    if should_retry(exc, attempt=attempt, policy=retry_policy):
                        delay_seconds = calculate_backoff_delay(attempt, retry_policy)
                        if delay_seconds > 0:
                            await asyncio.sleep(delay_seconds)
                        logger.warning(
                            "Retrying runnable=%s attempt=%d/%d after error=%s",
                            config.name,
                            attempt,
                            retry_policy.max_attempts,
                            type(exc).__name__,
                        )
                        continue
                    raise
        except Exception as exc:
            effective_error = last_error or exc
            if span is not None:
                span.record_error(effective_error)
                span.end(output={"error": str(effective_error)})
            await self._record_execution(
                context=context,
                config=config,
                input_data=input_data,
                output_data=None,
                status="error",
                error_message=str(effective_error),
                started_at=started_at,
                span=span,
                attempts=attempts or 1,
                governance_result=governance_result,
            )
            duration_ms = max(0, int((time.perf_counter() - started_at) * 1000))
            self._metrics.record_execution(
                capability=config.name,
                status="error",
                duration_ms=duration_ms,
                error_type=type(effective_error).__name__,
                fallback_used=bool(config.fallback),
                retry_count=max(0, attempts - 1),
            )
            if config.fallback:
                return await self._error_handler.handle_fallback(
                    config.fallback,
                    input_data,
                    context,
                    effective_error,
                )
            return self._error_handler.map_exception(effective_error)
        raise RuntimeError("LangChain adapter execution finished without result")

    def metrics(self, format: str = "json") -> dict[str, Any] | str:
        """Export collected metrics as JSON (default) or Prometheus text format."""
        normalized = format.strip().lower()
        if normalized == "json":
            return self._metrics.export_json()
        if normalized in {"prom", "prometheus", "text"}:
            return self._metrics.export_prometheus()
        raise ValueError("format must be one of: json, prom, prometheus, text")

    async def _execute_with_timeout(self, runnable: Any, input_data: Any, timeout_seconds: int | None) -> Any:
        """Execute runnable with async preference and timeout support."""
        self._validate_runnable(runnable)
        coroutine = self._as_coroutine(runnable, input_data)
        if timeout_seconds is None:
            return await coroutine
        return await asyncio.wait_for(coroutine, timeout=timeout_seconds)

    async def execute_stream(
        self,
        runnable: Any,
        input_data: dict[str, Any],
        context: Any,
        config: RunnableConfig,
    ):
        """Execute runnable in streaming mode and yield OwlClaw event payloads."""
        span = None
        if config.enable_tracing:
            span = self._trace_manager.create_span(
                name=f"langchain_stream_{config.name}",
                input_data=input_data,
                context=context if isinstance(context, dict) else None,
            )

        governance_result = await self._validate_governance(config.name, context)
        if not governance_result.get("allowed", True):
            status_code = int(governance_result.get("status_code", 403))
            reason = str(governance_result.get("reason", "capability denied by governance policy"))
            yield self._error_handler.create_error_response(
                error_type="PolicyDeniedError" if status_code == 403 else "RateLimitError",
                message=reason,
                status_code=status_code,
                details={"capability": config.name},
            ) | {"type": "error"}
            return

        transformed_input = self._schema_bridge.transform_input(input_data, config.input_transformer)
        chunks: list[Any] = []
        try:
            if callable(getattr(runnable, "astream", None)):
                async for chunk in runnable.astream(transformed_input):
                    chunks.append(chunk)
                    yield {"type": "chunk", "data": chunk}
            elif callable(getattr(runnable, "stream", None)):
                for chunk in runnable.stream(transformed_input):
                    chunks.append(chunk)
                    yield {"type": "chunk", "data": chunk}
            else:
                raise TypeError(
                    f"Unsupported stream runnable type: {type(runnable).__name__}. "
                    "Runnable must implement stream() or astream()."
                )
            final_output = self._schema_bridge.transform_output(chunks, config.output_transformer)
            if span is not None:
                span.end(output=final_output)
            yield {"type": "final", "data": final_output}
        except Exception as exc:
            if span is not None:
                span.record_error(exc)
                span.end(output={"error": str(exc)})
            yield {"type": "error", "error": self._error_handler.map_exception(exc)["error"]}

    @staticmethod
    def _as_coroutine(runnable: Any, input_data: Any) -> Any:
        if callable(getattr(runnable, "ainvoke", None)):
            return runnable.ainvoke(input_data)
        if callable(getattr(runnable, "invoke", None)):
            loop = asyncio.get_running_loop()
            return loop.run_in_executor(None, runnable.invoke, input_data)
        raise TypeError(
            f"Unsupported runnable type: {type(runnable).__name__}. "
            "Runnable must implement invoke() or ainvoke()."
        )

    @staticmethod
    def _validate_runnable(runnable: Any) -> None:
        if callable(getattr(runnable, "ainvoke", None)) or callable(getattr(runnable, "invoke", None)):
            return
        raise TypeError(
            f"Unsupported runnable type: {type(runnable).__name__}. "
            "Runnable must implement invoke() or ainvoke()."
        )

    def _register_handler(self, name: str, handler: Any) -> None:
        registry = getattr(self._app, "registry", None)
        if registry is not None:
            registry.register_handler(name, handler)
            return

        register_capability = getattr(self._app, "register_capability", None)
        if callable(register_capability):
            register_capability(name=name, handler=handler)
            return

        raise RuntimeError(
            "App does not expose capability registry. "
            "Expected app.registry.register_handler(...) or app.register_capability(...)."
        )

    @staticmethod
    def _build_retry_policy(raw_policy: dict[str, Any] | None) -> RetryPolicy:
        if raw_policy is None:
            return RetryPolicy(max_attempts=1, retryable_errors=[])
        return RetryPolicy(
            max_attempts=int(raw_policy.get("max_attempts", 1)),
            initial_delay_ms=int(raw_policy.get("initial_delay_ms", 0)),
            max_delay_ms=int(raw_policy.get("max_delay_ms", 0)),
            backoff_multiplier=float(raw_policy.get("backoff_multiplier", 2.0)),
            retryable_errors=list(raw_policy.get("retryable_errors", [])),
        )

    async def _validate_governance(self, capability_name: str, context: Any) -> dict[str, Any]:
        validator = getattr(self._app, "validate_capability_execution", None)
        if not callable(validator):
            return {"allowed": True}
        payload = {
            "capability_name": capability_name,
            "context": context if isinstance(context, dict) else {},
        }
        result = validator(**payload)
        if asyncio.iscoroutine(result):
            result = await result
        if isinstance(result, dict):
            return result
        return {"allowed": bool(result)}

    async def _record_execution(
        self,
        *,
        context: Any,
        config: RunnableConfig,
        input_data: dict[str, Any],
        output_data: dict[str, Any] | None,
        status: str,
        error_message: str | None,
        started_at: float,
        span: Any | None,
        attempts: int,
        governance_result: dict[str, Any],
    ) -> None:
        context_dict = context if isinstance(context, dict) else {}
        duration_ms = max(0, int((time.perf_counter() - started_at) * 1000))
        input_payload = (
            self._privacy_masker.mask_data(input_data)
            if self._config.privacy.mask_inputs
            else input_data
        )
        output_payload = (
            self._privacy_masker.mask_data(output_data)
            if self._config.privacy.mask_outputs
            else output_data
        )
        payload = {
            "event_type": "langchain_execution",
            "capability_name": config.name,
            "input": input_payload,
            "output": output_payload,
            "status": status,
            "duration_ms": duration_ms,
            "error_message": error_message,
            "trace_id": getattr(span, "trace_id", context_dict.get("trace_id")),
            "span_id": getattr(span, "span_id", None),
            "user_id": context_dict.get("user_id"),
            "agent_id": context_dict.get("agent_id"),
            "attempts": attempts,
            "governance": governance_result,
        }

        record_langchain = getattr(self._app, "record_langchain_execution", None)
        if callable(record_langchain):
            result = record_langchain(payload)
            if asyncio.iscoroutine(result):
                await result
            return

        record_execution = getattr(self._app, "record_execution", None)
        if not callable(record_execution):
            return

        run_id = str(context_dict.get("run_id") or uuid.uuid4())
        result = record_execution(
            tenant_id=str(context_dict.get("tenant_id", "default")),
            agent_id=str(context_dict.get("agent_id", "unknown")),
            run_id=run_id,
            capability_name=config.name,
            task_type="langchain_execution",
            input_params=input_payload,
            output_result=output_payload,
            decision_reasoning=str(governance_result.get("reason", "")) or None,
            execution_time_ms=duration_ms,
            llm_model="langchain",
            llm_tokens_input=0,
            llm_tokens_output=0,
            estimated_cost=0.0,
            status=status,
            error_message=error_message,
        )
        if asyncio.iscoroutine(result):
            await result

    async def _invoke_fallback(
        self,
        fallback_name: str,
        input_data: dict[str, Any],
        context: Any,
        error: Exception,
    ) -> dict[str, Any]:
        registry = getattr(self._app, "registry", None)
        invoke_handler = getattr(registry, "invoke_handler", None)
        if not callable(invoke_handler):
            raise RuntimeError("fallback capability cannot be invoked: app.registry.invoke_handler unavailable")

        session: dict[str, Any] = dict(input_data)
        session["_original_error"] = str(error)
        if isinstance(context, dict):
            session["context"] = context
        result = invoke_handler(fallback_name, session=session)
        if asyncio.iscoroutine(result):
            return cast(dict[str, Any], await result)
        return cast(dict[str, Any], result)
