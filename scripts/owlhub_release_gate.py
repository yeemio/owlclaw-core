"""Run OwlHub production release-gate checks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from owlclaw.owlhub.release_gate import run_release_gate


def main() -> int:
    parser = argparse.ArgumentParser(description="Run OwlHub production release-gate checks.")
    parser.add_argument("--api-base-url", required=True, help="OwlHub API base url, e.g. https://hub.example.com")
    parser.add_argument("--index-url", required=True, help="Public index.json url")
    parser.add_argument("--query", default="skill", help="Search query for CLI smoke check")
    parser.add_argument(
        "--work-dir",
        default=".owlhub/release-gate",
        help="Temporary workspace for lock/install cache",
    )
    parser.add_argument("--output", default="", help="Optional output json file path")
    args = parser.parse_args()

    report = run_release_gate(
        api_base_url=args.api_base_url,
        index_url=args.index_url,
        query=args.query,
        work_dir=Path(args.work_dir),
    )
    payload = json.dumps(report.as_dict(), ensure_ascii=False, indent=2)
    print(payload)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n", encoding="utf-8")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
