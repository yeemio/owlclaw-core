"""Generate Hatchet task module from mionyee APScheduler-equivalent scenarios."""

from __future__ import annotations

import argparse
from pathlib import Path

from owlclaw.integrations.hatchet_migration import (
    load_jobs_from_mionyee_scenarios,
    select_canary_batch,
    write_complexity_modules,
    write_generated_hatchet_module,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Hatchet tasks from mionyee scenario definitions")
    parser.add_argument(
        "--input",
        default="config/e2e/scenarios/mionyee-tasks.json",
        help="Input scenario JSON path",
    )
    parser.add_argument(
        "--output",
        default="examples/mionyee-trading/generated_hatchet_tasks.py",
        help="Output generated Python module path",
    )
    parser.add_argument(
        "--canary-only",
        action="store_true",
        help="Generate only canary batch jobs",
    )
    parser.add_argument(
        "--by-complexity",
        action="store_true",
        help="Generate one file per complexity bucket",
    )
    args = parser.parse_args()

    jobs = load_jobs_from_mionyee_scenarios(args.input)
    selected = select_canary_batch(jobs) if args.canary_only else jobs
    if args.by_complexity:
        outputs = write_complexity_modules(selected, Path(args.output).parent)
        print(
            "generated_complexity="
            + ",".join(f"{name}:{path}" for name, path in outputs.items())
            + f" jobs={len(selected)}"
        )
        return 0
    target = write_generated_hatchet_module(selected, args.output)
    print(f"generated={target} jobs={len(selected)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
