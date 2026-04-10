"""Trace management for LangChain integration."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from owlclaw.integrations.langchain.config import LangChainConfig

logger = logging.getLogger(__name__)


@dataclass
class TraceSpan:
    """Trace span metadata for one LangChain execution."""

    name: str
    trace_id: str
    span_id: str
    started_at: float
    metadata: dict[str, Any] = field(default_factory=dict)
    _langfuse_span: Any | None = None

    def end(self, output: Any | None = None) -> dict[str, Any]:
        """End span and return summary payload."""
        duration_ms = max(0, int((time.time() - self.started_at) * 1000))
        payload = {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "name": self.name,
            "duration_ms": duration_ms,
            "output": output,
        }
        if self._langfuse_span is not None:
            try:
                self._langfuse_span.update(output=output, metadata={"duration_ms": duration_ms})
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Failed to end langfuse span for %s: %s", self.name, exc)
        return payload

    def record_error(self, error: Exception) -> None:
        """Record error on span."""
        if self._langfuse_span is not None:
            try:
                self._langfuse_span.update(status="error", output=str(error))
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Failed to record langfuse error for %s: %s", self.name, exc)


class TraceManager:
    """Factory and integration boundary for execution traces."""

    def __init__(self, config: LangChainConfig, langfuse_client: Any | None = None) -> None:
        self._config = config
        self._langfuse_client = langfuse_client

    def create_span(
        self,
        name: str,
        input_data: Any | None = None,
        context: dict[str, Any] | None = None,
    ) -> TraceSpan:
        """Create a trace span for one runnable execution."""
        context = context or {}
        trace_id = str(context.get("trace_id") or self._generate_trace_id())
        span_id = uuid.uuid4().hex
        langfuse_span = self._create_langfuse_span(name=name, trace_id=trace_id, input_data=input_data, context=context)
        return TraceSpan(
            name=name,
            trace_id=trace_id,
            span_id=span_id,
            started_at=time.time(),
            metadata={"context": context},
            _langfuse_span=langfuse_span,
        )

    def _create_langfuse_span(
        self,
        *,
        name: str,
        trace_id: str,
        input_data: Any,
        context: dict[str, Any],
    ) -> Any | None:
        """Create underlying langfuse trace object when integration is enabled."""
        tracing_cfg = self._config.tracing
        if not tracing_cfg.enabled or not tracing_cfg.langfuse_integration:
            return None
        if self._langfuse_client is None:
            return None

        trace_fn = getattr(self._langfuse_client, "trace", None)
        if not callable(trace_fn):
            return None

        metadata = {"trace_id": trace_id, **context}
        try:
            return trace_fn(name=name, input=input_data, metadata=metadata)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to create langfuse trace for %s: %s", name, exc)
            return None

    @staticmethod
    def _generate_trace_id() -> str:
        """Generate trace id with stable prefix for filtering and observability."""
        return f"lc_{uuid.uuid4().hex}"
