"""Memory security helpers: auto classification + channel masking."""

from __future__ import annotations

import re
from dataclasses import replace

from owlclaw.agent.memory.models import MemoryEntry, SecurityLevel
from owlclaw.security import DataMasker


class SecurityClassifier:
    """Keyword/regex-based classifier for memory entry sensitivity."""

    _RESTRICTED_PATTERNS = (
        re.compile(r"(?i)\b(ssn|social security|passport|private key|seed phrase)\b"),
        re.compile(r"(?i)\b(card number|credit card|cvv)\b"),
    )
    _CONFIDENTIAL_PATTERNS = (
        re.compile(r"(?i)\b(api[_-]?key|access token|refresh token|secret|password)\b"),
        re.compile(r"(?i)\b(customer email|phone number|bank account)\b"),
    )

    def classify(self, content: str) -> SecurityLevel:
        for pattern in self._RESTRICTED_PATTERNS:
            if pattern.search(content):
                return SecurityLevel.RESTRICTED
        for pattern in self._CONFIDENTIAL_PATTERNS:
            if pattern.search(content):
                return SecurityLevel.CONFIDENTIAL
        return SecurityLevel.INTERNAL


class MemorySecurityFilter:
    """Mask memory content for external channels."""

    def __init__(self, masker: DataMasker | None = None) -> None:
        self._masker = masker or DataMasker()

    @staticmethod
    def _is_sensitive(level: SecurityLevel) -> bool:
        return level in (SecurityLevel.CONFIDENTIAL, SecurityLevel.RESTRICTED)

    @staticmethod
    def _normalize_channel(channel: object) -> str:
        if not isinstance(channel, str):
            return "internal"
        normalized = channel.strip().lower()
        return normalized or "internal"

    def for_channel(self, entry: MemoryEntry, channel: str) -> MemoryEntry:
        ch = self._normalize_channel(channel)
        if ch not in {"mcp", "langfuse"}:
            return entry
        if not self._is_sensitive(entry.security_level):
            return entry
        masked = self._masker.mask(entry.content)
        return replace(entry, content=masked)
