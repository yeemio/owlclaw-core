"""In-memory metrics for LangChain integration."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


class MetricsCollector:
    """Collect execution counters and latency metrics."""

    def __init__(self) -> None:
        self._executions_total: dict[tuple[str, str], int] = defaultdict(int)
        self._durations_ms: dict[str, list[int]] = defaultdict(list)
        self._errors_total: dict[tuple[str, str], int] = defaultdict(int)
        self._fallback_total: dict[str, int] = defaultdict(int)
        self._retries_total: dict[str, int] = defaultdict(int)

    def record_execution(
        self,
        *,
        capability: str,
        status: str,
        duration_ms: int,
        error_type: str | None = None,
        fallback_used: bool = False,
        retry_count: int = 0,
    ) -> None:
        self._executions_total[(capability, status)] += 1
        self._durations_ms[capability].append(duration_ms)
        if error_type:
            self._errors_total[(capability, error_type)] += 1
        if fallback_used:
            self._fallback_total[capability] += 1
        if retry_count > 0:
            self._retries_total[capability] += retry_count

    def export_json(self) -> dict[str, Any]:
        return {
            "executions_total": {
                f"{capability}:{status}": value
                for (capability, status), value in sorted(self._executions_total.items())
            },
            "errors_total": {
                f"{capability}:{error_type}": value
                for (capability, error_type), value in sorted(self._errors_total.items())
            },
            "fallback_total": dict(sorted(self._fallback_total.items())),
            "retries_total": dict(sorted(self._retries_total.items())),
            "latency_ms": {
                capability: {
                    "count": len(values),
                    "avg": (sum(values) / len(values)) if values else 0.0,
                    "max": max(values) if values else 0,
                }
                for capability, values in sorted(self._durations_ms.items())
            },
        }

    def export_prometheus(self) -> str:
        lines: list[str] = []
        for (capability, status), value in sorted(self._executions_total.items()):
            lines.append(
                f'langchain_executions_total{{capability="{capability}",status="{status}"}} {value}'
            )
        for (capability, error_type), value in sorted(self._errors_total.items()):
            lines.append(
                f'langchain_errors_total{{capability="{capability}",error_type="{error_type}"}} {value}'
            )
        for capability, value in sorted(self._fallback_total.items()):
            lines.append(f'langchain_fallback_total{{capability="{capability}"}} {value}')
        for capability, value in sorted(self._retries_total.items()):
            lines.append(f'langchain_retries_total{{capability="{capability}"}} {value}')
        for capability, values in sorted(self._durations_ms.items()):
            if not values:
                continue
            avg = sum(values) / len(values)
            lines.append(f'langchain_latency_ms_avg{{capability="{capability}"}} {avg:.4f}')
            lines.append(f'langchain_latency_ms_max{{capability="{capability}"}} {max(values)}')
        return "\n".join(lines)
