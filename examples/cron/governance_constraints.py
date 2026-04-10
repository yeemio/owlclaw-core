"""Cron governance constraints example."""

from owlclaw import OwlClaw

app = OwlClaw("governed-cron-agent")

app.configure(
    triggers={
        "cron": {"enabled": True, "max_concurrent": 5},
        "governance": {"max_daily_runs": 8, "max_daily_cost": 5.0, "cooldown_seconds": 120},
        "retry": {"retry_on_failure": True, "max_retries": 2, "retry_delay_seconds": 30},
        "notifications": {"enabled": True, "channels": ["slack"]},
    }
)


@app.cron("*/15 * * * *", event_name="governed_task", focus="operations")
async def governed_fallback() -> dict:
    return {"status": "fallback_ok", "task": "governed_task"}
