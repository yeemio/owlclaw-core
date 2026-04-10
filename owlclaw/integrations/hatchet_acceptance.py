"""Acceptance helpers for mionyee APScheduler -> Hatchet migration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from owlclaw.integrations.hatchet_migration import APSchedulerJob


def verify_restart_recovery(before: list[APSchedulerJob], after: list[APSchedulerJob]) -> dict[str, Any]:
    """Verify equivalent task registry survives restart/reload."""
    before_keys = {(job.name, job.cron, job.func_ref) for job in before}
    after_keys = {(job.name, job.cron, job.func_ref) for job in after}
    recovered = before_keys.issubset(after_keys)
    return {
        "before_count": len(before_keys),
        "after_count": len(after_keys),
        "recovered_count": len(before_keys.intersection(after_keys)),
        "recovered": recovered,
    }


def build_status_snapshot(report_path: str | Path, config_path: str | Path) -> dict[str, Any]:
    """Build status snapshot queryable from CLI artifacts."""
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    cfg_payload = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    migration = cfg_payload.get("migration", {}) if isinstance(cfg_payload, dict) else {}
    backend = migration.get("scheduler_backend") if isinstance(migration, dict) else None
    return {
        "backend": backend,
        "compared": int(report.get("compared", 0)),
        "matched": int(report.get("matched", 0)),
        "match_rate": float(report.get("match_rate", 0.0)),
        "mismatch_count": len(report.get("mismatches", [])) if isinstance(report.get("mismatches"), list) else 0,
    }


def evaluate_e2e_acceptance(
    *,
    recovery_ok: bool,
    status_snapshot: dict[str, Any],
    rollback_verified: bool,
    generated_files_ok: bool,
) -> dict[str, Any]:
    """Evaluate final acceptance gate for mionyee hatchet migration."""
    status_ok = (
        status_snapshot.get("backend") in {"hatchet", "dual", "apscheduler"}
        and int(status_snapshot.get("compared", 0)) >= 1
        and float(status_snapshot.get("match_rate", 0.0)) >= 1.0
        and int(status_snapshot.get("mismatch_count", 0)) == 0
    )
    passed = recovery_ok and status_ok and rollback_verified and generated_files_ok
    return {
        "recovery_ok": recovery_ok,
        "status_ok": status_ok,
        "rollback_verified": rollback_verified,
        "generated_files_ok": generated_files_ok,
        "passed": passed,
    }
