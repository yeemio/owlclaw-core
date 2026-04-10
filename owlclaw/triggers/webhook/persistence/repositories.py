"""Repository layer for webhook trigger persistence."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from owlclaw.triggers.webhook.persistence.models import (
    WebhookEndpointModel,
    WebhookEventModel,
    WebhookExecutionModel,
    WebhookIdempotencyKeyModel,
    WebhookTransformationRuleModel,
)


class EndpointRepository:
    """CRUD repository for webhook endpoints."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, endpoint: WebhookEndpointModel) -> WebhookEndpointModel:
        self._session.add(endpoint)
        await self._session.flush()
        await self._session.refresh(endpoint)
        return endpoint

    async def get(self, endpoint_id: UUID) -> WebhookEndpointModel | None:
        stmt = select(WebhookEndpointModel).where(WebhookEndpointModel.id == endpoint_id)
        return cast(WebhookEndpointModel | None, await self._session.scalar(stmt))

    async def list(self, *, tenant_id: str, enabled: bool | None = None) -> list[WebhookEndpointModel]:
        stmt = select(WebhookEndpointModel).where(WebhookEndpointModel.tenant_id == tenant_id)
        if enabled is not None:
            stmt = stmt.where(WebhookEndpointModel.enabled == enabled)
        result = await self._session.execute(stmt.order_by(WebhookEndpointModel.created_at.asc()))
        return list(result.scalars().all())

    async def update(self, endpoint: WebhookEndpointModel) -> WebhookEndpointModel:
        await self._session.flush()
        await self._session.refresh(endpoint)
        return endpoint

    async def delete(self, endpoint_id: UUID) -> None:
        await self._session.execute(delete(WebhookEndpointModel).where(WebhookEndpointModel.id == endpoint_id))


class EventRepository:
    """Repository for webhook event logs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, event: WebhookEventModel) -> WebhookEventModel:
        self._session.add(event)
        await self._session.flush()
        await self._session.refresh(event)
        return event

    async def list_by_request_id(self, *, tenant_id: str, request_id: str) -> list[WebhookEventModel]:
        stmt = (
            select(WebhookEventModel)
            .where(WebhookEventModel.tenant_id == tenant_id, WebhookEventModel.request_id == request_id)
            .order_by(WebhookEventModel.timestamp.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

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
    ) -> list[WebhookEventModel]:
        stmt = select(WebhookEventModel).where(WebhookEventModel.tenant_id == tenant_id)
        if endpoint_id is not None:
            stmt = stmt.where(WebhookEventModel.endpoint_id == endpoint_id)
        if request_id is not None:
            stmt = stmt.where(WebhookEventModel.request_id == request_id)
        if event_type is not None:
            stmt = stmt.where(WebhookEventModel.event_type == event_type)
        if status is not None:
            stmt = stmt.where(WebhookEventModel.status == status)
        if start_time is not None:
            stmt = stmt.where(WebhookEventModel.timestamp >= start_time)
        if end_time is not None:
            stmt = stmt.where(WebhookEventModel.timestamp <= end_time)
        stmt = stmt.order_by(WebhookEventModel.timestamp.asc()).offset(max(0, offset)).limit(max(1, limit))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class IdempotencyRepository:
    """Repository for webhook idempotency keys."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, key: str) -> WebhookIdempotencyKeyModel | None:
        stmt = select(WebhookIdempotencyKeyModel).where(WebhookIdempotencyKeyModel.key == key)
        return cast(WebhookIdempotencyKeyModel | None, await self._session.scalar(stmt))

    async def upsert(self, item: WebhookIdempotencyKeyModel) -> WebhookIdempotencyKeyModel:
        existing = await self.get(item.key)
        if existing is None:
            self._session.add(item)
            await self._session.flush()
            await self._session.refresh(item)
            return item
        existing.result = item.result
        existing.execution_id = item.execution_id
        existing.expires_at = item.expires_at
        await self._session.flush()
        await self._session.refresh(existing)
        return existing

    async def delete_expired(self, *, now: datetime) -> int:
        result = await self._session.execute(
            delete(WebhookIdempotencyKeyModel).where(WebhookIdempotencyKeyModel.expires_at < now)
        )
        return int(getattr(result, "rowcount", 0) or 0)


