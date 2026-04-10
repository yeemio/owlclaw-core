"""owlclaw db restore — restore database from pg_dump backup (SQL or custom)."""

import asyncio
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import typer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from typer.models import OptionInfo

from owlclaw.cli.progress import progress_after


def _normalize_optional_str_option(value: object) -> str:
    if isinstance(value, OptionInfo) or value is None:
        return ""
    if not isinstance(value, str):
        return ""
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


def _connection_string_for_restore(url: str) -> str:
    """Convert database URL to libpq form (postgresql://)."""
    url = (url or "").strip()
    if url.startswith("postgresql+asyncpg://"):
        url = "postgresql://" + url[len("postgresql+asyncpg://") :]
    elif not url.startswith("postgresql://"):
        url = "postgresql://" + url
    return url


def _build_restore_env(url: str) -> dict[str, str]:
    """Build environment for psql/pg_restore (PGPASSWORD)."""
    env = dict(os.environ)
    parsed = urlparse(url)
    if parsed.password:
        env["PGPASSWORD"] = parsed.password
    return env


def _detect_backup_format(path: Path) -> str:
    """Return 'custom' if file has pg_dump custom magic, else 'sql'."""
    try:
        with open(path, "rb") as f:
            header = f.read(5)
    except OSError:
        return "sql"
    if header == b"PGDMP":
        return "custom"
    return "sql"


def _check_database_empty_sync(url: str) -> bool:
    """Return True if target database has no user tables (best-effort)."""
    url_async = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url_async == url:
        url_async = "postgresql+asyncpg://" + url

    async def _run() -> bool:
        engine: AsyncEngine = create_async_engine(url_async)
        try:
            async with engine.connect() as conn:
                r = await conn.execute(
                    text("SELECT COUNT(*) FROM pg_stat_user_tables")
                )
                count = r.scalar()
                return (count or 0) == 0
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _get_restore_stats_sync(url: str) -> dict[str, int]:
    """Return table_count and total_rows after restore (best-effort)."""
    url_async = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url_async == url:
        url_async = "postgresql+asyncpg://" + url

    async def _run() -> dict[str, int]:
        engine: AsyncEngine = create_async_engine(url_async)
        try:
            async with engine.connect() as conn:
                r = await conn.execute(
                    text("""
                        SELECT
                            COUNT(*)::int AS table_count,
                            COALESCE(SUM(n_live_tup), 0)::bigint AS total_rows
                        FROM pg_stat_user_tables
                    """)
                )
                row = r.fetchone()
                if row:
                    return {
                        "table_count": int(row[0]) if row[0] is not None else 0,
                        "total_rows": int(row[1]) if row[1] is not None else 0,
                    }
        finally:
            await engine.dispose()
        return {"table_count": 0, "total_rows": 0}

    return asyncio.run(_run())


def _restore_from_sql(conn_str: str, input_path: Path, clean: bool, env: dict[str, str]) -> None:
    """Restore from plain SQL file using psql."""
    parsed = urlparse(conn_str)
    args = [
        "psql",
        "-h", parsed.hostname or "localhost",
        "-p", str(parsed.port or 5432),
        "-U", parsed.username or "postgres",
        "-d", (parsed.path or "/").lstrip("/") or "postgres",
        "-f", str(input_path.resolve()),
    ]
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or f"psql exited with {result.returncode}")


def _restore_from_custom(conn_str: str, input_path: Path, clean: bool, env: dict[str, str]) -> None:
    """Restore from custom format using pg_restore."""
    parsed = urlparse(conn_str)
    args = [
        "pg_restore",
        "-h", parsed.hostname or "localhost",
        "-p", str(parsed.port or 5432),
        "-U", parsed.username or "postgres",
        "-d", (parsed.path or "/").lstrip("/") or "postgres",
    ]
    if clean:
        args.append("--clean")
    args.append(str(input_path.resolve()))
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or f"pg_restore exited with {result.returncode}")


def restore_command(
    input_path: str = typer.Option(
        ...,
        "--input",
        "-i",
        help="Input backup file path (SQL or pg_dump custom).",
    ),
    clean: bool = typer.Option(
        False,
        "--clean",
        help="Drop existing objects before restore (pg_restore only).",
    ),
    database_url: str = typer.Option(
        "",
        "--database-url",
        help="Database URL (default: OWLCLAW_DATABASE_URL).",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompt.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed progress.",
    ),
) -> None:
    """Restore database from a backup file (psql or pg_restore)."""
    input_path = _normalize_optional_str_option(input_path).strip()
    clean = _normalize_bool_option(clean, False)
    database_url = _normalize_optional_str_option(database_url)
    database_url = (database_url or os.environ.get("OWLCLAW_DATABASE_URL") or "").strip()
    yes = _normalize_bool_option(yes, False)
    verbose = _normalize_bool_option(verbose, False)

    if not input_path:
        typer.echo("Error: --input is required.", err=True)
        raise typer.Exit(2)

    if not database_url:
        typer.echo("Error: Set OWLCLAW_DATABASE_URL or pass --database-url.", err=True)
        raise typer.Exit(2)

    path = Path(input_path)
    if not path.exists():
        typer.echo(f"Error: Backup file not found: {path}", err=True)
        raise typer.Exit(2)

    if not path.is_file():
        typer.echo(f"Error: Not a file: {path}", err=True)
        raise typer.Exit(2)

    conn_str = _connection_string_for_restore(database_url)
    env = _build_restore_env(conn_str)
    backup_format = _detect_backup_format(path)

    try:
        is_empty = _check_database_empty_sync(conn_str)
    except Exception:
        is_empty = True

    if not is_empty:
        typer.echo(
            typer.style("Warning: target database is not empty. Restore may conflict or duplicate data.", fg="yellow"),
            err=True,
        )

    typer.echo("This will restore the database from the backup file.")
    if not yes:
        try:
            reply = input("Continue? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            typer.echo("\nAborted.")
            raise typer.Exit(130) from None
        if reply not in ("y", "yes"):
            typer.echo("Aborted.")
            return

    if verbose:
        typer.echo(f"Detected format: {backup_format}. Restoring...")
    try:
        with progress_after(2.0, "Restoring..."):
            if backup_format == "sql":
                _restore_from_sql(conn_str, path, clean, env)
            else:
                _restore_from_custom(conn_str, path, clean, env)
    except subprocess.TimeoutExpired:
        typer.echo("Error: restore timed out.", err=True)
        typer.echo("Consider restoring from a backup or re-running migrations.", err=True)
        raise typer.Exit(1) from None
    except FileNotFoundError as e:
        cmd = "psql" if backup_format == "sql" else "pg_restore"
        typer.echo(f"Error: {cmd} not found. Install PostgreSQL client tools.", err=True)
        raise typer.Exit(2) from e
    except RuntimeError as e:
        typer.echo(f"Restore failed: {e}", err=True)
        typer.echo("Consider restoring from a backup or re-running migrations.", err=True)
        raise typer.Exit(1) from e

    try:
        stats = _get_restore_stats_sync(conn_str)
        typer.echo(f"Restore complete. Tables: {stats['table_count']}, total rows: {stats['total_rows']:,}")
    except Exception:
        typer.echo("Restore complete (stats could not be read).")
