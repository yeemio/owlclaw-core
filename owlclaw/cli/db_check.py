"""owlclaw db check — database health check (connection, migration, pgvector, pool, disk, slow queries)."""

import asyncio
import os
import time
from contextlib import suppress
from urllib.parse import urlsplit, urlunsplit

import typer
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from typer.models import OptionInfo

from owlclaw.db import ConfigurationError, get_engine


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


def _mask_url(url: str) -> str:
    """Hide password in URL."""
    if "://" not in url:
        return url
    split = urlsplit(url)
    if split.username is None:
        return url
    userinfo = split.username
    if split.password is not None:
        userinfo = f"{userinfo}:***"
    host = split.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        parsed_port = split.port
    except ValueError:
        return url
    port_suffix = f":{parsed_port}" if parsed_port is not None else ""
    netloc = f"{userinfo}@{host}{port_suffix}"
    return urlunsplit((split.scheme, netloc, split.path, split.query, split.fragment))


def _format_size(size_bytes: int) -> str:
    if size_bytes is None or size_bytes < 0:
        return "—"
    n = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


async def _check_connection(engine: AsyncEngine) -> dict:
    """Check database connection and response time."""
    try:
        start = time.perf_counter()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        latency_ms = (time.perf_counter() - start) * 1000
        if latency_ms < 10:
            status = "OK"
        elif latency_ms < 100:
            status = "WARN"
        else:
            status = "ERROR"
        return {"name": "Connection", "status": status, "message": f"responsive ({latency_ms:.0f}ms)"}
    except Exception as e:
        return {"name": "Connection", "status": "ERROR", "message": f"failed — {e}"}


async def _check_migration(engine: AsyncEngine) -> dict:
    """Check migration is up to date."""
    try:
        async with engine.connect() as conn:
            def _get_current(sync_conn):
                ctx = MigrationContext.configure(sync_conn)
                return ctx.get_current_revision()
            current_rev = await conn.run_sync(_get_current)
        if not current_rev:
            return {"name": "Migration", "status": "WARN", "message": "not initialized"}
        cfg = Config("alembic.ini")
        script = ScriptDirectory.from_config(cfg)
        pending = list(script.iterate_revisions("head", current_rev))
        if pending:
            return {"name": "Migration", "status": "WARN", "message": f"{len(pending)} pending migrations"}
        return {"name": "Migration", "status": "OK", "message": f"up to date ({current_rev[:8]})"}
    except Exception as e:
        return {"name": "Migration", "status": "ERROR", "message": f"check failed — {e}"}


async def _check_pgvector(engine: AsyncEngine) -> dict:
    """Check pgvector extension is installed."""
    try:
        async with engine.connect() as conn:
            r = await conn.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            version = r.scalar()
        if version:
            return {"name": "pgvector", "status": "OK", "message": f"installed ({version})"}
        return {"name": "pgvector", "status": "WARN", "message": "not installed"}
    except Exception as e:
        return {"name": "pgvector", "status": "ERROR", "message": f"check failed — {e}"}


async def _check_connection_pool(engine: AsyncEngine) -> dict:
    """Check connection pool usage."""
    try:
        pool = getattr(engine, "sync_engine", engine).pool
        size_fn = getattr(pool, "size", None)
        checkedout_fn = getattr(pool, "checkedout", None)
        if not callable(size_fn) or not callable(checkedout_fn):
            return {"name": "Connection pool", "status": "WARN", "message": "pool stats unavailable"}
        size = int(size_fn())
        checked_out = int(checkedout_fn())
        usage_pct = (checked_out / size * 100) if size > 0 else 0
        if usage_pct < 70:
            status = "OK"
        elif usage_pct < 90:
            status = "WARN"
        else:
            status = "ERROR"
        return {
            "name": "Connection pool",
            "status": status,
            "message": f"{checked_out}/{size} active ({usage_pct:.0f}%)",
        }
    except Exception as e:
        return {"name": "Connection pool", "status": "ERROR", "message": f"check failed — {e}"}


