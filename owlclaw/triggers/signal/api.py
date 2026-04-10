"""HTTP admin endpoint for signal dispatch."""

from __future__ import annotations

import logging
from typing import Literal

logger = logging.getLogger(__name__)

from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from owlclaw.triggers.api.auth import AuthProvider
from owlclaw.triggers.signal.models import Signal, SignalSource, SignalType
from owlclaw.triggers.signal.router import SignalRouter


class SignalAPIRequest(BaseModel):
    """Validated payload for POST /admin/signal."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["pause", "resume", "trigger", "instruct"]
    agent_id: str = Field(min_length=1)
    tenant_id: str = Field(default="default", min_length=1)
    operator: str | None = None
    message: str = ""
    focus: str | None = None
    ttl_seconds: int = Field(default=3600, ge=1, le=86400)

    @field_validator("agent_id", "tenant_id", mode="before")
    @classmethod
    def _strip_required(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


def register_signal_admin_route(
    *,
    app_routes: list,
    router: SignalRouter,
    auth_provider: AuthProvider | None,
    require_auth: bool = True,
    path: str = "/admin/signal",
) -> None:
    """Register a Signal admin endpoint on an existing Starlette app."""

    async def endpoint(request: Request) -> JSONResponse:
        if require_auth:
            if auth_provider is None:
                return JSONResponse({"error": "unauthorized", "reason": "auth_provider_missing"}, status_code=401)
            auth_result = await auth_provider.authenticate(request)
            if not auth_result.ok:
                return JSONResponse({"error": "unauthorized", "reason": auth_result.reason or "auth_failed"}, status_code=401)
            auth_identity = auth_result.identity
        else:
            auth_identity = None

        try:
            payload = SignalAPIRequest.model_validate(await request.json())
        except Exception as exc:
            logger.debug("Signal API request validation failed: %s", exc)
            return JSONResponse({"error": "bad_request", "reason": "invalid_request"}, status_code=400)

        operator = (payload.operator or "").strip() or (auth_identity or "api")
        if payload.type == "instruct" and not payload.message.strip():
            return JSONResponse({"error": "bad_request", "reason": "message_required_for_instruct"}, status_code=400)
        tenant_id = payload.tenant_id
        if require_auth:
            tenant_header = request.headers.get("x-owlclaw-tenant", "").strip()
            if not tenant_header:
                return JSONResponse({"error": "unauthorized", "reason": "tenant_binding_required"}, status_code=403)
            if tenant_header != payload.tenant_id:
                return JSONResponse({"error": "unauthorized", "reason": "tenant_mismatch"}, status_code=403)
            tenant_id = tenant_header

        signal = Signal(
            type=SignalType(payload.type),
            source=SignalSource.API,
            agent_id=payload.agent_id,
            tenant_id=tenant_id,
            operator=operator,
            message=payload.message,
            focus=payload.focus,
            ttl_seconds=payload.ttl_seconds,
        )
        result = await router.dispatch(signal)
        status_code = 200 if result.error_code is None else 400
        if result.error_code == "unauthorized":
            status_code = 403

        return JSONResponse(
            {
                "status": result.status,
                "message": result.message,
                "run_id": result.run_id,
                "error_code": result.error_code,
            },
            status_code=status_code,
        )

    app_routes.append(Route(path, endpoint=endpoint, methods=["POST"]))
