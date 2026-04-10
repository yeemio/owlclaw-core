"""Read and write audit runtime state for audit-a / audit-b windows."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import workflow_objects  # noqa: E402


VALID_AUDIT_AGENTS = {"audit-a", "audit-b"}
VALID_AUDIT_STATUS = {"idle", "started", "blocked", "done", "waiting_review"}
AUDIT_DIMENSIONS = {"core_logic", "lifecycle_integrations", "io_boundaries", "data_security"}
THINKING_LENSES = {"correctness", "failure", "adversary", "drift", "omission"}
AUDIT_PROFILES = {
    "audit-a": {
        "profile": "deep_audit",
        "source_type": "audit",
        "required_dimensions": sorted(AUDIT_DIMENSIONS),
        "required_lenses": sorted(THINKING_LENSES),
        "mode": "read_only_code_audit",
        "required_output": "finding_only",
        "prompt": "继续深度审计。严格按 deep-codebase-audit skill 做多维度代码审计：必须读代码，不得只读文档；不得修改代码；只允许通过 workflow_audit_state.py finding 向 main 提交结构化 findings。",
    },
    "audit-b": {
        "profile": "audit_review",
        "source_type": "audit_review",
        "required_dimensions": sorted(AUDIT_DIMENSIONS),
        "required_lenses": sorted(THINKING_LENSES),
        "mode": "read_only_code_audit_review",
        "required_output": "finding_only",
        "prompt": "继续审计复核。严格按 deep-codebase-audit skill 复核本轮审计：必须重新读代码验证并继续找漏项，不得只复述已有报告；不得修改代码；只允许通过 workflow_audit_state.py finding 向 main 提交结构化 findings。",
    },
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_dir(repo_root: Path) -> Path:
    return repo_root / ".kiro" / "runtime"


def _state_dir(repo_root: Path) -> Path:
    return _runtime_dir(repo_root) / "audit-state"


def _state_path(repo_root: Path, agent: str) -> Path:
    return _state_dir(repo_root) / f"{agent}.json"


def _validate_agent(agent: str) -> str:
    if agent not in VALID_AUDIT_AGENTS:
        raise ValueError(f"unknown audit agent '{agent}'")
    return agent


def ensure_dirs(repo_root: Path) -> None:
    _state_dir(repo_root).mkdir(parents=True, exist_ok=True)


def read_state(repo_root: Path, agent: str) -> dict[str, object] | None:
    _validate_agent(agent)
    path = _state_path(repo_root, agent)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_state(
    repo_root: Path,
    agent: str,
    *,
    status: str,
    summary: str = "",
    finding_ref: str = "",
    note: str = "",
    files_read: list[str] | None = None,
    dimensions_covered: list[str] | None = None,
    lines_read: int = 0,
) -> dict[str, object]:
    _validate_agent(agent)
    if status not in VALID_AUDIT_STATUS:
        raise ValueError(f"invalid audit status '{status}'")
    ensure_dirs(repo_root)
    profile = AUDIT_PROFILES[agent]
    payload = {
        "agent": agent,
        "profile": profile["profile"],
        "status": status,
        "summary": summary.strip(),
        "finding_ref": finding_ref.strip(),
        "note": note.strip(),
        "mode": profile["mode"],
        "required_output": profile["required_output"],
        "code_changes_allowed": False,
        "required_dimensions": profile["required_dimensions"],
        "required_lenses": profile["required_lenses"],
        "files_read": list(files_read or []),
        "dimensions_covered": list(dimensions_covered or []),
        "lines_read": lines_read,
        "updated_at": _utc_now(),
    }
    _state_path(repo_root, agent).write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read or write audit runtime state.")
    parser.add_argument("--repo-root", default=".", help="Path to the main repository root.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    show = subparsers.add_parser("show", help="Show current audit state.")
    show.add_argument("--agent", required=True, choices=sorted(VALID_AUDIT_AGENTS))
    show.add_argument("--json", action="store_true", help="Emit JSON output.")

    update = subparsers.add_parser("update", help="Update current audit state.")
    update.add_argument("--agent", required=True, choices=sorted(VALID_AUDIT_AGENTS))
    update.add_argument("--status", required=True, choices=sorted(VALID_AUDIT_STATUS))
    update.add_argument("--summary", default="", help="Short work summary.")
    update.add_argument("--finding-ref", default="", help="Finding or task reference, e.g. D48.")
    update.add_argument("--note", default="", help="Free-form note.")
    update.add_argument("--file-read", action="append", default=[], help="Code file read during this audit step.")
    update.add_argument(
        "--dimension-covered",
        action="append",
        default=[],
        choices=sorted(AUDIT_DIMENSIONS),
        help="Audit dimension covered in this step.",
    )
    update.add_argument("--lines-read", type=int, default=0, help="Approximate code lines reviewed in this step.")
    update.add_argument("--json", action="store_true", help="Emit JSON output.")

    finding = subparsers.add_parser("finding", help="Create a structured audit finding.")
    finding.add_argument("--agent", required=True, choices=sorted(VALID_AUDIT_AGENTS))
    finding.add_argument("--title", required=True, help="Short finding title.")
    finding.add_argument("--summary", required=True, help="Finding summary.")
    finding.add_argument("--severity", required=True, choices=["p0", "p1", "high", "medium", "low"])
    finding.add_argument("--spec", default="", help="Related spec name.")
    finding.add_argument("--task-ref", default="", help="Related task or finding reference.")
    finding.add_argument("--target-agent", default="", choices=["", "main", "review", "codex", "codex-gpt"], help="Suggested target agent.")
    finding.add_argument("--target-branch", default="", help="Suggested target branch.")
    finding.add_argument("--file", action="append", required=True, help="Code file inspected for this finding.")
    finding.add_argument(
        "--dimension",
        action="append",
        required=True,
        choices=sorted(AUDIT_DIMENSIONS),
        help="Audit dimension that surfaced this finding.",
    )
    finding.add_argument(
        "--lens",
        action="append",
        required=True,
        choices=sorted(THINKING_LENSES),
        help="Thinking lens used to derive the finding.",
    )
    finding.add_argument("--evidence", required=True, help="Concrete code evidence or traced data flow proving the finding.")
    finding.add_argument("--json", action="store_true", help="Emit JSON output.")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve()

    if args.command == "show":
        payload = read_state(repo_root, args.agent)
        if payload is None:
            payload = {"agent": args.agent, "status": "missing"}
        if args.json:
            print(json.dumps(payload, ensure_ascii=True, indent=2))
        else:
            print(f"{payload['agent']}: {payload['status']}")
        return 0

    if args.command == "finding":
        profile = AUDIT_PROFILES[args.agent]
        payload = workflow_objects.create_object(
            repo_root,
            "finding",
            payload={
                "status": "new",
                "owner": "main",
                "source": args.agent,
                "source_type": profile["source_type"],
                "title": args.title,
                "summary": args.summary,
                "severity": args.severity,
                "refs": {
                    "spec": args.spec,
                    "task_ref": args.task_ref,
                },
                "relations": {
                    "parent_delivery_id": "",
                    "parent_verdict_id": "",
                },
                "proposed_assignment": {
                    "target_agent": args.target_agent,
                    "target_branch": args.target_branch,
                },
                "audit_metadata": {
                    "profile": profile["profile"],
                    "files": args.file,
                    "dimensions": args.dimension,
                    "thinking_lenses": args.lens,
                    "evidence": args.evidence,
                    "code_changes_allowed": False,
                },
            },
        )
        if args.json:
            print(json.dumps(payload, ensure_ascii=True, indent=2))
        else:
            print(f"{payload['agent'] if 'agent' in payload else args.agent}: finding {payload['id']}")
        return 0

    payload = write_state(
        repo_root,
        args.agent,
        status=args.status,
        summary=args.summary,
        finding_ref=args.finding_ref,
        note=args.note,
        files_read=args.file_read,
        dimensions_covered=args.dimension_covered,
        lines_read=args.lines_read,
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=True, indent=2))
    else:
        print(f"{payload['agent']}: {payload['status']} {payload['finding_ref']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
