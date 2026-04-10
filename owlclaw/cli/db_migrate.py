"""owlclaw db migrate — run Alembic upgrade."""

import os
import sys

import typer
from alembic import command
from alembic.config import Config
from typer.models import OptionInfo


def _normalize_str_option(value: object, default: str) -> str:
    if isinstance(value, OptionInfo):
        return default
    if not isinstance(value, str):
        return default
    return value


def _normalize_optional_str_option(value: object) -> str | None:
    if isinstance(value, OptionInfo):
        return None
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    return value


def _normalize_bool_option(value: object, default: bool) -> bool:
    if isinstance(value, OptionInfo):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return default


def migrate_command(
    target: str = typer.Option(
        "head",
        "--target",
        "-t",
        help="Revision to upgrade to (default: head).",
    ),
    database_url: str = typer.Option(
        "",
        "--database-url",
        help="Database URL (default: OWLCLAW_DATABASE_URL).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show pending migrations without applying.",
    ),
) -> None:
    """Run schema migrations (Alembic upgrade)."""
    normalized_target = _normalize_str_option(target, "head").strip()
    database_url_opt = _normalize_optional_str_option(database_url)
    normalized_dry_run = _normalize_bool_option(dry_run, False)
    if not normalized_target:
        typer.echo("Error: --target must be a non-empty revision string.", err=True)
        raise typer.Exit(2)
    url = database_url_opt or os.environ.get("OWLCLAW_DATABASE_URL")
    if not url or not url.strip():
        typer.echo("Error: Set OWLCLAW_DATABASE_URL or pass --database-url.", err=True)
        raise typer.Exit(2)
    url = url.strip()
    if database_url_opt:
        os.environ["OWLCLAW_DATABASE_URL"] = url
    alembic_cfg = Config("alembic.ini")
    do_dry_run = bool(normalized_dry_run) or "--dry-run" in sys.argv
    if do_dry_run:
        command.current(alembic_cfg)
        command.heads(alembic_cfg)
        typer.echo("--dry-run: run without --dry-run to apply migrations.")
        return
    command.upgrade(alembic_cfg, normalized_target)
    typer.echo("Migrations applied.")
