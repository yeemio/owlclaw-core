"""Structured runtime object store for the workflow closed-loop protocol."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import sys


VALID_OBJECT_TYPES = {
    "finding",
    "triage_decision",
    "assignment",
    "delivery",
    "review_verdict",
    "merge_decision",
    "blocker",
}

OBJECT_DIRECTORIES = {
    "finding": "findings",
    "triage_decision": "triage",
    "assignment": "assignments",
    "delivery": "deliveries",
    "review_verdict": "verdicts",
    "merge_decision": "merges",
    "blocker": "blockers",
}

REQUIRED_FIELDS = {
    "finding": {"title", "summary", "severity", "refs", "relations"},
    "triage_decision": {"finding_ids", "decision", "reason"},
    "assignment": {"target_agent", "target_branch", "spec", "task_refs", "finding_ids", "acceptance"},
    "delivery": {"assignment_id", "branch", "commit_refs", "changed_files", "tests_run", "summary", "blockers"},
    "review_verdict": {"delivery_id", "verdict", "new_finding_ids", "merge_ready", "notes"},
    "merge_decision": {"verdict_id", "decision", "summary"},
    "blocker": {"source_type", "source_id", "summary", "owner"},
}

AUDIT_SOURCE_TYPES = {"audit", "audit_review"}
AUDIT_FINDING_METADATA_FIELDS = {"files", "dimensions", "thinking_lenses", "evidence", "code_changes_allowed"}

VALID_STATES = {
    "finding": {"new", "triaged", "assigned", "closed", "deferred", "rejected", "superseded", "merged"},
    "triage_decision": {"pending", "accepted", "deferred", "rejected", "split"},
    "assignment": {"pending", "claimed", "in_progress", "delivered", "returned", "cancelled", "blocked"},
    "delivery": {"pending_review", "reviewing", "approved", "fix_needed", "rejected"},
    "review_verdict": {"pending_main", "applied"},
    "merge_decision": {"pending", "merged", "reassigned", "blocked"},
    "blocker": {"open", "resolved"},
}

CLAIMABLE_OBJECT_TYPES = {"assignment", "delivery", "triage_decision", "review_verdict"}

STATE_TRANSITIONS = {
    "finding": {
        "new": {"triaged", "deferred", "rejected", "superseded"},
        "triaged": {"assigned", "closed", "deferred", "rejected"},
        "assigned": {"closed", "merged", "superseded"},
        "closed": set(),
        "deferred": {"triaged", "closed"},
        "rejected": set(),
        "superseded": set(),
        "merged": set(),
    },
    "triage_decision": {
        "pending": {"accepted", "deferred", "rejected", "split"},
        "accepted": set(),
        "deferred": set(),
        "rejected": set(),
        "split": set(),
    },
    "assignment": {
        "pending": {"claimed", "cancelled", "blocked"},
        "claimed": {"in_progress", "returned", "blocked", "cancelled"},
        "in_progress": {"delivered", "returned", "blocked", "cancelled"},
        "delivered": {"returned"},
        "returned": {"claimed", "cancelled"},
        "cancelled": set(),
        "blocked": {"claimed", "cancelled"},
    },
    "delivery": {
        "pending_review": {"reviewing", "approved", "fix_needed", "rejected"},
        "reviewing": {"approved", "fix_needed", "rejected"},
        "approved": set(),
        "fix_needed": set(),
        "rejected": set(),
    },
    "review_verdict": {
        "pending_main": {"applied"},
        "applied": set(),
    },
    "merge_decision": {
        "pending": {"merged", "reassigned", "blocked"},
        "merged": set(),
        "reassigned": set(),
        "blocked": set(),
    },
    "blocker": {
        "open": {"resolved"},
        "resolved": set(),
    },
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_dir(repo_root: Path) -> Path:
    return repo_root / ".kiro" / "runtime"


def _object_root(repo_root: Path, object_type: str) -> Path:
    return _runtime_dir(repo_root) / OBJECT_DIRECTORIES[object_type]


def _index_path(repo_root: Path, object_type: str) -> Path:
    return _object_root(repo_root, object_type) / "index.json"


def _bucket_name(object_type: str, status: str) -> str:
    if object_type == "finding":
        if status == "new":
            return "open"
        if status == "triaged":
            return "triaged"
        if status == "assigned":
            return "assigned"
        return "closed"
    if object_type == "triage_decision":
        return "pending" if status == "pending" else "completed"
    if object_type == "assignment":
        if status == "pending":
            return "pending"
        if status in {"claimed", "in_progress"}:
            return "active"
        if status == "delivered":
            return "delivered"
        return "reviewed"
    if object_type == "delivery":
        return "pending_review" if status in {"pending_review", "reviewing"} else "reviewed"
    if object_type == "review_verdict":
        return "pending_main" if status == "pending_main" else "applied"
    if object_type == "merge_decision":
        return "pending" if status == "pending" else "completed"
    if object_type == "blocker":
        return status
    raise ValueError(f"unsupported object type '{object_type}'")


def ensure_object_dirs(repo_root: Path) -> None:
    runtime_dir = _runtime_dir(repo_root)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    for object_type in sorted(VALID_OBJECT_TYPES):
        root = _object_root(repo_root, object_type)
        root.mkdir(parents=True, exist_ok=True)
        for bucket in _bucket_names_for_object(object_type):
            (root / bucket).mkdir(parents=True, exist_ok=True)
        index_path = _index_path(repo_root, object_type)
        if not index_path.exists():
            index_path.write_text(
                json.dumps(
                    {
                        "object_type": object_type,
                        "updated_at": _utc_now(),
                        "total": 0,
                        "by_status": {},
                        "objects": [],
                    },
                    ensure_ascii=True,
                    indent=2,
                ),
                encoding="utf-8",
            )


def _bucket_names_for_object(object_type: str) -> list[str]:
    if object_type == "finding":
        return ["open", "triaged", "assigned", "closed"]
    if object_type == "triage_decision":
        return ["pending", "completed"]
    if object_type == "assignment":
        return ["pending", "active", "delivered", "reviewed"]
    if object_type == "delivery":
        return ["pending_review", "reviewed"]
    if object_type == "review_verdict":
        return ["pending_main", "applied"]
    if object_type == "merge_decision":
        return ["pending", "completed"]
    if object_type == "blocker":
        return ["open", "resolved"]
    raise ValueError(f"unsupported object type '{object_type}'")


def generate_object_id(object_type: str) -> str:
    validate_object_type(object_type)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    return f"{object_type}-{timestamp}"


def validate_object_type(object_type: str) -> str:
    if object_type not in VALID_OBJECT_TYPES:
        raise ValueError(f"unsupported object type '{object_type}'")
    return object_type


def validate_payload(object_type: str, payload: dict[str, Any]) -> None:
    validate_object_type(object_type)
    missing = REQUIRED_FIELDS[object_type] - payload.keys()
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise ValueError(f"{object_type} missing required fields: {missing_list}")
    status = payload.get("status")
    if not isinstance(status, str) or status not in VALID_STATES[object_type]:
        raise ValueError(f"{object_type} has invalid status '{status}'")
    object_id = payload.get("id")
    if not isinstance(object_id, str) or not object_id.strip():
        raise ValueError(f"{object_type} requires non-empty id")
    owner = payload.get("owner")
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError(f"{object_type} requires non-empty owner")
    for field_name in ("created_at", "updated_at"):
        value = payload.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{object_type} requires non-empty {field_name}")
    history = payload.get("history")
    if not isinstance(history, list):
        raise ValueError(f"{object_type} requires history list")
    if object_type == "finding" and payload.get("source_type") in AUDIT_SOURCE_TYPES:
        _validate_audit_finding_metadata(payload.get("audit_metadata"))
    if object_type in CLAIMABLE_OBJECT_TYPES:
        _validate_claim(payload.get("claim"))


def _validate_claim(claim: Any) -> None:
    if claim is None:
        return
    if not isinstance(claim, dict):
        raise ValueError("claim must be a dict when present")
    required = {"claimed_by", "claimed_at", "heartbeat_at", "lease_expires_at", "lease_seconds"}
    missing = required - claim.keys()
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise ValueError(f"claim missing required fields: {missing_list}")
    for field_name in ("claimed_by", "claimed_at", "heartbeat_at", "lease_expires_at"):
        value = claim.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"claim requires non-empty {field_name}")
    lease_seconds = claim.get("lease_seconds")
    if not isinstance(lease_seconds, int) or lease_seconds <= 0:
        raise ValueError("claim requires positive integer lease_seconds")


def _validate_audit_finding_metadata(metadata: Any) -> None:
    if not isinstance(metadata, dict):
        raise ValueError("audit finding requires audit_metadata")
    missing = AUDIT_FINDING_METADATA_FIELDS - metadata.keys()
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise ValueError(f"audit_metadata missing required fields: {missing_list}")
    files = metadata.get("files")
    dimensions = metadata.get("dimensions")
    thinking_lenses = metadata.get("thinking_lenses")
    evidence = metadata.get("evidence")
    code_changes_allowed = metadata.get("code_changes_allowed")
    if not isinstance(files, list) or not files or not all(isinstance(item, str) and item.strip() for item in files):
        raise ValueError("audit_metadata.files must be a non-empty list of file paths")
    if not isinstance(dimensions, list) or not dimensions or not all(isinstance(item, str) and item.strip() for item in dimensions):
        raise ValueError("audit_metadata.dimensions must be a non-empty list")
    if not isinstance(thinking_lenses, list) or not thinking_lenses or not all(
        isinstance(item, str) and item.strip() for item in thinking_lenses
    ):
        raise ValueError("audit_metadata.thinking_lenses must be a non-empty list")
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("audit_metadata.evidence must be non-empty")
    if code_changes_allowed is not False:
        raise ValueError("audit_metadata.code_changes_allowed must be false for audit findings")


def create_object(
    repo_root: Path,
    object_type: str,
    *,
    payload: dict[str, Any],
) -> dict[str, Any]:
    validate_object_type(object_type)
    ensure_object_dirs(repo_root)
    now = _utc_now()
    object_id = str(payload.get("id") or generate_object_id(object_type))
    document = {
        "schema_version": 1,
        "id": object_id,
        "object_type": object_type,
        "status": payload.get("status"),
        "owner": payload.get("owner"),
        "created_at": payload.get("created_at", now),
        "updated_at": payload.get("updated_at", now),
        "history": list(payload.get("history", [])),
        **payload,
    }
    validate_payload(object_type, document)
    _write_object(repo_root, object_type, document)
    _rebuild_index(repo_root, object_type)
    return document


def read_object(repo_root: Path, object_type: str, object_id: str) -> dict[str, Any]:
    validate_object_type(object_type)
    ensure_object_dirs(repo_root)
    for bucket in _bucket_names_for_object(object_type):
        candidate = _object_root(repo_root, object_type) / bucket / f"{object_id}.json"
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"{object_type} '{object_id}' not found")


def update_object_status(
    repo_root: Path,
    object_type: str,
    object_id: str,
    *,
    new_status: str,
    actor: str,
    reason: str = "",
    extra_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    document = read_object(repo_root, object_type, object_id)
    current_status = str(document["status"])
    if new_status not in VALID_STATES[object_type]:
        raise ValueError(f"{object_type} has invalid target status '{new_status}'")
    allowed = STATE_TRANSITIONS[object_type][current_status]
    if new_status not in allowed:
        raise ValueError(f"illegal {object_type} state transition: {current_status} -> {new_status}")
    source_path = _object_path(repo_root, object_type, current_status, object_id)
    document["status"] = new_status
    document["updated_at"] = _utc_now()
    document["history"] = [
        *document.get("history", []),
        {
            "at": document["updated_at"],
            "actor": actor,
            "from": current_status,
            "to": new_status,
            "reason": reason,
        },
    ]
    if extra_updates:
        document.update(extra_updates)
    validate_payload(object_type, document)
    if source_path.exists():
        source_path.unlink()
    _write_object(repo_root, object_type, document)
    _rebuild_index(repo_root, object_type)
    return document


def list_objects(repo_root: Path, object_type: str) -> list[dict[str, Any]]:
    validate_object_type(object_type)
    ensure_object_dirs(repo_root)
    objects: list[dict[str, Any]] = []
    for bucket in _bucket_names_for_object(object_type):
        for file_path in sorted((_object_root(repo_root, object_type) / bucket).glob("*.json")):
            objects.append(json.loads(file_path.read_text(encoding="utf-8")))
    return sorted(objects, key=lambda item: str(item.get("created_at", "")))


def build_object_summary(repo_root: Path) -> dict[str, Any]:
    ensure_object_dirs(repo_root)
    summary: dict[str, Any] = {
        "updated_at": _utc_now(),
        "total_objects": 0,
        "by_type": {},
        "stalled_objects": [],
    }
    total = 0
    for object_type in sorted(VALID_OBJECT_TYPES):
        index = json.loads(_index_path(repo_root, object_type).read_text(encoding="utf-8"))
        summary["by_type"][object_type] = {
            "total": index.get("total", 0),
            "by_status": index.get("by_status", {}),
        }
        total += int(index.get("total", 0))
    summary["total_objects"] = total
    summary["stalled_objects"] = find_stale_objects(repo_root)
    return summary


def claim_object(
    repo_root: Path,
    object_type: str,
    object_id: str,
    *,
    actor: str,
    lease_seconds: int = 900,
) -> dict[str, Any]:
    validate_object_type(object_type)
    if object_type not in CLAIMABLE_OBJECT_TYPES:
        raise ValueError(f"{object_type} does not support claims")
    now = datetime.now(timezone.utc)
    lease_expires_at = (now.timestamp() + lease_seconds)
    return read_modify_write_object(
        repo_root,
        object_type,
        object_id,
        updates={
            "claim": {
                "claimed_by": actor,
                "claimed_at": now.isoformat(),
                "heartbeat_at": now.isoformat(),
                "lease_expires_at": datetime.fromtimestamp(lease_expires_at, timezone.utc).isoformat(),
                "lease_seconds": lease_seconds,
            }
        },
    )


def refresh_object_claim(repo_root: Path, object_type: str, object_id: str, *, actor: str) -> dict[str, Any]:
    document = read_object(repo_root, object_type, object_id)
    claim = document.get("claim")
    if not isinstance(claim, dict) or claim.get("claimed_by") != actor:
        raise ValueError(f"{object_type} '{object_id}' is not claimed by {actor}")
    lease_seconds = int(claim.get("lease_seconds", 0))
    if lease_seconds <= 0:
        raise ValueError(f"{object_type} '{object_id}' has invalid lease_seconds")
    now = datetime.now(timezone.utc)
    return read_modify_write_object(
        repo_root,
        object_type,
        object_id,
        updates={
            "claim": {
                **claim,
                "heartbeat_at": now.isoformat(),
                "lease_expires_at": datetime.fromtimestamp(now.timestamp() + lease_seconds, timezone.utc).isoformat(),
            }
        },
    )


def clear_object_claim(repo_root: Path, object_type: str, object_id: str) -> dict[str, Any]:
    return read_modify_write_object(repo_root, object_type, object_id, updates={"claim": None})


def read_modify_write_object(
    repo_root: Path,
    object_type: str,
    object_id: str,
    *,
    updates: dict[str, Any],
) -> dict[str, Any]:
    document = read_object(repo_root, object_type, object_id)
    status = str(document["status"])
    path = _object_path(repo_root, object_type, status, object_id)
    document.update(updates)
    document["updated_at"] = _utc_now()
    validate_payload(object_type, document)
    if path.exists():
        path.unlink()
    _write_object(repo_root, object_type, document)
    _rebuild_index(repo_root, object_type)
    return document


def find_stale_objects(repo_root: Path, *, stale_seconds: int = 900) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    stalled: list[dict[str, Any]] = []
    for object_type in sorted(VALID_OBJECT_TYPES):
        for item in list_objects(repo_root, object_type):
            claim = item.get("claim")
            if isinstance(claim, dict):
                lease_expires_at = _parse_dt(claim.get("lease_expires_at"))
                if lease_expires_at is not None and lease_expires_at <= now:
                    stalled.append(
                        {
                            "object_type": object_type,
                            "id": item["id"],
                            "status": item["status"],
                            "reason": "lease_expired",
                            "owner": item.get("owner", ""),
                        }
                    )
                    continue
            if object_type == "blocker" or item["status"] in {"closed", "resolved", "merged", "applied", "accepted"}:
                continue
            updated_at = _parse_dt(item.get("updated_at"))
            if updated_at is None:
                continue
            age_seconds = (now - updated_at).total_seconds()
            if age_seconds >= stale_seconds:
                stalled.append(
                    {
                        "object_type": object_type,
                        "id": item["id"],
                        "status": item["status"],
                        "reason": "stale_status",
                        "owner": item.get("owner", ""),
                        "age_seconds": int(age_seconds),
                    }
                )
    return stalled


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read and write workflow protocol objects.")
    parser.add_argument("--repo-root", default=".", help="Path to the main repository root.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    summary = subparsers.add_parser("summary", help="Show object summary.")
    summary.add_argument("--json", action="store_true", help="Emit JSON output.")

    create = subparsers.add_parser("create", help="Create a workflow object from a JSON payload file.")
    create.add_argument("--type", required=True, choices=sorted(VALID_OBJECT_TYPES))
    create.add_argument("--payload-file", required=True, help="Path to a JSON payload file.")
    create.add_argument("--json", action="store_true", help="Emit JSON output.")

    transition = subparsers.add_parser("transition", help="Update object status.")
    transition.add_argument("--type", required=True, choices=sorted(VALID_OBJECT_TYPES))
    transition.add_argument("--id", required=True, help="Object id.")
    transition.add_argument("--status", required=True, help="New status.")
    transition.add_argument("--actor", required=True, help="Actor performing the transition.")
    transition.add_argument("--reason", default="", help="Transition reason.")
    transition.add_argument("--json", action="store_true", help="Emit JSON output.")

    list_cmd = subparsers.add_parser("list", help="List objects of one type.")
    list_cmd.add_argument("--type", required=True, choices=sorted(VALID_OBJECT_TYPES))
    list_cmd.add_argument("--json", action="store_true", help="Emit JSON output.")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve()

    if args.command == "summary":
        payload = build_object_summary(repo_root)
        if args.json:
            print(json.dumps(payload, ensure_ascii=True, indent=2))
        else:
            print(f"total_objects={payload['total_objects']}")
        return 0

    if args.command == "create":
        payload_file = Path(args.payload_file).resolve()
        payload = json.loads(payload_file.read_text(encoding="utf-8"))
        created = create_object(repo_root, args.type, payload=payload)
        if args.json:
            print(json.dumps(created, ensure_ascii=True, indent=2))
        else:
            print(f"{args.type}:{created['id']} status={created['status']}")
        return 0

    if args.command == "transition":
        updated = update_object_status(
            repo_root,
            args.type,
            args.id,
            new_status=args.status,
            actor=args.actor,
            reason=args.reason,
        )
        if args.json:
            print(json.dumps(updated, ensure_ascii=True, indent=2))
        else:
            print(f"{args.type}:{updated['id']} status={updated['status']}")
        return 0

    if args.command == "list":
        payload = list_objects(repo_root, args.type)
        if args.json:
            print(json.dumps(payload, ensure_ascii=True, indent=2))
        else:
            print(f"{args.type} count={len(payload)}")
        return 0

    raise AssertionError(f"unsupported command: {args.command}")


def _write_object(repo_root: Path, object_type: str, document: dict[str, Any]) -> None:
    status = str(document["status"])
    target_path = _object_path(repo_root, object_type, status, str(document["id"]))
    target_path.write_text(json.dumps(document, ensure_ascii=True, indent=2), encoding="utf-8")


def _object_path(repo_root: Path, object_type: str, status: str, object_id: str) -> Path:
    bucket = _bucket_name(object_type, status)
    return _object_root(repo_root, object_type) / bucket / f"{object_id}.json"


def _rebuild_index(repo_root: Path, object_type: str) -> None:
    objects = list_objects(repo_root, object_type)
    counts = Counter(str(item["status"]) for item in objects)
    payload = {
        "object_type": object_type,
        "updated_at": _utc_now(),
        "total": len(objects),
        "by_status": dict(sorted(counts.items())),
        "objects": [
            {
                "id": item["id"],
                "status": item["status"],
                "owner": item["owner"],
                "updated_at": item["updated_at"],
            }
            for item in objects
        ],
    }
    _index_path(repo_root, object_type).write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
