"""Risk assessor for progressive migration decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RiskBreakdown:
    """Risk scores per factor and final weighted risk in [0.0, 1.0]."""

    operation_type: float
    impact_scope: float
    amount: float
    reversibility: float
    total: float


class RiskAssessor:
    """Assess risk level for one action using weighted factors."""

    _OPERATION_BASE: dict[str, float] = {
        "read": 0.0,
        "notify": 0.2,
        "write": 0.5,
        "delete": 0.8,
        "payment": 1.0,
    }
    _SCOPE_BASE: dict[str, float] = {
        "single": 0.1,
        "batch": 0.5,
        "global": 1.0,
    }
    _REVERSIBILITY_BASE: dict[str, float] = {
        "reversible": 0.0,
        "partially_reversible": 0.5,
        "irreversible": 1.0,
    }

    def __init__(
        self,
        *,
        operation_weight: float = 0.3,
        scope_weight: float = 0.3,
        amount_weight: float = 0.2,
        reversibility_weight: float = 0.2,
    ) -> None:
        weights = [operation_weight, scope_weight, amount_weight, reversibility_weight]
        if any(w < 0 for w in weights):
            raise ValueError("risk weights must be non-negative")
        total = sum(weights)
        if total <= 0:
            raise ValueError("risk weights sum must be positive")
        self._w_op = operation_weight / total
        self._w_scope = scope_weight / total
        self._w_amount = amount_weight / total
        self._w_rev = reversibility_weight / total

    def infer_operation_type(self, action: dict[str, Any]) -> str:
        """Infer operation type from method/binding metadata."""
        binding = action.get("binding", {})
        if not isinstance(binding, dict):
            binding = {}
        method = str(binding.get("method", "")).upper()
        sql_op = str(binding.get("sql_operation", "")).upper()
        action_type = str(action.get("action_type", "")).lower()

        if action_type in self._OPERATION_BASE:
            return action_type
        if method == "GET":
            return "read"
        if method in {"POST", "PUT", "PATCH"}:
            return "write"
        if method == "DELETE":
            return "delete"
        if sql_op in {"SELECT"}:
            return "read"
        if sql_op in {"INSERT", "UPDATE"}:
            return "write"
        if sql_op == "DELETE":
            return "delete"
        return "write"

    def assess(self, action: dict[str, Any], skill_owlclaw: dict[str, Any] | None = None) -> RiskBreakdown:
        """Compute weighted risk score for a candidate action."""
        overrides = self.parse_skill_risk_overrides(skill_owlclaw or {})
        op_type = str(overrides.get("operation_type") or self.infer_operation_type(action))
        scope = str(overrides.get("impact_scope") or action.get("impact_scope", "single"))
        reversibility = str(overrides.get("reversibility") or action.get("reversibility", "reversible"))
        amount = float(overrides.get("amount", action.get("amount", 0.0)) or 0.0)

        op_score = self._OPERATION_BASE.get(op_type, 0.5)
        scope_score = self._SCOPE_BASE.get(scope, 0.5)
        rev_score = self._REVERSIBILITY_BASE.get(reversibility, 0.5)
        amount_score = self._amount_to_score(amount)

        total = (
            op_score * self._w_op
            + scope_score * self._w_scope
            + amount_score * self._w_amount
            + rev_score * self._w_rev
        )
        bounded = max(0.0, min(1.0, total))
        return RiskBreakdown(
            operation_type=op_score,
            impact_scope=scope_score,
            amount=amount_score,
            reversibility=rev_score,
            total=bounded,
        )

    @staticmethod
    def parse_skill_risk_overrides(skill_owlclaw: dict[str, Any]) -> dict[str, Any]:
        """Parse optional risk hints from SKILL.md owlclaw block."""
        if not isinstance(skill_owlclaw, dict):
            return {}
        raw = skill_owlclaw.get("risk")
        if not isinstance(raw, dict):
            return {}
        out: dict[str, Any] = {}
        for key in ("operation_type", "impact_scope", "reversibility"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                out[key] = value.strip().lower()
        amount = raw.get("amount")
        if isinstance(amount, int | float):
            out["amount"] = float(amount)
        return out

    @staticmethod
    def _amount_to_score(amount: float) -> float:
        value = max(0.0, float(amount))
        if value < 1000:
            return 0.1
        if value < 10000:
            return 0.5
        return 1.0
