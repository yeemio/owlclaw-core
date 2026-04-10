"""Execute contract-testing drill and generate evidence artifacts."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run_drill(repo: Path) -> int:
    reports = repo / "docs" / "protocol" / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    before = repo / "tests" / "contracts" / "api" / "openapi_before.json"
    additive = repo / "tests" / "contracts" / "api" / "openapi_after_additive.json"
    breaking = repo / "tests" / "contracts" / "api" / "openapi_after_breaking.json"

    additive_report = reports / "contract-testing-additive-report.json"
    breaking_report = reports / "contract-testing-breaking-report.json"
    markdown = reports / "contract-testing-drill-latest.md"

    additive_result = _run_gate(repo, before, additive, additive_report)
    breaking_result = _run_gate(repo, before, breaking, breaking_report)

    additive_ok = additive_result.returncode == 0 and '"gate_decision": "pass"' in additive_result.stdout
    breaking_ok = breaking_result.returncode == 2 and '"gate_decision": "block"' in breaking_result.stdout

    markdown.write_text(
        "\n".join(
            [
                "# Contract Testing Drill Report",
                "",
                f"- additive_pass=true: {str(additive_ok).lower()}",
                f"- breaking_blocked=true: {str(breaking_ok).lower()}",
                "",
                "## Artifacts",
                f"- {additive_report.relative_to(repo)}",
                f"- {breaking_report.relative_to(repo)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return 0 if additive_ok and breaking_ok else 2


def _run_gate(repo: Path, before: Path, after: Path, output: Path) -> subprocess.CompletedProcess[str]:
    wrapper = repo / "scripts" / "contract_diff" / "run_contract_diff.py"
    return subprocess.run(
        [
            sys.executable,
            str(wrapper),
            "--before",
            str(before),
            "--after",
            str(after),
            "--mode",
            "blocking",
            "--output",
            str(output),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    raise SystemExit(run_drill(repo))


if __name__ == "__main__":
    main()

