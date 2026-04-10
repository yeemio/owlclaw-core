"""Anomaly alert handler."""

from __future__ import annotations

from typing import Any


async def detect_anomalies(session: dict[str, Any]) -> dict[str, Any]:
    spike_ratio = float(session.get("spike_ratio", 2.4))
    flagged = spike_ratio >= 2.0
    return {
        "action": "alert_ops" if flagged else "no_alert",
        "severity": "high" if spike_ratio >= 3.0 else "medium" if flagged else "low",
        "message": "Consumption spike detected" if flagged else "No anomaly detected",
        "spike_ratio": spike_ratio,
    }
