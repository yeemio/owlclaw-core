"""Risk confirmation constraint for high-risk capabilities."""

from __future__ import annotations

from typing import Any

from owlclaw.governance.visibility import CapabilityView, FilterResult, RunContext


class RiskConfirmationConstraint:
    """Hide capabilities that require confirmation when not confirmed in context.

    Default policy:
    - If ``requires_confirmation`` is true, capability is hidden unless confirmed.
    - If not explicitly required, ``high``/``critical`` risk can still require
      confirmation when ``enforce_high_risk_confirmation`` is enabled.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.enforce_high_risk_confirmation = bool(
            cfg.get("enforce_high_risk_confirmation", True)
        )

    async def evaluate(
        self,
        capability: CapabilityView,
        agent_id: str,  # noqa: ARG002
        context: RunContext,
    ) -> FilterResult:
        risk_level = (capability.risk_level or "low").strip().lower()
        requires_confirmation = bool(capability.requires_confirmation)
        if (
            not requires_confirmation
            and self.enforce_high_risk_confirmation
            and risk_level in {"high", "critical"}
        ):
            requires_confirmation = True

        if not requires_confirmation:
            return FilterResult(visible=True)
        if context.is_confirmed(capability.name):
            return FilterResult(visible=True)
        return FilterResult(
            visible=False,
            reason="requires_confirmation",
        )

