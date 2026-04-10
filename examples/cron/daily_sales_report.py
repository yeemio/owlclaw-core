"""Daily sales report cron example."""

from owlclaw import OwlClaw

app = OwlClaw("sales-agent")


@app.cron(
    "0 18 * * *",
    event_name="daily_sales_report",
    focus="sales_reporting",
    retry_on_failure=True,
    max_retries=3,
)
async def sales_report_fallback() -> dict:
    return {"status": "fallback_ok", "report": "daily_sales"}
