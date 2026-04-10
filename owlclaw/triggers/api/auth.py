"""Authentication providers for API call trigger."""

from __future__ import annotations

import hashlib
import hmac
from abc import ABC, abstractmethod
from dataclasses import dataclass

from starlette.requests import Request


@dataclass(slots=True)
class AuthResult:
    """Authentication result payload."""

    ok: bool
    identity: str | None = None
    reason: str | None = None


class AuthProvider(ABC):
    """Authentication provider contract."""

    @abstractmethod
    async def authenticate(self, request: Request) -> AuthResult: ...


def _constant_time_equals(a: str, b: str) -> bool:
    """Compare two strings in constant time to avoid timing side-channels."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


class APIKeyAuthProvider(AuthProvider):
    """Validate request via X-API-Key header."""

    def __init__(self, valid_keys: set[str]) -> None:
        self._valid_keys = [key.strip() for key in valid_keys if key and key.strip()]

    async def authenticate(self, request: Request) -> AuthResult:
        key = request.headers.get("X-API-Key", "").strip()
        if not key:
            return AuthResult(ok=False, reason="missing_api_key")
        if not any(_constant_time_equals(key, k) for k in self._valid_keys):
            return AuthResult(ok=False, reason="invalid_api_key")
        opaque = hashlib.sha256(key.encode()).hexdigest()[:16]
        return AuthResult(ok=True, identity=f"api_key:{opaque}")


class BearerTokenAuthProvider(AuthProvider):
    """Validate bearer token via Authorization header."""

    def __init__(self, valid_tokens: set[str]) -> None:
        self._valid_tokens = [token.strip() for token in valid_tokens if token and token.strip()]

    async def authenticate(self, request: Request) -> AuthResult:
        raw = request.headers.get("Authorization", "")
        if not raw.startswith("Bearer "):
            return AuthResult(ok=False, reason="missing_bearer")
        token = raw[len("Bearer ") :].strip()
        if not any(_constant_time_equals(token, t) for t in self._valid_tokens):
            return AuthResult(ok=False, reason="invalid_bearer")
        return AuthResult(ok=True, identity="bearer")
