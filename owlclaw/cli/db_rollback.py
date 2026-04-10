"""owlclaw db rollback — downgrade Alembic migrations (one step, --steps N, or --target)."""

import os
from contextlib import suppress

import typer
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncEngine
from typer.models import OptionInfo

from owlclaw.db import ConfigurationError, get_engine


def _get_current_revision_sync(engine: AsyncEngine) -> str | None:
    """Get current Alembic revision from DB (run in async context)."""
    with engine.sync_engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        return ctx.get_current_revision()


def _normalize_optional_str_option(value: object) -> str:
    if isinstance(value, OptionInfo) or value is None:
        return ""
    if not isinstance(value, str):
        return ""
    return value


def _normalize_int_option(value: object, default: int = 0) -> int:
    if isinstance(value, OptionInfo):
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip() or str(default))
        except ValueError:
            return default
    return default


def _normalize_bool_option(value: object, default: bool = False) -> bool:
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


def rollback_command(
    target: str = typer.Option(
        "",
        "--target",
        "-t",
        help="Revision to downgrade to (e.g. base or revision id).",
    ),
    steps: int = typer.Option(
        0,
        "--steps",
        "-s",
        help="Number of revisions to downgrade (e.g. 1 = one step back).",
    ),
    database_url: str = typer.Option(
        "",
        "--database-url",
        help="Database URL (default: OWLCLAW_DATABASE_URL).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be rolled back without executing.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompt.",
    ),
) -> None:
    """Roll back database migrations (Alembic downgrade)."""
    target = _normalize_optional_str_option(target).strip()
    steps = _normalize_int_option(steps, 0)
    database_url = _normalize_optional_str_option(database_url).strip() or os.environ.get("OWLCLAW_DATABASE_URL") or ""
    dry_run = _normalize_bool_option(dry_run, False)
    yes = _normalize_bool_option(yes, False)
    if not database_url.strip():
        typer.echo("Error: Set OWLCLAW_DATABASE_URL or pass --database-url.", err=True)
        raise typer.Exit(2)
    database_url = database_url.strip()
    if database_url:
        os.environ["OWLCLAW_DATABASE_URL"] = database_url

    if target and steps != 0:
        typer.echo("Error: Use either --target or --steps, not both.", err=True)
        raise typer.Exit(2)

    cfg = Config("alembic.ini")
    script = ScriptDirectory.from_config(cfg)

    try:
        engine = get_engine(database_url)
    except ConfigurationError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(2) from e

    try:
        current_rev = _get_current_revision_sync(engine)
    except Exception as e:
        typer.echo(f"Error: failed to read current migration revision: {e}", err=True)
        raise typer.Exit(1) from e
    finally:
        with suppress(Exception):
            engine.sync_engine.dispose()

    if not current_rev or current_rev.lower() == "base":
        typer.echo("Already at base revision.")
        return

    if steps and steps < 1:
        typer.echo("Error: --steps must be >= 1.", err=True)
        raise typer.Exit(2)

    if target and target == current_rev:
        typer.echo("Already at target revision.")
        return

    if target and target.lower() == "base":
        downgrade_target = "base"
        to_roll = list(script.iterate_revisions(current_rev, "base"))
    elif steps:
        rev_list = list(script.iterate_revisions(current_rev, "base"))
        if not rev_list:
            typer.echo("Already at base revision.")
            return
        n = min(steps, len(rev_list))
        if n < steps:
            typer.echo(
                typer.style(
                    f"Warning: only {n} revision(s) to roll back (requested --steps {steps}).",
                    fg="yellow",
                ),
                err=True,
            )
        downgrade_target = f"-{n}"
        to_roll = rev_list[:n]
    else:
        if target:
            downgrade_target = target
            try:
                to_roll = list(script.iterate_revisions(current_rev, target))
            except Exception as e:
                typer.echo(f"Error: invalid rollback target '{target}': {e}", err=True)
                raise typer.Exit(2) from e
        else:
            downgrade_target = "-1"
            rev_list = list(script.iterate_revisions(current_rev, "base"))
            if not rev_list:
                typer.echo("Already at base revision.")
                return
            to_roll = rev_list[:1]

    if not to_roll:
        if target:
            typer.echo(f"Error: target revision '{target}' is not behind current revision '{current_rev}'.", err=True)
            raise typer.Exit(2)
        typer.echo("Already at base revision.")
        return

    rev_ids = [r.revision for r in to_roll]
    typer.echo("Revisions to roll back:")
    for rev in to_roll:
        doc = getattr(rev, "doc", None) or ""
        typer.echo(f"  - {rev.revision[:12]}{' ' + doc if doc else ''}")

    if dry_run:
        typer.echo("--dry-run: run without --dry-run to apply rollback.")
        return

    typer.echo(f"Will roll back {len(rev_ids)} revision(s).")
    if not yes:
        try:
            confirm = input("Continue? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            typer.echo("\nAborted.")
            raise typer.Exit(130) from None
        if confirm not in ("y", "yes"):
            typer.echo("Aborted.")
            return

    try:
        command.downgrade(cfg, downgrade_target)
    except Exception as e:
        typer.echo(f"Rollback failed: {e}", err=True)
        raise typer.Exit(1) from e

    typer.echo(f"Rolled back: {', '.join(rev_ids)}.")
