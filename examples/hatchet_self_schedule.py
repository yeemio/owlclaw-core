"""
Self-schedule — a task that schedules itself to run again after a delay.

Requires Hatchet server. The task uses client.schedule_task() to enqueue a future run.
"""

import asyncio

from owlclaw.integrations.hatchet import HatchetClient, HatchetConfig


def main() -> None:
    config = HatchetConfig(
        server_url="http://localhost:7077",
        api_token=__import__("os").environ.get("HATCHET_API_TOKEN"),
    )
    client = HatchetClient(config)
    client.connect()

    @client.task(name="self-schedule-task", retries=2)
    async def self_schedule_task(ctx):
        # In a real run, ctx is Hatchet Context; we could call client.schedule_task(...)
        return {"scheduled": True}

    async def demo_schedule() -> None:
        run_id = await client.schedule_task("self-schedule-task", delay_seconds=60)
        print("Scheduled run id:", run_id)

    asyncio.run(demo_schedule())
    client.disconnect()


if __name__ == "__main__":
    main()
