"""Resolve natural-language trigger intent into structured trigger configs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TriggerResolveResult:
    """Structured trigger resolution result."""

    trigger_config: dict[str, Any]
    confidence: float
    warning: str | None = None


_WEEKDAY_TO_CRON = {
    "monday": 1,
    "mon": 1,
    "周一": 1,
    "星期一": 1,
    "tuesday": 2,
    "tue": 2,
    "周二": 2,
    "星期二": 2,
    "wednesday": 3,
    "wed": 3,
    "周三": 3,
    "星期三": 3,
    "thursday": 4,
    "thu": 4,
    "周四": 4,
    "星期四": 4,
    "friday": 5,
    "fri": 5,
    "周五": 5,
    "星期五": 5,
    "saturday": 6,
    "sat": 6,
    "周六": 6,
    "星期六": 6,
    "sunday": 0,
    "sun": 0,
    "周日": 0,
    "星期日": 0,
    "星期天": 0,
}


def resolve_trigger_intent(intent: str) -> TriggerResolveResult:
    """Best-effort mapping from NL trigger intent to trigger config."""
    normalized = intent.strip().lower()
    if not normalized:
        return TriggerResolveResult(
            trigger_config={"type": "cron", "expression": "0 0 * * *"},
            confidence=0.2,
            warning="empty trigger intent; fallback to daily midnight cron",
        )

    # Cron: daily at specific hour
    zh_daily_match = re.search(r"每天.*?(\d{1,2})\s*点(?:\s*(\d{1,2})\s*分)?", intent)
    if zh_daily_match:
        hour = int(zh_daily_match.group(1))
        minute = int(zh_daily_match.group(2) or "0")
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return TriggerResolveResult(
                trigger_config={"type": "cron", "expression": f"{minute} {hour} * * *"},
                confidence=0.95,
            )

    en_daily_match = re.search(r"(?:every day|daily).{0,24}?(?:at\s+)?(\d{1,2})(?::(\d{1,2}))?\s*(am|pm)?", normalized)
    if en_daily_match:
        hour = int(en_daily_match.group(1))
        minute = int(en_daily_match.group(2) or "0")
        meridiem = en_daily_match.group(3)
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return TriggerResolveResult(
                trigger_config={"type": "cron", "expression": f"{minute} {hour} * * *"},
                confidence=0.9,
            )

    # Cron: weekly schedule
    for token, cron_day in _WEEKDAY_TO_CRON.items():
        if token in normalized or token in intent:
            return TriggerResolveResult(
                trigger_config={"type": "cron", "expression": f"0 0 * * {cron_day}"},
                confidence=0.88,
            )

    # Event-driven mappings
    if "新订单" in intent or "new order" in normalized or "order created" in normalized:
        return TriggerResolveResult(
            trigger_config={"type": "webhook", "event": "order.created"},
            confidence=0.9,
        )
    if "库存变化" in intent or "inventory change" in normalized:
        return TriggerResolveResult(
            trigger_config={"type": "db_change", "table": "inventory"},
            confidence=0.85,
        )
    if "队列消息" in intent or "queue message" in normalized:
        return TriggerResolveResult(
            trigger_config={"type": "queue", "topic": "default"},
            confidence=0.8,
        )

    return TriggerResolveResult(
        trigger_config={"type": "cron", "expression": "0 0 * * *"},
        confidence=0.35,
        warning="unable to confidently resolve trigger intent; fallback to daily midnight cron",
    )
