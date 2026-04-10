"""Minimal `owlclaw start` command for local console hosting."""

from __future__ import annotations

import logging
from pathlib import Path

from starlette.applications import Starlette

# Load .env from project root so OWLCLAW_DATABASE_URL etc. are available
try:
    from dotenv import load_dotenv

    _env_path = Path.cwd() / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass
from starlette.responses import JSONResponse

from owlclaw.web.mount import mount_console

logger = logging.getLogger(__name__)


def create_start_app() -> Starlette:
    """Create Starlette app with optional console mount."""
    app = Starlette()

    async def healthz(_request) -> JSONResponse:
        return JSONResponse({"status": "ok"})
    app.add_route("/healthz", healthz)

    mounted = mount_console(app)
    logger.info("owlclaw start app initialized (console_mounted=%s)", mounted)
    return app


def start_command(*, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run local ASGI server for console and lightweight status endpoints."""
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("uvicorn is required for `owlclaw start`") from exc

    app = create_start_app()
    logger.info("Starting OwlClaw on http://%s:%s", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")
