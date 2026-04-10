"""Signal handlers for pause/resume/trigger/instruct."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from owlclaw.triggers.signal.models import PendingInstruction, Signal, SignalResult, SignalType
from owlclaw.triggers.signal.state import AgentStateManager


class AgentRuntimeProtocol(Protocol):
    async def trigger_event(
        self,
        event_name: str,
        payload: dict[str, Any],
        focus: str | None = None,
        tenant_id: str = "default",
    ) -> Any: ...


class GovernanceProtocol(Protocol):
    async def allow_trigger(self, event_name: str, tenant_id: str) -> bool: ...


class BaseSignalHandler:
    async def handle(self, signal: Signal) -> SignalResult:
        raise RuntimeError("signal handler not configured")


class PauseHandler(BaseSignalHandler):
    def __init__(
        self,
        state: AgentStateManager,
        on_pause: Callable[[Signal], Awaitable[None] | None] | None = None,
    ) -> None:
        self._state = state
        self._on_pause = on_pause

    async def handle(self, signal: Signal) -> SignalResult:
        current = await self._state.get(signal.agent_id, signal.tenant_id)
        if current.paused:
            return SignalResult(status="already_paused")
        await self._state.set_paused(signal.agent_id, signal.tenant_id, True)
        if self._on_pause is not None:
            maybe = self._on_pause(signal)
            if isinstance(maybe, Awaitable):
                await maybe
        return SignalResult(status="paused")


class ResumeHandler(BaseSignalHandler):
    def __init__(
        self,
        state: AgentStateManager,
        on_resume: Callable[[Signal], Awaitable[None] | None] | None = None,
    ) -> None:
        self._state = state
        self._on_resume = on_resume

    async def handle(self, signal: Signal) -> SignalResult:
        current = await self._state.get(signal.agent_id, signal.tenant_id)
        if not current.paused:
            return SignalResult(status="already_running")
        await self._state.set_paused(signal.agent_id, signal.tenant_id, False)
        if self._on_resume is not None:
            maybe = self._on_resume(signal)
            if isinstance(maybe, Awaitable):
                await maybe
        return SignalResult(status="resumed")


class TriggerHandler(BaseSignalHandler):
    def __init__(self, runtime: AgentRuntimeProtocol, governance: GovernanceProtocol | None = None) -> None:
        self._runtime = runtime
        self._governance = governance

    async def handle(self, signal: Signal) -> SignalResult:
        if self._governance is not None:
            allowed = await self._governance.allow_trigger("signal_manual", signal.tenant_id)
            if not allowed:
                return SignalResult(status="error", error_code="rate_limited", message="governance_blocked")
        result = await self._runtime.trigger_event(
            event_name="signal_manual",
            payload={"message": signal.message, "source": signal.source.value},
            focus=signal.focus,
            tenant_id=signal.tenant_id,
        )
        run_id = result.get("run_id") if isinstance(result, dict) else None
        return SignalResult(status="triggered", run_id=run_id)


class InstructHandler(BaseSignalHandler):
    def __init__(self, state: AgentStateManager, default_ttl_seconds: int = 3600) -> None:
        self._state = state
        self._default_ttl_seconds = default_ttl_seconds

    async def handle(self, signal: Signal) -> SignalResult:
        ttl_seconds = signal.ttl_seconds if signal.ttl_seconds > 0 else self._default_ttl_seconds
        instruction = PendingInstruction.create(
            content=signal.message,
            operator=signal.operator,
            source=signal.source,
            ttl_seconds=ttl_seconds,
        )
        await self._state.add_instruction(signal.agent_id, signal.tenant_id, instruction)
        return SignalResult(status="instruction_queued")


def default_handlers(
    *,
    state: AgentStateManager,
    runtime: AgentRuntimeProtocol,
    governance: GovernanceProtocol | None = None,
    on_pause: Callable[[Signal], Awaitable[None] | None] | None = None,
    on_resume: Callable[[Signal], Awaitable[None] | None] | None = None,
    default_ttl_seconds: int = 3600,
) -> dict[SignalType, Callable[[Signal], Awaitable[SignalResult]]]:
    pause = PauseHandler(state, on_pause=on_pause)
    resume = ResumeHandler(state, on_resume=on_resume)
    trigger = TriggerHandler(runtime, governance=governance)
    instruct = InstructHandler(state, default_ttl_seconds=default_ttl_seconds)
    return {
        SignalType.PAUSE: pause.handle,
        SignalType.RESUME: resume.handle,
        SignalType.TRIGGER: trigger.handle,
        SignalType.INSTRUCT: instruct.handle,
    }
