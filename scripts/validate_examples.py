"""Smoke validator for key examples.

Usage:
  poetry run python scripts/validate_examples.py
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path


def _run_mionyee(repo: Path) -> dict[str, object]:
    script = repo / "examples" / "mionyee-trading" / "app.py"
    result = subprocess.run(
        [sys.executable, str(script), "--all", "--json"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"mionyee-trading failed: {result.stderr.strip()}")
    payload = json.loads(result.stdout)
    rows = payload.get("results", [])
    if len(rows) != 3:
        raise RuntimeError("mionyee-trading returned unexpected result count")
    if any(item.get("status") != "passed" for item in rows):
        raise RuntimeError("mionyee-trading contains non-passed task")
    return {"ok": True, "tasks": 3}


def _run_cron(repo: Path) -> dict[str, object]:
    script = repo / "examples" / "cron" / "nightly_data_cleanup.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cron example failed: {result.stderr.strip()}")
    return {"ok": True}


def _run_langchain(repo: Path) -> dict[str, object]:
    try:
        from examples.langchain.basic_runnable_registration import build_app as build_basic
        from examples.langchain.decorator_registration import build_app as build_decorator
        from examples.langchain.fallback_and_retry import build_app as build_fallback

        apps = [build_basic(), build_decorator(), build_fallback()]
        names = [app.name for app in apps]
        if not all(names):
            raise RuntimeError("langchain example app registration returned empty app name")
        return {"ok": True, "mode": "runtime", "apps": names}
    except ImportError:
        base = repo / "examples" / "langchain"
        files = sorted(base.glob("*.py"))
        for file_path in files:
            ast.parse(file_path.read_text(encoding="utf-8"))
        return {"ok": True, "mode": "ast", "files": len(files)}


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    summary = {
        "cron": _run_cron(repo),
        "langchain": _run_langchain(repo),
        "mionyee": _run_mionyee(repo),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
