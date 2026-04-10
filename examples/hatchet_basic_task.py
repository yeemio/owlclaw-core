"""
Basic Hatchet task — register and run a single task (no server required for mock_run).

Run without server (mock):
  poetry run python examples/hatchet_basic_task.py

Run with server: set HATCHET_API_TOKEN, start Hatchet (e.g. deploy/docker-compose.lite.yml), then run.
"""

import asyncio

from owlclaw.integrations.hatchet import HatchetClient, HatchetConfig


def main() -> None:
    config = HatchetConfig(
        server_url="http://localhost:7077",
        api_token=__import__("os").environ.get("HATCHET_API_TOKEN"),
    )
    client = HatchetClient(config)

    # Connect only if token is set (otherwise demo mock_run below)
    if config.api_token:
        client.connect()
    else:
        print("No HATCHET_API_TOKEN; demonstrating config and task decorator (no connect).")
        print("To run tasks, set HATCHET_API_TOKEN and start Hatchet (see deploy/).")
        return

    @client.task(name="basic-task", retries=2)
    async def basic_task(ctx):
        """A simple task that returns a result."""
        return {"message": "Hello from Hatchet"}

    async def run_mock() -> None:
        standalone = client._workflows["basic-task"]
        result = await standalone.aio_mock_run({})
        print("mock_run result:", result)

    asyncio.run(run_mock())
    client.disconnect()


if __name__ == "__main__":
    main()
