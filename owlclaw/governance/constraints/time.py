"""Time constraint: hide capabilities outside trading hours when trading_hours_only is set."""

from collections.abc import Callable
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from owlclaw.governance.visibility import CapabilityView, FilterResult, RunContext


def _parse_time(value: str | time) -> time:
    """Parse 'HH:MM' or 'HH:MM:SS' to time, or return time as-is."""
    if isinstance(value, time):
        return value
    parts = str(value).strip().split(":")
    if len(parts) >= 2:
        return time(
            int(parts[0]),
            int(parts[1]),
            int(parts[2]) if len(parts) > 2 else 0,
        )
    return time(9, 30)


def _parse_weekdays(value: list[int] | None) -> list[int]:
    """Return list of weekday indices (0=Monday). Default Mon–Fri."""
    if value is not None and len(value) > 0:
        return list(value)
    return [0, 1, 2, 3, 4]


class TimeConstraint:
    """Evaluates visibility based on current time and trading_hours_only constraint."""

    def __init__(self, config: dict | None = None) -> None:
        config = config or {}
        th = config.get("trading_hours", {})
        if not isinstance(th, dict):
            th = {}
        self._start_time = _parse_time(th.get("start", "09:30"))
        self._end_time = _parse_time(th.get("end", "15:00"))
        self._weekdays = _parse_weekdays(th.get("weekdays"))
        self.trading_hours = {
            "start": self._start_time,
            "end": self._end_time,
            "weekdays": self._weekdays,
        }
        tz_name = config.get("timezone", "Asia/Shanghai")
        try:
            self.timezone = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            self.timezone = ZoneInfo("UTC")
        self._now_cb: Callable[[], datetime] | None = None  # for tests

    async def evaluate(
        self,
        capability: CapabilityView,
        agent_id: str,
        context: RunContext,
    ) -> FilterResult:
        """Allow capability unless trading_hours_only is set and we are outside window."""
        constraints = capability.metadata.get("owlclaw", {}).get(
            "constraints", {}
        )
        if not constraints.get("trading_hours_only"):
            return FilterResult(visible=True)

        now = (
            self._now_cb()
            if self._now_cb is not None
            else datetime.now(self.timezone)
        )
        if now.tzinfo is None:
            now = now.replace(tzinfo=self.timezone)
        if now.weekday() not in self._weekdays:
            return FilterResult(visible=False, reason="Outside trading weekdays")
        current_time = now.time()
        start = self._start_time
        end = self._end_time
        if not (start <= current_time <= end):
            return FilterResult(
                visible=False,
                reason=f"Outside trading hours ({start}-{end})",
            )
        return FilterResult(visible=True)
