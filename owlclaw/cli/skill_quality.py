"""owlclaw skill quality — display quality reports from local snapshots."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import typer

from owlclaw.governance.quality_detector import detect_degradation, suggest_improvements
from owlclaw.governance.quality_store import InMemoryQualityStore, SkillQualitySnapshot

_STORE = InMemoryQualityStore()


def _parse_period(period: str) -> timedelta:
    value = period.strip().lower()
    if value.endswith("d") and value[:-1].isdigit():
        return timedelta(days=int(value[:-1]))
    if value.endswith("w") and value[:-1].isdigit():
        return timedelta(days=7 * int(value[:-1]))
    if value.endswith("m") and value[:-1].isdigit():
        return timedelta(days=30 * int(value[:-1]))
    return timedelta(days=30)


def quality_command(
    skill_name: str = typer.Argument("", help="Skill name for single report."),
    all: bool = typer.Option(False, "--all", help="Show latest quality for all skills."),
    trend: bool = typer.Option(False, "--trend", help="Show quality trend in selected period."),
    period: str = typer.Option("30d", "--period", help="Trend period: 7d/30d/12w/etc."),
    suggest: bool = typer.Option(False, "--suggest", help="Show improvement suggestions."),
    tenant: str = typer.Option("default", "--tenant", help="Tenant id."),
) -> None:
    """Show quality report(s) from in-memory snapshots."""
    if not skill_name and not all:
        typer.echo("Error: provide <skill-name> or --all.", err=True)
        raise typer.Exit(2)

    if all:
        rows = _STORE.all_latest(tenant_id=tenant)
        if not rows:
            typer.echo("No quality snapshots found.")
            return
        for row in rows:
            typer.echo(f"{row.skill_name}: score={row.quality_score:.3f}")
        return

    snapshots = _STORE.list_for_skill(tenant_id=tenant, skill_name=skill_name)
    if not snapshots:
        typer.echo("No quality snapshots found.")
        return
    latest = snapshots[-1]
    typer.echo(f"{latest.skill_name}: score={latest.quality_score:.3f}")
    if trend:
        since = datetime.now(timezone.utc) - _parse_period(period)
        trend_rows = [s for s in snapshots if s.computed_at >= since]
        typer.echo(f"trend_points={len(trend_rows)} period={period}")
        for row in trend_rows[-10:]:
            typer.echo(f"{row.computed_at.date()}: {row.quality_score:.3f}")
        if detect_degradation(trend_rows):
            typer.echo("degradation_detected=true")
    if suggest:
        for item in suggest_improvements(latest):
            typer.echo(f"- {item}")


def _seed_snapshot(snapshot: SkillQualitySnapshot) -> None:
    """Test helper to populate local store."""
    _STORE.save(snapshot)
