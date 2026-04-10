"""Data masking utilities for sensitive text and structured payloads."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MaskRule:
    """Masking rule for field-level and content-level matching."""

    field_pattern: str
    mask_type: str = "full"
    replacement: str = "[REDACTED]"


class DataMasker:
    """Mask sensitive values from text or structured data."""

    _TEXT_PATTERNS = (
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        re.compile(r"\b(?:\+?\d[\d\-\s]{7,}\d)\b"),
        re.compile(r"\b(?:\d[ -]?){13,19}\b"),
        re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*([^\s,;]+)"),
    )

    def __init__(
        self,
        rules: list[MaskRule] | None = None,
        audit_log: Any | None = None,
        audit_source: str = "data_masker",
    ) -> None:
        self._rules = rules or self._default_rules()
        self._audit_log = audit_log
        self._audit_source = audit_source

    def mask(self, data: Any) -> Any:
        """Mask text or recursively mask structured data."""
        masked = self._mask_internal(data)
        if masked != data:
            self._audit("mask_applied", data_type=type(data).__name__)
        return masked

    def _mask_internal(self, data: Any) -> Any:
        """Mask without emitting duplicate audit events in recursion."""
        if isinstance(data, str):
            return self._mask_text(data)
        if isinstance(data, dict):
            return {key: self._mask_value(key, value) for key, value in data.items()}
        if isinstance(data, list):
            return [self._mask_internal(item) for item in data]
        if isinstance(data, tuple):
            return tuple(self._mask_internal(item) for item in data)
        return data

    def _mask_value(self, key: str, value: Any) -> Any:
        for rule in self._rules:
            if re.search(rule.field_pattern, key, flags=re.IGNORECASE):
                if isinstance(value, str):
                    return self._apply_mask(value, rule)
                return rule.replacement
        return self._mask_internal(value)

    def _mask_text(self, text: str) -> str:
        masked = text
        for pattern in self._TEXT_PATTERNS:
            masked = pattern.sub("[REDACTED]", masked)
        return masked

    @staticmethod
    def _apply_mask(value: str, rule: MaskRule) -> str:
        if rule.mask_type == "partial" and len(value) > 4:
            return f"{value[:2]}***{value[-2:]}"
        return rule.replacement

    @staticmethod
    def _default_rules() -> list[MaskRule]:
        return [
            MaskRule(field_pattern=r"phone|mobile", mask_type="partial"),
            MaskRule(field_pattern=r"id[_-]?card|identity", mask_type="partial"),
            MaskRule(field_pattern=r"bank[_-]?card|card[_-]?number", mask_type="partial"),
            MaskRule(field_pattern=r"email", mask_type="partial"),
            MaskRule(field_pattern=r"password|token|secret|api[_-]?key", mask_type="full"),
        ]

    def _audit(self, event_type: str, **details: Any) -> None:
        if self._audit_log is None:
            return
        record = getattr(self._audit_log, "record", None)
        if callable(record):
            record(event_type=event_type, source=self._audit_source, details=details)
