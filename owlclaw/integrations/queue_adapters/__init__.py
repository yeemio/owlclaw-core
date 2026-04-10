"""Queue adapter implementations."""

from owlclaw.integrations.queue_adapters.dependencies import ensure_adapter_dependency
from owlclaw.integrations.queue_adapters.kafka import KafkaQueueAdapter
from owlclaw.integrations.queue_adapters.mock import MockQueueAdapter

__all__ = ["KafkaQueueAdapter", "MockQueueAdapter", "ensure_adapter_dependency"]
