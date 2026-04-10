"""Capabilities provider implementation for console backend."""

from __future__ import annotations

import inspect
import logging
import types
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any, Union, get_args, get_origin, get_type_hints

from sqlalchemy import case, func, select

from owlclaw.db.exceptions import ConfigurationError
from owlclaw.capabilities.registry import CapabilityRegistry
from owlclaw.db import get_engine
from owlclaw.db.session import create_session_factory
from owlclaw.governance.ledger import LedgerRecord

logger = logging.getLogger(__name__)

StatsFetcher = Callable[[str], Awaitable[dict[str, dict[str, Any]]]]


class DefaultCapabilitiesProvider:
    """Read capability metadata and aggregate execution statistics."""

    def __init__(
        self,
        *,
        capability_registry: CapabilityRegistry | None = None,
        stats_fetcher: StatsFetcher | None = None,
    ) -> None:
        self._registry = capability_registry
        self._stats_fetcher = stats_fetcher or self._collect_capability_stats

    async def list_capabilities(
        self,
        tenant_id: str,
        category: str | None,
    ) -> list[dict[str, Any]]:
        if self._registry is None:
            return []

        normalized_category = category.strip().lower() if isinstance(category, str) and category.strip() else None
        stats_by_name = await self._stats_fetcher(tenant_id)
        items: list[dict[str, Any]] = []

        for name, handler in sorted(self._registry.handlers.items()):
            detected_category = self._detect_category(name=name, handler=handler)
            if normalized_category is not None and detected_category != normalized_category:
                continue

            metadata = self._registry.get_capability_metadata(name) or {}
            stats = stats_by_name.get(name, {})
            items.append(
                {
                    "name": name,
                    "category": detected_category,
                    "description": metadata.get("description", ""),
                    "task_type": metadata.get("task_type", ""),
                    "constraints": metadata.get("constraints", {}),
                    "focus": metadata.get("focus", []),
                    "risk_level": metadata.get("risk_level", "low"),
                    "requires_confirmation": bool(metadata.get("requires_confirmation", False)),
                    "handler": metadata.get("handler"),
                    "schema": self._build_schema_from_handler(handler),
                    "stats": {
                        "executions": int(stats.get("executions", 0)),
                        "success_rate": float(stats.get("success_rate", 0.0)),
                        "avg_latency_ms": float(stats.get("avg_latency_ms", 0.0)),
                    },
                }
            )
        return items

    async def get_capability_schema(self, capability_name: str) -> dict[str, Any] | None:
        if self._registry is None:
            return None

        handler = self._registry.handlers.get(capability_name)
        if handler is None:
            return None
        return self._build_schema_from_handler(handler)

    async def _collect_capability_stats(self, tenant_id: str) -> dict[str, dict[str, Any]]:
        """Collect capability execution statistics from ledger.

        Returns empty dict if database is not configured (ConfigurationError),
        allowing graceful degradation for Lite Mode.
        """
        try:
            success_case = case((func.lower(LedgerRecord.status) == "success", 1), else_=0)
            engine = get_engine()
            session_factory = create_session_factory(engine)
        except ConfigurationError:
            logger.debug("Database not configured, returning empty capability stats")
            return {}

        try:
            async with session_factory() as session:
                statement = (
                    select(
                        LedgerRecord.capability_name,
                        func.count(LedgerRecord.id).label("executions"),
                        func.coalesce(func.sum(success_case), 0).label("successes"),
                        func.coalesce(func.avg(LedgerRecord.execution_time_ms), 0).label("avg_latency_ms"),
                    )
                    .where(LedgerRecord.tenant_id == tenant_id)
                    .group_by(LedgerRecord.capability_name)
                )
                rows = (await session.execute(statement)).all()
        except ConfigurationError:
            return {}

        stats: dict[str, dict[str, Any]] = {}
        for capability_name, executions_raw, successes_raw, avg_latency_raw in rows:
            executions = int(executions_raw or 0)
            successes = int(successes_raw or 0)
            success_rate = (successes / executions) if executions > 0 else 0.0
            stats[str(capability_name)] = {
                "executions": executions,
                "success_rate": round(success_rate, 4),
                "avg_latency_ms": float(avg_latency_raw or 0.0),
            }
        return stats

    def _detect_category(self, *, name: str, handler: Any) -> str:
        if hasattr(handler, "binding_config"):
            return "binding"
        if self._registry is None:
            return "handler"
        skill = self._registry.skills_loader.get_skill(name)
        if skill is not None:
            return "skill"
        return "handler"

    def _build_schema_from_handler(self, handler: Any) -> dict[str, Any]:
        schema = getattr(handler, "parameters_schema", None)
        if isinstance(schema, dict):
            if schema.get("type") == "object":
                return dict(schema)
            return {"type": "object", "properties": dict(schema), "required": []}

        try:
            type_hints = get_type_hints(handler)
        except Exception:
            type_hints = {}
        signature = inspect.signature(handler)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for name, parameter in signature.parameters.items():
            if name == "self":
                continue
            if parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
                continue
            resolved_annotation = type_hints.get(name, parameter.annotation)
            properties[name] = self._annotation_to_schema(resolved_annotation)
            if parameter.default is inspect.Parameter.empty:
                required.append(name)
        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    def _annotation_to_schema(self, annotation: Any) -> dict[str, Any]:
        if annotation is inspect.Parameter.empty:
            return {"type": "string"}
        if annotation in {str}:
            return {"type": "string"}
        if annotation in {int}:
            return {"type": "integer"}
        if annotation in {float, Decimal}:
            return {"type": "number"}
        if annotation in {bool}:
            return {"type": "boolean"}
        if annotation is dict:
            return {"type": "object"}
        if annotation is list:
            return {"type": "array"}

        origin = get_origin(annotation)
        if origin in {list, tuple, set}:
            args = get_args(annotation)
            item_schema = self._annotation_to_schema(args[0]) if args else {"type": "string"}
            return {"type": "array", "items": item_schema}
        if origin in {dict}:
            return {"type": "object"}
        if origin in {types.UnionType, Union}:
            union_args = [arg for arg in get_args(annotation) if arg is not type(None)]
            if len(union_args) == 1:
                return self._annotation_to_schema(union_args[0])
            return {"type": "string"}
        return {"type": "string"}
