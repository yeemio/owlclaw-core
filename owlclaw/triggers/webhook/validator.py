"""Webhook request validation service."""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Protocol

from owlclaw.triggers.webhook.types import (
    HttpRequest,
    ValidationError,
    ValidationResult,
    WebhookEndpoint,
)


class EndpointReaderProtocol(Protocol):
    """Read-only protocol for endpoint retrieval."""

    async def get_endpoint(self, endpoint_id: str) -> WebhookEndpoint | None: ...


class RequestValidator:
    """Validate endpoint existence, auth headers, signature, and request format."""

    def __init__(self, endpoint_reader: EndpointReaderProtocol) -> None:
        self._endpoint_reader = endpoint_reader

    async def validate_endpoint(self, endpoint_id: str) -> tuple[WebhookEndpoint | None, ValidationResult]:
        endpoint = await self._endpoint_reader.get_endpoint(endpoint_id)
        if endpoint is None or not endpoint.config.enabled:
            return None, ValidationResult(
                valid=False,
                error=ValidationError(
                    code="ENDPOINT_NOT_FOUND",
                    message="endpoint not found",
                    status_code=404,
                ),
            )
        return endpoint, ValidationResult(valid=True)

    def validate_auth(self, request: HttpRequest, endpoint: WebhookEndpoint) -> ValidationResult:
        auth_method = endpoint.config.auth_method
        headers = _normalize_headers(request.headers)
        authorization = headers.get("authorization", "")
        if auth_method.type == "bearer":
            return _validate_bearer_hash(authorization, endpoint.auth_token_hash)
        if auth_method.type == "basic":
            return _validate_basic(authorization, auth_method.username, auth_method.password)
        if auth_method.type == "hmac":
            # HMAC authorization is validated via signature check.
            return ValidationResult(valid=True)
        return ValidationResult(
            valid=False,
            error=ValidationError(
                code="INVALID_AUTH_METHOD",
                message="unsupported auth method",
                status_code=401,
            ),
        )

    def validate_signature(self, request: HttpRequest, endpoint: WebhookEndpoint) -> ValidationResult:
        auth_method = endpoint.config.auth_method
        if auth_method.type != "hmac":
            return ValidationResult(valid=True)
        if not auth_method.secret or auth_method.algorithm not in {"sha256", "sha512"}:
            return ValidationResult(
                valid=False,
                error=ValidationError(
                    code="SIGNATURE_CONFIG_ERROR",
                    message="hmac secret and algorithm are required",
                    status_code=403,
                ),
            )
        headers = _normalize_headers(request.headers)
        signature_header = headers.get("x-signature")
        if not signature_header:
            return ValidationResult(
                valid=False,
                error=ValidationError(
                    code="MISSING_SIGNATURE",
                    message="x-signature header is required",
                    status_code=403,
                ),
            )
        expected = hmac.new(
            auth_method.secret.encode("utf-8"),
            request.body.encode("utf-8"),
            hashlib.sha256 if auth_method.algorithm == "sha256" else hashlib.sha512,
        ).hexdigest()
        normalized = _normalize_signature(signature_header, auth_method.algorithm)
        if normalized is None or not hmac.compare_digest(expected, normalized):
            return ValidationResult(
                valid=False,
                error=ValidationError(
                    code="INVALID_SIGNATURE",
                    message="signature verification failed",
                    status_code=403,
                ),
            )
        return ValidationResult(valid=True)

    def validate_format(self, request: HttpRequest) -> ValidationResult:
        headers = _normalize_headers(request.headers)
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if not content_type:
            return ValidationResult(
                valid=False,
                error=ValidationError(
                    code="INVALID_FORMAT",
                    message="content-type header is required",
                    status_code=400,
                ),
            )
        supported = {
            "application/json",
            "application/xml",
            "text/xml",
            "application/x-www-form-urlencoded",
        }
        if content_type not in supported:
            return ValidationResult(
                valid=False,
                error=ValidationError(
                    code="INVALID_FORMAT",
                    message="unsupported content type",
                    status_code=400,
                    details={"content_type": content_type},
                ),
            )
        return ValidationResult(valid=True)

    async def validate_request(self, endpoint_id: str, request: HttpRequest) -> tuple[WebhookEndpoint | None, ValidationResult]:
        endpoint, endpoint_result = await self.validate_endpoint(endpoint_id)
        if not endpoint_result.valid:
            return None, endpoint_result
        assert endpoint is not None
        auth_result = self.validate_auth(request, endpoint)
        if not auth_result.valid:
            return None, auth_result
        signature_result = self.validate_signature(request, endpoint)
        if not signature_result.valid:
            return None, signature_result
        format_result = self.validate_format(request)
        if not format_result.valid:
            return None, format_result
        return endpoint, ValidationResult(valid=True)


def _normalize_headers(headers: dict[str, str]) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items()}


def _validate_bearer_hash(authorization: str, expected_token_hash: str) -> ValidationResult:
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return ValidationResult(
            valid=False,
            error=ValidationError(
                code="INVALID_TOKEN",
                message="missing bearer token",
                status_code=401,
            ),
        )
    provided_token = authorization[len(prefix) :].strip()
    if not provided_token:
        return ValidationResult(
            valid=False,
            error=ValidationError(
                code="INVALID_TOKEN",
                message="invalid bearer token",
                status_code=401,
            ),
        )
    provided_hash = hashlib.sha256(provided_token.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(provided_hash, expected_token_hash):
        return ValidationResult(
            valid=False,
            error=ValidationError(
                code="INVALID_TOKEN",
                message="invalid bearer token",
                status_code=401,
            ),
        )
    return ValidationResult(valid=True)


def _validate_basic(authorization: str, username: str | None, password: str | None) -> ValidationResult:
    if not username or not password:
        return ValidationResult(
            valid=False,
            error=ValidationError(
                code="INVALID_TOKEN",
                message="basic auth credential is not configured",
                status_code=401,
            ),
        )
    prefix = "Basic "
    if not authorization.startswith(prefix):
        return ValidationResult(
            valid=False,
            error=ValidationError(
                code="INVALID_TOKEN",
                message="missing basic auth token",
                status_code=401,
            ),
        )
    encoded = authorization[len(prefix) :].strip()
    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except Exception:
        return ValidationResult(
            valid=False,
            error=ValidationError(
                code="INVALID_TOKEN",
                message="invalid basic auth token",
                status_code=401,
            ),
        )
    if ":" not in decoded:
        return ValidationResult(
            valid=False,
            error=ValidationError(
                code="INVALID_TOKEN",
                message="invalid basic auth token",
                status_code=401,
            ),
        )
    provided_username, provided_password = decoded.split(":", 1)
    if not (
        hmac.compare_digest(provided_username, username)
        and hmac.compare_digest(provided_password, password)
    ):
        return ValidationResult(
            valid=False,
            error=ValidationError(
                code="INVALID_TOKEN",
                message="invalid basic auth token",
                status_code=401,
            ),
        )
    return ValidationResult(valid=True)


def _normalize_signature(signature_header: str, algorithm: str) -> str | None:
    lower = signature_header.strip().lower()
    prefix = f"{algorithm}="
    if lower.startswith(prefix):
        return lower[len(prefix) :]
    if lower.startswith("sha256=") or lower.startswith("sha512="):
        return None
    return lower
