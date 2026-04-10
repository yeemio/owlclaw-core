"""Retry policy utilities for LangChain runnable execution."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RetryPolicy:
    """Retry policy with exponential backoff."""

    max_attempts: int = 3
    initial_delay_ms: int = 100
    max_delay_ms: int = 5000
    backoff_multiplier: float = 2.0
    retryable_errors: list[str] = field(default_factory=lambda: ["TimeoutError", "RateLimitError"])

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.initial_delay_ms < 0:
            raise ValueError("initial_delay_ms must be >= 0")
        if self.max_delay_ms < 0:
            raise ValueError("max_delay_ms must be >= 0")
        if self.backoff_multiplier < 1.0:
            raise ValueError("backoff_multiplier must be >= 1.0")


def calculate_backoff_delay(attempt: int, policy: RetryPolicy) -> float:
    """Calculate exponential backoff delay in seconds for current attempt index."""
    if attempt <= 1:
        return policy.initial_delay_ms / 1000.0
    raw_ms = policy.initial_delay_ms * (policy.backoff_multiplier ** (attempt - 1))
    delay_ms = min(policy.max_delay_ms, int(raw_ms))
    return delay_ms / 1000.0


def should_retry(error: Exception, attempt: int, policy: RetryPolicy) -> bool:
    """Check if current attempt should retry for given exception type."""
    if attempt >= policy.max_attempts:
        return False
    return type(error).__name__ in set(policy.retryable_errors)
