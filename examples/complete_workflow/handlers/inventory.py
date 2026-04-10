"""Inventory check handler."""

from __future__ import annotations

from typing import Any


async def check_inventory(session: dict[str, Any]) -> dict[str, Any]:
    items = session.get(
        "items",
        [
            {"sku": "WIDGET-42", "current": 5, "threshold": 20},
            {"sku": "GADGET-99", "current": 12, "threshold": 50},
            {"sku": "BOLT-7", "current": 90, "threshold": 30},
        ],
    )
    low_stock = [
        item for item in items if int(item.get("current", 0)) < int(item.get("threshold", 0))
    ]
    return {
        "action": "trigger_reorder_review",
        "low_stock_items": low_stock,
        "low_stock_count": len(low_stock),
    }
