"""Final acceptance report generator for mionyee hatchet migration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from owlclaw.integrations.hatchet_acceptance import (
    build_status_snapshot,
    evaluate_e2e_acceptance,
    verify_restart_recovery,
)
from owlclaw.integrations.hatchet_migration import load_jobs_from_mionyee_scenarios


def _set_backend(config_path: Path, backend: str) -> None:
    payload: dict[str, Any] = {}
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        payload = loaded if isinstance(loaded, dict) else {}
    migration = payload.get("migration")
    if not isinstance(migration, dict):
        migration = {}
        payload["migration"] = migration
    migration["scheduler_backend"] = backend
    dual_run = migration.get("dual_run")
    if not isinstance(dual_run, dict):
        dual_run = {}
        migration["dual_run"] = dual_run
    dual_run["enabled"] = backend == "dual"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _verify_rollback(config_path: Path) -> bool:
    original = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    try:
        _set_backend(config_path, "apscheduler")
        aps = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if aps.get("migration", {}).get("scheduler_backend") != "apscheduler":
            return False
        _set_backend(config_path, "hatchet")
        hat = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return hat.get("migration", {}).get("scheduler_backend") == "hatchet"
    finally:
        config_path.write_text(yaml.safe_dump(original, sort_keys=False, allow_unicode=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate final acceptance report for mionyee hatchet migration")
    parser.add_argument(
        "--input",
        default="config/e2e/scenarios/mionyee-tasks.json",
        help="Input scenario JSON path",
    )
    parser.add_argument(
        "--report",
        default=".kiro/specs/mionyee-hatchet-migration/dual_run_replay_report_all.json",
        help="Dual-run replay report path",
    )
    parser.add_argument(
        "--config",
        default="examples/mionyee-trading/owlclaw.yaml",
        help="Scheduler config path",
    )
    parser.add_argument(
        "--output",
        default=".kiro/specs/mionyee-hatchet-migration/final_acceptance_report.json",
        help="Output acceptance report path",
    )
    args = parser.parse_args()

    jobs_before = load_jobs_from_mionyee_scenarios(args.input)
    jobs_after = load_jobs_from_mionyee_scenarios(args.input)
    recovery = verify_restart_recovery(jobs_before, jobs_after)
    status_snapshot = build_status_snapshot(args.report, args.config)

    generated_files = [
        "examples/mionyee-trading/generated_hatchet_tasks.py",
        "examples/mionyee-trading/generated_hatchet_tasks_canary.py",
        "examples/mionyee-trading/generated_hatchet_tasks_simple_cron.py",
        "examples/mionyee-trading/generated_hatchet_tasks_stateful_cron.py",
        "examples/mionyee-trading/generated_hatchet_tasks_chained.py",
    ]
    generated_files_ok = all(Path(item).exists() for item in generated_files)
    rollback_verified = _verify_rollback(Path(args.config))

    gate = evaluate_e2e_acceptance(
        recovery_ok=bool(recovery.get("recovered")),
        status_snapshot=status_snapshot,
        rollback_verified=rollback_verified,
        generated_files_ok=generated_files_ok,
    )
    report = {
        "recovery": recovery,
        "status_snapshot": status_snapshot,
        "rollback_verified": rollback_verified,
        "generated_files_ok": generated_files_ok,
        "gate": gate,
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"acceptance={output} passed={gate['passed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
