"""owlclaw db init — create database, role, and pgvector extension."""

import asyncio
import os
import secrets
from typing import Any
from urllib.parse import urlparse

import typer
from typer.models import OptionInfo

try:
    import asyncpg  # type: ignore[import-untyped]
except ImportError:
    asyncpg = None

try:
    import psycopg2  # type: ignore[import-untyped]
    from psycopg2 import errors as psycopg2_errors  # type: ignore[import-untyped]
except ImportError:
    psycopg2 = None
    psycopg2_errors = None


def _parse_pg_url(url: str) -> dict:
    """Parse postgresql:// or postgresql+asyncpg:// URL into connection kwargs."""
    u = url.strip()
    lower_u = u.lower()
    if lower_u.startswith("postgresql+asyncpg://"):
        u = "postgresql://" + u[len("postgresql+asyncpg://"):]
    elif not lower_u.startswith("postgresql://"):
        return {}
    parsed = urlparse(u)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
        "database": (parsed.path or "/").lstrip("/") or "postgres",
    }


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


async def _init_impl(
    admin_url: str,
    owlclaw_password: str | None,
    hatchet_password: str | None,
    skip_hatchet: bool,
    dry_run: bool,
    pw_owl: str | None = None,
    pw_hatchet: str | None = None,
) -> None:
    if asyncpg is None:
        typer.echo("Error: asyncpg is required for db init. Install with: pip install asyncpg", err=True)
        raise typer.Exit(1)
    params = _parse_pg_url(admin_url)
    if not params:
        typer.echo("Error: admin-url must be postgresql://user:pass@host:port/postgres", err=True)
        raise typer.Exit(2)
    if params["database"] != "postgres":
        typer.echo("Warning: admin-url should connect to database 'postgres'. Using it anyway.", err=True)

    def _to_bool(v: Any, default: bool) -> bool:
        if v is None:
            return default
        if isinstance(v, bool):
            return v
        return str(v).lower() not in ("0", "false", "no", "")

    if dry_run:
        typer.echo("--dry-run: would create role owlclaw, database owlclaw, extension vector")
        if not _to_bool(skip_hatchet, default=False):
            typer.echo("Would create role hatchet, database hatchet")
        if not owlclaw_password:
            typer.echo("Would generate random owlclaw password")
        return

    def _escape(s: str) -> str:
        return s.replace("'", "''")

    if pw_owl is None:
        pw_owl = owlclaw_password or secrets.token_urlsafe(16)
    if pw_hatchet is None:
        pw_hatchet = hatchet_password or secrets.token_urlsafe(16)
    conn = await asyncpg.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        database=params["database"],
    )
    try:
        await conn.execute("CREATE ROLE owlclaw WITH LOGIN PASSWORD '" + _escape(pw_owl) + "'")
        typer.echo("Created role owlclaw")
    except asyncpg.DuplicateObjectError:
        typer.echo("Role owlclaw already exists")
    try:
        await conn.execute("CREATE DATABASE owlclaw OWNER owlclaw")
        typer.echo("Created database owlclaw")
    except (asyncpg.DuplicateObjectError, asyncpg.DuplicateDatabaseError):
        typer.echo("Database owlclaw already exists")
    finally:
        try:
            await conn.close()
        except (ConnectionResetError, OSError):
            typer.echo("Warning: connection close failed (e.g. WinError 64); continuing. Run init again if hatchet was not created.", err=True)
        except Exception:
            pass
    conn_owl: asyncpg.Connection | None = None
    for _ in range(2):
        try:
            conn_owl = await asyncpg.connect(
                host=params["host"],
                port=params["port"],
                user="owlclaw",
                password=pw_owl,
                database="owlclaw",
            )
            try:
                await conn_owl.execute("CREATE EXTENSION IF NOT EXISTS vector")
                typer.echo("Enabled extension vector in database owlclaw")
            except Exception as e:
                if "vector" in str(e).lower() or "extension" in str(e).lower():
                    typer.echo("Warning: pgvector extension not available; install it for Agent memory. Continuing.", err=True)
                else:
                    raise
            break
        except (ConnectionResetError, OSError) as e:
            winerr = getattr(e, "winerror", None)
            if winerr == 64 or "64" in str(e):
                typer.echo("Warning: connection reset (WinError 64); retrying once.", err=True)
                await asyncio.sleep(1.0)
                continue
            raise
        finally:
            if conn_owl is not None:
                try:
                    await conn_owl.close()
                except (ConnectionResetError, OSError):
                    pass
                except Exception:
                    pass
                conn_owl = None
    skip_hatchet_bool = _to_bool(skip_hatchet, default=False)
    if not skip_hatchet_bool:
        last_err: Exception | None = None
        conn2: asyncpg.Connection | None = None
        for attempt in range(2):
            try:
                conn2 = await asyncpg.connect(
                    host=params["host"],
                    port=params["port"],
                    user=params["user"],
                    password=params["password"],
                    database=params["database"],
                )
                try:
                    await conn2.execute("CREATE ROLE hatchet WITH LOGIN PASSWORD '" + _escape(pw_hatchet) + "'")
                    typer.echo("Created role hatchet")
                except asyncpg.DuplicateObjectError:
                    typer.echo("Role hatchet already exists")
                try:
                    await conn2.execute("CREATE DATABASE hatchet OWNER hatchet")
                    typer.echo("Created database hatchet")
                except (asyncpg.DuplicateObjectError, asyncpg.DuplicateDatabaseError):
                    typer.echo("Database hatchet already exists")
                last_err = None
                break
            except (ConnectionResetError, OSError) as e:
                last_err = e
                winerr = getattr(e, "winerror", None)
                if attempt == 0 and (winerr == 64 or "64" in str(e)):
                    typer.echo("Warning: connection reset (WinError 64); retrying hatchet creation once.", err=True)
                    await asyncio.sleep(1.0)
                    continue
                raise
            finally:
                if conn2 is not None:
                    try:
                        await conn2.close()
                    except (ConnectionResetError, OSError):
                        pass
                    except Exception:
                        pass
                    conn2 = None
        if last_err is not None:
            raise last_err
    if not owlclaw_password:
        typer.echo("OwlClaw password (save it): " + pw_owl)


