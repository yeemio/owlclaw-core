"""owlclaw db revision — create new Alembic migration (autogenerate or empty)."""

import os
from pathlib import Path

import typer
from alembic import command
from alembic.config import Config
from typer.models import OptionInfo


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


def _script_versions_dir(cfg: Config) -> Path:
    """Return Path to migrations/versions directory."""
    script_location = cfg.get_main_option("script_location", "migrations")
    return Path(script_location) / "versions"


def _find_newest_revision_file(cfg: Config, revision_id: str | None) -> Path | None:
    """Find the revision file that matches revision_id or the newest by mtime."""
    versions_dir = _script_versions_dir(cfg)
    if not versions_dir.exists():
        return None
    candidates: list[tuple[float, Path]] = []
    for p in versions_dir.glob("*.py"):
        if p.name.startswith("__"):
            continue
        if revision_id:
            if revision_id in p.name:
                return p
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                content = ""
            if f'"{revision_id}"' in content or f"'{revision_id}'" in content:
                return p
        try:
            mtime = p.stat().st_mtime
            candidates.append((mtime, p))
        except OSError:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _check_dangerous_operations(path: Path) -> None:
    """Warn if migration script contains DROP TABLE or DROP COLUMN."""
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    dangerous: list[str] = []
    if "drop_table" in content or "op.drop_table" in content:
        dangerous.append("DROP TABLE")
    if "drop_column" in content or "op.drop_column" in content:
        dangerous.append("DROP COLUMN")
    if dangerous:
        typer.echo(
            typer.style(
                f"Warning: migration contains dangerous operations: {', '.join(dangerous)}. Review before applying.",
                fg="yellow",
            ),
            err=True,
        )


def revision_command(
    message: str = typer.Argument(
        "empty",
        help="Revision message (required for autogenerate; default 'empty' with --empty).",
    ),
    empty_template: bool = typer.Option(
        False,
        "--empty",
        help="Create an empty migration template (no autogenerate).",
    ),
    database_url: str = typer.Option(
        "",
        "--database-url",
        help="Database URL (default: OWLCLAW_DATABASE_URL).",
    ),
) -> None:
    """Create a new migration script (autogenerate from models or empty template)."""
    message = (_normalize_optional_str_option(message) or "").strip()
    empty = _normalize_bool_option(empty_template, False)
    database_url_opt = _normalize_optional_str_option(database_url)
    if not message.strip():
        message = "empty" if empty else ""
    if not empty and not message.strip():
        typer.echo("Error: --message / -m is required when not using --empty.", err=True)
        raise typer.Exit(2)
    url = (database_url_opt or os.environ.get("OWLCLAW_DATABASE_URL") or "").strip()
    # Require URL only for autogenerate (not for --empty)
    if not empty and not url:
        typer.echo("Error: OWLCLAW_DATABASE_URL or --database-url required for autogenerate.", err=True)
        raise typer.Exit(2)
    if database_url_opt:
        os.environ["OWLCLAW_DATABASE_URL"] = url
    cfg = Config("alembic.ini")
    try:
        script = command.revision(cfg, message=message or "empty", autogenerate=not empty)
    except Exception as e:
        err_msg = str(e).lower()
        if "no changes detected" in err_msg or "target database is not up to date" in err_msg:
            typer.echo("No changes detected. Schema is in sync with models.")
            return
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from e
    if script is None:
        typer.echo("No changes detected. No migration file created.")
        return
    # command.revision can return a single Script or a list
    scripts = script if isinstance(script, list) else [script]
    if not scripts:
        typer.echo("No changes detected. No migration file created.")
        return
    script = scripts[0]
    revision_id = getattr(script, "revision", None)
    if isinstance(revision_id, list | tuple):
        revision_id = revision_id[0] if revision_id else None
    rev_path = getattr(script, "path", None)
    if rev_path is None and revision_id:
        rev_path = _find_newest_revision_file(cfg, revision_id)
    if rev_path is None:
        rev_path = _find_newest_revision_file(cfg, None)
    if rev_path is not None:
        path_str = str(Path(rev_path).resolve())
        typer.echo(f"Created migration: {path_str}")
        if revision_id:
            typer.echo(f"Revision ID: {revision_id}")
        _check_dangerous_operations(Path(rev_path))
    else:
        typer.echo("Migration script created (path not resolved).")
