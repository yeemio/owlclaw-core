"""Release gate checks for OwlHub production rollout readiness."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from owlclaw.owlhub import OwlHubClient


@dataclass
class GateCheckResult:
    """Result of one release-gate check."""

    name: str
    passed: bool
    detail: str


@dataclass
class GateReport:
    """Aggregated release-gate report."""

    generated_at: str
    checks: list[GateCheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def as_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "passed": self.passed,
            "checks": [
                {"name": check.name, "passed": check.passed, "detail": check.detail}
                for check in self.checks
            ],
        }


def _get_json(url: str, timeout: int = 10) -> dict[str, object]:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    data = json.loads(payload)
    return data if isinstance(data, dict) else {}


def _get_text(url: str, timeout: int = 10) -> str:
    request = Request(url, headers={"Accept": "text/plain"})
    with urlopen(request, timeout=timeout) as response:
        payload = response.read()
    return str(payload.decode("utf-8"))


def check_api_health(api_base_url: str) -> GateCheckResult:
    try:
        data = _get_json(f"{api_base_url.rstrip('/')}/health")
        status = str(data.get("status", "")).lower()
        if status == "ok":
            return GateCheckResult("api_health", True, "health endpoint returned status=ok")
        return GateCheckResult("api_health", False, f"unexpected health payload: {data}")
    except Exception as exc:  # pragma: no cover - protected by unit tests via mocks
        return GateCheckResult("api_health", False, f"health endpoint failed: {exc}")


def check_api_metrics(api_base_url: str) -> GateCheckResult:
    try:
        metrics = _get_text(f"{api_base_url.rstrip('/')}/metrics")
        has_signal = "owlhub_" in metrics or "http_" in metrics
        if has_signal and metrics.strip():
            return GateCheckResult("api_metrics", True, "metrics endpoint returned prometheus payload")
        return GateCheckResult("api_metrics", False, "metrics payload is empty or missing expected prefixes")
    except Exception as exc:  # pragma: no cover
        return GateCheckResult("api_metrics", False, f"metrics endpoint failed: {exc}")


def check_index_access(index_url: str) -> GateCheckResult:
    try:
        data = _get_json(index_url)
        skills = data.get("skills")
        if isinstance(skills, list):
            return GateCheckResult(
                "index_access",
                True,
                f"index loaded with {len(skills)} skills",
            )
        return GateCheckResult("index_access", False, f"invalid index payload: {list(data.keys())}")
    except Exception as exc:  # pragma: no cover
        return GateCheckResult("index_access", False, f"index request failed: {exc}")


def check_cli_search(index_url: str, query: str, install_dir: Path, lock_file: Path) -> GateCheckResult:
    try:
        client = OwlHubClient(index_url=index_url, install_dir=install_dir, lock_file=lock_file)
        results = client.search(query=query)
        if results:
            return GateCheckResult(
                "cli_search",
                True,
                f"cli client search returned {len(results)} results for query '{query}'",
            )
        return GateCheckResult("cli_search", False, f"cli client search returned no results for query '{query}'")
    except Exception as exc:  # pragma: no cover
        return GateCheckResult("cli_search", False, f"cli client search failed: {exc}")


def run_release_gate(
    *,
    api_base_url: str,
    index_url: str,
    query: str,
    work_dir: Path,
) -> GateReport:
    report = GateReport(generated_at=datetime.now(timezone.utc).isoformat())
    report.checks.append(check_api_health(api_base_url))
    report.checks.append(check_api_metrics(api_base_url))
    report.checks.append(check_index_access(index_url))
    report.checks.append(
        check_cli_search(
            index_url=index_url,
            query=query,
            install_dir=work_dir / "skills",
            lock_file=work_dir / "skill-lock.json",
        )
    )
    return report
