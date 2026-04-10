"""Nightly data cleanup cron example."""

from owlclaw import OwlClaw

app = OwlClaw("cleanup-agent")


@app.cron(
    "0 2 * * *",
    event_name="nightly_cleanup",
    focus="maintenance",
    max_daily_runs=1,
    cooldown_seconds=0,
)
async def cleanup_fallback() -> dict:
    return {"status": "fallback_ok", "task": "cleanup"}
