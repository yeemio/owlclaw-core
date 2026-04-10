"""Local validation script for QueueTrigger using MockQueueAdapter."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from owlclaw.integrations.queue_adapters import MockQueueAdapter
from owlclaw.triggers.queue import MockIdempotencyStore, QueueTrigger, QueueTriggerConfig, RawMessage

logger = logging.getLogger("queue_validation")


class _RuntimeRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def trigger_event(
        self,
        *,
        event_name: str,
        payload: dict[str, Any],
        focus: str | None = None,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "event_name": event_name,
                "payload": payload,
                "focus": focus,
                "tenant_id": tenant_id,
            }
        )
        return {"run_id": f"mock-run-{len(self.calls)}"}


class _GovernanceAllowAll:
    async def check_permission(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"allowed": True, "context": context}


class _LedgerRecorder:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record_execution(self, **kwargs: Any) -> None:
        self.records.append(kwargs)


def _raw_message(message_id: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> RawMessage:
    return RawMessage(
        message_id=message_id,
        body=json.dumps(payload).encode("utf-8"),
        headers=headers or {},
        timestamp=datetime.now(timezone.utc),
        metadata={},
    )


async def _flush_queue(adapter: MockQueueAdapter) -> None:
    for _ in range(100):
        if adapter.pending_count() == 0:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)


async def run_validation() -> int:
    adapter = MockQueueAdapter()
    runtime = _RuntimeRecorder()
    governance = _GovernanceAllowAll()
    ledger = _LedgerRecorder()
    store = MockIdempotencyStore()

    trigger = QueueTrigger(
        config=QueueTriggerConfig(
            queue_name="agent-events",
            consumer_group="local-validation",
            parser_type="json",
            ack_policy="ack",
            max_retries=1,
            retry_backoff_base=0.01,
            enable_dedup=True,
            focus="validation",
        ),
        adapter=adapter,
        agent_runtime=runtime,
        governance=governance,
        ledger=ledger,
        idempotency_store=store,
    )

    adapter.enqueue(
        _raw_message(
            "msg-1",
            {"action": "create_order", "order_id": 1},
            headers={"x-event-name": "order_created", "x-tenant-id": "tenant-a", "x-dedup-key": "dedup-1"},
        )
    )
    adapter.enqueue(
        _raw_message(
            "msg-2",
            {"action": "create_order", "order_id": 1},
            headers={"x-event-name": "order_created", "x-tenant-id": "tenant-a", "x-dedup-key": "dedup-1"},
        )
    )

    await trigger.start()
    await _flush_queue(adapter)
    await trigger.stop()

    health = await trigger.health_check()
    logger.info("Validation health snapshot: %s", health)
    logger.info("Acked IDs: %s", adapter.get_acked())
    logger.info("DLQ entries: %s", adapter.get_dlq())
    logger.info("Runtime calls: %s", len(runtime.calls))
    logger.info("Ledger records: %s", len(ledger.records))

    assert len(adapter.get_dlq()) == 0, "Expected no DLQ messages"
    assert len(runtime.calls) == 1, "Expected dedup to suppress duplicate execution"
    assert len(adapter.get_acked()) == 2, "Expected both messages to be acked"
    assert len(ledger.records) == 1, "Expected one success ledger record"
    assert health["dedup_hits"] == 1, "Expected one dedup hit"

    logger.info("QueueTrigger mock validation PASSED")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        return asyncio.run(run_validation())
    except AssertionError as exc:
        logger.error("QueueTrigger mock validation FAILED: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
