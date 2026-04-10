"""
Durable sleep — a task that sleeps across process restarts (Hatchet durable execution).

Uses ctx.aio_sleep_for() so that if the worker crashes, the sleep completes after restart.
Requires Hatchet server and a worker process. Run with:
  HATCHET_API_TOKEN=... poetry run python examples/hatchet_durable_sleep.py
"""

import asyncio
from datetime import timedelta

from owlclaw.integrations.hatchet import HatchetClient, HatchetConfig


def main() -> None:
    import os
    token = os.environ.get("HATCHET_API_TOKEN", "").strip()
    if not token:
        print("Set HATCHET_API_TOKEN and start Hatchet (see deploy/).")
        return

    config = HatchetConfig(server_url="http://localhost:7077", api_token=token)
    client = HatchetClient(config)
    client.connect()

    @client.durable_task(name="durable-sleep-demo", timeout=60)
    async def durable_sleep_demo(ctx):
        """Sleep durably for 2 seconds then return."""
        await ctx.aio_sleep_for(timedelta(seconds=2))
        return {"slept": True}

    async def run_with_worker() -> None:
        # In a real app you would start the worker in a separate process.
        # Here we use aio_mock_run to exercise the task locally (no durable sleep across restart).
        standalone = client._workflows["durable-sleep-demo"]
        result = await standalone.aio_mock_run({})
        print("Result:", result)

    asyncio.run(run_with_worker())
    client.disconnect()


if __name__ == "__main__":
    main()
