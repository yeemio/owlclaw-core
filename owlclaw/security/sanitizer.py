"""Input sanitization for prompt-injection defensive filtering."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from owlclaw.security.rules import default_sanitize_rules


@dataclass(frozen=True)
class SanitizationRule:
    """Sanitization rule contract."""

    pattern: str
    action: str = "remove"
    description: str = ""
    replacement: str = ""
    flags: int = re.IGNORECASE


@dataclass(frozen=True)
class SanitizeResult:
    """Sanitization output with traceable modifications."""

    original: str
    sanitized: str
    modifications: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.original != self.sanitized


class InputSanitizer:
    """Pattern-based sanitizer for untrusted external text."""

    def __init__(self, rules: list[SanitizationRule] | None = None) -> None:
        if rules is not None:
            self._rules = rules
        else:
            self._rules = [
                SanitizationRule(
                    pattern=rule.pattern,
                    action=rule.action,
                    description=rule.description,
                    replacement=rule.replacement,
                    flags=rule.flags,
                )
                for rule in default_sanitize_rules()
            ]

    def sanitize(self, input_text: str, source: str = "unknown") -> SanitizeResult:
        """Sanitize input text and return result with modification descriptions."""
        normalized = unicodedata.normalize("NFKC", input_text)
        text = normalized
        mods: list[str] = []
        if normalized != input_text:
            mods.append(f"{source}:unicode-nfkc-normalized")
        for rule in self._rules:
            try:
                pattern = re.compile(rule.pattern, rule.flags)
            except re.error:
                # Skip invalid custom rules; keep sanitizer resilient.
                continue
            if not pattern.search(text):
                continue
            if rule.action == "replace":
                replacement = rule.replacement or "[filtered]"
                text = pattern.sub(replacement, text)
            elif rule.action == "flag":
                # Keep content unchanged but record detection.
                pass
            else:
                text = pattern.sub("", text)
            mods.append(f"{source}:{rule.description or rule.pattern}")
        return SanitizeResult(original=input_text, sanitized=text, modifications=mods)
