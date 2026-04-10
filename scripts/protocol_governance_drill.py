"""Run protocol governance drills and generate evidence artifacts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def run_drill(repo: Path) -> int:
    report_dir = repo / "docs" / "protocol" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    before = report_dir / "contract-before.json"
    additive_after = report_dir / "contract-additive.json"
    breaking_after = report_dir / "contract-breaking.json"
    blocked_report = report_dir / "breaking-blocked-report.json"
    exempt_report = report_dir / "breaking-exempted-report.json"
    audit_log = report_dir / "governance-gate-audit.jsonl"
    markdown_report = report_dir / "governance-drill-latest.md"

    before.write_text(json.dumps({"paths": {"GET /v1/ping": {"summary": "ping"}}}), encoding="utf-8")
    additive_after.write_text(
        json.dumps({"paths": {"GET /v1/ping": {"summary": "ping"}, "GET /v1/status": {"summary": "status"}}}),
        encoding="utf-8",
    )
    breaking_after.write_text(json.dumps({"paths": {}}), encoding="utf-8")

    additive = _run_contract_diff(repo, before, additive_after, "warning")
    blocked = _run_contract_diff(
        repo,
        before,
        breaking_after,
        "blocking",
        output=blocked_report,
    )
    exempted = _run_contract_diff(
        repo,
        before,
        breaking_after,
        "blocking",
        exemption_ticket="EX-2026-DRILL",
        audit_log=audit_log,
        output=exempt_report,
    )

    additive_ok = additive.returncode == 0 and "\"change_level\": \"additive\"" in additive.stdout
    blocked_ok = blocked.returncode == 2 and "\"gate_decision\": \"block\"" in blocked.stdout
    exempt_ok = exempted.returncode == 0 and "\"gate_decision\": \"warn\"" in exempted.stdout and audit_log.exists()

    markdown_report.write_text(
        "\n".join(
            [
                "# Protocol Governance Drill Report",
                "",
                f"- additive_pass=true: {str(additive_ok).lower()}",
                f"- breaking_blocked=true: {str(blocked_ok).lower()}",
                f"- exemption_audited=true: {str(exempt_ok).lower()}",
                "",
                "## Artifacts",
                f"- {blocked_report.relative_to(repo)}",
                f"- {exempt_report.relative_to(repo)}",
                f"- {audit_log.relative_to(repo)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return 0 if additive_ok and blocked_ok and exempt_ok else 2


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    raise SystemExit(run_drill(repo))


def _run_contract_diff(
    repo: Path,
    before: Path,
    after: Path,
    mode: str,
    exemption_ticket: str | None = None,
    audit_log: Path | None = None,
    output: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        str(repo / "scripts" / "contract_diff.py"),
        "--before",
        str(before),
        "--after",
        str(after),
        "--mode",
        mode,
    ]
    if exemption_ticket:
        cmd.extend(["--exemption-ticket", exemption_ticket])
    if audit_log:
        cmd.extend(["--audit-log", str(audit_log)])
    if output:
        cmd.extend(["--output", str(output)])
    return subprocess.run(
        cmd,
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


if __name__ == "__main__":
    main()

