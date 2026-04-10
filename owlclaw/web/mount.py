"""Console mounting utilities for OwlClaw web integration."""

from __future__ import annotations

import importlib
import logging
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


class SPAStaticFiles(StaticFiles):
    """Serve static files and fallback to index.html for client-side routes."""

    async def get_response(self, path: str, scope: MutableMapping[str, Any]) -> Response:
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and "." not in path.split("/")[-1]:
                return await super().get_response("index.html", scope)
            raise
        is_not_found = response.status_code == 404
        has_extension = "." in path.split("/")[-1]
        if is_not_found and not has_extension:
            return await super().get_response("index.html", scope)
        return response


def _has_route(app: Starlette, path: str) -> bool:
    for route in app.routes:
        route_path = getattr(route, "path", None)
        if route_path == path:
            return True
    return False


def _load_console_api_app() -> Any | None:
    try:
        module = importlib.import_module("owlclaw.web.app")
    except ModuleNotFoundError:
        return None
    factory = getattr(module, "create_console_app", None)
    if factory is None:
        return None
    return factory()


def _api_app_requires_prefix_adapter(api_app: Any) -> bool:
    routes = getattr(api_app, "routes", None)
    if not isinstance(routes, list | tuple):
        return False
    for route in routes:
        route_path = getattr(route, "path", "")
        if isinstance(route_path, str) and route_path.startswith("/api/v1"):
            return True
    return False


def mount_console(app: Starlette, *, api_app: Any | None = None) -> bool:
    """Mount Console static + API routes when assets are available.

    Returns True when console static files are mounted, False otherwise.
    """
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        logger.info("Console static files not found at %s; skip mounting", STATIC_DIR)
        return False

    resolved_api_app = api_app if api_app is not None else _load_console_api_app()

    if not _has_route(app, "/console"):
        app.mount("/console", SPAStaticFiles(directory=str(STATIC_DIR), html=True))

    if not _has_route(app, "/"):
        async def console_root_redirect(_: Request) -> RedirectResponse:
            return RedirectResponse(url="/console/", status_code=307)
        app.add_route("/", console_root_redirect)

    if resolved_api_app is not None:
        if _api_app_requires_prefix_adapter(resolved_api_app):
            if not getattr(app.state, "_console_api_root_mounted", False):
                app.mount("/", resolved_api_app)
                app.state._console_api_root_mounted = True
        elif not _has_route(app, "/api/v1"):
            app.mount("/api/v1", resolved_api_app)

    logger.info("Console mounted at /console/ (API mounted=%s)", resolved_api_app is not None)
    return True
