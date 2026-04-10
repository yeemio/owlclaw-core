"""Replay-style dual-run comparison for APScheduler vs Hatchet migration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from owlclaw.integrations.hatchet_migration import (
    dual_run_replay_compare,
    load_jobs_from_mionyee_scenarios,
    select_canary_batch,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay compare APScheduler and Hatchet outputs")
    parser.add_argument(
        "--input",
        default="config/e2e/scenarios/mionyee-tasks.json",
        help="Input scenario JSON path",
    )
    parser.add_argument(
        "--canary-only",
        action="store_true",
        help="Compare only canary batch",
    )
    parser.add_argument(
        "--output",
        default=".kiro/specs/mionyee-hatchet-migration/dual_run_replay_report.json",
        help="Output report path",
    )
    args = parser.parse_args()

    jobs = load_jobs_from_mionyee_scenarios(args.input)
    selected = select_canary_batch(jobs) if args.canary_only else jobs
    report = dual_run_replay_compare(selected)
    report["mode"] = "canary" if args.canary_only else "all"
    report["jobs"] = [job.name for job in selected]

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report={output} compared={report['compared']} matched={report['matched']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
