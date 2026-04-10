"""Hourly inventory check cron example."""

from owlclaw import OwlClaw

app = OwlClaw("inventory-agent")


@app.cron("0 * * * *", event_name="hourly_inventory_check", focus="inventory")
async def inventory_fallback() -> dict:
    return {"status": "fallback_ok", "source": "inventory"}
