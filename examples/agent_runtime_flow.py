"""Basic AgentRuntime flow example."""

from __future__ import annotations

import asyncio

from owlclaw.agent.runtime.runtime import AgentRuntime


async def main() -> None:
    runtime = AgentRuntime(
        agent_id="demo-agent",
        app_dir="examples",
        config={
            "model": "gpt-4o-mini",
            "max_function_calls": 5,
            "heartbeat": {"enabled": True},
        },
    )
    await runtime.setup()
    result = await runtime.trigger_event(
        "manual_run",
        payload={"task_type": "analysis", "message": "status check"},
    )
    print(result)
    print(runtime.get_performance_metrics())


if __name__ == "__main__":
    asyncio.run(main())