def _init_sync_fallback(
    admin_url: str,
    pw_owl: str,
    pw_hatchet: str,
    skip_hatchet: bool,
    echo_owl_password: bool,
) -> None:
    """Sync fallback using psycopg2 when async init fails (e.g. WinError 64 on Windows)."""
    if psycopg2 is None or psycopg2_errors is None:
        typer.echo("Error: psycopg2 is required for sync fallback. Install with: pip install psycopg2-binary", err=True)
        raise typer.Exit(1)
    params = _parse_pg_url(admin_url)
    if not params:
        raise typer.Exit(2)

    def _escape(s: str) -> str:
        return s.replace("'", "''")

    try:
        conn = psycopg2.connect(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            dbname=params["database"],
            options="-c client_encoding=UTF8",
        )
    except (UnicodeDecodeError, Exception) as e:  # noqa: BLE001
        try:
            msg = str(e)
        except Exception:  # noqa: BLE001
            msg = "Connection failed."
        else:
            if "decode" in msg.lower() or "continuation byte" in msg:
                msg = (
                    "Connection failed. PostgreSQL returned a non-UTF8 error "
                    "(e.g. password authentication failed). Check OWLCLAW_ADMIN_URL "
                    "password."
                )
            elif not msg.isascii():
                msg = "Connection failed. Check host, port, user, and password."
        raise RuntimeError(msg) from e

    conn.set_client_encoding("UTF8")
    conn.autocommit = True
    try:
        cur = conn.cursor()
        try:
            cur.execute("CREATE ROLE owlclaw WITH LOGIN PASSWORD '" + _escape(pw_owl) + "'")
            typer.echo("Created role owlclaw")
        except psycopg2_errors.DuplicateObject:
            typer.echo("Role owlclaw already exists")
            cur.execute("ALTER ROLE owlclaw PASSWORD '" + _escape(pw_owl) + "'")
        try:
            cur.execute("CREATE DATABASE owlclaw OWNER owlclaw")
            typer.echo("Created database owlclaw")
        except (psycopg2_errors.DuplicateObject, psycopg2_errors.DuplicateDatabase):
            typer.echo("Database owlclaw already exists")
    finally:
        conn.close()

    conn_owl = psycopg2.connect(
        host=params["host"],
        port=params["port"],
        user="owlclaw",
        password=pw_owl,
        dbname="owlclaw",
    )
    conn_owl.set_client_encoding("UTF8")
    conn_owl.autocommit = True
    try:
        try:
            conn_owl.cursor().execute("CREATE EXTENSION IF NOT EXISTS vector")
            typer.echo("Enabled extension vector in database owlclaw")
        except Exception as e:
            if "vector" in str(e).lower() or "extension" in str(e).lower():
                typer.echo("Warning: pgvector extension not available; install it for Agent memory. Continuing.", err=True)
            else:
                raise
    finally:
        conn_owl.close()

    if not skip_hatchet:
        conn2 = psycopg2.connect(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            dbname=params["database"],
        )
        conn2.set_client_encoding("UTF8")
        conn2.autocommit = True
        try:
            try:
                conn2.cursor().execute("CREATE ROLE hatchet WITH LOGIN PASSWORD '" + _escape(pw_hatchet) + "'")
                typer.echo("Created role hatchet")
            except psycopg2_errors.DuplicateObject:
                typer.echo("Role hatchet already exists")
            try:
                conn2.cursor().execute("CREATE DATABASE hatchet OWNER hatchet")
                typer.echo("Created database hatchet")
            except (psycopg2_errors.DuplicateObject, psycopg2_errors.DuplicateDatabase):
                typer.echo("Database hatchet already exists")
        finally:
            conn2.close()

    if echo_owl_password:
        typer.echo("OwlClaw password (save it): " + pw_owl)


