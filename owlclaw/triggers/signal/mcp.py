"""MCP tool bindings for signal operations."""

from __future__ import annotations

from typing import Any

from owlclaw.capabilities.registry import CapabilityRegistry
from owlclaw.triggers.signal.models import Signal, SignalResult, SignalSource, SignalType
from owlclaw.triggers.signal.router import SignalRouter


def _result_dict(result: SignalResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "message": result.message,
        "run_id": result.run_id,
        "error_code": result.error_code,
    }


def register_signal_mcp_tools(
    *,
    registry: CapabilityRegistry,
    router: SignalRouter,
    default_tenant_id: str = "default",
    default_operator: str = "mcp",
) -> None:
    """Register MCP-facing signal tools into capability registry."""

    async def owlclaw_pause(agent_id: str, tenant_id: str = default_tenant_id, operator: str = default_operator) -> dict[str, Any]:
        result = await router.dispatch(
            Signal(
                type=SignalType.PAUSE,
                source=SignalSource.MCP,
                agent_id=agent_id,
                tenant_id=tenant_id,
                operator=operator,
            )
        )
        return _result_dict(result)

    async def owlclaw_resume(agent_id: str, tenant_id: str = default_tenant_id, operator: str = default_operator) -> dict[str, Any]:
        result = await router.dispatch(
            Signal(
                type=SignalType.RESUME,
                source=SignalSource.MCP,
                agent_id=agent_id,
                tenant_id=tenant_id,
                operator=operator,
            )
        )
        return _result_dict(result)

    async def owlclaw_trigger(
        agent_id: str,
        message: str = "",
        focus: str | None = None,
        tenant_id: str = default_tenant_id,
        operator: str = default_operator,
    ) -> dict[str, Any]:
        result = await router.dispatch(
            Signal(
                type=SignalType.TRIGGER,
                source=SignalSource.MCP,
                agent_id=agent_id,
                tenant_id=tenant_id,
                operator=operator,
                message=message,
                focus=focus,
            )
        )
        return _result_dict(result)

    async def owlclaw_instruct(
        agent_id: str,
        message: str,
        ttl_seconds: int = 3600,
        tenant_id: str = default_tenant_id,
        operator: str = default_operator,
    ) -> dict[str, Any]:
        if not message.strip():
            return {"status": "error", "message": "message_required", "run_id": None, "error_code": "bad_request"}
        result = await router.dispatch(
            Signal(
                type=SignalType.INSTRUCT,
                source=SignalSource.MCP,
                agent_id=agent_id,
                tenant_id=tenant_id,
                operator=operator,
                message=message,
                ttl_seconds=ttl_seconds,
            )
        )
        return _result_dict(result)

    registry.register_handler("owlclaw_pause", owlclaw_pause)
    registry.register_handler("owlclaw_resume", owlclaw_resume)
    registry.register_handler("owlclaw_trigger", owlclaw_trigger)
    registry.register_handler("owlclaw_instruct", owlclaw_instruct)
