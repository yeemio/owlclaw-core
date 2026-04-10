"""Privacy masking utilities for LangChain integration."""

from __future__ import annotations

import re
from typing import Any

_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_PATTERN = re.compile(r"\b\d{3}[-\s]?\d{4}[-\s]?\d{4}\b")
_KEY_PATTERN = re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*\S+")


class PrivacyMasker:
    """Recursive masker for sensitive payload values."""

    def __init__(self, custom_patterns: list[str] | None = None) -> None:
        self._custom_patterns = [re.compile(pattern, flags=re.IGNORECASE) for pattern in (custom_patterns or [])]

    def mask_data(self, data: Any) -> Any:
        """Mask common PII/secrets while preserving original structure."""
        if isinstance(data, dict):
            return {k: self.mask_data(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self.mask_data(item) for item in data]
        if isinstance(data, str):
            return self._mask_text(data)
        return data

    def _mask_text(self, text: str) -> str:
        masked = _EMAIL_PATTERN.sub("***@***", text)
        masked = _PHONE_PATTERN.sub("***-****-****", masked)
        masked = _KEY_PATTERN.sub(lambda m: self._mask_secret_entry(m.group(0)), masked)
        for pattern in self._custom_patterns:
            masked = pattern.sub("***", masked)
        return masked

    @staticmethod
    def _mask_secret_entry(entry: str) -> str:
        if ":" in entry:
            key, _, value = entry.partition(":")
            return f"{key}: {PrivacyMasker._mask_value(value.strip())}"
        if "=" in entry:
            key, _, value = entry.partition("=")
            return f"{key}={PrivacyMasker._mask_value(value.strip())}"
        return PrivacyMasker._mask_value(entry)

    @staticmethod
    def _mask_value(value: str) -> str:
        if len(value) <= 4:
            return "*" * len(value)
        return value[:2] + "*" * (len(value) - 4) + value[-2:]
