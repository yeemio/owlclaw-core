"""Webhook monitoring service."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol

from owlclaw.triggers.webhook.types import (
    AlertRecord,
    HealthCheckResult,
    HealthStatusSnapshot,
    MetricRecord,
    MetricStats,
)


class AlertNotifierProtocol(Protocol):
    """Protocol for alert notification sink."""

    async def notify(self, alert: AlertRecord) -> None: ...


class MonitoringService:
    """Record metrics, evaluate health checks, and trigger alerts."""

    def __init__(
        self,
        *,
        alert_notifier: AlertNotifierProtocol | None = None,
        failure_rate_threshold: float = 0.2,
        response_time_threshold_ms: float = 3000.0,
        dedup_window_seconds: int = 300,
    ) -> None:
        self._alert_notifier = alert_notifier
        self._failure_rate_threshold = failure_rate_threshold
        self._response_time_threshold_ms = response_time_threshold_ms
        self._dedup_window = timedelta(seconds=dedup_window_seconds)
        self._metrics: list[MetricRecord] = []
        self._health_checks: dict[str, Callable[[], bool | Awaitable[bool]]] = {}
        self._alerts: list[AlertRecord] = []
        self._last_alert_at: dict[str, datetime] = {}

    def register_health_check(self, name: str, checker: Callable[[], bool | Awaitable[bool]]) -> None:
        self._health_checks[name] = checker

    async def record_metric(self, metric: MetricRecord) -> None:
        self._metrics.append(metric)
        await self._evaluate_thresholds(metric)

    async def get_health_status(self) -> HealthStatusSnapshot:
        checks: list[HealthCheckResult] = []
        for name, checker in self._health_checks.items():
            try:
                outcome = checker()
                healthy = await outcome if inspect.isawaitable(outcome) else bool(outcome)
            except Exception as exc:
                healthy = False
                checks.append(HealthCheckResult(name=name, status="fail", message=str(exc)))
                continue
            checks.append(HealthCheckResult(name=name, status="pass" if healthy else "fail"))
        failed = sum(1 for item in checks if item.status == "fail")
        status: Literal["healthy", "degraded", "unhealthy"]
        if not checks or failed == len(checks):
            status = "unhealthy"
        elif failed > 0:
            status = "degraded"
        else:
            status = "healthy"
        return HealthStatusSnapshot(status=status, checks=checks)

    async def trigger_alert(self, alert: AlertRecord) -> bool:
        now = alert.timestamp
        last = self._last_alert_at.get(alert.name)
        if last is not None and now - last < self._dedup_window:
            return False
        self._alerts.append(alert)
        self._last_alert_at[alert.name] = now
        if self._alert_notifier is not None:
            await self._alert_notifier.notify(alert)
        return True

    async def get_metrics(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        window: str = "realtime",
        tags: dict[str, str] | None = None,
    ) -> MetricStats:
        filtered = self._filter_metrics(start_time=start_time, end_time=end_time, window=window, tags=tags)
        request_samples = [m for m in filtered if m.name == "request_count"]
        response_samples = [m.value for m in filtered if m.name == "response_time_ms"]
        status_samples = [m for m in filtered if m.name == "request_status"]

        request_count = int(sum(m.value for m in request_samples))
        success = sum(1 for m in status_samples if m.tags.get("status") == "success")
        failure = sum(1 for m in status_samples if m.tags.get("status") == "failure")
        total_status = success + failure
        success_rate = (success / total_status) if total_status else 1.0
        failure_rate = (failure / total_status) if total_status else 0.0

        avg_response = _mean(response_samples)
        p95 = _percentile(response_samples, 95)
        p99 = _percentile(response_samples, 99)

        return MetricStats(
            request_count=request_count,
            success_rate=success_rate,
            failure_rate=failure_rate,
            avg_response_time=avg_response,
            p95_response_time=p95,
            p99_response_time=p99,
        )

    def get_alerts(self) -> list[AlertRecord]:
        return list(self._alerts)

    async def _evaluate_thresholds(self, metric: MetricRecord) -> None:
        if metric.name == "response_time_ms" and metric.value > self._response_time_threshold_ms:
            await self.trigger_alert(
                AlertRecord(
                    name="high_response_time",
                    severity="warning",
                    message=f"response time exceeded threshold: {metric.value}ms",
                    tags=metric.tags,
                )
            )
            return
        if metric.name != "request_status":
            return
        # Evaluate failure rate in recent realtime window.
        stats = await self.get_metrics(window="realtime")
        if stats.failure_rate > self._failure_rate_threshold:
            await self.trigger_alert(
                AlertRecord(
                    name="high_failure_rate",
                    severity="critical",
                    message=f"failure rate exceeded threshold: {stats.failure_rate:.3f}",
                )
            )

    def _filter_metrics(
        self,
        *,
        start_time: datetime | None,
        end_time: datetime | None,
        window: str,
        tags: dict[str, str] | None,
    ) -> list[MetricRecord]:
        now = datetime.now(timezone.utc)
        if window == "hour":
            start = now - timedelta(hours=1)
        elif window == "day":
            start = now - timedelta(days=1)
        else:
            start = now - timedelta(minutes=5)
        if start_time is not None:
            start = start_time
        end = end_time or now
        filtered = [m for m in self._metrics if start <= m.timestamp <= end]
        if not tags:
            return filtered
        return [m for m in filtered if all(m.tags.get(k) == v for k, v in tags.items())]


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(sum(values) / len(values))


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = int(round((p / 100.0) * (len(ordered) - 1)))
    return float(ordered[max(0, min(rank, len(ordered) - 1))])
