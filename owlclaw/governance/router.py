"""Model router: select LLM model by task_type with fallback chain."""

import logging
from dataclasses import dataclass

from owlclaw.governance.visibility import RunContext

logger = logging.getLogger(__name__)


@dataclass
class ModelSelection:
    """Selected model and its fallback chain."""

    model: str
    fallback: list[str]


class Router:
    """Selects LLM model by task_type; supports fallback on failure."""

    def __init__(self, config: dict, *, default_model: str = "gpt-4o-mini") -> None:
        self._rules: list[dict] = []
        self._default_model: str = default_model.strip() if isinstance(default_model, str) and default_model.strip() else "gpt-4o-mini"
        self.reload_config(config)

    def reload_config(self, config: dict) -> None:
        """Hot-reload router rules and default model from new config."""
        cfg = config if isinstance(config, dict) else {}
        raw_rules = cfg.get("rules", [])
        self._rules = raw_rules if isinstance(raw_rules, list) else []
        raw_default_model = cfg.get("default_model", self._default_model)
        if isinstance(raw_default_model, str) and raw_default_model.strip():
            self._default_model = raw_default_model.strip()
        else:
            self._default_model = "gpt-4o-mini"

    def update_config(self, config: dict) -> None:
        """Backward-compatible alias for reload_config()."""
        self.reload_config(config)

    async def select_model(
        self,
        task_type: str,
        context: RunContext,
    ) -> ModelSelection | None:
        """Return model and fallback chain for the given task_type.

        Returns ``None`` when no routing rule matches, so callers keep their
        current runtime model unchanged.
        """
        normalized_task_type = task_type.strip() if isinstance(task_type, str) else ""
        for rule in self._rules:
            if not isinstance(rule, dict):
                continue
            rule_task_type = rule.get("task_type")
            if isinstance(rule_task_type, str):
                rule_task_type = rule_task_type.strip()
            if rule_task_type == normalized_task_type:
                model = rule.get("model", self._default_model)
                if not isinstance(model, str) or not model.strip():
                    model = self._default_model
                raw_fallback = rule.get("fallback", [])
                if isinstance(raw_fallback, list):
                    fallback = [
                        item.strip()
                        for item in raw_fallback
                        if isinstance(item, str) and item.strip()
                    ]
                else:
                    fallback = []
                return ModelSelection(
                    model=model,
                    fallback=fallback,
                )
        return None

    async def handle_model_failure(
        self,
        failed_model: str,
        task_type: str,
        error: Exception,
        fallback_chain: list[str],
    ) -> str | None:
        """Return next model from fallback chain, or None if exhausted."""
        if not fallback_chain:
            return None
        next_model = next(
            (
                model.strip()
                for model in fallback_chain
                if isinstance(model, str) and model.strip()
            ),
            None,
        )
        if next_model is None:
            return None
        logger.warning(
            "Model %s failed for task_type %s, falling back to %s: %s",
            failed_model,
            task_type,
            next_model,
            error,
        )
        return next_model
