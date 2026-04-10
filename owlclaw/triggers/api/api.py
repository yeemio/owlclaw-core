"""Public registration APIs for api-call triggers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

from owlclaw.triggers.api.config import APITriggerConfig


@dataclass(slots=True)
class APITriggerRegistration:
    """Normalized registration payload for app.trigger(api_call(...))."""

    config: APITriggerConfig


def api_call(
    *,
    path: str,
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST",
    event_name: str,
    tenant_id: str = "default",
    response_mode: Literal["sync", "async"] = "async",
    sync_timeout_seconds: int = 60,
    focus: str | None = None,
    auth_required: bool = True,
    description: str | None = None,
    **_: Any,
) -> APITriggerRegistration:
    """Create an api-call trigger registration payload."""
    return APITriggerRegistration(
        config=APITriggerConfig(
            path=path,
            method=cast(Literal["GET", "POST", "PUT", "PATCH", "DELETE"], method.upper()),
            event_name=event_name,
            tenant_id=tenant_id,
            response_mode=response_mode,
            sync_timeout_seconds=sync_timeout_seconds,
            focus=focus,
            auth_required=auth_required,
            description=description,
        )
    )