async def _check_disk_usage(engine: AsyncEngine) -> dict:
    """Check database disk usage."""
    try:
        async with engine.connect() as conn:
            r = await conn.execute(text("SELECT pg_database_size(current_database())"))
            size_bytes = int(r.scalar() or 0)
        size_str = _format_size(size_bytes)
        status = "OK" if size_bytes < 10 * 1024 * 1024 * 1024 else "WARN"
        return {"name": "Disk usage", "status": status, "message": size_str}
    except Exception as e:
        return {"name": "Disk usage", "status": "ERROR", "message": f"check failed — {e}"}


async def _check_slow_queries(engine: AsyncEngine) -> dict:
    """Check slow queries (pg_stat_statements; N/A if not enabled)."""
    try:
        async with engine.connect() as conn:
            r = await conn.execute(
                text("""
                    SELECT COUNT(*)::bigint
                    FROM pg_stat_statements
                    WHERE mean_exec_time > 1000 AND calls > 0
                """)
            )
            slow_count = int(r.scalar() or 0)
        if slow_count == 0:
            return {"name": "Slow queries", "status": "OK", "message": "no slow queries"}
        if slow_count < 5:
            return {"name": "Slow queries", "status": "WARN", "message": f"{slow_count} queries > 1s"}
        return {"name": "Slow queries", "status": "ERROR", "message": f"{slow_count} queries > 1s"}
    except Exception:
        return {"name": "Slow queries", "status": "OK", "message": "N/A (pg_stat_statements not enabled)"}


async def _run_health_checks(engine: AsyncEngine, verbose: bool = False) -> list[dict]:
    """Run all health checks and return list of {name, status, message}."""
    checks = []
    for name, check_fn in [
        ("Connection", _check_connection),
        ("Migration", _check_migration),
        ("pgvector", _check_pgvector),
        ("Connection pool", _check_connection_pool),
        ("Disk usage", _check_disk_usage),
        ("Slow queries", _check_slow_queries),
    ]:
        if verbose:
            typer.echo(f"  Checking {name}...")
        checks.append(await check_fn(engine))
    return checks


def _print_health_report(checks: list[dict], connection_label: str) -> None:
    """Print health check report and overall status."""
    typer.echo("")
    typer.echo("OwlClaw Database Health Check")
    typer.echo("=" * 50)
    typer.echo(f"Connection: {connection_label}")
    typer.echo("")
    for c in checks:
        status = c["status"]
        name = c["name"]
        msg = c["message"]
        if status == "OK":
            icon = "[OK]  "
        elif status == "WARN":
            icon = "[WARN]"
        else:
            icon = "[ERROR]"
        typer.echo(f"  {icon} {name:20} {msg}")
    error_count = sum(1 for c in checks if c["status"] == "ERROR")
    warn_count = sum(1 for c in checks if c["status"] == "WARN")
    typer.echo("")
    if error_count > 0:
        typer.echo(f"Overall: UNHEALTHY ({error_count} errors, {warn_count} warnings)")
    elif warn_count > 0:
        typer.echo(f"Overall: HEALTHY ({warn_count} warnings)")
    else:
        typer.echo("Overall: HEALTHY")
    typer.echo("")


def check_command(
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
    """Run database health checks (connection, migration, pgvector, pool, disk, slow queries)."""
    normalized_database_url = _normalize_optional_str_option(database_url)
    verbose = _normalize_bool_option(verbose, False)
    url = (normalized_database_url or os.environ.get("OWLCLAW_DATABASE_URL") or "").strip()
    if not url:
        typer.echo("Error: Set OWLCLAW_DATABASE_URL or pass --database-url.", err=True)
        raise typer.Exit(2)
    try:
        engine = get_engine(url)
    except ConfigurationError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(2) from e
    try:
        checks = asyncio.run(_run_health_checks(engine, verbose=verbose))
        _print_health_report(checks, _mask_url(url))
        if any(c["status"] == "ERROR" for c in checks):
            raise typer.Exit(1)
    finally:
        with suppress(Exception):
            engine.sync_engine.dispose()
