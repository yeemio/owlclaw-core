"""Webhook event logging service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID, uuid4

from owlclaw.triggers.webhook.persistence.models import WebhookEventModel
from owlclaw.triggers.webhook.types import EventFilter, EventType, WebhookEventRecord


class EventRepositoryProtocol(Protocol):
    """Repository protocol used by EventLogger."""

    async def create(self, event: WebhookEventModel) -> WebhookEventModel: ...

    async def query(
        self,
        *,
        tenant_id: str,
        endpoint_id: UUID | None = None,
        request_id: str | None = None,
        event_type: str | None = None,
        status: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[WebhookEventModel]: ...


class EventLogger:
    """Record and query webhook lifecycle events."""

    def __init__(self, repository: EventRepositoryProtocol) -> None:
        self._repository = repository

    async def log_request(self, event: WebhookEventRecord) -> WebhookEventRecord:
        return await self._log("request", event)

    async def log_validation(self, event: WebhookEventRecord) -> WebhookEventRecord:
        return await self._log("validation", event)

    async def log_transformation(self, event: WebhookEventRecord) -> WebhookEventRecord:
        return await self._log("transformation", event)

    async def log_execution(self, event: WebhookEventRecord) -> WebhookEventRecord:
        return await self._log("execution", event)

    async def query_events(self, event_filter: EventFilter) -> list[WebhookEventRecord]:
        offset = max(0, event_filter.page - 1) * max(1, event_filter.page_size)
        items = await self._repository.query(
            tenant_id=event_filter.tenant_id,
            endpoint_id=(None if event_filter.endpoint_id is None else UUID(event_filter.endpoint_id)),
            request_id=event_filter.request_id,
            event_type=event_filter.event_type,
            status=event_filter.status,
            start_time=event_filter.start_time,
            end_time=event_filter.end_time,
            offset=offset,
            limit=event_filter.page_size,
        )
        return [self._to_record(item) for item in items]

    async def _log(self, event_type: EventType, event: WebhookEventRecord) -> WebhookEventRecord:
        model = WebhookEventModel(
            id=UUID(event.id) if event.id else uuid4(),
            tenant_id=event.tenant_id,
            endpoint_id=UUID(event.endpoint_id),
            event_type=event_type,
            timestamp=event.timestamp,
            source_ip=event.source_ip,
            user_agent=event.user_agent,
            request_id=event.request_id,
            duration=event.duration_ms,
            status=event.status,
            data=event.data,
            error=event.error,
        )
        created = await self._repository.create(model)
        return self._to_record(created)

    @staticmethod
    def _to_record(model: WebhookEventModel) -> WebhookEventRecord:
        return WebhookEventRecord(
            id=str(model.id),
            endpoint_id=str(model.endpoint_id),
            event_type=model.event_type,  # type: ignore[arg-type]
            timestamp=model.timestamp,
            request_id=model.request_id,
            source_ip=model.source_ip,
            user_agent=model.user_agent,
            duration_ms=model.duration,
            status=model.status,
            data=model.data,
            error=model.error,
            tenant_id=model.tenant_id,
        )


def build_event(
    *,
    endpoint_id: str,
    request_id: str,
    event_type: EventType,
    tenant_id: str = "default",
    source_ip: str | None = None,
    user_agent: str | None = None,
    duration_ms: int | None = None,
    status: str | None = None,
    data: dict | None = None,
    error: dict | None = None,
) -> WebhookEventRecord:
    """Build a normalized event record with generated ID and UTC timestamp."""

    return WebhookEventRecord(
        id=str(uuid4()),
        endpoint_id=endpoint_id,
        event_type=event_type,
        timestamp=datetime.now(timezone.utc),
        request_id=request_id,
        source_ip=source_ip,
        user_agent=user_agent,
        duration_ms=duration_ms,
        status=status,
        data=data,
        error=error,
        tenant_id=tenant_id,
    )
