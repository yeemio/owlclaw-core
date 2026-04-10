"""Decide and optionally apply scheduler cutover backend from replay report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from owlclaw.integrations.hatchet_cutover import build_cutover_decision


def _load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _apply_backend(config_path: str | Path, backend: str) -> None:
    path = Path(config_path)
    payload: dict[str, Any] = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
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
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply scheduler cutover based on replay report")
    parser.add_argument(
        "--report",
        default=".kiro/specs/mionyee-hatchet-migration/dual_run_replay_report_all.json",
        help="Replay report JSON path",
    )
    parser.add_argument(
        "--config",
        default="examples/mionyee-trading/owlclaw.yaml",
        help="Config file path to update",
    )
    parser.add_argument("--apply", action="store_true", help="Apply recommended backend into config")
    parser.add_argument(
        "--force-backend",
        choices=["apscheduler", "dual", "hatchet"],
        default="",
        help="Force apply backend instead of recommendation",
    )
    parser.add_argument(
        "--output",
        default=".kiro/specs/mionyee-hatchet-migration/cutover_decision.json",
        help="Output decision JSON path",
    )
    args = parser.parse_args()

    report = _load_json(args.report)
    decision = build_cutover_decision(
        match_rate=float(report.get("match_rate", 0.0)),
        mismatch_count=len(report.get("mismatches", [])) if isinstance(report.get("mismatches"), list) else 0,
    )
    decision["report"] = str(args.report)
    decision["config"] = str(args.config)

    target_backend = args.force_backend or str(decision["recommended_backend"])
    decision["target_backend"] = target_backend

    if args.apply:
        _apply_backend(args.config, target_backend)
        decision["applied"] = True
    else:
        decision["applied"] = False

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"decision={output} backend={decision['recommended_backend']} applied={decision['applied']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
