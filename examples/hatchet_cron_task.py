"""
Cron task — register a task that runs on a schedule (e.g. every 5 minutes).

Requires Hatchet server and HATCHET_API_TOKEN. Start worker to consume cron triggers.
"""

from pathlib import Path

from owlclaw.integrations.hatchet import HatchetClient, HatchetConfig


def main() -> None:
    config_path = Path(__file__).parent.parent / "deploy" / "owlclaw.yaml.example"
    if config_path.exists():
        config = HatchetConfig.from_yaml(config_path)
    else:
        config = HatchetConfig(
            server_url="http://localhost:7077",
            api_token=__import__("os").environ.get("HATCHET_API_TOKEN"),
        )

    client = HatchetClient(config)
    client.connect()

    # Cron: 5 fields = min hour day month dow (e.g. every 5 minutes: */5 * * * *)
    @client.task(name="cron-task", cron="*/5 * * * *")
    async def cron_task(ctx):
        return {"trigger": "cron", "minute": "every 5"}

    print("Registered cron task. Start worker with client.start_worker() to run (blocking).")
    # client.start_worker()


if __name__ == "__main__":
    main()
