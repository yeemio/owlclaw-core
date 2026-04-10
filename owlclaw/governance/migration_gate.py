"""Migration gate for progressive migration (migration_weight)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from collections.abc import Callable
from typing import Any

from owlclaw.config.loader import YAMLConfigLoader
from owlclaw.governance.risk_assessor import RiskAssessor


class MigrationDecision(str, Enum):
    """Decision result for one action."""

    OBSERVE_ONLY = "observe_only"
    AUTO_EXECUTE = "auto_execute"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class MigrationOutcome:
    """Migration gate evaluation outcome."""

    decision: MigrationDecision
    migration_weight: int
    risk_level: float
    execution_probability: float


class MigrationGate:
    """Evaluate whether one action should execute, observe, or require approval."""

    def __init__(
        self,
        *,
        skill_weights: dict[str, int] | None = None,
        risk_assessor: RiskAssessor | None = None,
        random_fn: Callable[[], float] | None = None,
        config_path: str | Path | None = None,
        auto_reload: bool = True,
    ) -> None:
        self._weights: dict[str, int] = {}
        for name, value in (skill_weights or {}).items():
            self._weights[str(name).strip()] = self._normalize_weight(value)
        self._risk_assessor = risk_assessor or RiskAssessor()
        self._random_fn = random_fn or __import__("random").random
        self._config_path = Path(config_path) if config_path is not None else YAMLConfigLoader.resolve_path()
        self._auto_reload = auto_reload
        self._config_mtime_ns: int | None = None
        self.refresh_from_config()

    def set_weight(self, skill_name: str, weight: int) -> None:
        """Set one skill weight at runtime."""
        normalized_name = skill_name.strip()
        if not normalized_name:
            raise ValueError("skill_name must be a non-empty string")
        self._weights[normalized_name] = self._normalize_weight(weight)

    def get_weight(self, skill_name: str, skill_owlclaw: dict[str, Any] | None = None) -> int:
        """Resolve weight from SKILL override first, then runtime config map."""
        if self._auto_reload:
            self._refresh_if_config_changed()
        inline = self._extract_weight_from_skill(skill_owlclaw or {})
        if inline is not None:
            return inline
        return self._weights.get(skill_name.strip(), 100)

    def evaluate(
        self,
        *,
        skill_name: str,
        action: dict[str, Any],
        skill_owlclaw: dict[str, Any] | None = None,
    ) -> MigrationOutcome:
        """Evaluate one action and return migration decision."""
        weight = self.get_weight(skill_name, skill_owlclaw)
        risk = self._risk_assessor.assess(action, skill_owlclaw).total
        probability = max(0.0, min(1.0, (weight / 100.0) * (1.0 - risk)))

        if weight <= 0:
            return MigrationOutcome(
                decision=MigrationDecision.OBSERVE_ONLY,
                migration_weight=weight,
                risk_level=risk,
                execution_probability=0.0,
            )
        if weight >= 100:
            return MigrationOutcome(
                decision=MigrationDecision.AUTO_EXECUTE,
                migration_weight=weight,
                risk_level=risk,
                execution_probability=1.0,
            )

        if float(self._random_fn()) < probability:
            decision = MigrationDecision.AUTO_EXECUTE
        else:
            decision = MigrationDecision.REQUIRE_APPROVAL
        return MigrationOutcome(
            decision=decision,
            migration_weight=weight,
            risk_level=risk,
            execution_probability=probability,
        )

    def refresh_from_config(self) -> None:
        """Reload migration weights from owlclaw.yaml."""
        config = YAMLConfigLoader.load_dict(self._config_path)
        loaded = self._parse_weights_from_config(config)
        if loaded:
            self._weights.update(loaded)
        try:
            self._config_mtime_ns = self._config_path.stat().st_mtime_ns
        except OSError:
            self._config_mtime_ns = None

    @staticmethod
    def _parse_weights_from_config(config: dict[str, Any]) -> dict[str, int]:
        out: dict[str, int] = {}
        skills = config.get("skills")
        if not isinstance(skills, dict):
            return out
        entries = skills.get("entries")
        if isinstance(entries, dict):
            for name, payload in entries.items():
                if not isinstance(name, str):
                    continue
                weight = MigrationGate._extract_weight_from_skill(payload if isinstance(payload, dict) else {})
                if weight is not None:
                    out[name.strip()] = weight
        for name, payload in skills.items():
            if name == "entries" or not isinstance(name, str) or not isinstance(payload, dict):
                continue
            weight = MigrationGate._extract_weight_from_skill(payload)
            if weight is not None:
                out[name.strip()] = weight
        return out

    def _refresh_if_config_changed(self) -> None:
        try:
            current = self._config_path.stat().st_mtime_ns
        except OSError:
            return
        if self._config_mtime_ns is None or current > self._config_mtime_ns:
            self.refresh_from_config()

    @staticmethod
    def _extract_weight_from_skill(skill_owlclaw: dict[str, Any]) -> int | None:
        value = skill_owlclaw.get("migration_weight")
        if value is None:
            return None
        try:
            return MigrationGate._normalize_weight(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_weight(value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError("migration_weight must be an integer between 0 and 100")
        as_int = int(value)
        if as_int < 0 or as_int > 100:
            raise ValueError("migration_weight must be an integer between 0 and 100")
        return as_int
