"""Complete workflow example for inventory management."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from owlclaw import OwlClaw

from examples.complete_workflow.handlers import (
    build_daily_report,
    check_inventory,
    decide_reorder,
    detect_anomalies,
)

APP_DIR = Path(__file__).resolve().parent

GOVERNANCE_CONFIG: dict[str, Any] = {
    "use_inmemory_ledger": True,
    "visibility": {
        "budget": {
            "high_cost_threshold": "0.5",
            "budget_limits": {"inventory-workflow-agent": "10.0"},
        },
        "circuit_breaker": {"failure_threshold": 3, "recovery_timeout": 60},
    },
}

app = OwlClaw.lite(
    "inventory-workflow-agent",
    skills_path=str(APP_DIR / "skills"),
    heartbeat_interval_minutes=1,
    governance=GOVERNANCE_CONFIG,
)


@app.handler("inventory-check")
async def inventory_check_handler(session: dict[str, Any]) -> dict[str, Any]:
    return await check_inventory(session)


@app.handler("reorder-decision")
async def reorder_decision_handler(session: dict[str, Any]) -> dict[str, Any]:
    return await decide_reorder(session)


@app.handler("anomaly-alert")
async def anomaly_alert_handler(session: dict[str, Any]) -> dict[str, Any]:
    return await detect_anomalies(session)


@app.handler("daily-report")
async def daily_report_handler(session: dict[str, Any]) -> dict[str, Any]:
    return await build_daily_report(session)


async def run_once() -> None:
    runtime = await app.start(app_dir=str(APP_DIR))
    try:
        if app.registry is None:
            raise RuntimeError("registry is not initialized")

        inventory_result = await app.registry.invoke_handler("inventory-check", session={})
        reorder_result = await app.registry.invoke_handler(
            "reorder-decision",
            session={"low_stock_items": inventory_result["low_stock_items"]},
        )
        alert_result = await app.registry.invoke_handler("anomaly-alert", session={"spike_ratio": 2.4})
        report_result = await app.registry.invoke_handler(
            "daily-report",
            session={
                "low_stock_skus": inventory_result["low_stock_count"],
                "alerts_sent": 1 if alert_result["action"] == "alert_ops" else 0,
                "reorder_items": reorder_result["count"],
            },
        )

        print(
            json.dumps(
                {
                    "status": "ok",
                    "runtime_initialized": runtime.is_initialized,
                    "decisions": {
                        "inventory-check": inventory_result,
                        "reorder-decision": reorder_result,
                        "anomaly-alert": alert_result,
                        "daily-report": report_result,
                    },
                },
                ensure_ascii=False,
            )
        )
    finally:
        await app.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="OwlClaw complete workflow example")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one end-to-end workflow and exit.",
    )
    args = parser.parse_args()

    if args.once:
        asyncio.run(run_once())
        return

    app.run(app_dir=str(APP_DIR))


if __name__ == "__main__":
    main()
