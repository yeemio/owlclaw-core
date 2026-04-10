"""Error handling helpers for LangChain integration."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


class ErrorHandler:
    """Maps runtime exceptions to structured OwlClaw error payloads."""

    EXCEPTION_MAPPING: dict[str, tuple[str, int]] = {
        "ValueError": ("ValidationError", 400),
        "SchemaValidationError": ("ValidationError", 400),
        "TimeoutError": ("TimeoutError", 504),
        "RateLimitError": ("RateLimitError", 429),
        "APIError": ("ExternalServiceError", 502),
        "Exception": ("InternalError", 500),
    }

    def __init__(
        self,
        fallback_executor: Callable[[str, dict[str, Any], Any, Exception], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self._fallback_executor = fallback_executor

    def map_exception(self, exception: Exception) -> dict[str, Any]:
        """Map exception instance to structured error response payload."""
        exception_name = type(exception).__name__
        error_type, status_code = self.EXCEPTION_MAPPING.get(
            exception_name,
            self.EXCEPTION_MAPPING["Exception"],
        )
        logger.exception("LangChain execution failed: %s", exception_name)
        return self.create_error_response(
            error_type=error_type,
            message=str(exception),
            status_code=status_code,
            details={"original_exception": exception_name},
        )

    @staticmethod
    def create_error_response(
        error_type: str,
        message: str,
        status_code: int,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create standard error response payload."""
        return {
            "error": {
                "type": error_type,
                "message": message,
                "status_code": status_code,
                "details": details or {},
            }
        }

    async def handle_fallback(
        self,
        fallback_name: str,
        input_data: dict[str, Any],
        context: Any,
        error: Exception,
    ) -> dict[str, Any]:
        """Execute configured fallback handler when primary runnable fails."""
        logger.warning(
            "Primary runnable failed; invoking fallback '%s'",
            fallback_name,
        )
        if self._fallback_executor is None:
            fallback_error = RuntimeError("fallback executor not configured")
            return self.create_error_response(
                error_type="FallbackError",
                message=str(fallback_error),
                status_code=500,
                details={"fallback": fallback_name, "original_error": str(error)},
            )

        try:
            result = await self._fallback_executor(fallback_name, input_data, context, error)
            if isinstance(result, dict):
                result.setdefault("_fallback_used", True)
                result.setdefault("_fallback_name", fallback_name)
                return result
            return {
                "result": result,
                "_fallback_used": True,
                "_fallback_name": fallback_name,
            }
        except Exception as fallback_exc:  # pragma: no cover - defensive path
            logger.exception("Fallback execution failed for '%s'", fallback_name)
            return self.map_exception(fallback_exc)