class TransformationRuleRepository:
    """Repository for webhook transformation rules."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, rule: WebhookTransformationRuleModel) -> WebhookTransformationRuleModel:
        self._session.add(rule)
        await self._session.flush()
        await self._session.refresh(rule)
        return rule

    async def get(self, rule_id: UUID) -> WebhookTransformationRuleModel | None:
        stmt = select(WebhookTransformationRuleModel).where(WebhookTransformationRuleModel.id == rule_id)
        return cast(WebhookTransformationRuleModel | None, await self._session.scalar(stmt))

    async def list(self, *, tenant_id: str) -> list[WebhookTransformationRuleModel]:
        stmt = (
            select(WebhookTransformationRuleModel)
            .where(WebhookTransformationRuleModel.tenant_id == tenant_id)
            .order_by(WebhookTransformationRuleModel.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class ExecutionRepository:
    """Repository for webhook execution records."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, execution: WebhookExecutionModel) -> WebhookExecutionModel:
        self._session.add(execution)
        await self._session.flush()
        await self._session.refresh(execution)
        return execution

    async def get(self, execution_id: UUID) -> WebhookExecutionModel | None:
        stmt = select(WebhookExecutionModel).where(WebhookExecutionModel.id == execution_id)
        return cast(WebhookExecutionModel | None, await self._session.scalar(stmt))


class InMemoryEndpointRepository:
    """In-memory endpoint repository for deterministic property tests."""

    def __init__(self) -> None:
        self._items: dict[UUID, WebhookEndpointModel] = {}

    async def create(self, endpoint: WebhookEndpointModel) -> WebhookEndpointModel:
        self._items[endpoint.id] = endpoint
        return endpoint

    async def get(self, endpoint_id: UUID) -> WebhookEndpointModel | None:
        return self._items.get(endpoint_id)

    async def list(self, *, tenant_id: str, enabled: bool | None = None) -> list[WebhookEndpointModel]:
        items = [item for item in self._items.values() if item.tenant_id == tenant_id]
        if enabled is not None:
            items = [item for item in items if item.enabled == enabled]
        return sorted(items, key=lambda item: item.created_at)

    async def update(self, endpoint: WebhookEndpointModel) -> WebhookEndpointModel:
        self._items[endpoint.id] = endpoint
        return endpoint

    async def delete(self, endpoint_id: UUID) -> None:
        self._items.pop(endpoint_id, None)


class InMemoryEventRepository:
    """In-memory event repository for round-trip property tests."""

    def __init__(self) -> None:
        self._items: list[WebhookEventModel] = []

    async def create(self, event: WebhookEventModel) -> WebhookEventModel:
        self._items.append(event)
        return event

    async def list_by_request_id(self, *, tenant_id: str, request_id: str) -> list[WebhookEventModel]:
        items = [item for item in self._items if item.tenant_id == tenant_id and item.request_id == request_id]
        return sorted(items, key=lambda item: item.timestamp)

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
    ) -> list[WebhookEventModel]:
        items = [item for item in self._items if item.tenant_id == tenant_id]
        if endpoint_id is not None:
            items = [item for item in items if item.endpoint_id == endpoint_id]
        if request_id is not None:
            items = [item for item in items if item.request_id == request_id]
        if event_type is not None:
            items = [item for item in items if item.event_type == event_type]
        if status is not None:
            items = [item for item in items if item.status == status]
        if start_time is not None:
            items = [item for item in items if item.timestamp >= start_time]
        if end_time is not None:
            items = [item for item in items if item.timestamp <= end_time]
        ordered = sorted(items, key=lambda item: item.timestamp)
        return ordered[max(0, offset) : max(0, offset) + max(1, limit)]


class InMemoryIdempotencyRepository:
    """In-memory idempotency repository for tests."""

    def __init__(self) -> None:
        self._items: dict[str, WebhookIdempotencyKeyModel] = {}

    async def get(self, key: str) -> WebhookIdempotencyKeyModel | None:
        return self._items.get(key)

    async def upsert(self, item: WebhookIdempotencyKeyModel) -> WebhookIdempotencyKeyModel:
        self._items[item.key] = item
        return item


class InMemoryTransformationRuleRepository:
    """In-memory transformation rule repository for tests."""

    def __init__(self) -> None:
        self._items: dict[UUID, WebhookTransformationRuleModel] = {}

    async def create(self, rule: WebhookTransformationRuleModel) -> WebhookTransformationRuleModel:
        self._items[rule.id] = rule
        return rule

    async def get(self, rule_id: UUID) -> WebhookTransformationRuleModel | None:
        return self._items.get(rule_id)


class InMemoryExecutionRepository:
    """In-memory execution repository for tests."""

    def __init__(self) -> None:
        self._items: dict[UUID, WebhookExecutionModel] = {}

    async def create(self, execution: WebhookExecutionModel) -> WebhookExecutionModel:
        self._items[execution.id] = execution
        return execution

    async def get(self, execution_id: UUID) -> WebhookExecutionModel | None:
        return self._items.get(execution_id)