def _try_sync_fallback(
    e: Exception,
    url: str,
    pw_owl: str | None,
    pw_hatchet: str | None,
    skip_hatchet: Any,
    do_dry_run: bool,
    owlclaw_password: str | None,
) -> None:
    winerr = getattr(e, "winerror", None)
    if do_dry_run or psycopg2 is None:
        raise
    if winerr != 64 and "64" not in str(e) and "connection was closed" not in str(e).lower():
        raise
    typer.echo("Warning: async init failed (e.g. WinError 64 / connection closed); retrying with sync driver (psycopg2).", err=True)
    skip_hatchet_bool = str(skip_hatchet).lower() not in ("0", "false", "no", "")
    _init_sync_fallback(
        admin_url=url,
        pw_owl=pw_owl or "",
        pw_hatchet=pw_hatchet or "",
        skip_hatchet=skip_hatchet_bool,
        echo_owl_password=owlclaw_password is None,
    )


def init_command(
    admin_url: str = typer.Option(
        "",
        "--admin-url",
        help="PostgreSQL superuser URL (default: OWLCLAW_ADMIN_URL).",
    ),
    owlclaw_password: str = typer.Option(
        "",
        "--owlclaw-password",
        help="Password for role owlclaw (default: random).",
    ),
    hatchet_password: str = typer.Option(
        "",
        "--hatchet-password",
        help="Password for role hatchet (default: random).",
    ),
    skip_hatchet: bool = typer.Option(
        False,
        "--skip-hatchet",
        help="Do not create hatchet database/role.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        is_flag=True,
        help="Show what would be done without executing.",
    ),
) -> None:
    """Create owlclaw (and optionally hatchet) database, role, and pgvector."""
    import sys
    normalized_admin_url = (_normalize_optional_str_option(admin_url) or "").strip() or None
    normalized_owlclaw_password = (_normalize_optional_str_option(owlclaw_password) or "").strip() or None
    normalized_hatchet_password = (_normalize_optional_str_option(hatchet_password) or "").strip() or None
    normalized_dry_run = _normalize_bool_option(dry_run, False)
    normalized_skip_hatchet = _normalize_bool_option(skip_hatchet, False)
    url = normalized_admin_url or os.environ.get("OWLCLAW_ADMIN_URL")
    if not url or not url.strip():
        typer.echo("Error: Set --admin-url or OWLCLAW_ADMIN_URL.", err=True)
        raise typer.Exit(2)
    # Keep argv fallback for Typer/Click bool edge cases, but honor explicit call args too.
    do_dry_run = bool(normalized_dry_run) or "--dry-run" in sys.argv
    do_skip_hatchet = bool(normalized_skip_hatchet) or "--skip-hatchet" in sys.argv
    pw_owl = (normalized_owlclaw_password or secrets.token_urlsafe(16)) if not do_dry_run else None
    pw_hatchet = (normalized_hatchet_password or secrets.token_urlsafe(16)) if not do_dry_run else None
    try:
        asyncio.run(
            _init_impl(
                admin_url=url,
                owlclaw_password=normalized_owlclaw_password,
                hatchet_password=normalized_hatchet_password,
                skip_hatchet=do_skip_hatchet,
                dry_run=do_dry_run,
                pw_owl=pw_owl,
                pw_hatchet=pw_hatchet,
            )
        )
    except (ConnectionResetError, OSError) as e:
        _try_sync_fallback(e, url, pw_owl, pw_hatchet, do_skip_hatchet, do_dry_run, normalized_owlclaw_password)
    except Exception as e:
        # asyncpg.ConnectionDoesNotExistError etc. on Windows after connection reset
        if "connection was closed" in str(e).lower() or "64" in str(e) or getattr(e, "winerror", None) == 64:
            _try_sync_fallback(e, url, pw_owl, pw_hatchet, do_skip_hatchet, do_dry_run, normalized_owlclaw_password)
        else:
            raise
