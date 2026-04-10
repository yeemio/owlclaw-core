"""Authentication and authorization helpers for OwlHub API."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel


@dataclass(frozen=True)
class Principal:
    """Authenticated principal."""

    user_id: str
    role: str
    auth_type: str  # bearer | api_key


class TokenRequest(BaseModel):
    """OAuth2 exchange request."""

    github_code: str
    role: str = "publisher"


class TokenResponse(BaseModel):
    """OAuth2 exchange response."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class ApiKeyCreateResponse(BaseModel):
    """API key creation response."""

    api_key: str
    user_id: str
    role: str


class AuthManager:
    """Manage OAuth code exchange, JWT session, and API key validation."""

    def __init__(
        self,
        *,
        secret: str | None = None,
        token_ttl_seconds: int = 3600,
        max_sessions: int = 5000,
        max_api_keys: int = 5000,
        max_rate_buckets: int = 10000,
    ) -> None:
        raw_secret = secret if secret is not None else os.getenv("OWLHUB_AUTH_SECRET", "owlhub-dev-secret")
        self.secret = raw_secret if raw_secret else "owlhub-dev-secret"
        self.token_ttl_seconds = token_ttl_seconds
        self.max_sessions = max(1, int(max_sessions))
        self.max_api_keys = max(1, int(max_api_keys))
        self.max_rate_buckets = max(1, int(max_rate_buckets))
        self.sessions: OrderedDict[str, float] = OrderedDict()
        self.api_keys: OrderedDict[str, tuple[str, str]] = OrderedDict()
        self.rate_limit_window_seconds = 60
        self.rate_limit_per_window = 120
        self.rate_bucket: OrderedDict[str, tuple[float, int]] = OrderedDict()

    def exchange_github_code(self, *, github_code: str, role: str) -> TokenResponse:
        """Exchange pseudo GitHub OAuth2 code into signed JWT token."""
        if not github_code.startswith("gho_") or len(github_code) < 8:
            raise HTTPException(status_code=401, detail="invalid github oauth code")
        user_id = f"github:{github_code[4:12]}"
        token = self.issue_jwt(user_id=user_id, role=role)
        return TokenResponse(access_token=token, expires_in=self.token_ttl_seconds)

    def issue_jwt(self, *, user_id: str, role: str) -> str:
        now = int(time.time())
        exp = now + self.token_ttl_seconds
        jti = str(uuid.uuid4())
        payload = {"sub": user_id, "role": role, "iat": now, "exp": exp, "jti": jti}
        token = _encode_jwt(payload, self.secret)
        self.sessions[jti] = float(exp)
        self._trim_sessions(now=time.time())
        return token

    def validate_jwt(self, token: str) -> Principal:
        payload = _decode_jwt(token, self.secret)
        exp = int(payload.get("exp", 0))
        if exp <= int(time.time()):
            raise HTTPException(status_code=401, detail="token expired")
        jti = str(payload.get("jti", ""))
        now = time.time()
        self._trim_sessions(now=now)
        if not jti or self.sessions.get(jti, 0) < now:
            raise HTTPException(status_code=401, detail="invalid session")
        return Principal(
            user_id=str(payload.get("sub", "")),
            role=str(payload.get("role", "publisher")),
            auth_type="bearer",
        )

    def create_api_key(self, *, user_id: str, role: str) -> str:
        raw = f"ok_{secrets.token_urlsafe(24)}"
        digest = _sha256(raw)
        self.api_keys[digest] = (user_id, role)
        self._trim_api_keys()
        return raw

    def validate_api_key(self, api_key: str) -> Principal:
        digest = _sha256(api_key)
        mapped = self.api_keys.get(digest)
        if not mapped:
            raise HTTPException(status_code=401, detail="invalid api key")
        user_id, role = mapped
        return Principal(user_id=user_id, role=role, auth_type="api_key")

    def authenticate(self, *, authorization: str | None, api_key: str | None) -> Principal:
        if authorization and authorization.lower().startswith("bearer "):
            return self.validate_jwt(authorization.split(" ", 1)[1].strip())
        if api_key:
            return self.validate_api_key(api_key)
        raise HTTPException(status_code=401, detail="missing credentials")

    def check_rate_limit(self, identity: str) -> None:
        now = time.time()
        start, count = self.rate_bucket.get(identity, (now, 0))
        if now - start >= self.rate_limit_window_seconds:
            start, count = now, 0
        count += 1
        self.rate_bucket[identity] = (start, count)
        self._trim_rate_bucket(now=now)
        if count > self.rate_limit_per_window:
            raise HTTPException(status_code=429, detail="rate limit exceeded")

    def enforce_request_rate_limit(self, request: Request) -> None:
        client = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if not client:
            client = request.client.host if request.client is not None else "unknown"
        self.check_rate_limit(f"ip:{client}")

    def _trim_sessions(self, *, now: float) -> None:
        expired = [session_id for session_id, expires_at in self.sessions.items() if expires_at < now]
        for session_id in expired:
            self.sessions.pop(session_id, None)
        while len(self.sessions) > self.max_sessions:
            self.sessions.popitem(last=False)

    def _trim_api_keys(self) -> None:
        while len(self.api_keys) > self.max_api_keys:
            self.api_keys.popitem(last=False)

    def _trim_rate_bucket(self, *, now: float) -> None:
        stale_cutoff = now - self.rate_limit_window_seconds
        stale = [identity for identity, (start, _count) in self.rate_bucket.items() if start < stale_cutoff]
        for identity in stale:
            self.rate_bucket.pop(identity, None)
        while len(self.rate_bucket) > self.max_rate_buckets:
            self.rate_bucket.popitem(last=False)


