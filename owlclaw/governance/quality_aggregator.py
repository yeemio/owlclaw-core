"""Skill quality metric aggregation from ledger records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev
from typing import Any, Literal


@dataclass
class QualityWeights:
    """Weights for overall quality score."""

    success_rate: float = 0.30
    intervention_rate: float = 0.25
    satisfaction: float = 0.20
    consistency: float = 0.15
    latency: float = 0.05
    cost: float = 0.05


@dataclass
class SkillQualityReport:
    """Aggregated quality report for one skill."""

    skill_name: str
    tenant_id: str
    window_start: datetime
    window_end: datetime
    total_runs: int
    success_rate: float
    avg_latency_ms: float
    avg_cost: float
    intervention_rate: float
    consistency: float
    satisfaction: float
    quality_score: float


class QualityAggregator:
    """Aggregate ledger-like records into quality metrics."""

    def __init__(self, weights: QualityWeights | None = None) -> None:
        self.weights = weights or QualityWeights()

    def compute_report(
        self,
        *,
        tenant_id: str,
        skill_name: str,
        records: list[Any],
        window_end: datetime | None = None,
        window: timedelta = timedelta(days=30),
    ) -> SkillQualityReport:
        end = window_end or datetime.now(timezone.utc)
        start = end - window
        filtered = self._filter_records(
            tenant_id=tenant_id,
            skill_name=skill_name,
            records=records,
            window_start=start,
            window_end=end,
        )
        total_runs = len(filtered)

        if total_runs == 0:
            return SkillQualityReport(
                skill_name=skill_name,
                tenant_id=tenant_id,
                window_start=start,
                window_end=end,
                total_runs=0,
                success_rate=0.0,
                avg_latency_ms=0.0,
                avg_cost=0.0,
                intervention_rate=0.0,
                consistency=0.0,
                satisfaction=0.0,
                quality_score=0.0,
            )

        success_rate = sum(1 for r in filtered if str(getattr(r, "status", "")).lower() == "success") / total_runs
        latencies = [float(getattr(r, "execution_time_ms", 0) or 0) for r in filtered]
        costs = [float(getattr(r, "estimated_cost", 0) or 0) for r in filtered]
        avg_latency = mean(latencies)
        avg_cost = mean(costs)
        intervention_rate = self._calc_intervention_rate(filtered)
        satisfaction = self._calc_satisfaction(filtered)
        consistency = self._calc_consistency(filtered)
        quality_score = self._calc_weighted_score(
            success_rate=success_rate,
            avg_latency_ms=avg_latency,
            avg_cost=avg_cost,
            intervention_rate=intervention_rate,
            consistency=consistency,
            satisfaction=satisfaction,
        )

        return SkillQualityReport(
            skill_name=skill_name,
            tenant_id=tenant_id,
            window_start=start,
            window_end=end,
            total_runs=total_runs,
            success_rate=round(success_rate, 6),
            avg_latency_ms=round(avg_latency, 3),
            avg_cost=round(avg_cost, 6),
            intervention_rate=round(intervention_rate, 6),
            consistency=round(consistency, 6),
            satisfaction=round(satisfaction, 6),
            quality_score=round(quality_score, 6),
        )

    def compute_trend(
        self,
        *,
        tenant_id: str,
        skill_name: str,
        records: list[Any],
        window_end: datetime | None = None,
        periods: int = 6,
        granularity: Literal["day", "week", "month"] = "week",
    ) -> list[SkillQualityReport]:
        """Compute per-window quality reports (day/week/month)."""
        end = window_end or datetime.now(timezone.utc)
        span = self._period_span(granularity)
        output: list[SkillQualityReport] = []
        cursor = end
        for _ in range(periods):
            report = self.compute_report(
                tenant_id=tenant_id,
                skill_name=skill_name,
                records=records,
                window_end=cursor,
                window=span,
            )
            output.append(report)
            cursor = report.window_start
        output.reverse()
        return output

    @staticmethod
    def _period_span(granularity: Literal["day", "week", "month"]) -> timedelta:
        if granularity == "day":
            return timedelta(days=1)
        if granularity == "week":
            return timedelta(days=7)
        return timedelta(days=30)

    @staticmethod
    def _record_timestamp(record: Any) -> datetime | None:
        raw = getattr(record, "created_at", None)
        if not isinstance(raw, datetime):
            return None
        if raw.tzinfo is None:
            return raw.replace(tzinfo=timezone.utc)
        return raw.astimezone(timezone.utc)

    def _filter_records(
        self,
        *,
        tenant_id: str,
        skill_name: str,
        records: list[Any],
        window_start: datetime,
        window_end: datetime,
    ) -> list[Any]:
        out: list[Any] = []
        for record in records:
            if getattr(record, "tenant_id", "") != tenant_id:
                continue
            if getattr(record, "capability_name", "") != skill_name:
                continue
            record_time = self._record_timestamp(record)
            if record_time is not None and (record_time < window_start or record_time > window_end):
                continue
            out.append(record)
        return out

    @staticmethod
    def _calc_intervention_rate(records: list[Any]) -> float:
        interventions = 0
        for record in records:
            params = getattr(record, "input_params", {}) or {}
            if isinstance(params, dict):
                if bool(params.get("requires_confirmation")) or bool(params.get("manual_intervention")):
                    interventions += 1
        return interventions / len(records) if records else 0.0

    @staticmethod
    def _calc_satisfaction(records: list[Any]) -> float:
        total = len(records)
        if total == 0:
            return 0.0
        approvals = 0
        modifications = 0
        for record in records:
            params = getattr(record, "input_params", {}) or {}
            if isinstance(params, dict) and params.get("approval") in {"approved", True, "pass"}:
                approvals += 1
            output = getattr(record, "output_result", {}) or {}
            if isinstance(output, dict) and (bool(output.get("modified")) or bool(output.get("requires_revision"))):
                modifications += 1
        approval_rate = approvals / total
        modification_rate = modifications / total
        return max(0.0, min(1.0, (approval_rate + (1.0 - modification_rate)) / 2.0))

    @staticmethod
    def _calc_consistency(records: list[Any]) -> float:
        outcomes = [1.0 if str(getattr(r, "status", "")).lower() == "success" else 0.0 for r in records]
        if len(outcomes) <= 1:
            return 1.0
        variance = pstdev(outcomes)
        return max(0.0, min(1.0, 1.0 - variance))

    def _calc_weighted_score(
        self,
        *,
        success_rate: float,
        avg_latency_ms: float,
        avg_cost: float,
        intervention_rate: float,
        consistency: float,
        satisfaction: float,
    ) -> float:
        latency_norm = min(avg_latency_ms / 10_000.0, 1.0)
        cost_norm = min(avg_cost / 1.0, 1.0)
        score = (
            self.weights.success_rate * success_rate
            + self.weights.intervention_rate * (1.0 - intervention_rate)
            + self.weights.satisfaction * satisfaction
            + self.weights.consistency * consistency
            + self.weights.latency * (1.0 - latency_norm)
            + self.weights.cost * (1.0 - cost_norm)
        )
        return max(0.0, min(1.0, score))
