"""Config/init commands for cli-migrate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
import yaml  # type: ignore[import-untyped]


def validate_migrate_config_command(*, config: str = ".owlclaw-migrate.yaml") -> None:
    config_path = Path(config).resolve()
    if not config_path.exists():
        typer.echo(f"config not found: {config_path}", err=True)
        raise typer.Exit(2)

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        typer.echo("invalid config: expected object", err=True)
        raise typer.Exit(2)

    allowed = {"project", "openapi", "orm", "output", "output_mode", "include", "exclude"}
    unknown = [key for key in payload if key not in allowed]
    if unknown:
        typer.echo(f"invalid config keys: {', '.join(sorted(str(x) for x in unknown))}", err=True)
        raise typer.Exit(2)

    mode = str(payload.get("output_mode", "handler")).strip().lower()
    if mode not in {"handler", "binding", "both", "mcp"}:
        typer.echo("invalid output_mode, expected handler|binding|both|mcp", err=True)
        raise typer.Exit(2)

    has_input = any(str(payload.get(name, "")).strip() for name in ("project", "openapi", "orm"))
    if not has_input:
        typer.echo("config must define at least one input: project/openapi/orm", err=True)
        raise typer.Exit(2)

    typer.echo(f"OK: {config_path}")


def init_migrate_config_command(
    *,
    path: str = ".",
    force: bool = False,
    project: str = "",
    output: str = "",
    output_mode: str = "handler",
    interactive: bool = True,
) -> None:
    base_dir = Path(path).resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    config_path = base_dir / ".owlclaw-migrate.yaml"
    if config_path.exists() and not force:
        typer.echo(f"config already exists: {config_path}", err=True)
        raise typer.Exit(2)

    cfg_project = project.strip() or "."
    cfg_output = output.strip() or "./migrate-output"
    cfg_mode = output_mode.strip().lower() or "handler"

    if interactive:
        entered_project = input(f"project path [{cfg_project}]: ").strip()
        entered_output = input(f"output dir [{cfg_output}]: ").strip()
        entered_mode = input(f"output_mode handler|binding|both|mcp [{cfg_mode}]: ").strip().lower()
        if entered_project:
            cfg_project = entered_project
        if entered_output:
            cfg_output = entered_output
        if entered_mode:
            cfg_mode = entered_mode

    if cfg_mode not in {"handler", "binding", "both", "mcp"}:
        typer.echo("invalid output_mode, expected handler|binding|both|mcp", err=True)
        raise typer.Exit(2)

    payload: dict[str, Any] = {
        "project": cfg_project,
        "output": cfg_output,
        "output_mode": cfg_mode,
        "include": ["**/*.py"],
        "exclude": [".venv/**", "tests/**"],
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    typer.echo(f"created: {config_path}")
