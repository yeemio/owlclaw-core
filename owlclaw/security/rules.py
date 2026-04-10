"""Built-in security rules used by sanitizer and data masker."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class BuiltinSanitizeRule:
    """Pattern-based sanitize rule."""

    pattern: str
    action: str = "remove"
    replacement: str = ""
    description: str = ""
    flags: int = re.IGNORECASE


@dataclass(frozen=True)
class BuiltinMaskRule:
    """Field-based masking rule."""

    field_pattern: str
    mask_type: str = "full"
    replacement: str = "***"
    description: str = ""


def default_sanitize_rules() -> list[BuiltinSanitizeRule]:
    """Return default prompt-injection sanitization rules."""
    return [
        BuiltinSanitizeRule(r"(?i)ignore\s+(all\s+)?previous\s+instructions", "remove", description="Ignore previous"),
        BuiltinSanitizeRule(r"(?i)disregard\s+the\s+above\s+instructions", "remove", description="Disregard above"),
        BuiltinSanitizeRule(r"(?i)you\s+are\s+now\s+(developer|system)", "remove", description="Role rewrite"),
        BuiltinSanitizeRule(r"(?im)^\s*system\s*:", "remove", description="System prefix"),
        BuiltinSanitizeRule(r"(?im)^\s*assistant\s*:", "remove", description="Assistant prefix"),
        BuiltinSanitizeRule(
            r"(?i)\\n\s*(system|assistant|developer)\s*:",
            "remove",
            description="Escaped role prefix",
        ),
        BuiltinSanitizeRule(r"(?i)reveal\s+(your\s+)?(system\s+prompt|instructions)", "remove", description="Prompt exfiltration"),
        BuiltinSanitizeRule(r"(?i)print\s+hidden\s+prompt", "remove", description="Hidden prompt print"),
        BuiltinSanitizeRule(r"(?i)tool\s*:\s*.*", "remove", description="Tool spoofing"),
        BuiltinSanitizeRule(r"(?i)```(?:system|assistant|developer)", "replace", "[filtered-block]", "Role fenced block"),
        BuiltinSanitizeRule(r"(?i)<\s*system\s*>.*?<\s*/\s*system\s*>", "remove", description="XML system tag"),
    ]


def default_mask_rules() -> list[BuiltinMaskRule]:
    """Return default field-level masking rules."""
    return [
        BuiltinMaskRule(r"(?i)phone|mobile|tel", "partial", description="Phone"),
        BuiltinMaskRule(r"(?i)id[_-]?card|身份证", "partial", description="ID card"),
        BuiltinMaskRule(r"(?i)card|bank|银行卡", "partial", description="Bank card"),
        BuiltinMaskRule(r"(?i)email|mail", "partial", description="Email"),
        BuiltinMaskRule(r"(?i)token|secret|password|api[_-]?key", "full", description="Credential"),
    ]
