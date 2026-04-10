"""Execute gateway runtime ops drills for rollback and full rollout paths."""

from __future__ import annotations

from pathlib import Path

from scripts.gateway_ops_gate import GateInput, evaluate_gate, execute_rollback


def run_drill(repo: Path) -> int:
    reports = repo / "docs" / "ops" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    report = reports / "gateway-ops-drill-latest.md"

    canary_fail = evaluate_gate(
        GateInput(
            error_rate=0.03,
            p95_latency_increase=0.10,
            has_critical_alert=False,
            metrics_available=True,
        )
    )
    rollback = execute_rollback(target_version="v1.0.0", current_version="v1.1.0")

    full_pass = evaluate_gate(
        GateInput(
            error_rate=0.005,
            p95_latency_increase=0.05,
            has_critical_alert=False,
            metrics_available=True,
        )
    )

    canary_ok = canary_fail.decision == "rollback" and rollback["status"] == "executed"
    full_ok = full_pass.decision == "promote"
    report.write_text(
        "\n".join(
            [
                "# Gateway Ops Drill Report",
                "",
                f"- canary_auto_rollback=true: {str(canary_ok).lower()}",
                f"- full_rollout_success=true: {str(full_ok).lower()}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return 0 if canary_ok and full_ok else 2


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    raise SystemExit(run_drill(repo))


if __name__ == "__main__":
    main()

