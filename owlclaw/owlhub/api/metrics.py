"""In-process metrics collector for OwlHub API observability."""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass

from owlclaw.owlhub.statistics import SkillStatistics


@dataclass
class _LatencyAggregate:
    count: int = 0
    sum_ms: float = 0.0


class MetricsCollector:
    """Collect API request metrics and export Prometheus text format."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests_total = 0
        self._errors_total = 0
        self._requests_by_route: dict[tuple[str, str, int], int] = defaultdict(int)
        self._latency_by_route: dict[tuple[str, str], _LatencyAggregate] = defaultdict(_LatencyAggregate)

    def record_request(self, *, method: str, path: str, status_code: int, duration_ms: float) -> None:
        key = (method, path, status_code)
        latency_key = (method, path)
        with self._lock:
            self._requests_total += 1
            if status_code >= 500:
                self._errors_total += 1
            self._requests_by_route[key] += 1
            agg = self._latency_by_route[latency_key]
            agg.count += 1
            agg.sum_ms += duration_ms

    def export_prometheus(self, *, skill_stats: list[SkillStatistics]) -> str:
        lines: list[str] = []
        lines.append("# HELP owlhub_api_requests_total Total number of API requests.")
        lines.append("# TYPE owlhub_api_requests_total counter")
        lines.append(f"owlhub_api_requests_total {float(self._requests_total):.0f}")

        lines.append("# HELP owlhub_api_errors_total Total number of API 5xx responses.")
        lines.append("# TYPE owlhub_api_errors_total counter")
        lines.append(f"owlhub_api_errors_total {float(self._errors_total):.0f}")

        error_rate = 0.0
        if self._requests_total > 0:
            error_rate = self._errors_total / self._requests_total
        lines.append("# HELP owlhub_api_error_rate API error rate over all requests.")
        lines.append("# TYPE owlhub_api_error_rate gauge")
        lines.append(f"owlhub_api_error_rate {error_rate:.6f}")

        lines.append("# HELP owlhub_api_requests_by_route_total API requests grouped by method/path/status.")
        lines.append("# TYPE owlhub_api_requests_by_route_total counter")
        for (method, path, status_code), value in sorted(self._requests_by_route.items()):
            labels = f'method="{method}",path="{path}",status="{status_code}"'
            lines.append(f"owlhub_api_requests_by_route_total{{{labels}}} {float(value):.0f}")

        lines.append("# HELP owlhub_api_request_latency_ms_sum Sum of request latency in milliseconds.")
        lines.append("# TYPE owlhub_api_request_latency_ms_sum counter")
        lines.append("# HELP owlhub_api_request_latency_ms_count Count of requests with measured latency.")
        lines.append("# TYPE owlhub_api_request_latency_ms_count counter")
        for (method, path), agg in sorted(self._latency_by_route.items()):
            labels = f'method="{method}",path="{path}"'
            lines.append(f"owlhub_api_request_latency_ms_sum{{{labels}}} {agg.sum_ms:.6f}")
            lines.append(f"owlhub_api_request_latency_ms_count{{{labels}}} {float(agg.count):.0f}")

        lines.append("# HELP owlhub_skill_downloads_total Skill download counts.")
        lines.append("# TYPE owlhub_skill_downloads_total gauge")
        lines.append("# HELP owlhub_skill_installs_total Skill install counts.")
        lines.append("# TYPE owlhub_skill_installs_total gauge")
        for item in sorted(skill_stats, key=lambda row: (row.publisher, row.skill_name)):
            labels = f'publisher="{item.publisher}",skill="{item.skill_name}"'
            lines.append(f"owlhub_skill_downloads_total{{{labels}}} {float(item.total_downloads):.0f}")
            lines.append(f"owlhub_skill_installs_total{{{labels}}} {float(item.total_installs):.0f}")

        lines.append("# HELP owlhub_db_pool_configured Whether a DB pool is configured (1/0).")
        lines.append("# TYPE owlhub_db_pool_configured gauge")
        lines.append("owlhub_db_pool_configured 0")
        lines.append("# HELP owlhub_db_pool_size Current DB pool size (0 when index-backed mode).")
        lines.append("# TYPE owlhub_db_pool_size gauge")
        lines.append("owlhub_db_pool_size 0")
        lines.append("# HELP owlhub_db_pool_in_use Current DB pool in-use connections (0 when index-backed mode).")
        lines.append("# TYPE owlhub_db_pool_in_use gauge")
        lines.append("owlhub_db_pool_in_use 0")

        return "\n".join(lines) + "\n"
