"""Capability visibility filter and constraint evaluator protocol.

Filters capabilities before they are presented to the LLM, based on
constraints (budget, time, rate limit, circuit breaker). Evaluators
run in parallel; failures follow configurable fail-policy (open/close).
"""

import asyncio
import inspect
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from owlclaw.security.risk_gate import RiskDecision, RiskGate

logger = logging.getLogger(__name__)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return False


def _coerce_focus(value: Any) -> list[str]:
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else []
    if isinstance(value, list | tuple | set):
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                continue
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
        return out
    return []


def _coerce_risk_level(value: Any) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"low", "medium", "high", "critical"}:
            return normalized
    return "low"


@dataclass
class FilterResult:
    """Result of a single constraint evaluation."""

    visible: bool
    reason: str = ""


@dataclass
class RunContext:
    """Context passed to constraint evaluators (tenant, optional extras)."""

    tenant_id: str
    confirmed_capabilities: set[str] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string")
        self.tenant_id = self.tenant_id.strip()
        if self.confirmed_capabilities is None:
            return
        self.confirmed_capabilities = {
            str(name).strip()
            for name in self.confirmed_capabilities
            if str(name).strip()
        } or None

    def is_confirmed(self, capability_name: str) -> bool:
        if not self.confirmed_capabilities:
            return False
        if not isinstance(capability_name, str):
            return False
        normalized = capability_name.strip()
        if not normalized:
            return False
        return normalized in self.confirmed_capabilities


class CapabilityView:
    """Read-only view of a capability for constraint evaluation.

    Matches the shape produced by CapabilityRegistry.list_capabilities()
    and Skill metadata (name, task_type, constraints from owlclaw_config).
    """

    def __init__(
        self,
        name: str,
        description: str = "",
        task_type: str | None = None,
        constraints: dict[str, Any] | None = None,
        focus: list[str] | None = None,
        risk_level: str | None = None,
        requires_confirmation: bool | None = None,
    ):
        self.name = name
        self.description = description
        self.task_type = task_type or ""
        self.constraints = constraints or {}
        self.focus = _coerce_focus(focus)
        self.risk_level = _coerce_risk_level(risk_level)
        self.requires_confirmation = _coerce_bool(requires_confirmation)

    @property
    def metadata(self) -> dict[str, Any]:
        """OwlClaw metadata for constraints (design: capability.metadata.get('owlclaw', {}).get('constraints')."""
        return {
            "owlclaw": {
                "constraints": self.constraints,
                "task_type": self.task_type,
                "focus": self.focus,
                "risk_level": self.risk_level,
                "requires_confirmation": self.requires_confirmation,
            }
        }


class ConstraintEvaluator(Protocol):
    """Protocol for constraint evaluators used by VisibilityFilter."""

    async def evaluate(
        self,
        capability: CapabilityView,
        agent_id: str,
        context: RunContext,
    ) -> FilterResult:
        """Evaluate whether the capability should be visible. Must not raise."""
        ...


