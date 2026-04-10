"""Gateway rollout gate and rollback helpers."""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import json


@dataclass(frozen=True)
class GateInput:
    error_rate: float
    p95_latency_increase: float
    has_critical_alert: bool
    metrics_available: bool


@dataclass(frozen=True)
class GateResult:
    decision: str
    reason: str
    should_rollback: bool


def evaluate_gate(metrics: GateInput) -> GateResult:
    if not metrics.metrics_available:
        return GateResult(decision="block", reason="missing_metrics", should_rollback=False)
    if metrics.has_critical_alert:
        return GateResult(decision="rollback", reason="critical_alert", should_rollback=True)
    if metrics.error_rate > 0.02:
        return GateResult(decision="rollback", reason="error_rate_threshold", should_rollback=True)
    if metrics.p95_latency_increase > 0.40:
        return GateResult(decision="rollback", reason="latency_threshold", should_rollback=True)
    return GateResult(decision="promote", reason="slo_green", should_rollback=False)


def execute_rollback(target_version: str, current_version: str) -> dict[str, str]:
    return {
        "action": "rollback",
        "from": current_version,
        "to": target_version,
        "status": "executed",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate gateway rollout gate input.")
    parser.add_argument("--error-rate", type=float, required=True)
    parser.add_argument("--latency-increase", type=float, required=True)
    parser.add_argument("--critical-alert", action="store_true")
    parser.add_argument("--metrics-missing", action="store_true")
    args = parser.parse_args()
    result = evaluate_gate(
        GateInput(
            error_rate=args.error_rate,
            p95_latency_increase=args.latency_increase,
            has_critical_alert=args.critical_alert,
            metrics_available=not args.metrics_missing,
        )
    )
    print(json.dumps(result.__dict__, sort_keys=True))


if __name__ == "__main__":
    main()
