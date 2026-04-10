"""Weekday morning decision cron example."""

from owlclaw import OwlClaw

app = OwlClaw("trading-agent")


@app.cron(
    "25 9 * * 1-5",
    event_name="morning_decision",
    focus="trading_decision",
    max_daily_runs=1,
)
async def morning_fallback() -> dict:
    return {"status": "fallback_ok", "stage": "morning_decision"}
