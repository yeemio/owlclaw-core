"""Webhook application bootstrap and dependency container."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI

from owlclaw.triggers.webhook.event_logger import EventLogger
from owlclaw.triggers.webhook.execution import ExecutionTrigger
from owlclaw.triggers.webhook.governance import GovernanceClient
from owlclaw.triggers.webhook.http.app import HttpGatewayConfig, create_webhook_app
from owlclaw.triggers.webhook.manager import WebhookEndpointManager
from owlclaw.triggers.webhook.monitoring import MonitoringService
from owlclaw.triggers.webhook.persistence.repositories import InMemoryEndpointRepository, InMemoryEventRepository
from owlclaw.triggers.webhook.transformer import PayloadTransformer
from owlclaw.triggers.webhook.validator import RequestValidator


@dataclass(slots=True)
class WebhookApplication:
    """Assemble webhook services and expose lifecycle methods."""

    manager: WebhookEndpointManager
    validator: RequestValidator
    transformer: PayloadTransformer
    governance: GovernanceClient
    execution: ExecutionTrigger
    event_logger: EventLogger
    monitoring: MonitoringService
    config: HttpGatewayConfig
    started: bool = False

    async def start(self) -> None:
        if self.started:
            return
        self.monitoring.register_health_check("database", lambda: True)
        self.monitoring.register_health_check("runtime", lambda: True)
        self.monitoring.register_health_check("governance", lambda: True)
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def health_status(self) -> dict[str, Any]:
        status = await self.monitoring.get_health_status()
        return {
            "started": self.started,
            "status": status.status,
            "checks": [{"name": c.name, "status": c.status, "message": c.message} for c in status.checks],
            "tls_enabled": self.config.tls_enabled,
        }

    def build_http_app(self) -> FastAPI:
        return create_webhook_app(
            manager=self.manager,
            validator=self.validator,
            transformer=self.transformer,
            governance=self.governance,
            execution=self.execution,
            event_logger=self.event_logger,
            monitoring=self.monitoring,
            config=self.config,
        )


def build_webhook_application(
    *,
    runtime: Any,
    governance_policy: Any | None = None,
    config: HttpGatewayConfig | None = None,
) -> WebhookApplication:
    """Factory for webhook application with in-memory default dependencies."""

    manager = WebhookEndpointManager(InMemoryEndpointRepository())
    return WebhookApplication(
        manager=manager,
        validator=RequestValidator(manager),
        transformer=PayloadTransformer(),
        governance=GovernanceClient(governance_policy),
        execution=ExecutionTrigger(runtime),
        event_logger=EventLogger(InMemoryEventRepository()),
        monitoring=MonitoringService(),
        config=config or HttpGatewayConfig(),
    )