def create_auth_router(auth: AuthManager) -> APIRouter:
    """Create auth endpoints router."""
    router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

    @router.post("/token", response_model=TokenResponse)
    def exchange_token(request: TokenRequest) -> TokenResponse:
        return auth.exchange_github_code(github_code=request.github_code, role=request.role)

    @router.get("/me")
    def get_me(
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> dict[str, str]:
        principal = auth.authenticate(authorization=authorization, api_key=x_api_key)
        return {"user_id": principal.user_id, "role": principal.role, "auth_type": principal.auth_type}

    @router.post("/api-keys", response_model=ApiKeyCreateResponse)
    def create_key(
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> ApiKeyCreateResponse:
        principal = auth.authenticate(authorization=authorization, api_key=x_api_key)
        key = auth.create_api_key(user_id=principal.user_id, role=principal.role)
        return ApiKeyCreateResponse(api_key=key, user_id=principal.user_id, role=principal.role)

    return router


def enforce_write_auth(request: Request) -> None:
    """Middleware-compatible helper enforcing write auth and role checks."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    _enforce_csrf_for_form_request(request)
    path = request.url.path
    if path.startswith("/api/v1/auth") or path in {"/health"}:
        return
    auth: AuthManager = request.app.state.auth_manager
    principal = auth.authenticate(
        authorization=request.headers.get("Authorization"),
        api_key=request.headers.get("X-API-Key"),
    )
    auth.check_rate_limit(f"{principal.auth_type}:{principal.user_id}")

    # Basic role gate: only admin can mutate under /api/v1/admin
    if path.startswith("/api/v1/admin") and principal.role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")

    request.state.principal = principal


def get_current_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Principal:
    """Dependency to resolve principal for protected route handlers."""
    auth: AuthManager = request.app.state.auth_manager
    principal = auth.authenticate(authorization=authorization, api_key=x_api_key)
    auth.check_rate_limit(f"{principal.auth_type}:{principal.user_id}")
    return principal


def _encode_jwt(payload: dict[str, Any], secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode()
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    sig_b64 = _b64url_encode(sig)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def _decode_jwt(token: str, secret: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=401, detail="invalid token format")
    header_b64, payload_b64, sig_b64 = parts
    signing_input = f"{header_b64}.{payload_b64}".encode()
    expected_sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(_b64url_encode(expected_sig), sig_b64):
        raise HTTPException(status_code=401, detail="invalid token signature")
    try:
        payload_raw = _b64url_decode(payload_b64)
        payload = json.loads(payload_raw.decode("utf-8"))
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=401, detail="invalid token payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=401, detail="invalid token payload")
    return payload


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_FORM_CONTENT_TYPE_PATTERN = re.compile(r"^(application/x-www-form-urlencoded|multipart/form-data)", re.IGNORECASE)


def _enforce_csrf_for_form_request(request: Request) -> None:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    content_type = request.headers.get("Content-Type", "")
    if not _FORM_CONTENT_TYPE_PATTERN.match(content_type):
        return
    expected = os.getenv("OWLHUB_CSRF_TOKEN", "owlhub-csrf-token")
    provided = request.headers.get("X-CSRF-Token", "")
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="csrf token required")
