"""Workflow status monitor for the OwlClaw multi-worktree process."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


AUDIT_SPEC_NAME = "audit-deep-remediation"
OPERATIONAL_DIRTY_IGNORE_PATTERNS = (
    ".kiro/reviews/",
    ".kiro/specs/SPEC_TASKS_SCAN.md",
    "docs/review/REVIEW_VERDICT_",
    "docs/review/REVIEW_LOOP_",
)


@dataclass(frozen=True)
class WorktreeConfig:
    name: str
    branch: str
    path: Path
    role: str


@dataclass(frozen=True)
class WorktreeState:
    name: str
    branch: str
    path: str
    role: str
    clean: bool
    dirty_files: list[str]
    ahead_of_main: int
    ahead_of_remote: int
    pending_commits: list[str]


@dataclass(frozen=True)
class AuditSummary:
    total_findings: int | None
    p1: int | None
    low: int | None
    spec_progress: str | None
    spec_status: str | None
    spec_summary: str | None


@dataclass(frozen=True)
class WorkflowSnapshot:
    repo_root: str
    audit: AuditSummary
    worktrees: list[WorktreeState]
    next_action: str
    blockers: list[str]


def _run_git(args: list[str], workdir: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=workdir,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _default_worktrees(repo_root: Path) -> list[WorktreeConfig]:
    parent = repo_root.parent
    return [
        WorktreeConfig("main", "main", repo_root, "orchestrator"),
        WorktreeConfig("review", "review-work", parent / "owlclaw-review", "review"),
        WorktreeConfig("codex", "codex-work", parent / "owlclaw-codex", "coding"),
        WorktreeConfig("codex-gpt", "codex-gpt-work", parent / "owlclaw-codex-gpt", "coding"),
    ]


def _normalize_status_path(line: str) -> str:
    parts = line.split(maxsplit=1)
    if len(parts) == 2:
        return parts[1].strip()
    return line.strip()


def _is_ignored_operational_dirty(line: str) -> bool:
    path = _normalize_status_path(line)
    normalized = path.replace("\\", "/")
    return any(pattern in normalized for pattern in OPERATIONAL_DIRTY_IGNORE_PATTERNS)


def _parse_status_output(output: str) -> tuple[bool, list[str]]:
    lines = output.splitlines()
    dirty: list[str] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        if _is_ignored_operational_dirty(line):
            continue
        dirty.append(line.strip())
    return len(dirty) == 0, dirty


def _ahead_of_remote(status_output: str) -> int:
    match = re.search(r"\[ahead (\d+)\]", status_output)
    if match:
        return int(match.group(1))
    return 0


def _branch_state(repo_root: Path, cfg: WorktreeConfig) -> WorktreeState:
    status_output = _run_git(["status", "--short", "--branch"], cfg.path)
    clean, dirty_files = _parse_status_output(status_output)
    ahead_of_main = int(_run_git(["rev-list", "--count", f"main..{cfg.branch}"], repo_root))
    commit_output = _run_git(["log", "--oneline", f"main..{cfg.branch}"], repo_root)
    pending_commits = [line for line in commit_output.splitlines() if line.strip()]
    return WorktreeState(
        name=cfg.name,
        branch=cfg.branch,
        path=str(cfg.path),
        role=cfg.role,
        clean=clean,
        dirty_files=dirty_files,
        ahead_of_main=ahead_of_main,
        ahead_of_remote=_ahead_of_remote(status_output),
        pending_commits=pending_commits,
    )


def _parse_audit_report(report_path: Path) -> AuditSummary:
    text = report_path.read_text(encoding="utf-8")
    total_match = re.search(r"\*\*Total Findings\*\*:\s*(\d+)", text)
    p1_match = re.search(r"- P1/Medium:\s*(\d+)", text)
    low_match = re.search(r"- Low:\s*(\d+)", text)
    return AuditSummary(
        total_findings=int(total_match.group(1)) if total_match else None,
        p1=int(p1_match.group(1)) if p1_match else None,
        low=int(low_match.group(1)) if low_match else None,
        spec_progress=None,
        spec_status=None,
        spec_summary=None,
    )


def _merge_audit_progress(audit: AuditSummary, spec_scan_path: Path) -> AuditSummary:
    text = spec_scan_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"\|\s+\*\*audit-deep-remediation\*\*\s+\|\s+`[^`]+`\s+\|\s+(.*?)\s+\|\s+(.*?)\s*\|"
    )
    match = pattern.search(text)
    if not match:
        return audit
    status = match.group(1).strip()
    summary = match.group(2).strip()
    progress_match = re.search(r"（(\d+/\d+)）", status)
    return AuditSummary(
        total_findings=audit.total_findings,
        p1=audit.p1,
        low=audit.low,
        spec_progress=progress_match.group(1) if progress_match else None,
        spec_status=status,
        spec_summary=summary,
    )


def _decide_next_action(worktrees: list[WorktreeState]) -> tuple[str, list[str]]:
    blockers: list[str] = []
    state_by_name = {state.name: state for state in worktrees}
    main = state_by_name["main"]
    review = state_by_name["review"]
    coding = [state for state in worktrees if state.role == "coding"]

    if not main.clean:
        blockers.append("main worktree has uncommitted changes")
    dirty_coding = [state.name for state in coding if not state.clean]
    if dirty_coding:
        blockers.append(f"coding worktrees dirty: {', '.join(dirty_coding)}")

    if review.ahead_of_main > 0:
        return "merge review-work into main", blockers

    pending_coding = [state for state in coding if state.ahead_of_main > 0]
    if pending_coding:
        names = ", ".join(state.branch for state in pending_coding)
        return f"review-work should review pending coding branches: {names}", blockers

    if blockers:
        return "clean dirty worktrees before next orchestration step", blockers

    return "workflow stable: no pending review or merge action", blockers


def build_snapshot(repo_root: Path) -> WorkflowSnapshot:
    repo_root = repo_root.resolve()
    worktrees = [_branch_state(repo_root, cfg) for cfg in _default_worktrees(repo_root)]
    audit = _parse_audit_report(repo_root / "docs" / "review" / "DEEP_AUDIT_REPORT.md")
    audit = _merge_audit_progress(audit, repo_root / ".kiro" / "specs" / "SPEC_TASKS_SCAN.md")
    next_action, blockers = _decide_next_action(worktrees)
    return WorkflowSnapshot(
        repo_root=str(repo_root),
        audit=audit,
        worktrees=worktrees,
        next_action=next_action,
        blockers=blockers,
    )


def _format_text(snapshot: WorkflowSnapshot) -> str:
    lines = [
        f"Repo: {snapshot.repo_root}",
        (
            "Audit: "
            f"findings={snapshot.audit.total_findings or '?'} "
            f"p1={snapshot.audit.p1 or '?'} "
            f"low={snapshot.audit.low or '?'} "
            f"progress={snapshot.audit.spec_progress or '?'}"
        ),
        f"Next action: {snapshot.next_action}",
    ]
    if snapshot.blockers:
        lines.append(f"Blockers: {', '.join(snapshot.blockers)}")
    lines.append("")
    lines.append("Worktrees:")
    for state in snapshot.worktrees:
        dirty = "clean" if state.clean else f"dirty({len(state.dirty_files)})"
        lines.append(
            f"- {state.name:<10} branch={state.branch:<14} {dirty:<10} "
            f"ahead_main={state.ahead_of_main:<2} ahead_remote={state.ahead_of_remote}"
        )
        if state.pending_commits:
            lines.append(f"  pending: {state.pending_commits[0]}")
        if state.dirty_files:
            lines.append(f"  dirty_files: {', '.join(state.dirty_files[:3])}")
    if snapshot.audit.spec_summary:
        lines.append("")
        lines.append(f"{AUDIT_SPEC_NAME}: {snapshot.audit.spec_summary}")
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect the OwlClaw multi-worktree workflow state.")
    parser.add_argument("--repo-root", default=".", help="Path to the main repository root.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument(
        "--fail-on-blockers",
        action="store_true",
        help="Exit with code 1 when blockers are detected.",
    )
    parser.add_argument(
        "--fail-on-pending-review",
        action="store_true",
        help="Exit with code 1 when coding branches are ahead of main.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    snapshot = build_snapshot(Path(args.repo_root))

    if args.json:
        print(json.dumps(asdict(snapshot), ensure_ascii=True, indent=2))
    else:
        print(_format_text(snapshot))

    if args.fail_on_blockers and snapshot.blockers:
        return 1
    if args.fail_on_pending_review and any(
        state.role == "coding" and state.ahead_of_main > 0 for state in snapshot.worktrees
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
