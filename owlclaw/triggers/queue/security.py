"""Security helpers for queue trigger logging and error messages."""

from __future__ import annotations

import logging
import re
from typing import Any

_SENSITIVE_KEYS = ("password", "passwd", "pwd", "token", "api_key", "apikey", "secret")
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)\b(password|passwd|pwd|token|api[_-]?key|secret)\b\s*([:=])\s*([^\s,;]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+\S+")
_OPENAI_KEY_PATTERN = re.compile(r"\bsk-[a-zA-Z0-9\-]{8,}\b")


def redact_sensitive_text(text: str) -> str:
    """Redact known credential patterns in arbitrary text."""
    redacted = _KEY_VALUE_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}***", text)
    redacted = _BEARER_PATTERN.sub("Bearer ***", redacted)
    redacted = _OPENAI_KEY_PATTERN.sub("sk-***", redacted)
    return redacted


def redact_sensitive_data(value: Any) -> Any:
    """Recursively redact sensitive fields from nested data."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_lower = key.lower()
            if any(marker in key_lower for marker in _SENSITIVE_KEYS):
                redacted[key] = "***"
                continue
            redacted[key] = redact_sensitive_data(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_data(item) for item in value)
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def redact_error_message(error: Exception | str) -> str:
    """Normalize and redact exception text before logging or persistence."""
    return redact_sensitive_text(str(error))


class SensitiveDataLogFilter(logging.Filter):
    """Logging filter that redacts credentials from messages and arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            safe_args = tuple(redact_sensitive_data(item) for item in record.args)
            try:
                rendered = str(record.msg) % safe_args
            except Exception:
                rendered = f"{record.msg} {safe_args!r}"
            record.msg = redact_sensitive_text(rendered)
            record.args = ()
        elif isinstance(record.args, dict):
            safe_args = redact_sensitive_data(record.args)
            try:
                rendered = str(record.msg) % safe_args
            except Exception:
                rendered = f"{record.msg} {safe_args!r}"
            record.msg = redact_sensitive_text(rendered)
            record.args = ()
        else:
            record.msg = redact_sensitive_text(str(record.msg))
        return True
