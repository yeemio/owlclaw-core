"""owlclaw db status — show connection, version, extensions, table count, disk usage."""

import asyncio
import os
from urllib.parse import urlsplit, urlunsplit

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import create_engine as create_sync_engine
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
    """Format byte count as human-readable size."""
    if size_bytes is None or size_bytes < 0:
        return "—"
    n = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _to_sync_postgres_url(url: str) -> str:
    """Convert asyncpg URL to sync psycopg2 URL when needed."""
    u = url.strip()
    if u.startswith("postgresql+asyncpg://"):
        return "postgresql://" + u[len("postgresql+asyncpg://") :]
    return u


async def _collect_status_info(engine: AsyncEngine, url: str) -> dict:
    """Collect DB version, extensions, table stats, disk usage, migration revision."""
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    info: dict = {
        "connection": _mask_url(url),
        "server_version": "—",
        "extensions": [],
        "current_migration": "—",
        "pending_migrations": 0,
        "table_count": 0,
        "total_rows": 0,
        "disk_usage_bytes": 0,
    }
    async with engine.connect() as conn:
        # Server version (first line of version())
        try:
            r = await conn.execute(text("SELECT version()"))
            v = r.scalar()
            info["server_version"] = (v.split(",")[0].strip() if v else "—")
        except Exception:
            info["server_version"] = "—"

        # Extensions (name + version)
        try:
            r = await conn.execute(
                text("SELECT extname, extversion FROM pg_extension ORDER BY extname")
            )
            info["extensions"] = [f"{row[0]} {row[1]}" for row in r]
        except Exception:
            pass

        # Table count, total rows, disk usage
        try:
            r = await conn.execute(
                text("""
                    SELECT
                        COUNT(*) AS table_count,
                        COALESCE(SUM(n_live_tup), 0)::bigint AS total_rows
                    FROM pg_stat_user_tables
                """)
            )
            row = r.fetchone()
            if row:
                info["table_count"] = int(row[0]) if row[0] is not None else 0
                info["total_rows"] = int(row[1]) if row[1] is not None else 0
        except Exception:
            pass
        try:
            r = await conn.execute(text("SELECT pg_database_size(current_database())"))
            info["disk_usage_bytes"] = int(r.scalar() or 0)
        except Exception:
            pass

        # Current migration revision (sync call on connection)
        def _get_current_rev(sync_conn):
            ctx = MigrationContext.configure(sync_conn)
            return ctx.get_current_revision()

        try:
            current_rev = await conn.run_sync(_get_current_rev)
        except Exception:
            current_rev = None

    # Pending migrations (Alembic script)
    try:
        alembic_cfg = Config("alembic.ini")
        script = ScriptDirectory.from_config(alembic_cfg)
        if current_rev:
            rev_obj = script.get_revision(current_rev)
            doc = getattr(rev_obj, "doc", None) if rev_obj else None
            info["current_migration"] = f"{current_rev[:8]} ({doc})" if doc else (current_rev[:8] if current_rev else "—")
            pending = list(script.iterate_revisions("head", current_rev))
            info["pending_migrations"] = len(pending)
        else:
            info["current_migration"] = "none (run: owlclaw db migrate)"
            info["pending_migrations"] = len(list(script.iterate_revisions("head", None)))
    except Exception:
        info["current_migration"] = "—"
        info["pending_migrations"] = 0

    return info


def _collect_status_info_sync(url: str) -> dict:
    """Sync fallback probe for environments where asyncpg is unstable."""
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    info: dict = {
        "connection": _mask_url(url),
        "server_version": "—",
        "extensions": [],
        "current_migration": "—",
        "pending_migrations": 0,
        "table_count": 0,
        "total_rows": 0,
        "disk_usage_bytes": 0,
    }

    sync_url = _to_sync_postgres_url(url)
    engine = create_sync_engine(sync_url, pool_pre_ping=True)
    current_rev = None
    try:
        with engine.connect() as conn:
            try:
                r = conn.execute(text("SELECT version()"))
                v = r.scalar()
                info["server_version"] = (v.split(",")[0].strip() if v else "—")
            except Exception:
                info["server_version"] = "—"

            try:
                r = conn.execute(text("SELECT extname, extversion FROM pg_extension ORDER BY extname"))
                info["extensions"] = [f"{row[0]} {row[1]}" for row in r]
            except Exception:
                pass

            try:
                r = conn.execute(
                    text(
                        """
                        SELECT
                            COUNT(*) AS table_count,
                            COALESCE(SUM(n_live_tup), 0)::bigint AS total_rows
                        FROM pg_stat_user_tables
                        """
                    )
                )
                row = r.fetchone()
                if row:
                    info["table_count"] = int(row[0]) if row[0] is not None else 0
                    info["total_rows"] = int(row[1]) if row[1] is not None else 0
            except Exception:
                pass

            try:
                r = conn.execute(text("SELECT pg_database_size(current_database())"))
                info["disk_usage_bytes"] = int(r.scalar() or 0)
            except Exception:
                pass

            try:
                ctx = MigrationContext.configure(conn)
                current_rev = ctx.get_current_revision()
            except Exception:
                current_rev = None
    finally:
        engine.dispose()

    try:
        alembic_cfg = Config("alembic.ini")
        script = ScriptDirectory.from_config(alembic_cfg)
        if current_rev:
            rev_obj = script.get_revision(current_rev)
            doc = getattr(rev_obj, "doc", None) if rev_obj else None
            info["current_migration"] = f"{current_rev[:8]} ({doc})" if doc else current_rev[:8]
            pending = list(script.iterate_revisions("head", current_rev))
            info["pending_migrations"] = len(pending)
        else:
            info["current_migration"] = "none (run: owlclaw db migrate)"
            info["pending_migrations"] = len(list(script.iterate_revisions("head", None)))
    except Exception:
        info["current_migration"] = "—"
        info["pending_migrations"] = 0

    return info


def _print_status_table(info: dict) -> None:
    """Print status info as a Rich table."""
    console = Console()
    table = Table(title="OwlClaw Database Status", show_header=True, header_style="bold")
    table.add_column("Item", style="dim")
    table.add_column("Value")
    table.add_row("Connection", info["connection"])
    table.add_row("Server version", info["server_version"])
    table.add_row("Extensions", ", ".join(info["extensions"]) if info["extensions"] else "—")
    table.add_row("Migration", info["current_migration"])
    table.add_row("Pending migrations", str(info["pending_migrations"]))
    table.add_row("Tables", str(info["table_count"]))
    table.add_row("Total rows", f"{info['total_rows']:,}")
    table.add_row("Disk usage", _format_size(info["disk_usage_bytes"]))
    console.print(table)


def status_command(
    database_url: str = typer.Option(
        "",
        "--database-url",
        help="Database URL (default: OWLCLAW_DATABASE_URL).",
    ),
) -> None:
    """Show database connection, version, extensions, table stats, and migration status."""
    normalized_database_url = _normalize_optional_str_option(database_url)
    url = normalized_database_url or os.environ.get("OWLCLAW_DATABASE_URL")
    if not url or not url.strip():
        typer.echo("Error: Set OWLCLAW_DATABASE_URL or pass --database-url.", err=True)
        raise typer.Exit(2)
    url = url.strip()
    try:
        engine = get_engine(url)
    except ConfigurationError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(2) from e
    try:
        info = asyncio.run(_collect_status_info(engine, url))
    except Exception as exc:
        typer.echo(
            f"Warning: async status probe failed ({exc}); falling back to sync probe.",
            err=True,
        )
        info = _collect_status_info_sync(url)
    _print_status_table(info)
