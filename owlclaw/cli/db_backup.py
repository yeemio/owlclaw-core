"""owlclaw db backup — create database backup using pg_dump."""

import os
import shutil
import subprocess
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import typer
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


def _format_size(size_bytes: int) -> str:
    """Format byte count as human-readable size."""
    if size_bytes is None or size_bytes < 0:
        return "0 B"
    n = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _check_pg_dump_available() -> bool:
    """Return True if pg_dump is available on PATH."""
    return shutil.which("pg_dump") is not None


def _connection_string_for_pg_dump(url: str) -> str:
    """Convert database URL to libpq connection string (postgresql://)."""
    url = (url or "").strip()
    if url.startswith("postgresql+asyncpg://"):
        url = "postgresql://" + url[len("postgresql+asyncpg://") :]
    elif url.startswith("postgresql://"):
        pass
    else:
        url = "postgresql://" + url
    parsed = urlparse(url)
    if not parsed.password:
        return url
    username = parsed.username or ""
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port is not None else ""
    userinfo = f"{username}@" if username else ""
    sanitized_netloc = f"{userinfo}{host}{port}"
    return urlunparse(parsed._replace(netloc=sanitized_netloc))


def _build_pg_dump_env(url: str) -> dict[str, str]:
    """Build environment for pg_dump, including PGPASSWORD if present."""
    env = dict(os.environ)
    parsed = urlparse(url)
    if parsed.password:
        env["PGPASSWORD"] = parsed.password
    return env


def _build_pg_dump_args(
    url: str,
    output_path: Path,
    format_name: str,
    schema_only: bool,
    data_only: bool,
) -> list[str]:
    """Build pg_dump command argv (without pg_dump binary)."""
    conn_str = _connection_string_for_pg_dump(url)
    # pg_dump accepts -d connection_string (URI or key=value)
    args = [
        "pg_dump",
        "-d",
        conn_str,
        "-f",
        str(output_path.resolve()),
    ]
    if format_name == "custom":
        args.extend(["-F", "c"])
    if schema_only:
        args.append("--schema-only")
    elif data_only:
        args.append("--data-only")
    return args


def backup_command(
    output: str = typer.Option(
        ...,
        "--output",
        "-o",
        help="Output file path for the backup.",
    ),
    format_name: str = typer.Option(
        "plain",
        "--format",
        "-F",
        help="Backup format: plain (SQL) or custom (for pg_restore).",
    ),
    schema_only: bool = typer.Option(
        False,
        "--schema-only",
        help="Dump schema only, no data.",
    ),
    data_only: bool = typer.Option(
        False,
        "--data-only",
        help="Dump data only, no schema.",
    ),
    database_url: str = typer.Option(
        "",
        "--database-url",
        help="Database URL (default: OWLCLAW_DATABASE_URL).",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed progress.",
    ),
) -> None:
    """Create a database backup using pg_dump."""
    output = _normalize_optional_str_option(output).strip()
    format_name = (_normalize_optional_str_option(format_name) or "plain").strip().lower()
    schema_only = _normalize_bool_option(schema_only, False)
    data_only = _normalize_bool_option(data_only, False)
    verbose = _normalize_bool_option(verbose, False)
    database_url = _normalize_optional_str_option(database_url)
    database_url = (database_url or os.environ.get("OWLCLAW_DATABASE_URL") or "").strip()

    if not output:
        typer.echo("Error: --output is required.", err=True)
        raise typer.Exit(2)

    if schema_only and data_only:
        typer.echo("Error: --schema-only and --data-only cannot be used together.", err=True)
        raise typer.Exit(2)

    if format_name not in ("plain", "custom"):
        typer.echo(
            f"Error: unsupported format '{format_name}'. Use plain or custom.",
            err=True,
        )
        raise typer.Exit(2)

    if not database_url:
        typer.echo("Error: Set OWLCLAW_DATABASE_URL or pass --database-url.", err=True)
        raise typer.Exit(2)

    if not _check_pg_dump_available():
        typer.echo(
            "Error: pg_dump not found. Install PostgreSQL client tools:\n"
            "  - Ubuntu/Debian: apt install postgresql-client\n"
            "  - macOS: brew install postgresql\n"
            "  - Windows: install from PostgreSQL website",
            err=True,
        )
        raise typer.Exit(2)

    output_path = Path(output)
    if output_path.exists():
        try:
            reply = input(f"File {output_path} already exists. Overwrite? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            typer.echo("\nAborted.")
            raise typer.Exit(130) from None
        if reply not in ("y", "yes"):
            typer.echo("Aborted.")
            return

    args = _build_pg_dump_args(
        database_url,
        output_path,
        format_name,
        schema_only,
        data_only,
    )
    env = _build_pg_dump_env(database_url)

    if verbose:
        typer.echo("Running pg_dump...")
    try:
        with progress_after(2.0, "Backing up..."):
            result = subprocess.run(
                args,
                env=env,
                capture_output=True,
                text=True,
                timeout=3600,
            )
    except subprocess.TimeoutExpired:
        if output_path.exists():
            with suppress(OSError):
                output_path.unlink()
        typer.echo("Error: backup timed out.", err=True)
        raise typer.Exit(1) from None
    except FileNotFoundError:
        typer.echo("Error: pg_dump not found.", err=True)
        raise typer.Exit(2) from None

    if result.returncode != 0:
        if output_path.exists():
            with suppress(OSError):
                output_path.unlink()
        stderr = (result.stderr or "").strip()
        typer.echo(f"Backup failed: {stderr or result.returncode}", err=True)
        raise typer.Exit(1) from None

    size = output_path.stat().st_size
    typer.echo(f"Backup written: {output_path.resolve()}")
    typer.echo(f"Size: {_format_size(size)}")
