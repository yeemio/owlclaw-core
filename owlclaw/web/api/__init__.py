"""FastAPI application factory for console backend APIs."""

from __future__ import annotations

from fastapi import FastAPI

from owlclaw.web.api.agents import router as agents_router
from owlclaw.web.api.capabilities import router as capabilities_router
from owlclaw.web.api.governance import router as governance_router
from owlclaw.web.api.ledger import router as ledger_router
from owlclaw.web.api.middleware import TokenAuthMiddleware, add_cors_middleware, register_exception_handlers
from owlclaw.web.api.overview import router as overview_router
from owlclaw.web.api.settings import router as settings_router
from owlclaw.web.api.triggers import router as triggers_router
from owlclaw.web.api.ws import router as ws_router


def create_api_app() -> FastAPI:
    """Create console API app with `/api/v1` route namespace."""
    app = FastAPI(
        title="OwlClaw Console API",
        version="v1",
        openapi_url="/api/v1/openapi.json",
        docs_url=None,
        redoc_url=None,
    )
    app.state.ws_max_connections = 10
    app.state.ws_push_interval_seconds = 30.0

    add_cors_middleware(app)
    app.add_middleware(
        TokenAuthMiddleware,
        exempt_paths={
            "/api/v1/openapi.json",
            "/api/v1/health",
        },
    )
    register_exception_handlers(app)

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(overview_router, prefix="/api/v1", tags=["overview"])
    app.include_router(agents_router, prefix="/api/v1", tags=["agents"])
    app.include_router(governance_router, prefix="/api/v1", tags=["governance"])
    app.include_router(capabilities_router, prefix="/api/v1", tags=["capabilities"])
    app.include_router(ledger_router, prefix="/api/v1", tags=["ledger"])
    app.include_router(settings_router, prefix="/api/v1", tags=["settings"])
    app.include_router(triggers_router, prefix="/api/v1", tags=["triggers"])
    app.include_router(ws_router, prefix="/api/v1", tags=["ws"])
    return app


__all__ = ["create_api_app"]
