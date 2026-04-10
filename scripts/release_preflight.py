"""Local preflight checks for release readiness."""

from __future__ import annotations

import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


def run_preflight(repo: Path) -> int:
    required = [
        repo / "CHANGELOG.md",
        repo / "CONTRIBUTING.md",
        repo / ".github" / "workflows" / "release.yml",
        repo / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml",
        repo / ".github" / "ISSUE_TEMPLATE" / "feature_request.yml",
        repo / "docs" / "release" / "SECURITY_SCAN_REPORT.md",
        repo / "docs" / "release" / "CREDENTIAL_AUDIT.md",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        print("missing assets:")
        for path in missing:
            print(f"- {path}")
        return 2

    version = _run_cli(["--version"])
    if version[0] != 0:
        print("version check failed")
        print(version[1])
        return 2
    print(f"owlclaw_version={version[1].strip()}")

    skills = _run_cli(["skill", "list", "--path", str(repo / "examples" / "capabilities")])
    if skills[0] != 0:
        print("skill list check failed")
        print(skills[1])
        return 2
    print("skill_list_ok=true")
    return 0


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    raise SystemExit(run_preflight(repo))


def _run_cli(args: list[str]) -> tuple[int, str]:
    from owlclaw.cli import main as cli_main

    original_argv = list(sys.argv)
    buf = StringIO()
    code = 0
    try:
        sys.argv = ["owlclaw", *args]
        with redirect_stdout(buf):
            try:
                cli_main()
            except SystemExit as exc:
                code = int(exc.code or 0)
    finally:
        sys.argv = original_argv
    return code, buf.getvalue().strip()


if __name__ == "__main__":
    main()
