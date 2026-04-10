"""Webhook endpoint management service."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from secrets import token_urlsafe
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from owlclaw.triggers.webhook.persistence.models import WebhookEndpointModel
from owlclaw.triggers.webhook.types import (
    AuthMethod,
    AuthMethodType,
    EndpointConfig,
    EndpointFilter,
    ExecutionMode,
    RetryPolicy,
    ValidationError,
    ValidationResult,
    WebhookEndpoint,
)


class EndpointRepositoryProtocol(Protocol):
    """Repository protocol used by WebhookEndpointManager."""

    async def create(self, endpoint: WebhookEndpointModel) -> WebhookEndpointModel: ...

    async def get(self, endpoint_id: UUID) -> WebhookEndpointModel | None: ...

    async def list(self, *, tenant_id: str, enabled: bool | None = None) -> list[WebhookEndpointModel]: ...

    async def update(self, endpoint: WebhookEndpointModel) -> WebhookEndpointModel: ...

    async def delete(self, endpoint_id: UUID) -> None: ...


class WebhookEndpointManager:
    """Create and maintain webhook endpoints with validation."""

    def __init__(
        self,
        repository: EndpointRepositoryProtocol,
        *,
        base_url: str = "/webhooks",
        token_bytes: int = 24,
    ) -> None:
        self._repository = repository
        self._base_url = base_url.rstrip("/")
        self._token_bytes = token_bytes

    async def create_endpoint(self, config: EndpointConfig, *, tenant_id: str = "default") -> WebhookEndpoint:
        validation = self.validate_endpoint(config)
        if not validation.valid:
            assert validation.error is not None
            raise ValueError(validation.error.message)

        endpoint_id = uuid4()
        now = datetime.now(timezone.utc)
        raw_auth_token = token_urlsafe(self._token_bytes)
        auth_token_hash = _hash_auth_token(raw_auth_token)
        if config.auth_method.type == "bearer" and config.auth_method.token:
            raw_auth_token = config.auth_method.token.strip()
            auth_token_hash = _hash_auth_token(raw_auth_token)
        model = WebhookEndpointModel(
            id=endpoint_id,
            tenant_id=tenant_id,
            name=config.name,
            url=f"{self._base_url}/{endpoint_id}",
            auth_token_hash=auth_token_hash,
            target_agent_id=config.target_agent_id,
            auth_method=_sanitize_auth_method(config.auth_method),
            transformation_rule_id=(UUID(config.transformation_rule_id) if config.transformation_rule_id else None),
            execution_mode=config.execution_mode,
            timeout=(None if config.timeout_seconds is None else int(config.timeout_seconds)),
            retry_policy=(
                None
                if config.retry_policy is None
                else {
                    "max_attempts": config.retry_policy.max_attempts,
                    "initial_delay_ms": config.retry_policy.initial_delay_ms,
                    "max_delay_ms": config.retry_policy.max_delay_ms,
                    "backoff_multiplier": config.retry_policy.backoff_multiplier,
                }
            ),
            enabled=config.enabled,
            created_at=now,
            updated_at=now,
        )
        created = await self._repository.create(model)
        return self._to_endpoint(created, auth_token=raw_auth_token)

    async def get_endpoint(self, endpoint_id: str) -> WebhookEndpoint | None:
        model = await self._repository.get(UUID(endpoint_id))
        return None if model is None else self._to_endpoint(model)

    async def update_endpoint(self, endpoint_id: str, config: EndpointConfig) -> WebhookEndpoint:
        validation = self.validate_endpoint(config)
        if not validation.valid:
            assert validation.error is not None
            raise ValueError(validation.error.message)
        model = await self._repository.get(UUID(endpoint_id))
        if model is None:
            raise KeyError(f"Endpoint not found: {endpoint_id}")
        now = datetime.now(timezone.utc)
        model.name = config.name
        model.target_agent_id = config.target_agent_id
        model.execution_mode = config.execution_mode
        model.timeout = None if config.timeout_seconds is None else int(config.timeout_seconds)
        model.enabled = config.enabled
        model.auth_method = _sanitize_auth_method(config.auth_method)
        if config.auth_method.type == "bearer" and config.auth_method.token:
            model.auth_token_hash = _hash_auth_token(config.auth_method.token.strip())
        model.transformation_rule_id = UUID(config.transformation_rule_id) if config.transformation_rule_id else None
        model.retry_policy = (
            None
            if config.retry_policy is None
            else {
                "max_attempts": config.retry_policy.max_attempts,
                "initial_delay_ms": config.retry_policy.initial_delay_ms,
                "max_delay_ms": config.retry_policy.max_delay_ms,
                "backoff_multiplier": config.retry_policy.backoff_multiplier,
            }
        )
        model.updated_at = now
        updated = await self._repository.update(model)
        return self._to_endpoint(updated)

    async def delete_endpoint(self, endpoint_id: str) -> None:
        await self._repository.delete(UUID(endpoint_id))

    async def list_endpoints(self, endpoint_filter: EndpointFilter | None = None) -> list[WebhookEndpoint]:
        filter_obj = endpoint_filter if endpoint_filter is not None else EndpointFilter()
        items = await self._repository.list(tenant_id=filter_obj.tenant_id, enabled=filter_obj.enabled)
        endpoints = [self._to_endpoint(item) for item in items]
        if filter_obj.target_agent_id is None:
            return endpoints
        return [item for item in endpoints if item.config.target_agent_id == filter_obj.target_agent_id]

    def validate_endpoint(self, config: EndpointConfig) -> ValidationResult:
        if not config.name.strip():
            return ValidationResult(valid=False, error=ValidationError(code="INVALID_CONFIG", message="name is required"))
        if not config.target_agent_id.strip():
            return ValidationResult(
                valid=False, error=ValidationError(code="INVALID_CONFIG", message="target_agent_id is required")
            )
        auth = config.auth_method
        if auth.type == "bearer" and not (auth.token and auth.token.strip()):
            return ValidationResult(
                valid=False, error=ValidationError(code="INVALID_CONFIG", message="bearer token is required")
            )
        if auth.type == "hmac" and (not (auth.secret and auth.secret.strip()) or auth.algorithm not in {"sha256", "sha512"}):
            return ValidationResult(
                valid=False, error=ValidationError(code="INVALID_CONFIG", message="hmac secret and algorithm are required")
            )
        if auth.type == "basic" and (
            not (auth.username and auth.username.strip()) or not (auth.password and auth.password.strip())
        ):
            return ValidationResult(
                valid=False, error=ValidationError(code="INVALID_CONFIG", message="basic auth username/password required")
            )
        if config.timeout_seconds is not None and config.timeout_seconds <= 0:
            return ValidationResult(
                valid=False, error=ValidationError(code="INVALID_CONFIG", message="timeout_seconds must be positive")
            )
        retry = config.retry_policy
        if retry is not None:
            if retry.max_attempts <= 0:
                return ValidationResult(
                    valid=False, error=ValidationError(code="INVALID_CONFIG", message="retry max_attempts must be positive")
                )
            if retry.initial_delay_ms < 0 or retry.max_delay_ms < 0:
                return ValidationResult(
                    valid=False, error=ValidationError(code="INVALID_CONFIG", message="retry delays must be non-negative")
                )
            if retry.backoff_multiplier < 1:
                return ValidationResult(
                    valid=False, error=ValidationError(code="INVALID_CONFIG", message="backoff_multiplier must be >= 1")
                )
        return ValidationResult(valid=True)

    @staticmethod
    def _to_endpoint(model: WebhookEndpointModel, *, auth_token: str = "") -> WebhookEndpoint:
        auth_method = model.auth_method or {}
        retry_policy = model.retry_policy or None
        auth = AuthMethod(
            type=_normalize_auth_method_type(auth_method.get("type", "bearer")),
            token=auth_method.get("token"),
            secret=auth_method.get("secret"),
            algorithm=auth_method.get("algorithm"),
            username=auth_method.get("username"),
            password=auth_method.get("password"),
        )
        config = EndpointConfig(
            name=model.name,
            target_agent_id=model.target_agent_id,
            auth_method=auth,
            transformation_rule_id=(None if model.transformation_rule_id is None else str(model.transformation_rule_id)),
            execution_mode=_normalize_execution_mode(model.execution_mode),
            timeout_seconds=(None if model.timeout is None else float(model.timeout)),
            retry_policy=(
                None
                if retry_policy is None
                else RetryPolicy(
                    max_attempts=int(retry_policy.get("max_attempts", 3)),
                    initial_delay_ms=int(retry_policy.get("initial_delay_ms", 1000)),
                    max_delay_ms=int(retry_policy.get("max_delay_ms", 30000)),
                    backoff_multiplier=float(retry_policy.get("backoff_multiplier", 2.0)),
                )
            ),
            enabled=model.enabled,
        )
        return WebhookEndpoint(
            id=str(model.id),
            url=model.url,
            auth_token=auth_token,
            auth_token_hash=model.auth_token_hash,
            config=config,
            created_at=model.created_at,
            updated_at=model.updated_at,
            tenant_id=model.tenant_id,
        )


def _hash_auth_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _sanitize_auth_method(auth_method: AuthMethod) -> dict[str, Any]:
    if auth_method.type == "bearer":
        return {"type": auth_method.type}
    if auth_method.type == "hmac":
        return {
            "type": auth_method.type,
            "secret": auth_method.secret,
            "algorithm": auth_method.algorithm,
        }
    return {
        "type": auth_method.type,
        "username": auth_method.username,
        "password": auth_method.password,
    }


def _normalize_auth_method_type(value: object) -> AuthMethodType:
    normalized = str(value)
    if normalized not in {"bearer", "hmac", "basic"}:
        return "bearer"
    return cast(AuthMethodType, normalized)


def _normalize_execution_mode(value: Any) -> ExecutionMode:
    normalized = str(value)
    if normalized not in {"sync", "async"}:
        return "async"
    return cast(ExecutionMode, normalized)
