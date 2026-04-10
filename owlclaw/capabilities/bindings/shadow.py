"""Shadow mode query helpers for declarative bindings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from owlclaw.governance.ledger import LedgerQueryFilters


@dataclass(slots=True)
class ShadowExecutionRecord:
    """Normalized shadow execution view from ledger records."""

    tool_name: str
    run_id: str
    binding_type: str
    mode: str
    status: str
    parameters: dict[str, Any]
    result_summary: str
    elapsed_ms: int
    created_at: datetime | None


class LedgerQueryProtocol(Protocol):
    """Protocol for querying ledger records."""

    async def query_records(self, tenant_id: str, filters: LedgerQueryFilters) -> list[Any]:
        """Query records from governance ledger."""


def _redact_shadow_parameters(raw: dict[str, Any]) -> dict[str, str]:
    """Keep parameter shape only, never raw values."""
    redacted: dict[str, str] = {}
    for key, value in raw.items():
        redacted[str(key)] = type(value).__name__
    return redacted


def _build_shadow_result_summary(output_result: dict[str, Any], *, row_status: str) -> str:
    """Build a shadow summary from non-sensitive execution metadata only."""
    parts: list[str] = []
    for key in ("status", "mode", "executed", "row_count", "affected_rows", "truncated", "column_count", "sent"):
        if key in output_result:
            parts.append(f"{key}={output_result[key]}")
    if not parts:
        return f"status={row_status or 'unknown'}"
    return ", ".join(parts)


async def query_shadow_results(
    ledger: LedgerQueryProtocol,
    *,
    tenant_id: str,
    tool_name: str,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = 100,
) -> list[ShadowExecutionRecord]:
    """Query shadow-mode binding results by tool name and optional time range."""
    filters = LedgerQueryFilters(
        capability_name=tool_name,
        start_date=start_at.date() if start_at is not None else None,
        end_date=end_at.date() if end_at is not None else None,
        limit=limit,
        order_by="created_at DESC",
    )
    rows = await ledger.query_records(tenant_id, filters)
    records: list[ShadowExecutionRecord] = []
    for row in rows:
        input_params = getattr(row, "input_params", {}) or {}
        output_result = getattr(row, "output_result", {}) or {}
        mode = str(output_result.get("mode", input_params.get("mode", ""))).strip().lower()
        if mode != "shadow":
            continue
        raw_parameters = input_params.get("parameters", {})
        parameters = _redact_shadow_parameters(raw_parameters) if isinstance(raw_parameters, dict) else {}
        row_status = str(getattr(row, "status", ""))
        records.append(
            ShadowExecutionRecord(
                tool_name=str(getattr(row, "capability_name", tool_name)),
                run_id=str(getattr(row, "run_id", "")),
                binding_type=str(input_params.get("binding_type", "")),
                mode=mode,
                status=row_status,
                parameters=parameters,
                result_summary=_build_shadow_result_summary(output_result, row_status=row_status),
                elapsed_ms=int(output_result.get("elapsed_ms", getattr(row, "execution_time_ms", 0)) or 0),
                created_at=getattr(row, "created_at", None),
            )
        )
    return records
