"""Mailbox protocol helpers for OwlClaw workflow agents."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


VALID_AGENT_NAMES = {"main", "review", "codex", "codex-gpt"}
VALID_ACK_STATUS = {
    "seen",
    "started",
    "blocked",
    "done",
    "idle",
    "waiting_review",
    "waiting_merge",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_dir(repo_root: Path) -> Path:
    return repo_root / ".kiro" / "runtime"


def _mailbox_dir(repo_root: Path) -> Path:
    return _runtime_dir(repo_root) / "mailboxes"


def _ack_dir(repo_root: Path) -> Path:
    return _runtime_dir(repo_root) / "acks"


def _validate_agent(agent: str) -> str:
    if agent not in VALID_AGENT_NAMES:
        raise ValueError(f"unknown agent '{agent}', expected one of: {', '.join(sorted(VALID_AGENT_NAMES))}")
    return agent


def _mailbox_path(repo_root: Path, agent: str) -> Path:
    return _mailbox_dir(repo_root) / f"{agent}.json"


def _ack_path(repo_root: Path, agent: str) -> Path:
    return _ack_dir(repo_root) / f"{agent}.json"


def ensure_runtime_dirs(repo_root: Path) -> None:
    _mailbox_dir(repo_root).mkdir(parents=True, exist_ok=True)
    _ack_dir(repo_root).mkdir(parents=True, exist_ok=True)


def read_mailbox(repo_root: Path, agent: str) -> dict[str, object]:
    _validate_agent(agent)
    path = _mailbox_path(repo_root, agent)
    if not path.exists():
        raise FileNotFoundError(f"mailbox not found for agent '{agent}': {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def read_ack(repo_root: Path, agent: str) -> dict[str, object] | None:
    _validate_agent(agent)
    path = _ack_path(repo_root, agent)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_ack(
    repo_root: Path,
    agent: str,
    *,
    status: str,
    note: str = "",
    task_ref: str = "",
    commit_ref: str = "",
) -> dict[str, object]:
    _validate_agent(agent)
    if status not in VALID_ACK_STATUS:
        raise ValueError(f"invalid ack status '{status}'")
    ensure_runtime_dirs(repo_root)
    payload = {
        "mailbox_version": 1,
        "agent": agent,
        "acked_at": _utc_now(),
        "status": status,
        "note": note.strip(),
        "task_ref": task_ref.strip(),
        "commit_ref": commit_ref.strip(),
    }
    _ack_path(repo_root, agent).write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return payload


def _format_mailbox(mailbox: dict[str, object], ack: dict[str, object] | None) -> str:
    lines = [
        f"# Mailbox: {mailbox['agent']}",
        "",
        f"- generated_at: {mailbox.get('generated_at', '')}",
        f"- stage: {mailbox.get('stage', '')}",
        f"- owner: {mailbox.get('owner', '')}",
        f"- action: {mailbox.get('action', '')}",
        f"- priority: {mailbox.get('priority', '')}",
        "",
        "## Summary",
        f"{mailbox.get('summary', '')}",
    ]
    blockers = mailbox.get("blockers") or []
    if blockers:
        lines.extend(["", "## Blockers"])
        lines.extend(f"- {item}" for item in blockers)
    commits = mailbox.get("pending_commits") or []
    if commits:
        lines.extend(["", "## Pending Commits"])
        lines.extend(f"- {item}" for item in commits)
    if ack:
        lines.extend(
            [
                "",
                "## Ack",
                f"- status: {ack.get('status', '')}",
                f"- acked_at: {ack.get('acked_at', '')}",
                f"- note: {ack.get('note', '')}",
            ]
        )
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read or acknowledge workflow mailbox instructions.")
    parser.add_argument("--repo-root", default=".", help="Path to the main repository root.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    pull = subparsers.add_parser("pull", help="Read the latest mailbox for an agent.")
    pull.add_argument("--agent", required=True, choices=sorted(VALID_AGENT_NAMES))
    pull.add_argument("--json", action="store_true", help="Emit mailbox as JSON.")

    ack = subparsers.add_parser("ack", help="Write an acknowledgement for an agent.")
    ack.add_argument("--agent", required=True, choices=sorted(VALID_AGENT_NAMES))
    ack.add_argument("--status", required=True, choices=sorted(VALID_ACK_STATUS))
    ack.add_argument("--note", default="", help="Short free-form note.")
    ack.add_argument("--task-ref", default="", help="Task or finding reference, e.g. D23.")
    ack.add_argument("--commit-ref", default="", help="Commit hash or branch note.")
    ack.add_argument("--json", action="store_true", help="Emit ack as JSON.")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    repo_root = Path(args.repo_root).resolve()
    agent = _validate_agent(args.agent)

    if args.command == "pull":
        mailbox = read_mailbox(repo_root, agent)
        ack = read_ack(repo_root, agent)
        if args.json:
            print(json.dumps({"mailbox": mailbox, "ack": ack}, ensure_ascii=True, indent=2))
        else:
            print(_format_mailbox(mailbox, ack))
        return 0

    if args.command == "ack":
        payload = write_ack(
            repo_root,
            agent,
            status=args.status,
            note=args.note,
            task_ref=args.task_ref,
            commit_ref=args.commit_ref,
        )
        if args.json:
            print(json.dumps(payload, ensure_ascii=True, indent=2))
        else:
            print(f"{agent}: ack status={payload['status']} task={payload['task_ref']} commit={payload['commit_ref']}")
        return 0

    raise AssertionError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
