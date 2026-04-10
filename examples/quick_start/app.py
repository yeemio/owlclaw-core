"""Quick Start example: run OwlClaw in Lite Mode with zero external dependencies."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from owlclaw import OwlClaw

APP_DIR = Path(__file__).resolve().parent
SKILLS_DIR = APP_DIR / "skills"

_DEMO_PAYLOAD = {"sku": "WIDGET-42", "available": 6, "threshold": 10}
_MOCK_RESPONSES = {
    "default": {
        "content": "Inventory risk detected. I will call inventory-check to decide the action.",
        "function_calls": [
            {
                "name": "inventory-check",
                "arguments": {"session": _DEMO_PAYLOAD},
            }
        ],
    }
}

app = OwlClaw.lite(
    "inventory-agent",
    skills_path=str(SKILLS_DIR),
    mock_responses=_MOCK_RESPONSES,
    heartbeat_interval_minutes=1,
)


@app.handler("inventory-check")
async def inventory_check(session: dict[str, Any]) -> dict[str, Any]:
    available = int(session.get("available", 6))
    threshold = int(session.get("threshold", 10))
    if available < threshold:
        return {
            "action": "reorder",
            "sku": session.get("sku", "WIDGET-42"),
            "quantity": max(50, threshold * 10),
            "reason": "stock below threshold",
        }
    return {"action": "hold", "reason": "stock level is healthy"}


async def run_once() -> None:
    runtime = await app.start(app_dir=str(APP_DIR))
    try:
        payload = dict(_DEMO_PAYLOAD)
        if app.registry is None:
            raise RuntimeError("registry is not initialized")
        result = await app.registry.invoke_handler("inventory-check", session=payload)
        output = {
            "status": "ok",
            "mode": "decision_preview",
            "runtime_initialized": runtime.is_initialized,
            "mock_content": _MOCK_RESPONSES["default"]["content"],
            "expected_function_call": _MOCK_RESPONSES["default"]["function_calls"][0],
            "result": result,
        }
        print(json.dumps(output, ensure_ascii=False))
    finally:
        await app.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="OwlClaw Quick Start example")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one event and exit (for CI/verification).",
    )
    args = parser.parse_args()

    if args.once:
        asyncio.run(run_once())
        return

    app.run(app_dir=str(APP_DIR))


if __name__ == "__main__":
    main()
