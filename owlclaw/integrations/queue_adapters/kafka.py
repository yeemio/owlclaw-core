"""Kafka queue adapter implementation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from owlclaw.triggers.queue.models import RawMessage

logger = logging.getLogger(__name__)


def _import_aiokafka() -> tuple[type[Any], type[Any], type[Any]]:
    """Import aiokafka lazily so optional dependency is only needed at runtime."""
    try:
        from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Kafka adapter requires aiokafka. Install with: poetry add aiokafka") from exc
    return AIOKafkaConsumer, AIOKafkaProducer, TopicPartition


class KafkaQueueAdapter:
    """Queue adapter backed by Kafka (aiokafka)."""

    def __init__(
        self,
        *,
        topic: str,
        bootstrap_servers: str | list[str],
        consumer_group: str,
        dlq_topic: str | None = None,
        consumer: Any | None = None,
        producer: Any | None = None,
        connect_timeout: float = 30.0,
    ) -> None:
        self.topic = topic
        self.bootstrap_servers = bootstrap_servers
        self.consumer_group = consumer_group
        self.dlq_topic = dlq_topic or f"{topic}.dlq"
        self._connect_timeout = max(0.1, connect_timeout)

        self._consumer = consumer
        self._producer = producer
        self._connected = False
        self._closed = False
        self._inflight: dict[str, Any] = {}
        self._topic_partition_type: type[Any] | None = None

    async def connect(self) -> None:
        """Connect and start Kafka consumer/producer."""
        if self._consumer is None or self._producer is None:
            consumer_cls, producer_cls, topic_partition_cls = _import_aiokafka()
            self._topic_partition_type = topic_partition_cls
            if self._consumer is None:
                self._consumer = consumer_cls(
                    self.topic,
                    bootstrap_servers=self.bootstrap_servers,
                    group_id=self.consumer_group,
                    enable_auto_commit=False,
                )
            if self._producer is None:
                self._producer = producer_cls(bootstrap_servers=self.bootstrap_servers)

        if self._topic_partition_type is None:
            _, _, topic_partition_cls = _import_aiokafka()
            self._topic_partition_type = topic_partition_cls

        try:
            await asyncio.wait_for(self._consumer.start(), timeout=self._connect_timeout)
        except asyncio.TimeoutError as e:
            logger.warning(
                "Kafka consumer start timed out",
                extra={"bootstrap_servers": self.bootstrap_servers, "timeout_s": self._connect_timeout},
            )
            raise TimeoutError(
                f"Kafka consumer failed to start within {self._connect_timeout:.1f}s (broker unreachable?)"
            ) from e
        try:
            await asyncio.wait_for(self._producer.start(), timeout=self._connect_timeout)
        except asyncio.TimeoutError as e:
            logger.warning(
                "Kafka producer start timed out",
                extra={"bootstrap_servers": self.bootstrap_servers, "timeout_s": self._connect_timeout},
            )
            await self._consumer.stop()
            raise TimeoutError(
                f"Kafka producer failed to start within {self._connect_timeout:.1f}s (broker unreachable?)"
            ) from e
        self._connected = True
        self._closed = False

    async def consume(self) -> AsyncIterator[RawMessage]:
        """Consume Kafka records as RawMessage entries."""
        if self._consumer is None:
            return
        async for record in self._consumer:
            if not self._connected or self._closed:
                break
            message = self._record_to_raw_message(record)
            self._inflight[message.message_id] = record
            yield message

    async def ack(self, message: RawMessage) -> None:
        """Commit message offset to Kafka."""
        if self._consumer is None or self._topic_partition_type is None:
            return
        record = self._inflight.pop(message.message_id, None)
        if record is None:
            return
        topic_partition = self._topic_partition_type(record.topic, record.partition)
        await self._consumer.commit({topic_partition: record.offset + 1})

    async def nack(self, message: RawMessage, requeue: bool = False) -> None:
        """Reject processing; optionally requeue by producing message again."""
        record = self._inflight.pop(message.message_id, None)
        if not requeue or self._producer is None:
            return
        await self._producer.send_and_wait(
            self.topic,
            message.body,
            key=(message.message_id.encode("utf-8") if message.message_id else None),
            headers=self._encode_headers(message.headers),
        )
        if record is not None and self._consumer is not None:
            if self._topic_partition_type is None:
                _, _, topic_partition_cls = _import_aiokafka()
                self._topic_partition_type = topic_partition_cls
            topic_partition = self._topic_partition_type(record.topic, record.partition)
            await self._consumer.seek(topic_partition, record.offset)

    async def send_to_dlq(self, message: RawMessage, reason: str) -> None:
        """Produce a failed message to configured DLQ topic."""
        if self._producer is None:
            return
        headers = dict(message.headers)
        headers["x-dlq-reason"] = reason
        await self._producer.send_and_wait(
            self.dlq_topic,
            message.body,
            key=(message.message_id.encode("utf-8") if message.message_id else None),
            headers=self._encode_headers(headers),
        )

    async def publish(self, topic: str, message: bytes, headers: dict[str, str] | None = None) -> None:
        """Publish a message payload to the given topic."""
        if self._producer is None:
            await self.connect()
        if self._producer is None:
            raise RuntimeError("Kafka producer is not initialized")
        await self._producer.send_and_wait(
            topic,
            message,
            headers=self._encode_headers(headers or {}),
        )

    async def close(self) -> None:
        """Close Kafka producer/consumer."""
        if self._consumer is not None:
            await self._consumer.stop()
        if self._producer is not None:
            await self._producer.stop()
        self._connected = False
        self._closed = True
        self._inflight.clear()

    async def health_check(self) -> bool:
        """Return adapter connection health."""
        return self._connected and not self._closed and self._consumer is not None and self._producer is not None

    @staticmethod
    def _encode_headers(headers: dict[str, str]) -> list[tuple[str, bytes]]:
        return [(key, value.encode("utf-8")) for key, value in headers.items()]

    @staticmethod
    def _decode_headers(headers: list[tuple[str, bytes]] | None) -> dict[str, str]:
        if not headers:
            return {}
        decoded: dict[str, str] = {}
        for key, value in headers:
            decoded[key] = value.decode("utf-8", errors="replace")
        return decoded

    def _record_to_raw_message(self, record: Any) -> RawMessage:
        headers = self._decode_headers(getattr(record, "headers", None))
        key = getattr(record, "key", None)
        key_text = key.decode("utf-8", errors="replace") if isinstance(key, bytes) else None
        message_id = headers.get("x-message-id") or key_text or f"{record.topic}:{record.partition}:{record.offset}"
        timestamp_ms = getattr(record, "timestamp", 0) or 0
        timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        return RawMessage(
            message_id=message_id,
            body=record.value,
            headers=headers,
            timestamp=timestamp,
            metadata={
                "topic": record.topic,
                "partition": record.partition,
                "offset": record.offset,
            },
        )
