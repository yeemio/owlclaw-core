"""Signal trigger package."""

from owlclaw.triggers.signal.api import SignalAPIRequest, register_signal_admin_route
from owlclaw.triggers.signal.config import SignalTriggerConfig
from owlclaw.triggers.signal.handlers import (
    BaseSignalHandler,
    InstructHandler,
    PauseHandler,
    ResumeHandler,
    TriggerHandler,
    default_handlers,
)
from owlclaw.triggers.signal.mcp import register_signal_mcp_tools
from owlclaw.triggers.signal.models import PendingInstruction, Signal, SignalResult, SignalSource, SignalType
from owlclaw.triggers.signal.persistence import AgentControlStateORM, PendingInstructionORM
from owlclaw.triggers.signal.router import SignalRouter
from owlclaw.triggers.signal.state import AgentState, AgentStateManager

__all__ = [
    "AgentState",
    "AgentStateManager",
    "AgentControlStateORM",
    "BaseSignalHandler",
    "InstructHandler",
    "PauseHandler",
    "PendingInstruction",
    "PendingInstructionORM",
    "ResumeHandler",
    "Signal",
    "SignalAPIRequest",
    "SignalResult",
    "SignalRouter",
    "SignalSource",
    "SignalTriggerConfig",
    "SignalType",
    "TriggerHandler",
    "default_handlers",
    "register_signal_mcp_tools",
    "register_signal_admin_route",
]
