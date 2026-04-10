"""API middleware and exception handlers for console backend."""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response

logger = logging.getLogger(__name__)


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Bearer token auth middleware for console API."""

    def __init__(
        self,
        app: FastAPI,
        *,
        token_env: str = "OWLCLAW_CONSOLE_API_TOKEN",
        legacy_token_env: str = "OWLCLAW_CONSOLE_TOKEN",
        require_auth_env: str = "OWLCLAW_REQUIRE_AUTH",
        exempt_paths: set[str] | None = None,
    ) -> None:
        super().__init__(app)
        self._token_env = token_env
        self._legacy_token_env = legacy_token_env
        self._require_auth_env = require_auth_env
        self._exempt_paths = exempt_paths or set()
        expected_token = self._read_expected_token()
        require_auth = _is_truthy_env(os.getenv(self._require_auth_env))
        if require_auth and not expected_token:
            logger.warning(
                "Console API auth required but no token configured: %s/%s",
                self._token_env,
                self._legacy_token_env,
            )
        elif not expected_token:
            logger.warning(
                "Console API token is empty; auth middleware allows requests. "
                "Set %s or %s, or enable %s=true to enforce configuration.",
                self._token_env,
                self._legacy_token_env,
                self._require_auth_env,
            )

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method.upper() == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        if path in self._exempt_paths:
            return await call_next(request)

        expected_token = self._read_expected_token()
        require_auth = _is_truthy_env(os.getenv(self._require_auth_env))
        if not expected_token:
            if require_auth:
                return JSONResponse(
                    status_code=500,
                    content={
                        "error": {
                            "code": "AUTH_NOT_CONFIGURED",
                            "message": "auth not configured",
                        },
                    },
                )
            return await call_next(request)

        api_token_header = request.headers.get("x-api-token", "").strip()
        if hmac.compare_digest(api_token_header.encode("utf-8"), expected_token.encode("utf-8")):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "Missing bearer token.",
                    },
                },
            )

        provided_token = auth_header[7:].strip()
        # Use constant-time comparison to prevent timing attacks
        if not hmac.compare_digest(provided_token.encode("utf-8"), expected_token.encode("utf-8")):
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "Invalid token.",
                    },
                },
            )
        return await call_next(request)

    def _read_expected_token(self) -> str:
        expected_token = os.getenv(self._token_env, "").strip()
        if not expected_token:
            expected_token = os.getenv(self._legacy_token_env, "").strip()
        return expected_token


def _is_truthy_env(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def parse_cors_origins(raw_origins: str | None) -> list[str]:
    """Parse comma-separated CORS origins env value."""
    if raw_origins is None:
        return []
    parts = [item.strip() for item in raw_origins.split(",")]
    origins = [item for item in parts if item]
    return origins


def add_cors_middleware(app: FastAPI) -> None:
    """Attach CORS middleware using env-driven configuration."""
    origins = parse_cors_origins(os.getenv("OWLCLAW_CONSOLE_CORS_ORIGINS"))
    allow_credentials_raw = os.getenv("OWLCLAW_CONSOLE_CORS_ALLOW_CREDENTIALS")
    allow_credentials = True if allow_credentials_raw is None else _is_truthy_env(allow_credentials_raw)
    if allow_credentials and "*" in origins:
        logger.warning(
            "Invalid CORS config: allow_credentials=true is not compatible with wildcard origin '*'. "
            "Forcing allow_credentials=false."
        )
        allow_credentials = False
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers using unified error shape."""

    @app.exception_handler(HTTPException)
    async def handle_http_exception(_: Request, exc: HTTPException) -> JSONResponse:
        code = "HTTP_ERROR"
        if exc.status_code == 404:
            code = "NOT_FOUND"
        elif exc.status_code == 401:
            code = "UNAUTHORIZED"
        elif exc.status_code == 403:
            code = "FORBIDDEN"
        elif exc.status_code == 422:
            code = "VALIDATION_ERROR"
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": code,
                    "message": str(exc.detail),
                },
            },
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed.",
                    "details": {"errors": exc.errors()},
                },
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "Unexpected server error.",
                    "details": {"type": exc.__class__.__name__},
                },
            },
        )
