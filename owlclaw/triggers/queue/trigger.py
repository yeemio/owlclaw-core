"""Queue trigger runtime: lifecycle management and consumption loop."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from owlclaw.triggers.queue.config import QueueTriggerConfig, validate_config
from owlclaw.triggers.queue.idempotency import IdempotencyStore
from owlclaw.triggers.queue.models import MessageEnvelope, RawMessage
from owlclaw.triggers.queue.parsers import BinaryParser, JSONParser, MessageParser, ParseError, TextParser
from owlclaw.triggers.queue.protocols import QueueAdapter
from owlclaw.triggers.queue.security import SensitiveDataLogFilter, redact_error_message

logger = logging.getLogger(__name__)
if not any(isinstance(item, SensitiveDataLogFilter) for item in logger.filters):
    logger.addFilter(SensitiveDataLogFilter())


@dataclass(slots=True)
class ProcessResult:
    """Outcome of processing one queue message."""

    message_id: str
    status: str
    trace_id: str
    detail: str | None = None


@dataclass(slots=True)
class GovernanceDecision:
    """Normalized governance decision used by queue trigger."""

    allowed: bool
    reason: str = ""
    policies: dict[str, Any] | None = None


class QueueTriggerMetrics:
    """In-process metrics recorder with optional Prometheus counters/histogram."""

    def __init__(self) -> None:
        self.processed_total = 0
        self.failed_total = 0
        self.retries_total = 0
        self.dedup_hits_total = 0
        self._latency_sum_ms = 0.0
        self._latency_count = 0

        self._prometheus_available = False
        self._processed_counter: Any | None = None
        self._failed_counter: Any | None = None
        self._retry_counter: Any | None = None
        self._dedup_counter: Any | None = None
        self._latency_histogram: Any | None = None
        try:
            from prometheus_client import Counter, Histogram  # type: ignore[import-not-found]

            self._processed_counter = Counter(
                "owlclaw_queue_processed_total",
                "Total successfully processed queue messages",
            )
            self._failed_counter = Counter(
                "owlclaw_queue_failed_total",
                "Total failed queue messages",
            )
            self._retry_counter = Counter(
                "owlclaw_queue_retries_total",
                "Total queue message retry attempts",
            )
            self._dedup_counter = Counter(
                "owlclaw_queue_dedup_hits_total",
                "Total deduplicated queue messages",
            )
            self._latency_histogram = Histogram(
                "owlclaw_queue_processing_latency_ms",
                "Queue message processing latency in milliseconds",
            )
            self._prometheus_available = True
        except Exception:
            self._prometheus_available = False

    def record_success(self, duration_ms: float) -> None:
        self.processed_total += 1
        self._latency_sum_ms += duration_ms
        self._latency_count += 1
        if self._prometheus_available:
            assert self._processed_counter is not None
            assert self._latency_histogram is not None
            self._processed_counter.inc()
            self._latency_histogram.observe(duration_ms)

    def record_failure(self) -> None:
        self.failed_total += 1
        if self._prometheus_available:
            assert self._failed_counter is not None
            self._failed_counter.inc()

    def record_retry(self) -> None:
        self.retries_total += 1
        if self._prometheus_available:
            assert self._retry_counter is not None
            self._retry_counter.inc()

    def record_dedup_hit(self) -> None:
        self.dedup_hits_total += 1
        if self._prometheus_available:
            assert self._dedup_counter is not None
            self._dedup_counter.inc()

    def snapshot(self) -> dict[str, float | int]:
        average_latency_ms = 0.0
        if self._latency_count > 0:
            average_latency_ms = self._latency_sum_ms / self._latency_count
        return {
            "processed_total": self.processed_total,
            "failed_total": self.failed_total,
            "retries_total": self.retries_total,
            "dedup_hits_total": self.dedup_hits_total,
            "latency_count": self._latency_count,
            "latency_avg_ms": average_latency_ms,
        }


class QueueTrigger:
    """Core queue-trigger runtime for message consumption and Agent dispatch."""

    def __init__(
        self,
        *,
        config: QueueTriggerConfig,
        adapter: QueueAdapter,
        agent_runtime: Any | None = None,
        governance: Any | None = None,
        ledger: Any | None = None,
        idempotency_store: IdempotencyStore | None = None,
    ) -> None:
        config_errors = validate_config(config)
        if config_errors:
            joined = "; ".join(config_errors)
            raise ValueError(f"Invalid QueueTriggerConfig: {joined}")

        self.config = config
        self.adapter = adapter
        self.agent_runtime = agent_runtime
        self.governance = governance
        self.ledger = ledger
        self.idempotency_store = idempotency_store

        self._running = False
        self._paused = False
        self._tasks: list[asyncio.Task[Any]] = []
        self._processed_count = 0
        self._failed_count = 0
        self._dedup_hits = 0
        self._metrics = QueueTriggerMetrics()
        self._parser = self._create_parser(config.parser_type)

    @staticmethod
    def _create_parser(parser_type: str) -> MessageParser:
        parser_type = parser_type.lower().strip()
        if parser_type == "text":
            return TextParser()
        if parser_type == "binary":
            return BinaryParser()
        return JSONParser()

    async def start(self) -> None:
        """Start queue consumption workers."""
        if self._running:
            raise RuntimeError("QueueTrigger already running")
        self._running = True
        self._paused = False
        await self.adapter.connect()
        self._tasks = [
            asyncio.create_task(self._consume_loop(worker_id=i), name=f"queue-trigger-worker-{i}")
            for i in range(self.config.concurrency)
        ]

    async def stop(self) -> None:
        """Stop workers gracefully and close adapter connection."""
        self._running = False
        self._paused = False
        await self.adapter.close()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks = []

    async def pause(self) -> None:
        """Pause message processing without disconnecting adapter."""
        self._paused = True

    async def resume(self) -> None:
        """Resume message processing."""
        self._paused = False

    async def health_check(self) -> dict[str, Any]:
        """Return runtime health and basic counters."""
        adapter_healthy = await self.adapter.health_check()
        active_workers = len([task for task in self._tasks if not task.done()])
        return {
            "status": "healthy" if self._running and adapter_healthy else "unhealthy",
            "running": self._running,
            "paused": self._paused,
            "adapter_healthy": adapter_healthy,
            "active_workers": active_workers,
            "processed_count": self._processed_count,
            "failed_count": self._failed_count,
            "dedup_hits": self._dedup_hits,
            "metrics": self._metrics.snapshot(),
        }

    async def _consume_loop(self, worker_id: int) -> None:
        """Consume queue messages until stopped."""
        logger.debug("Queue worker %s started", worker_id)
        try:
            async for raw_message in self.adapter.consume():
                if not self._running:
                    break
                while self._paused and self._running:
                    await asyncio.sleep(0.01)
                if not self._running:
                    break
                try:
                    await self._process_message(raw_message)
                except Exception:
                    self._failed_count += 1
                    self._metrics.record_failure()
                    logger.exception("Queue worker %s failed to process message", worker_id)
                await asyncio.sleep(0)
        except Exception:
            self._failed_count += 1
            self._metrics.record_failure()
            logger.exception("Queue worker %s consume loop crashed", worker_id)
        finally:
            logger.debug("Queue worker %s stopped", worker_id)

    async def _process_message(self, raw_message: RawMessage) -> ProcessResult:
        """Parse and process one message; parse errors are routed to DLQ."""
        trace_id = f"queue-{raw_message.message_id}"
        started = time.perf_counter()
        try:
            envelope = MessageEnvelope.from_raw_message(
                raw_message,
                source=self.config.queue_name,
                parser=self._parser,
            )
        except ParseError as exc:
            self._failed_count += 1
            self._metrics.record_failure()
            safe_error = redact_error_message(exc)
            await self.adapter.send_to_dlq(raw_message, reason=safe_error)
            logger.warning(
                "Queue parse error message_id=%s trace_id=%s error=%s",
                raw_message.message_id,
                trace_id,
                safe_error,
            )
            return ProcessResult(
                message_id=raw_message.message_id,
                status="parse_error",
                trace_id=trace_id,
                detail=safe_error,
            )

        status, agent_run_id, tenant_id = await self._process_envelope(raw_message, envelope, trace_id)
        if status == "processed":
            duration_ms = (time.perf_counter() - started) * 1000
            await self._record_success_execution(envelope, tenant_id, trace_id, duration_ms, agent_run_id)
            self._processed_count += 1
            self._metrics.record_success(duration_ms)
            logger.info(
                "Queue processed message_id=%s trace_id=%s queue=%s tenant_id=%s duration_ms=%.3f",
                envelope.message_id,
                trace_id,
                self.config.queue_name,
                tenant_id,
                duration_ms,
            )
            return ProcessResult(
                message_id=raw_message.message_id,
                status="processed",
                trace_id=trace_id,
            )
        return ProcessResult(
            message_id=raw_message.message_id,
            status=status,
            trace_id=trace_id,
        )

    async def _process_envelope(
        self,
        raw_message: RawMessage,
        envelope: MessageEnvelope,
        trace_id: str,
    ) -> tuple[str, str | None, str]:
        """Process a parsed envelope and apply ack policy behavior."""
        tenant_id = self._resolve_tenant_id(envelope)
        governance_decision = await self._check_governance(envelope, tenant_id=tenant_id)
        if not governance_decision.allowed:
            await self._handle_governance_rejection(raw_message, envelope, tenant_id, governance_decision, trace_id)
            return ("governance_rejected", None, tenant_id)

        if self.config.enable_dedup and self.idempotency_store is not None:
            dedup_key = envelope.dedup_key or envelope.message_id
            try:
                exists = await self.idempotency_store.exists(dedup_key)
            except Exception:
                exists = False
                logger.exception("Idempotency check failed for key %s", dedup_key)
            if exists:
                self._dedup_hits += 1
                self._metrics.record_dedup_hit()
                await self.adapter.ack(raw_message)
                return ("deduplicated", None, tenant_id)

        trigger_result: Any | None = None
        try:
            trigger_result = await self._trigger_agent_with_retry(envelope, trace_id, tenant_id)
        except Exception as exc:
            self._failed_count += 1
            self._metrics.record_failure()
            await self._handle_processing_error(raw_message, exc)
            return ("processing_error", None, tenant_id)

        if self.config.enable_dedup and self.idempotency_store is not None:
            dedup_key = envelope.dedup_key or envelope.message_id
            try:
                await self.idempotency_store.set(
                    dedup_key,
                    {"trace_id": trace_id, "status": "processed"},
                    ttl=self.config.idempotency_window,
                )
            except Exception:
                logger.exception("Idempotency write failed for key %s", dedup_key)

        await self.adapter.ack(raw_message)
        return ("processed", self._extract_agent_run_id(trigger_result), tenant_id)

    async def _check_governance(self, envelope: MessageEnvelope, *, tenant_id: str) -> GovernanceDecision:
        """Run governance permission check when governance hook is provided."""
        if self.governance is None:
            return GovernanceDecision(allowed=True)
        check_permission = getattr(self.governance, "check_permission", None)
        if not callable(check_permission):
            return GovernanceDecision(allowed=True)

        context = {
            "source": "queue",
            "queue": self.config.queue_name,
            "message_id": envelope.message_id,
            "tenant_id": tenant_id,
            "event_name": envelope.event_name or "queue_message",
        }
        try:
            result = await check_permission(context)
        except Exception:
            logger.exception("Governance check failed for message %s", envelope.message_id)
            return GovernanceDecision(
                allowed=bool(self.config.governance_fail_open),
                reason="governance_unavailable",
            )

        if isinstance(result, bool):
            return GovernanceDecision(allowed=result)
        if isinstance(result, dict):
            return GovernanceDecision(
                allowed=bool(result.get("allowed", True)),
                reason=str(result.get("reason", "")),
                policies=result.get("policies"),
            )
        allowed = bool(getattr(result, "allowed", True))
        reason = str(getattr(result, "reason", ""))
        policies = getattr(result, "policies", None)
        if policies is not None and not isinstance(policies, dict):
            policies = None
        return GovernanceDecision(allowed=allowed, reason=reason, policies=policies)

    async def _handle_governance_rejection(
        self,
        raw_message: RawMessage,
        envelope: MessageEnvelope,
        tenant_id: str,
        decision: GovernanceDecision,
        trace_id: str,
    ) -> None:
        """Handle governance rejection with ledger audit and ack policy behavior."""
        self._failed_count += 1
        self._metrics.record_failure()
        await self._record_governance_rejection(envelope, tenant_id, decision, trace_id)

        reason = decision.reason or "governance_rejected"
        policy = self.config.ack_policy
        if policy == "dlq":
            await self.adapter.send_to_dlq(raw_message, reason=reason)
            return
        if policy == "requeue":
            await self.adapter.nack(raw_message, requeue=True)
            return
        if policy == "nack":
            await self.adapter.nack(raw_message, requeue=False)
            return
        await self.adapter.ack(raw_message)

    async def _record_governance_rejection(
        self,
        envelope: MessageEnvelope,
        tenant_id: str,
        decision: GovernanceDecision,
        trace_id: str,
    ) -> None:
        """Record governance rejection when ledger is available."""
        if self.ledger is None:
            return
        record_execution = getattr(self.ledger, "record_execution", None)
        if not callable(record_execution):
            return
        try:
            await record_execution(
                tenant_id=tenant_id,
                agent_id="queue-trigger",
                run_id=trace_id,
                capability_name="queue_trigger",
                task_type="queue_trigger",
                input_params={
                    "message_id": envelope.message_id,
                    "queue": self.config.queue_name,
                    "event_name": envelope.event_name,
                },
                output_result=None,
                decision_reasoning=decision.reason or "governance_rejected",
                execution_time_ms=0,
                llm_model="none",
                llm_tokens_input=0,
                llm_tokens_output=0,
                estimated_cost=Decimal("0"),
                status="blocked",
                error_message=decision.reason or "governance_rejected",
            )
        except Exception:
            logger.exception("Failed to record governance rejection for message %s", envelope.message_id)

    async def _trigger_agent_with_retry(self, envelope: MessageEnvelope, trace_id: str, tenant_id: str) -> Any | None:
        """Trigger AgentRuntime with retry and exponential backoff."""
        if self.agent_runtime is None:
            return None
        trigger_event = getattr(self.agent_runtime, "trigger_event", None)
        if not callable(trigger_event):
            return None
        payload = {
            "message": envelope.payload,
            "headers": envelope.headers,
            "source": envelope.source,
            "message_id": envelope.message_id,
            "received_at": envelope.received_at.isoformat(),
            "trace_id": trace_id,
        }
        last_error: Exception | None = None
        max_attempts = self.config.max_retries + 1
        for attempt in range(max_attempts):
            try:
                return await trigger_event(
                    event_name=envelope.event_name or "queue_message",
                    payload=payload,
                    focus=self.config.focus,
                    tenant_id=tenant_id,
                )
            except Exception as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    break
                backoff_seconds = self._compute_backoff_seconds(attempt)
                self._metrics.record_retry()
                safe_error = redact_error_message(exc)
                logger.warning(
                    "Queue message %s trace_id=%s trigger retry %s/%s after %.3fs: %s",
                    envelope.message_id,
                    trace_id,
                    attempt + 1,
                    self.config.max_retries,
                    backoff_seconds,
                    safe_error,
                )
                await asyncio.sleep(backoff_seconds)
        if last_error is not None:
            raise last_error
        return None

    def _compute_backoff_seconds(self, attempt: int) -> float:
        """Compute exponential backoff delay for retry attempt."""
        return self.config.retry_backoff_base * (self.config.retry_backoff_multiplier**attempt)

    async def _handle_processing_error(self, raw_message: RawMessage, error: Exception) -> None:
        """Handle runtime processing failures by configured ack policy."""
        trace_id = f"queue-{raw_message.message_id}"
        safe_error = redact_error_message(error)
        logger.warning("Queue message %s trace_id=%s failed: %s", raw_message.message_id, trace_id, safe_error)
        policy = self.config.ack_policy
        if policy == "ack":
            await self.adapter.ack(raw_message)
            return
        if policy == "nack":
            await self.adapter.nack(raw_message, requeue=False)
            return
        if policy == "requeue":
            await self.adapter.nack(raw_message, requeue=True)
            return
        await self.adapter.send_to_dlq(raw_message, reason=safe_error)

    @staticmethod
    def _extract_agent_run_id(trigger_result: Any | None) -> str | None:
        """Extract agent run id from trigger_event response."""
        if trigger_result is None:
            return None
        if isinstance(trigger_result, dict):
            run_id = trigger_result.get("run_id") or trigger_result.get("agent_run_id")
            return str(run_id) if run_id is not None else None
        run_id = getattr(trigger_result, "run_id", None) or getattr(trigger_result, "agent_run_id", None)
        return str(run_id) if run_id is not None else None

    async def _record_success_execution(
        self,
        envelope: MessageEnvelope,
        tenant_id: str,
        trace_id: str,
        duration_ms: float,
        agent_run_id: str | None,
    ) -> None:
        """Record successful processing when ledger is available."""
        if self.ledger is None:
            return
        record_execution = getattr(self.ledger, "record_execution", None)
        if not callable(record_execution):
            return
        try:
            await record_execution(
                tenant_id=tenant_id,
                agent_id="queue-trigger",
                run_id=trace_id,
                capability_name="queue_trigger",
                task_type="queue_trigger",
                input_params={
                    "trace_id": trace_id,
                    "message_id": envelope.message_id,
                    "queue": self.config.queue_name,
                    "event_name": envelope.event_name or "queue_message",
                    "tenant_id": tenant_id,
                },
                output_result={
                    "status": "success",
                    "duration_ms": int(duration_ms),
                    "agent_run_id": agent_run_id,
                },
                decision_reasoning="queue_trigger_execution",
                execution_time_ms=max(0, int(duration_ms)),
                llm_model="none",
                llm_tokens_input=0,
                llm_tokens_output=0,
                estimated_cost=Decimal("0"),
                status="success",
                error_message=None,
            )
        except Exception:
            logger.exception("Failed to record successful execution for message %s", envelope.message_id)

    def _resolve_tenant_id(self, envelope: MessageEnvelope) -> str:
        """Resolve effective tenant id using secure default behavior."""
        default_tenant = self.config.default_tenant_id.strip() or "default"
        if not self.config.trust_tenant_header:
            return default_tenant
        trusted_producers = self.config.trusted_producers
        producer_header = self.config.trusted_producer_header.strip().lower() or "x-producer-id"
        producer_id = envelope.headers.get(producer_header, "")
        producer_id = producer_id.strip() if isinstance(producer_id, str) else ""
        if trusted_producers:
            if producer_id not in trusted_producers:
                logger.warning(
                    "Queue tenant header ignored because producer is not trusted message_id=%s producer_id=%s",
                    envelope.message_id,
                    producer_id or "<missing>",
                )
                return default_tenant
        header_name = self.config.tenant_header_name.strip().lower() or "x-tenant-id"
        raw_tenant = envelope.headers.get(header_name, "")
        if isinstance(raw_tenant, str) and raw_tenant.strip():
            tenant_candidate = raw_tenant.strip()
            if not self._is_valid_tenant_signature(envelope, tenant_candidate, producer_id):
                return default_tenant
            return tenant_candidate
        return default_tenant

    def _is_valid_tenant_signature(self, envelope: MessageEnvelope, tenant_id: str, producer_id: str) -> bool:
        """Validate tenant header signature when configured."""
        env_names = self._signature_secret_env_names()
        if not env_names:
            return True
        signature_header = self.config.tenant_signature_header.strip().lower() or "x-tenant-signature"
        signature = envelope.headers.get(signature_header, "")
        signature = signature.strip() if isinstance(signature, str) else ""
        if not signature:
            logger.warning("Queue tenant signature missing message_id=%s", envelope.message_id)
            return False
        payload = f"{envelope.message_id}:{tenant_id}:{producer_id}"
        for env_name in env_names:
            secret = os.getenv(env_name, "").strip()
            if not secret:
                continue
            expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
            if hmac.compare_digest(signature, expected):
                return True
        logger.warning(
            "Queue tenant signature invalid message_id=%s checked_secrets=%d",
            envelope.message_id,
            len(env_names),
        )
        return False

    def _signature_secret_env_names(self) -> list[str]:
        """Return normalized signature secret env names for key rotation windows."""
        env_names: list[str] = []
        if self.config.tenant_signature_secret_env is not None:
            normalized = self.config.tenant_signature_secret_env.strip()
            if normalized:
                env_names.append(normalized)
        if self.config.tenant_signature_secret_envs:
            for item in self.config.tenant_signature_secret_envs:
                normalized = item.strip()
                if normalized and normalized not in env_names:
                    env_names.append(normalized)
        return env_names
