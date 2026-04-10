"""Rate limit constraint: hide capability when daily limit or cooldown is exceeded."""

from datetime import date, datetime, timezone

from owlclaw.governance.ledger import Ledger, LedgerQueryFilters
from owlclaw.governance.visibility import CapabilityView, FilterResult, RunContext


class RateLimitConstraint:
    """Evaluates visibility based on max_daily_calls and cooldown_seconds."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    @staticmethod
    def _parse_non_negative_int(value: object) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if not isinstance(value, int | float | str | bytes | bytearray):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        if parsed < 0:
            return None
        return parsed

    async def evaluate(
        self,
        capability: CapabilityView,
        agent_id: str,
        context: RunContext,
    ) -> FilterResult:
        """Allow capability unless daily limit or cooldown is exceeded."""
        constraints = capability.metadata.get("owlclaw", {}).get(
            "constraints", {}
        )
        max_daily_calls = self._parse_non_negative_int(constraints.get("max_daily_calls"))
        cooldown_seconds = self._parse_non_negative_int(constraints.get("cooldown_seconds"))

        if max_daily_calls is None and cooldown_seconds is None:
            return FilterResult(visible=True)

        if max_daily_calls is not None:
            count = await self._get_daily_call_count(
                context.tenant_id, agent_id, capability.name
            )
            if count >= max_daily_calls:
                return FilterResult(
                    visible=False,
                    reason=(
                        f"Daily call limit exceeded ({count}/{max_daily_calls})"
                    ),
                )

        if cooldown_seconds is not None:
            last_call = await self._get_last_call_time(
                context.tenant_id, agent_id, capability.name
            )
            if last_call is not None:
                elapsed = (
                    datetime.now(timezone.utc) - last_call
                ).total_seconds()
                if elapsed < cooldown_seconds:
                    remaining = int(cooldown_seconds - elapsed)
                    return FilterResult(
                        visible=False,
                        reason=f"Cooldown active ({remaining}s remaining)",
                    )
        return FilterResult(visible=True)

    async def _get_daily_call_count(
        self, tenant_id: str, agent_id: str, capability_name: str
    ) -> int:
        """Return number of records for today for this agent/capability."""
        today = date.today()
        records = await self.ledger.query_records(
            tenant_id=tenant_id,
            filters=LedgerQueryFilters(
                agent_id=agent_id,
                capability_name=capability_name,
                start_date=today,
                end_date=today,
            ),
        )
        return len(records)

    async def _get_last_call_time(
        self, tenant_id: str, agent_id: str, capability_name: str
    ) -> datetime | None:
        """Return created_at of most recent record, or None."""
        records = await self.ledger.query_records(
            tenant_id=tenant_id,
            filters=LedgerQueryFilters(
                agent_id=agent_id,
                capability_name=capability_name,
                limit=1,
                order_by="created_at DESC",
            ),
        )
        if not records:
            return None
        created_at = records[0].created_at
        if not isinstance(created_at, datetime):
            return None
        if created_at.tzinfo is None:
            return created_at.replace(tzinfo=timezone.utc)
        return created_at.astimezone(timezone.utc)
