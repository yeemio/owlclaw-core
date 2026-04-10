"""Request handlers for API trigger server."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request


class InvalidJSONPayloadError(ValueError):
    """Raised when request body is not valid JSON."""


class BodyTooLargeError(ValueError):
    """Raised when request body exceeds the configured size limit."""


@dataclass(slots=True)
class APITriggerRequest:
    """Normalized request payload consumed by trigger runtime."""

    body: dict[str, Any]
    query: dict[str, str]
    path_params: dict[str, str]


async def _read_body_with_limit(request: Request, max_bytes: int) -> bytes:
    """Read request body up to max_bytes; raise BodyTooLargeError if larger."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise BodyTooLargeError("Request body exceeds size limit")
        chunks.append(chunk)
    return b"".join(chunks)


async def parse_request_payload(request: Request) -> APITriggerRequest:
    """Parse request body/query/path into normalized payload."""
    body: dict[str, Any] = {}
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        try:
            parsed = await request.json()
            body = parsed if isinstance(parsed, dict) else {"value": parsed}
        except Exception as exc:
            raise InvalidJSONPayloadError("Invalid JSON payload") from exc
    query = {key: value for key, value in request.query_params.items()}
    path_params = {str(k): str(v) for k, v in request.path_params.items()}
    return APITriggerRequest(body=body, query=query, path_params=path_params)


async def parse_request_payload_with_limit(
    request: Request,
    max_body_bytes: int,
) -> APITriggerRequest:
    """Parse request with hard limit on body size (by actual bytes read, not Content-Length)."""
    body: dict[str, Any] = {}
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        raw = await _read_body_with_limit(request, max_body_bytes)
        try:
            parsed = json.loads(raw.decode("utf-8"))
            body = parsed if isinstance(parsed, dict) else {"value": parsed}
        except (ValueError, UnicodeDecodeError) as exc:
            raise InvalidJSONPayloadError("Invalid JSON payload") from exc
    query = {key: value for key, value in request.query_params.items()}
    path_params = {str(k): str(v) for k, v in request.path_params.items()}
    return APITriggerRequest(body=body, query=query, path_params=path_params)
