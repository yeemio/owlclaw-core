"""Release-gate CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from owlclaw.owlhub.release_gate import run_release_gate


def release_gate_owlhub_command(
    *,
    api_base_url: str,
    index_url: str,
    query: str,
    work_dir: str,
    output: str,
) -> None:
    """Run OwlHub release gate checks and emit JSON report."""
    report = run_release_gate(
        api_base_url=api_base_url,
        index_url=index_url,
        query=query,
        work_dir=Path(work_dir),
    )
    payload = json.dumps(report.as_dict(), ensure_ascii=False, indent=2)
    typer.echo(payload)
    if output:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
    if not report.passed:
        raise typer.Exit(code=1)
