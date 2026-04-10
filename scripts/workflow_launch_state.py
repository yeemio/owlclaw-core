"""Track workflow window launch state during sequential startup."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


VALID_STATUSES = {"starting", "running", "exited"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_dir(repo_root: Path) -> Path:
    return repo_root / ".kiro" / "runtime" / "launch-state"


def _state_path(repo_root: Path, agent: str) -> Path:
    return _state_dir(repo_root) / f"{agent}.json"


def read_state(repo_root: Path, agent: str) -> dict[str, object] | None:
    path = _state_path(repo_root, agent)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_state(
    repo_root: Path,
    agent: str,
    *,
    status: str,
    pid: int | None = None,
    note: str = "",
    exit_code: int | None = None,
) -> dict[str, object]:
    if status not in VALID_STATUSES:
        raise ValueError(f"Unsupported status: {status}")
    _state_dir(repo_root).mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "agent": agent,
        "status": status,
        "updated_at": _utc_now(),
    }
    if pid is not None:
        payload["pid"] = pid
    if note:
        payload["note"] = note
    if exit_code is not None:
        payload["exit_code"] = exit_code
    _state_path(repo_root, agent).write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return payload


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read and write workflow launch state.")
    parser.add_argument("--repo-root", default=".", help="Main repository root.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    update = subparsers.add_parser("update", help="Write launch state.")
    update.add_argument("--agent", required=True)
    update.add_argument("--status", required=True, choices=sorted(VALID_STATUSES))
    update.add_argument("--pid", type=int)
    update.add_argument("--note", default="")
    update.add_argument("--exit-code", type=int)
    update.add_argument("--json", action="store_true")

    show = subparsers.add_parser("show", help="Read launch state.")
    show.add_argument("--agent", required=True)
    show.add_argument("--json", action="store_true")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    repo_root = Path(args.repo_root).resolve()

    if args.command == "update":
        payload = write_state(
            repo_root,
            args.agent,
            status=args.status,
            pid=args.pid,
            note=args.note,
            exit_code=args.exit_code,
        )
    else:
        payload = read_state(repo_root, args.agent) or {}

    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=True, indent=2))
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
