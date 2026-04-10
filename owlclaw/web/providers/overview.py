"""Overview provider implementation for console backend."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import case, distinct, func, select, text

from owlclaw.db import get_engine
from owlclaw.db.session import create_session_factory
from owlclaw.governance.ledger import LedgerRecord
from owlclaw.integrations.hatchet import HatchetClient, HatchetConfig
from owlclaw.web.contracts import HealthStatus, OverviewMetrics

logger = logging.getLogger(__name__)

HealthChecker = Callable[[], Awaitable[list[HealthStatus]]]
MetricsFetcher = Callable[[str, bool], Awaitable[tuple[Decimal, int, float, int]]]
Clock = Callable[[], float]


@dataclass(frozen=True)
class _CacheEntry:
    metrics: OverviewMetrics
    expires_at: float


class DefaultOverviewProvider:
    """Overview provider with health probes and short-lived metrics cache."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 30.0,
        clock: Clock | None = None,
        health_checker: HealthChecker | None = None,
        metrics_fetcher: MetricsFetcher | None = None,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock or time.monotonic
        self._health_checker = health_checker or self._collect_health_checks
        self._metrics_fetcher = metrics_fetcher or self._collect_metrics
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()

    async def get_overview(self, tenant_id: str) -> OverviewMetrics:
        now = self._clock()
        cached = self._cache.get(tenant_id)
        if cached is not None and cached.expires_at > now:
            return cached.metrics

        health_checks = await self._health_checker()
        db_healthy = next((row.healthy for row in health_checks if row.component == "db"), False)
        total_cost, total_executions, success_rate, active_agents = await self._metrics_fetcher(
            tenant_id, db_healthy
        )
        overview = OverviewMetrics(
            total_cost_today=total_cost,
            total_executions_today=total_executions,
            success_rate_today=success_rate,
            active_agents=active_agents,
            health_checks=health_checks,
        )

        async with self._lock:
            now = self._clock()
            cached = self._cache.get(tenant_id)
            if cached is not None and cached.expires_at > now:
                return cached.metrics
            self._cache[tenant_id] = _CacheEntry(
                metrics=overview,
                expires_at=now + self._ttl_seconds,
            )
            return overview

    async def _collect_health_checks(self) -> list[HealthStatus]:
        return [
            HealthStatus(component="runtime", healthy=True, message="api_process_alive"),
            await self._check_database_health(),
            await self._check_hatchet_health(),
            await self._check_llm_health(),
            await self._check_langfuse_health(),
        ]

    async def _collect_metrics(
        self,
        tenant_id: str,
        db_healthy: bool,
    ) -> tuple[Decimal, int, float, int]:
        if not db_healthy:
            return Decimal("0"), 0, 0.0, 0

        start_of_day = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        current_time = datetime.now(tz=timezone.utc)
        success_case = case((func.lower(LedgerRecord.status) == "success", 1), else_=0)

        try:
            engine = get_engine()
            session_factory = create_session_factory(engine)
            async with session_factory() as session:
                statement = (
                    select(
                        func.coalesce(func.sum(LedgerRecord.estimated_cost), 0),
                        func.count(LedgerRecord.id),
                        func.coalesce(func.sum(success_case), 0),
                        func.count(distinct(LedgerRecord.agent_id)),
                    )
                    .where(LedgerRecord.tenant_id == tenant_id)
                    .where(LedgerRecord.created_at >= start_of_day)
                    .where(LedgerRecord.created_at <= current_time)
                )
                row = (await session.execute(statement)).one()
        except Exception:
            logger.exception("Failed to aggregate overview metrics from ledger.")
            return Decimal("0"), 0, 0.0, 0

        raw_cost, raw_total, raw_success, raw_active_agents = row
        total_cost = Decimal(str(raw_cost)) if raw_cost is not None else Decimal("0")
        total_executions = int(raw_total or 0)
        success_count = int(raw_success or 0)
        active_agents = int(raw_active_agents or 0)
        success_rate = (success_count / total_executions) if total_executions > 0 else 0.0
        return total_cost, total_executions, round(success_rate, 4), active_agents

    async def _check_database_health(self) -> HealthStatus:
        started = time.perf_counter()
        try:
            engine = get_engine()
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            latency = (time.perf_counter() - started) * 1000
            return HealthStatus(
                component="db",
                healthy=True,
                latency_ms=round(latency, 3),
                message="connected",
            )
        except Exception as exc:
            latency = (time.perf_counter() - started) * 1000
            return HealthStatus(
                component="db",
                healthy=False,
                latency_ms=round(latency, 3),
                message=f"{exc.__class__.__name__}: {exc}",
            )

    async def _check_hatchet_health(self) -> HealthStatus:
        started = time.perf_counter()
        token = os.getenv("HATCHET_API_TOKEN", "").strip()
        server_url = os.getenv("HATCHET_SERVER_URL", "").strip() or "http://localhost:7077"
        if not token:
            return HealthStatus(component="hatchet", healthy=False, message="HATCHET_API_TOKEN not set")

        client = HatchetClient(
            HatchetConfig(
                server_url=server_url,
                api_token=token,
            )
        )
        try:
            await asyncio.wait_for(asyncio.to_thread(client.connect), timeout=3.0)
            await asyncio.to_thread(client.disconnect)
            latency = (time.perf_counter() - started) * 1000
            return HealthStatus(
                component="hatchet",
                healthy=True,
                latency_ms=round(latency, 3),
                message="connected",
            )
        except Exception as exc:
            latency = (time.perf_counter() - started) * 1000
            return HealthStatus(
                component="hatchet",
                healthy=False,
                latency_ms=round(latency, 3),
                message=f"{exc.__class__.__name__}: {exc}",
            )

    async def _check_llm_health(self) -> HealthStatus:
        started = time.perf_counter()
        env_candidates = (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "AZURE_OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "DEEPSEEK_API_KEY",
        )
        has_key = any(bool(os.getenv(key, "").strip()) for key in env_candidates)
        latency = (time.perf_counter() - started) * 1000
        if has_key:
            return HealthStatus(
                component="llm",
                healthy=True,
                latency_ms=round(latency, 3),
                message="provider_key_detected",
            )
        return HealthStatus(
            component="llm",
            healthy=False,
            latency_ms=round(latency, 3),
            message="no_llm_provider_key",
        )

    async def _check_langfuse_health(self) -> HealthStatus:
        started = time.perf_counter()
        public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
        latency = (time.perf_counter() - started) * 1000
        if public_key and secret_key:
            return HealthStatus(
                component="langfuse",
                healthy=True,
                latency_ms=round(latency, 3),
                message="credentials_present",
            )
        return HealthStatus(
            component="langfuse",
            healthy=False,
            latency_ms=round(latency, 3),
            message="LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY missing",
        )
