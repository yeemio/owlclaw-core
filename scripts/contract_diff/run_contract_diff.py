"""Wrapper to execute the root contract diff gate script."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts" / "contract_diff.py"
    completed = subprocess.run(
        [sys.executable, str(script), *sys.argv[1:]],
        cwd=repo,
        check=False,
    )
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()