class VisibilityFilter:
    """Filters capabilities using registered constraint evaluators.

    All evaluators must return visible=True for a capability to be shown.
    Evaluators are run in parallel per capability; evaluator failures follow
    configured fail-policy (`open` keeps visible, `close` hides capability).
    Default policy is `close` for secure-by-default behavior.

    Args:
        fail_policy: Either 'open' or 'close'. Default 'close'.
        evaluator_timeout_seconds: Timeout for each evaluator execution.
            Default None (no timeout). Set to avoid single evaluator blocking.
    """

    def __init__(
        self,
        *,
        fail_policy: str = "close",
        evaluator_timeout_seconds: float | None = 5.0,
    ) -> None:
        normalized_policy = fail_policy.strip().lower()
        if normalized_policy not in {"open", "close"}:
            raise ValueError("fail_policy must be either 'open' or 'close'")
        self._fail_policy = normalized_policy
        self._evaluator_timeout_seconds = evaluator_timeout_seconds
        self._evaluators: list[ConstraintEvaluator] = []
        self._risk_gate = RiskGate()
        self._inject_quality_score = False
        self._quality_cache: dict[str, float] = {}
        # Per-evaluator timeout to avoid a single slow evaluator blocking capability filtering.
        # None or <=0 disables timeout.
        self._evaluator_timeout_seconds: float | None = (
            float(evaluator_timeout_seconds) if evaluator_timeout_seconds is not None and evaluator_timeout_seconds > 0
            else None
        )

    def register_evaluator(self, evaluator: ConstraintEvaluator) -> None:
        """Register a constraint evaluator."""
        if not hasattr(evaluator, "evaluate") or not callable(evaluator.evaluate):
            raise TypeError("evaluator must provide an evaluate(capability, agent_id, context) method")
        self._evaluators.append(evaluator)

    def configure_quality_score_injection(self, *, enabled: bool, quality_scores: dict[str, float] | None = None) -> None:
        """Enable/disable quality score hints in capability descriptions."""
        self._inject_quality_score = bool(enabled)
        if isinstance(quality_scores, dict):
            self._quality_cache = {
                str(name).strip(): float(score)
                for name, score in quality_scores.items()
                if str(name).strip()
            }

    async def filter_capabilities(
        self,
        capabilities: list[CapabilityView],
        agent_id: str,
        context: RunContext,
    ) -> list[CapabilityView]:
        """Return only capabilities that pass all evaluators.

        For each capability, evaluators run in parallel. Evaluator failures
        are handled by configured fail-policy.
        """
        valid_capabilities: list[CapabilityView] = []
        for cap in capabilities:
            if isinstance(cap, CapabilityView):
                valid_capabilities.append(cap)
            else:
                logger.warning("Skipping invalid capability entry of type %s", type(cap).__name__)

        filtered: list[CapabilityView] = []
        for cap in valid_capabilities:
            risk_block_reason = self._evaluate_risk_gate(cap, agent_id, context)
            if risk_block_reason is not None:
                logger.info("Capability %s filtered: %s", cap.name, risk_block_reason)
                continue
            results = await asyncio.gather(
                *(
                    self._safe_evaluate(eval_fn, cap, agent_id, context)
                    for eval_fn in self._evaluators
                )
            )
            reasons: list[str] = []
            for r in results:
                if not r.visible:
                    reasons.append(r.reason)
            if reasons:
                logger.info(
                    "Capability %s filtered: %s",
                    cap.name,
                    "; ".join(reasons),
                )
                continue
            if self._inject_quality_score:
                self._inject_quality_hint(cap)
            filtered.append(cap)
        return filtered

    def _inject_quality_hint(self, capability: CapabilityView) -> None:
        score = self._quality_cache.get(capability.name)
        if score is None:
            return
        hint = f"[quality_score={score:.3f}]"
        if hint in capability.description:
            return
        if capability.description:
            capability.description = f"{capability.description} {hint}"
        else:
            capability.description = hint

    def _evaluate_risk_gate(
        self,
        capability: CapabilityView,
        agent_id: str,  # noqa: ARG002
        context: RunContext,
    ) -> str | None:
        """Run security risk gate before constraint evaluators."""
        if context.is_confirmed(capability.name):
            return None
        decision, _ = self._risk_gate.evaluate(
            capability.name,
            risk_level=capability.risk_level,
            requires_confirmation=capability.requires_confirmation,
        )
        if decision == RiskDecision.EXECUTE:
            return None
        if decision == RiskDecision.PAUSE:
            return "requires_confirmation"
        return "risk_rejected"

    async def _safe_evaluate(
        self,
        evaluator: ConstraintEvaluator,
        capability: CapabilityView,
        agent_id: str,
        context: RunContext,
    ) -> FilterResult:
        """Run one evaluator and apply fail-policy on errors.

        If evaluator_timeout_seconds is configured, the evaluator execution
        is wrapped with asyncio.timeout to prevent single evaluator from
        blocking capability filtering indefinitely.
        """
        try:
            if self._evaluator_timeout_seconds is not None:
                async with asyncio.timeout(self._evaluator_timeout_seconds):
                    raw_result: Any = evaluator.evaluate(capability, agent_id, context)
                    if inspect.isawaitable(raw_result):
                        raw_result = await raw_result
            else:
                raw_result = evaluator.evaluate(capability, agent_id, context)
                if inspect.isawaitable(raw_result):
                    raw_result = await raw_result
            if isinstance(raw_result, FilterResult):
                return raw_result
            logger.warning(
                "Evaluator %s returned invalid result type %s (fail-%s)",
                type(evaluator).__name__,
                type(raw_result).__name__,
                self._fail_policy,
            )
            return self._handle_evaluator_failure()
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.warning(
                "Evaluator %s timed out after %.1fs (fail-%s)",
                type(evaluator).__name__,
                self._evaluator_timeout_seconds or 0,
                self._fail_policy,
            )
            return self._handle_evaluator_failure()
        except Exception as e:
            logger.warning(
                "Evaluator %s raised (fail-%s): %s",
                type(evaluator).__name__,
                self._fail_policy,
                e,
            )
            return self._handle_evaluator_failure()

    def _handle_evaluator_failure(self) -> FilterResult:
        if self._fail_policy == "close":
            return FilterResult(visible=False, reason="constraint_evaluator_error")
        return FilterResult(visible=True)
