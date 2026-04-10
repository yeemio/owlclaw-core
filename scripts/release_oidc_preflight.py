"""Release OIDC preflight checks and blocker diagnostics."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml  # type: ignore[import-untyped]


EXPECTED_CONTEXTS = {"Lint", "Test", "Build"}


@dataclass
class CheckResult:
    workflow_ok: bool
    main_branch_protection_ok: bool
    release_ruleset_ok: bool
    trusted_publisher_blocked: bool
    details: list[str]

    @property
    def ready(self) -> bool:
        return (
            self.workflow_ok
            and self.main_branch_protection_ok
            and self.release_ruleset_ok
            and not self.trusted_publisher_blocked
        )


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]

    workflow_data = load_workflow(repo_root / args.workflow)
    branch_protection, branch_warning = load_json_input(
        args.branch_protection_json,
        ["api", f"repos/{args.repo}/branches/main/protection"],
        default={},
    )
    rulesets, ruleset_warning = load_json_input(
        args.rulesets_json,
        ["api", f"repos/{args.repo}/rulesets"],
        default=[],
    )

    warnings: list[str] = []
    if branch_warning:
        warnings.append(branch_warning)
    if ruleset_warning:
        warnings.append(ruleset_warning)

    run_log = ""
    if args.run_log:
        run_log = Path(args.run_log).read_text(encoding="utf-8")
    elif args.run_id:
        try:
            run_log = run_gh(["run", "view", str(args.run_id), "--repo", args.repo, "--log"])
        except RuntimeError as exc:
            warnings.append(f"failed to read run log from gh: {exc}")

    result = evaluate(workflow_data, branch_protection, rulesets, run_log)
    if warnings:
        result.details.extend(warnings)
    report_path = repo_root / args.output
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(args, result), encoding="utf-8")
    print(f"report={report_path}")

    if result.ready:
        raise SystemExit(0)
    if result.trusted_publisher_blocked:
        raise SystemExit(3)
    raise SystemExit(2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Release OIDC preflight checks.")
    parser.add_argument("--repo", default="yeemio/owlclaw", help="GitHub repository in owner/name format")
    parser.add_argument("--workflow", default=".github/workflows/release.yml", help="Path to release workflow file")
    parser.add_argument("--output", default="docs/release/reports/release-oidc-preflight-latest.md")
    parser.add_argument("--run-id", type=int, default=0, help="Optional GitHub Actions run id")
    parser.add_argument("--branch-protection-json", default="", help="Use local JSON instead of gh api")
    parser.add_argument("--rulesets-json", default="", help="Use local JSON instead of gh api")
    parser.add_argument("--run-log", default="", help="Use local log file instead of gh run view")
    return parser.parse_args()


def evaluate(
    workflow_data: dict[str, object],
    branch_protection: dict[str, object],
    rulesets: list[dict[str, object]],
    run_log: str,
) -> CheckResult:
    details: list[str] = []

    workflow_ok = has_oidc_publish_workflow(workflow_data)
    if not workflow_ok:
        details.append("release workflow missing required OIDC publish baseline")

    main_ok = has_expected_main_branch_protection(branch_protection)
    if not main_ok:
        details.append("main branch protection does not enforce strict Lint/Test/Build checks")

    release_ok = has_release_branch_ruleset(rulesets)
    if not release_ok:
        details.append("release/* ruleset not found or inactive")

    blocked = is_trusted_publisher_blocked(run_log)
    if blocked:
        details.append("latest release run indicates TestPyPI 403 Forbidden (Trusted Publisher binding missing)")

    return CheckResult(
        workflow_ok=workflow_ok,
        main_branch_protection_ok=main_ok,
        release_ruleset_ok=release_ok,
        trusted_publisher_blocked=blocked,
        details=details,
    )


def has_oidc_publish_workflow(workflow_data: dict[str, object]) -> bool:
    jobs = workflow_data.get("jobs")
    if not isinstance(jobs, dict):
        return False
    release_job = jobs.get("release")
    if not isinstance(release_job, dict):
        return False

    permissions = release_job.get("permissions")
    if not isinstance(permissions, dict):
        return False
    if permissions.get("id-token") != "write":
        return False

    steps = release_job.get("steps")
    if not isinstance(steps, list):
        return False

    has_publish_action = False
    has_testpypi_url = False
    for step in steps:
        if not isinstance(step, dict):
            continue
        uses = step.get("uses")
        if isinstance(uses, str) and "pypa/gh-action-pypi-publish" in uses:
            has_publish_action = True
        with_payload = step.get("with")
        if isinstance(with_payload, dict):
            repo_url = with_payload.get("repository-url")
            if repo_url == "https://test.pypi.org/legacy/":
                has_testpypi_url = True

    return has_publish_action and has_testpypi_url


def has_expected_main_branch_protection(branch_protection: dict[str, object]) -> bool:
    status_checks = branch_protection.get("required_status_checks")
    if not isinstance(status_checks, dict):
        return False
    if status_checks.get("strict") is not True:
        return False
    contexts = status_checks.get("contexts")
    if not isinstance(contexts, list):
        return False
    return EXPECTED_CONTEXTS.issubset({str(item) for item in contexts})


def has_release_branch_ruleset(rulesets: list[dict[str, object]]) -> bool:
    for ruleset in rulesets:
        if ruleset.get("target") != "branch":
            continue
        if ruleset.get("enforcement") != "active":
            continue
        name = str(ruleset.get("name", "")).lower()
        if "release" in name:
            return True
    return False


def is_trusted_publisher_blocked(run_log: str) -> bool:
    if not run_log:
        return False
    return (
        "Publish to TestPyPI" in run_log
        and "403 Forbidden" in run_log
        and "https://test.pypi.org/legacy/" in run_log
    )


def load_workflow(path: Path) -> dict[str, object]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid workflow yaml")
    return payload


def load_json_input(path: str, gh_cmd: list[str], default: object) -> tuple[object, str]:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8")), ""
    try:
        return json.loads(run_gh(gh_cmd)), ""
    except RuntimeError as exc:
        return default, f"failed to fetch {' '.join(gh_cmd)}: {exc}"


def run_gh(args: list[str]) -> str:
    completed = subprocess.run(
        ["gh", *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "gh command failed")
    return completed.stdout


def render_report(args: argparse.Namespace, result: CheckResult) -> str:
    status = "READY" if result.ready else "BLOCKED"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    details = "\n".join(f"- {item}" for item in result.details) or "- no issues detected"
    return f"""# Release OIDC Preflight Report

> generated_at: {now}
> repo: {args.repo}
> run_id: {args.run_id or "n/a"}
> status: {status}

## Checks

- workflow_oidc_publish: {"PASS" if result.workflow_ok else "FAIL"}
- main_branch_protection: {"PASS" if result.main_branch_protection_ok else "FAIL"}
- release_ruleset: {"PASS" if result.release_ruleset_ok else "FAIL"}
- trusted_publisher_blocker: {"DETECTED" if result.trusted_publisher_blocked else "NOT_DETECTED"}

## Findings

{details}

## Manual Trusted Publisher Checklist

1. TestPyPI project -> Publishing -> Trusted Publishers -> Add.
2. PyPI project -> Publishing -> Trusted Publishers -> Add.
3. Repository: `{args.repo}`.
4. Workflow filename: `.github/workflows/release.yml`.
5. Environment name: leave empty unless workflow explicitly uses one.
6. Re-run `gh workflow run release.yml -f target=testpypi`.
"""


if __name__ == "__main__":
    main()
