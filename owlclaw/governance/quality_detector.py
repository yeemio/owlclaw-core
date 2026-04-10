"""Quality trend degradation detector and suggestion generator."""

from __future__ import annotations

from typing import Any

from owlclaw.governance.quality_store import SkillQualitySnapshot
from owlclaw.triggers.signal import Signal, SignalRouter, SignalSource, SignalType


def detect_degradation(snapshots: list[SkillQualitySnapshot]) -> bool:
    """Return True when last 3 snapshots each drop by >10%."""
    if len(snapshots) < 3:
        return False
    ordered = sorted(snapshots, key=lambda s: s.computed_at)
    recent = ordered[-3:]
    scores = [s.quality_score for s in recent]
    return all(scores[i] < scores[i - 1] * 0.9 for i in range(1, len(scores)))


def suggest_improvements(snapshot: SkillQualitySnapshot) -> list[str]:
    """Generate suggestions from weakest metrics."""
    metrics: dict[str, Any] = snapshot.metrics if isinstance(snapshot.metrics, dict) else {}
    suggestions: list[str] = []

    if float(metrics.get("success_rate", 1.0)) < 0.8:
        suggestions.append("Improve success rate: review failure categories and add guardrails.")
    if float(metrics.get("intervention_rate", 0.0)) > 0.3:
        suggestions.append("Reduce manual intervention by clarifying business rules and thresholds.")
    if float(metrics.get("consistency", 1.0)) < 0.7:
        suggestions.append("Increase consistency by tightening trigger conditions and expected outputs.")
    if float(metrics.get("avg_latency_ms", 0.0)) > 3000:
        suggestions.append("Lower latency by reducing tool chain depth and context size.")
    if float(metrics.get("avg_cost", 0.0)) > 0.3:
        suggestions.append("Lower cost by routing simple tasks to cheaper models.")
    if not suggestions:
        suggestions.append("Quality is stable; continue monitoring weekly trends.")
    return suggestions


async def notify_quality_degradation(
    *,
    router: SignalRouter,
    tenant_id: str,
    agent_id: str,
    skill_name: str,
    score: float,
    operator: str = "quality-monitor",
) -> None:
    """Dispatch a Signal INSTRUCT alert for quality degradation."""
    signal = Signal(
        type=SignalType.INSTRUCT,
        source=SignalSource.API,
        agent_id=agent_id,
        tenant_id=tenant_id,
        operator=operator,
        message=f"Quality degradation detected for '{skill_name}' (score={score:.3f}).",
        focus=skill_name,
    )
    await router.dispatch(signal)
