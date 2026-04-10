"""Queue binding executor implementation."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol

from owlclaw.capabilities.bindings.credential import CredentialResolver
from owlclaw.capabilities.bindings.executor import BindingExecutor
from owlclaw.capabilities.bindings.schema import BindingConfig, QueueBindingConfig
from owlclaw.integrations.queue_adapters import KafkaQueueAdapter


class QueuePublisher(Protocol):
    """Minimal publish protocol for queue adapters."""

    async def publish(self, topic: str, message: bytes, headers: dict[str, str] | None = None) -> None:
        """Publish a message to queue topic."""


def _default_adapter_factory(provider: str, connection: str, topic: str) -> QueuePublisher:
    if provider != "kafka":
        raise ValueError(f"Queue provider '{provider}' is not supported yet")
    return KafkaQueueAdapter(
        topic=topic,
        bootstrap_servers=connection,
        consumer_group="owlclaw-binding",
    )


class QueueBindingExecutor(BindingExecutor):
    """Execute queue bindings in active or shadow mode."""

    def __init__(
        self,
        credential_resolver: CredentialResolver | None = None,
        adapter_factory: Callable[[str, str, str], QueuePublisher] | None = None,
    ) -> None:
        self._credential_resolver = credential_resolver or CredentialResolver()
        self._adapter_factory = adapter_factory or _default_adapter_factory
        self._adapter_cache: dict[tuple[str, str, str], QueuePublisher] = {}

    def _get_adapter(self, provider: str, connection: str, topic: str) -> QueuePublisher:
        key = (provider, connection, topic)
        adapter = self._adapter_cache.get(key)
        if adapter is not None:
            return adapter
        created = self._adapter_factory(provider, connection, topic)
        self._adapter_cache[key] = created
        return created

    async def execute(self, config: BindingConfig, parameters: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(config, QueueBindingConfig):
            raise TypeError("QueueBindingExecutor requires QueueBindingConfig")
        connection = self._credential_resolver.resolve(config.connection)
        headers = self._render_headers_mapping(config.headers_mapping, parameters)
        payload = json.dumps(parameters, ensure_ascii=False, default=str).encode("utf-8")

        if config.mode == "shadow":
            return {
                "status": "shadow",
                "mode": "shadow",
                "provider": config.provider,
                "topic": config.topic,
                "headers": headers,
                "payload": parameters,
                "sent": False,
            }

        adapter = self._get_adapter(config.provider, connection, config.topic)
        await adapter.publish(config.topic, payload, headers=headers)
        return {
            "status": "ok",
            "mode": config.mode,
            "provider": config.provider,
            "topic": config.topic,
            "headers": headers,
            "sent": True,
        }

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not str(config.get("connection", "")).strip():
            errors.append("Queue binding requires 'connection' field")
        if not str(config.get("topic", "")).strip():
            errors.append("Queue binding requires 'topic' field")
        provider = str(config.get("provider", "kafka")).strip()
        if provider and provider not in {"kafka", "rabbitmq", "redis"}:
            errors.append(f"Unsupported queue provider: {provider}")
        return errors

    @property
    def supported_modes(self) -> list[str]:
        return ["active", "shadow"]

    @staticmethod
    def _render_headers_mapping(headers_mapping: dict[str, str], parameters: dict[str, Any]) -> dict[str, str]:
        headers: dict[str, str] = {}
        for key, template in headers_mapping.items():
            rendered = template
            for param_key, value in parameters.items():
                rendered = rendered.replace(f"{{{param_key}}}", str(value))
            headers[key] = rendered
        return headers
