"""FastAPI application factory for OwlHub."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from owlclaw.owlhub.api.audit import AuditLogger, create_audit_router
from owlclaw.owlhub.api.auth import (
    AuthManager,
    Principal,
    create_auth_router,
    enforce_write_auth,
    get_current_principal,
)
from owlclaw.owlhub.api.metrics import MetricsCollector
from owlclaw.owlhub.api.routes.blacklist import router as blacklist_router
from owlclaw.owlhub.api.routes.reviews import router as reviews_router
from owlclaw.owlhub.api.routes.skills import router as skills_router
from owlclaw.owlhub.api.routes.statistics import router as statistics_router
from owlclaw.owlhub.models import BlacklistManager
from owlclaw.owlhub.review import ReviewSystem
from owlclaw.owlhub.statistics import StatisticsTracker
from owlclaw.owlhub.validator import Validator

current_principal_type = Annotated[Principal, Depends(get_current_principal)]
logger = logging.getLogger(__name__)


def _resolve_log_level() -> int:
    level_name = os.getenv("OWLHUB_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    return int(level)


def _log_json(level: int, event: str, **fields: object) -> None:
    if not logger.isEnabledFor(level):
        return
    payload = {"event": event, **fields}
    logger.log(level, "%s", json.dumps(payload, ensure_ascii=False, sort_keys=True))


def create_app() -> FastAPI:
    """Create FastAPI app with basic middleware and health endpoint."""
    app = FastAPI(
        title="OwlHub API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    validator = Validator()
    review_dir = Path(os.getenv("OWLHUB_REVIEW_DIR", "./.owlhub/reviews")).resolve()
    statistics_db = Path(os.getenv("OWLHUB_STATISTICS_DB", "./.owlhub/skill_statistics.json")).resolve()
    blacklist_db = Path(os.getenv("OWLHUB_BLACKLIST_DB", "./.owlhub/blacklist.json")).resolve()
    app.state.validator = validator
    app.state.review_system = ReviewSystem(storage_dir=review_dir, validator=validator)
    app.state.audit_logger = AuditLogger()
    app.state.statistics_tracker = StatisticsTracker(storage_path=statistics_db)
    app.state.blacklist_manager = BlacklistManager(path=blacklist_db)
    app.state.auth_manager = AuthManager()
    app.state.log_level = _resolve_log_level()
    app.state.metrics = MetricsCollector()
    app.state.index_path = Path(os.getenv("OWLHUB_INDEX_PATH", "./index.json")).resolve()
    app.state.review_dir = review_dir
    app.state.statistics_db = statistics_db
    app.state.blacklist_db = blacklist_db

    @app.middleware("http")
    async def authz_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        started = time.perf_counter()
        method = request.method
        path = request.url.path
        auth_manager = app.state.auth_manager
        response: Response
        try:
            auth_manager.enforce_request_rate_limit(request)
            enforce_write_auth(request)
        except HTTPException as exc:
            _log_json(
                app.state.log_level,
                "api_request",
                method=method,
                path=path,
                status_code=exc.status_code,
                duration_ms=round((time.perf_counter() - started) * 1000.0, 3),
            )
            app.state.metrics.record_request(
                method=method,
                path=path,
                status_code=exc.status_code,
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )
            response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
            _attach_security_headers(response)
            return response
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "Unhandled API exception: %s",
                json.dumps({"event": "api_request_error", "method": method, "path": path}, ensure_ascii=False),
            )
            app.state.metrics.record_request(
                method=method,
                path=path,
                status_code=500,
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )
            raise
        duration_ms = (time.perf_counter() - started) * 1000.0
        _log_json(
            app.state.log_level,
            "api_request",
            method=method,
            path=path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 3),
        )
        app.state.metrics.record_request(method=method, path=path, status_code=response.status_code, duration_ms=duration_ms)
        _attach_security_headers(response)
        return response

    @app.get("/health")
    def health() -> dict[str, object]:
        checks: dict[str, dict[str, str]] = {}
        index_path = app.state.index_path
        if index_path.exists():
            checks["index"] = {"status": "ok", "detail": str(index_path)}
        else:
            checks["index"] = {"status": "warn", "detail": f"index file not found: {index_path}"}

        for key, path in (
            ("review_storage", app.state.review_dir),
            ("statistics_storage", app.state.statistics_db),
            ("blacklist_storage", app.state.blacklist_db),
        ):
            target = path if path.is_dir() else path.parent
            if target.exists():
                checks[key] = {"status": "ok", "detail": str(target)}
            else:
                checks[key] = {"status": "warn", "detail": f"path not found: {target}"}
        return {"status": "ok", "checks": checks}

    @app.get("/metrics")
    def metrics() -> PlainTextResponse:
        tracker = app.state.statistics_tracker
        payload = app.state.metrics.export_prometheus(skill_stats=tracker.list_all_statistics())
        return PlainTextResponse(payload, media_type="text/plain; version=0.0.4")

    @app.post("/api/v1/skills/publish-probe")
    def publish_probe(principal: current_principal_type) -> dict[str, str]:
        return {"status": "accepted", "user_id": principal.user_id, "role": principal.role}

    app.include_router(create_auth_router(app.state.auth_manager))
    app.include_router(blacklist_router)
    app.include_router(skills_router)
    app.include_router(reviews_router)
    app.include_router(statistics_router)
    app.include_router(create_audit_router(app.state.audit_logger))

    return app


def _attach_security_headers(response: Response) -> None:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
    response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
