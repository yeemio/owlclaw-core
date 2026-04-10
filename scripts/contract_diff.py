"""Protocol contract diff classifier and governance gate helper."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml


def load_contract(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"Contract root must be an object: {path}")
    return data


def compute_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    changes = {"removed": [], "added": [], "changed": []}
    _walk_diff(before, after, "", changes)
    return changes


def classify_change_level(diff: dict[str, list[str]]) -> str:
    if diff["removed"] or diff["changed"]:
        return "breaking"
    if diff["added"]:
        return "additive"
    return "compatible"


def evaluate_gate(
    change_level: str,
    mode: str,
    migration_plan: str | None,
    exemption_ticket: str | None,
) -> tuple[str, int]:
    has_migration = bool(migration_plan)
    if mode == "warning":
        if change_level == "breaking" and not has_migration:
            return "warn", 0
        return "pass", 0
    if change_level == "breaking" and exemption_ticket:
        return "warn", 0
    if change_level == "breaking" and not has_migration:
        return "block", 2
    return "pass", 0


def run(
    before_path: Path,
    after_path: Path,
    mode: str,
    migration_plan: str | None,
    exemption_ticket: str | None,
    audit_log: Path | None,
    output: Path | None,
) -> int:
    before = load_contract(before_path)
    after = load_contract(after_path)
    diff = compute_diff(before, after)
    change_level = classify_change_level(diff)
    decision, code = evaluate_gate(change_level, mode, migration_plan, exemption_ticket)

    report = {
        "before": str(before_path),
        "after": str(after_path),
        "mode": mode,
        "change_level": change_level,
        "gate_decision": decision,
        "migration_plan": migration_plan,
        "exemption_ticket": exemption_ticket,
        "diff": diff,
    }
    payload = json.dumps(report, indent=2, sort_keys=True)
    print(payload)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
    if audit_log is not None and exemption_ticket:
        audit_log.parent.mkdir(parents=True, exist_ok=True)
        with audit_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report, sort_keys=True) + "\n")
    return code


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classify contract diffs and evaluate governance gate decision."
    )
    parser.add_argument("--before", type=Path, required=True, help="Path to previous contract JSON/YAML.")
    parser.add_argument("--after", type=Path, required=True, help="Path to current contract JSON/YAML.")
    parser.add_argument(
        "--mode",
        choices=["warning", "blocking"],
        default="warning",
        help="Gate mode: warning does not fail CI; blocking fails on breaking without migration plan.",
    )
    parser.add_argument(
        "--migration-plan",
        default=None,
        help="Migration plan reference or URL. Required for breaking changes in blocking mode.",
    )
    parser.add_argument(
        "--exemption-ticket",
        default=None,
        help="Approved exemption ticket id; allows warning pass in blocking mode and records audit.",
    )
    parser.add_argument(
        "--audit-log",
        type=Path,
        default=None,
        help="Optional JSONL audit log path for exemption records.",
    )
    parser.add_argument("--output", type=Path, default=None, help="Optional report output path.")
    args = parser.parse_args()
    raise SystemExit(
        run(
            args.before,
            args.after,
            args.mode,
            args.migration_plan,
            args.exemption_ticket,
            args.audit_log,
            args.output,
        )
    )


def _walk_diff(
    before: Any,
    after: Any,
    path: str,
    changes: dict[str, list[str]],
) -> None:
    if isinstance(before, dict) and isinstance(after, dict):
        keys = sorted(set(before.keys()) | set(after.keys()))
        for key in keys:
            child = f"{path}.{key}" if path else key
            if key not in after:
                changes["removed"].append(child)
                continue
            if key not in before:
                changes["added"].append(child)
                continue
            _walk_diff(before[key], after[key], child, changes)
        return
    if isinstance(before, list) and isinstance(after, list):
        if before == after:
            return
        before_set = {json.dumps(item, sort_keys=True) for item in before}
        after_set = {json.dumps(item, sort_keys=True) for item in after}
        for value in sorted(before_set - after_set):
            changes["removed"].append(f"{path}[]:{value}")
        for value in sorted(after_set - before_set):
            changes["added"].append(f"{path}[]:{value}")
        return
    if before != after:
        changes["changed"].append(path or "<root>")


if __name__ == "__main__":
    main()
