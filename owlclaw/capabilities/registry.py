"""Capability registry for managing handlers and state providers.

This module implements the Capability Registry component, which manages
the registration, lookup, and invocation of capability handlers and state
providers.
"""

import asyncio
import inspect
import logging
import math
from collections.abc import Callable
from typing import Any

from owlclaw.capabilities.skills import SkillsLoader

logger = logging.getLogger(__name__)


class CapabilityRegistry:
    """Registry for capability handlers and state providers.

    The CapabilityRegistry connects Skills (knowledge documents) with their
    corresponding handlers (Python functions). It validates registrations,
    manages lookups, and handles invocations.

    Attributes:
        skills_loader: SkillsLoader instance for Skill metadata access
        handlers: Dictionary mapping Skill names to handler functions
        states: Dictionary mapping state names to provider functions
    """

    def __init__(self, skills_loader: SkillsLoader, *, handler_timeout_seconds: float = 30.0):
        """Initialize the CapabilityRegistry.

        Args:
            skills_loader: SkillsLoader instance for accessing Skill metadata
            handler_timeout_seconds: Async handler invocation timeout in seconds.
        """
        self.skills_loader = skills_loader
        self.handlers: dict[str, Callable] = {}
        self.states: dict[str, Callable] = {}
        self._handler_timeout_seconds = self._normalize_timeout(handler_timeout_seconds)

    @staticmethod
    def _normalize_timeout(value: Any) -> float:
        if isinstance(value, bool):
            return 30.0
        try:
            timeout = float(value)
        except (TypeError, ValueError):
            return 30.0
        if timeout <= 0 or not math.isfinite(timeout):
            return 30.0
        return timeout

    @staticmethod
    def _normalize_name(value: str, field: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a non-empty string")
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field} must be a non-empty string")
        return normalized

    @staticmethod
    def _callable_name(func: Callable) -> str:
        name = getattr(func, "__name__", None)
        if isinstance(name, str) and name:
            return name
        return func.__class__.__name__

    def register_handler(self, skill_name: str, handler: Callable) -> None:
        """Register a handler function for a Skill.

        Args:
            skill_name: Name of the Skill this handler implements
            handler: Python function to execute when Skill is invoked

        Raises:
            TypeError: If handler is not callable
            ValueError: If handler is already registered for this Skill
        """
        if not callable(handler):
            raise TypeError(
                f"Handler for '{skill_name}' must be callable"
            )
        skill_name = self._normalize_name(skill_name, "skill_name")

        # Validate Skill exists (warning only, not blocking)
        try:
            skill = self.skills_loader.get_skill(skill_name)
        except Exception as e:
            logger.warning(
                "Skill metadata lookup failed for '%s': %s",
                skill_name,
                e,
            )
            skill = None
        if not skill:
            logger.warning(
                "Registering handler for non-existent Skill '%s'", skill_name
            )

        # Check for duplicate registration
        if skill_name in self.handlers:
            raise ValueError(
                f"Handler for '{skill_name}' already registered. "
                f"Existing: {self._callable_name(self.handlers[skill_name])}, "
                f"New: {self._callable_name(handler)}"
            )

        self.handlers[skill_name] = handler

    def register_state(self, state_name: str, provider: Callable) -> None:
        """Register a state provider function.

        Args:
            state_name: Name of the state this provider supplies
            provider: Python function that returns state dict

        Raises:
            TypeError: If provider is not callable
            ValueError: If state provider is already registered
        """
        # Validate provider is callable (sync or async)
        if not callable(provider):
            raise TypeError(
                f"State provider '{state_name}' must be callable"
            )
        state_name = self._normalize_name(state_name, "state_name")

        # Check for duplicate registration
        if state_name in self.states:
            raise ValueError(
                f"State provider for '{state_name}' already registered"
            )

        self.states[state_name] = provider

    async def invoke_handler(self, skill_name: str, **kwargs) -> Any:
        """Invoke a registered handler by Skill name.

        Args:
            skill_name: Name of the Skill to invoke
            **kwargs: Arguments to pass to the handler

        Returns:
            Result from the handler function

        Raises:
            ValueError: If no handler is registered for the Skill
            RuntimeError: If handler execution fails
        """
        skill_name = self._normalize_name(skill_name, "skill_name")
        handler = self.handlers.get(skill_name)
        if not handler:
            raise ValueError(
                f"No handler registered for Skill '{skill_name}'"
            )

        try:
            invoke_kwargs = self._prepare_handler_kwargs(handler, kwargs)
            result = handler(**invoke_kwargs)
            if inspect.isawaitable(result):
                return await asyncio.wait_for(result, timeout=self._handler_timeout_seconds)
            return result
        except asyncio.TimeoutError as e:
            raise RuntimeError(
                f"Handler '{skill_name}' timed out after {self._handler_timeout_seconds:.2f}s"
            ) from e
        except Exception as e:
            logger.exception("Handler '%s' invocation failed", skill_name)
            raise RuntimeError(
                f"Handler '{skill_name}' failed: {type(e).__name__}"
            ) from e

    def _prepare_handler_kwargs(
        self,
        handler: Callable,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Prepare invocation kwargs according to handler signature.

        Compatibility rules:
        - If handler accepts ``**kwargs``, pass all arguments through.
        - If handler has no named params, drop all kwargs.
        - If handler has a ``session`` param and caller did not provide it,
          map the full kwargs dict to ``session``.
        - If handler has exactly one named param and no matching key was
          provided, map the full kwargs dict to that parameter.
        - Otherwise, keep only parameters explicitly declared by handler.
        """
        sig = inspect.signature(handler)
        params = sig.parameters

        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
            return dict(kwargs)

        named_params = {
            name: p
            for name, p in params.items()
            if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        }

        if not named_params:
            return {}

        if "session" in named_params and "session" not in kwargs:
            return {"session": dict(kwargs)}

        filtered = {k: v for k, v in kwargs.items() if k in named_params}
        if filtered:
            return filtered

        if kwargs and len(named_params) == 1:
            param_name = next(iter(named_params))
            return {param_name: dict(kwargs)}

        return filtered

    @staticmethod
    def _handler_metadata(handler: Callable) -> dict[str, Any] | None:
        """Best-effort metadata extraction for non-skill-backed handlers."""
        task_type = getattr(handler, "task_type", None)
        constraints = getattr(handler, "constraints", None)
        focus = getattr(handler, "focus", None)
        risk_level = getattr(handler, "risk_level", None)
        requires_confirmation = getattr(handler, "requires_confirmation", None)

        if not any(value is not None for value in (task_type, constraints, focus, risk_level, requires_confirmation)):
            return None

        normalized_focus: list[str] = []
        if isinstance(focus, list):
            normalized_focus = [item.strip() for item in focus if isinstance(item, str) and item.strip()]
        normalized_constraints = constraints if isinstance(constraints, dict) else {}
        normalized_task_type = task_type.strip() if isinstance(task_type, str) else None
        normalized_risk = risk_level.strip().lower() if isinstance(risk_level, str) else "low"
        if normalized_risk not in {"low", "medium", "high", "critical"}:
            normalized_risk = "low"

        normalized_requires = False
        if isinstance(requires_confirmation, bool):
            normalized_requires = requires_confirmation

        return {
            "task_type": normalized_task_type,
            "constraints": normalized_constraints,
            "focus": normalized_focus,
            "risk_level": normalized_risk,
            "requires_confirmation": normalized_requires,
        }

    async def get_state(self, state_name: str) -> dict:
        """Get state from a registered state provider.

        Args:
            state_name: Name of the state to retrieve

        Returns:
            State dictionary from the provider

        Raises:
            ValueError: If no provider is registered for the state
            TypeError: If provider doesn't return a dict
            RuntimeError: If provider execution fails
        """
        state_name = self._normalize_name(state_name, "state_name")
        provider = self.states.get(state_name)
        if not provider:
            raise ValueError(
                f"No state provider registered for '{state_name}'"
            )

        try:
            result = provider()
            if inspect.isawaitable(result):
                result = await result

            if not isinstance(result, dict):
                raise TypeError(
                    f"State provider '{state_name}' must return dict, "
                    f"got {type(result)}"
                )

            return result
        except Exception as e:
            logger.exception("State provider '%s' invocation failed", state_name)
            raise RuntimeError(
                f"State provider '{state_name}' failed: {type(e).__name__}"
            ) from e

    def list_capabilities(self) -> list[dict]:
        """List all registered capabilities with metadata.

        Returns:
            List of capability metadata dictionaries
        """
        capabilities = []

        for skill_name, handler in self.handlers.items():
            skill = self.skills_loader.get_skill(skill_name)
            if skill:
                capabilities.append({
                    "name": skill.name,
                    "description": skill.description,
                    "task_type": skill.task_type,
                    "constraints": skill.constraints,
                    "focus": skill.focus,
                    "risk_level": skill.risk_level,
                    "requires_confirmation": skill.requires_confirmation,
                    "handler": self._callable_name(handler),
                })
                continue
            handler_meta = self._handler_metadata(handler)
            if handler_meta is not None:
                capabilities.append(
                    {
                        "name": skill_name,
                        "description": getattr(handler, "description", "") or "",
                        "task_type": handler_meta["task_type"],
                        "constraints": handler_meta["constraints"],
                        "focus": handler_meta["focus"],
                        "risk_level": handler_meta["risk_level"],
                        "requires_confirmation": handler_meta["requires_confirmation"],
                        "handler": self._callable_name(handler),
                    }
                )

        return capabilities

    def get_capability_metadata(self, skill_name: str) -> dict | None:
        """Get metadata for a specific capability.

        Args:
            skill_name: Name of the Skill to query

        Returns:
            Capability metadata dict if found, None otherwise
        """
        try:
            normalized = self._normalize_name(skill_name, "skill_name")
        except ValueError:
            return None
        skill = self.skills_loader.get_skill(normalized)
        if not skill:
            handler = self.handlers.get(normalized)
            if handler is None:
                return None
            handler_meta = self._handler_metadata(handler)
            if handler_meta is None:
                return None
            return {
                "name": normalized,
                "description": getattr(handler, "description", "") or "",
                "task_type": handler_meta["task_type"],
                "constraints": handler_meta["constraints"],
                "focus": handler_meta["focus"],
                "risk_level": handler_meta["risk_level"],
                "requires_confirmation": handler_meta["requires_confirmation"],
                "handler": self._callable_name(handler),
            }

        handler = self.handlers.get(normalized)

        return {
            "name": skill.name,
            "description": skill.description,
            "task_type": skill.task_type,
            "constraints": skill.constraints,
            "focus": skill.focus,
            "risk_level": skill.risk_level,
            "requires_confirmation": skill.requires_confirmation,
            "handler": self._callable_name(handler) if handler else None,
        }

    def filter_by_task_type(self, task_type: str) -> list[str]:
        """Filter capabilities by task_type.

        Args:
            task_type: Task type to filter by

        Returns:
            List of Skill names matching the task_type
        """
        if not isinstance(task_type, str):
            return []
        normalized_task_type = task_type.strip()
        if not normalized_task_type:
            return []
        matching = []

        for skill_name in self.handlers:
            skill = self.skills_loader.get_skill(skill_name)
            if skill:
                skill_task_type = getattr(skill, "task_type", None)
            else:
                handler = self.handlers.get(skill_name)
                handler_meta = self._handler_metadata(handler) if handler else None
                skill_task_type = handler_meta["task_type"] if handler_meta else None
            if isinstance(skill_task_type, str) and skill_task_type.strip() == normalized_task_type:
                matching.append(skill_name)

        return matching
