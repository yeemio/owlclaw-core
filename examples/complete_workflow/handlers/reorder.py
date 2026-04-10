"""Reorder decision handler."""

from __future__ import annotations

from typing import Any


async def decide_reorder(session: dict[str, Any]) -> dict[str, Any]:
    low_stock_items = session.get("low_stock_items", [])
    recommendations: list[dict[str, Any]] = []
    for item in low_stock_items:
        threshold = int(item.get("threshold", 0))
        current = int(item.get("current", 0))
        recommendations.append(
            {
                "sku": item.get("sku", "UNKNOWN"),
                "recommended_qty": max(50, threshold * 2 - current),
                "risk": "high" if current <= max(1, threshold // 4) else "medium",
            }
        )
    return {
        "action": "request_confirmation",
        "recommendations": recommendations,
        "count": len(recommendations),
    }
