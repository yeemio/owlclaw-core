"""Keep audit runtime state fresh for audit-a / audit-b windows."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import workflow_audit_state


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Continuously refresh audit runtime state.")
    parser.add_argument("--repo-root", default=".", help="Path to the main repository root.")
    parser.add_argument("--agent", required=True, choices=sorted(workflow_audit_state.VALID_AUDIT_AGENTS))
    parser.add_argument("--status", default="idle", choices=sorted(workflow_audit_state.VALID_AUDIT_STATUS))
    parser.add_argument("--summary", default="", help="Short work summary.")
    parser.add_argument("--finding-ref", default="", help="Finding reference, e.g. D48.")
    parser.add_argument("--note", default="", help="Free-form note.")
    parser.add_argument("--interval", type=int, default=60, help="Heartbeat interval in seconds.")
    parser.add_argument("--once", action="store_true", help="Write one heartbeat and exit.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve()

    while True:
        workflow_audit_state.write_state(
            repo_root,
            args.agent,
            status=args.status,
            summary=args.summary,
            finding_ref=args.finding_ref,
            note=args.note,
        )
        if args.once:
            return 0
        time.sleep(max(10, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
