"""Daily report handler."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


async def build_daily_report(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": "publish_report",
        "date": datetime.now(timezone.utc).date().isoformat(),
        "summary": {
            "low_stock_skus": int(session.get("low_stock_skus", 2)),
            "alerts_sent": int(session.get("alerts_sent", 1)),
            "reorder_items": int(session.get("reorder_items", 2)),
        },
    }
