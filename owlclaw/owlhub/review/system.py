"""Review system for OwlHub Phase 2."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from owlclaw.owlhub.validator import Validator


class ReviewStatus(str, Enum):
    """Review status lifecycle."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ReviewRecord:
    """One review record."""

    review_id: str
    skill_name: str
    version: str
    publisher: str
    status: ReviewStatus
    comments: str
    reviewed_at: str


@dataclass(frozen=True)
class AppealRecord:
    """One appeal record for a rejected review."""

    review_id: str
    publisher: str
    reason: str
    appealed_at: str


class ReviewSystem:
    """Store and update review records with automated validation checks."""

    def __init__(self, *, storage_dir: Path, validator: Validator | None = None) -> None:
        self.storage_dir = storage_dir
        self.validator = validator or Validator()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.assigned_reviewers: dict[str, str] = {}
        self.notifications: list[dict[str, str]] = []

    def submit_for_review(self, *, skill_path: Path, skill_name: str, version: str, publisher: str) -> ReviewRecord:
        """Submit one skill for automated validation and review."""
        structure_result = self.validator.validate_structure(skill_path)
        if not structure_result.is_valid:
            comments = "; ".join(error.message for error in structure_result.errors) or "structure validation failed"
            return self._store_submission(
                skill_name=skill_name,
                version=version,
                publisher=publisher,
                status=ReviewStatus.REJECTED,
                comments=comments,
            )

        return self._store_submission(
            skill_name=skill_name,
            version=version,
            publisher=publisher,
            status=ReviewStatus.PENDING,
            comments="automated validation passed",
        )

    def submit_manifest_for_review(
        self,
        *,
        manifest: dict[str, Any],
        skill_name: str,
        version: str,
        publisher: str,
    ) -> ReviewRecord:
        """Submit one manifest payload for automated validation and review."""
        manifest_result = self.validator.validate_manifest(manifest)
        if not manifest_result.is_valid:
            comments = "; ".join(error.message for error in manifest_result.errors) or "manifest validation failed"
            return self._store_submission(
                skill_name=skill_name,
                version=version,
                publisher=publisher,
                status=ReviewStatus.REJECTED,
                comments=comments,
            )
        return self._store_submission(
            skill_name=skill_name,
            version=version,
            publisher=publisher,
            status=ReviewStatus.PENDING,
            comments="automated validation passed",
        )

    def approve(self, *, review_id: str, reviewer: str, comments: str = "") -> ReviewRecord:
        """Approve one pending review record."""
        current = self._read_record(review_id)
        if current.status != ReviewStatus.PENDING:
            raise ValueError("only pending review can be approved")
        approved = ReviewRecord(
            review_id=current.review_id,
            skill_name=current.skill_name,
            version=current.version,
            publisher=current.publisher,
            status=ReviewStatus.APPROVED,
            comments=(comments or "approved").strip() + f" by {reviewer}",
            reviewed_at=_utc_now(),
        )
        self._write_record(approved)
        return approved

    def reject(self, *, review_id: str, reviewer: str, reason: str) -> ReviewRecord:
        """Reject one pending review record."""
        current = self._read_record(review_id)
        if current.status != ReviewStatus.PENDING:
            raise ValueError("only pending review can be rejected")
        rejected = ReviewRecord(
            review_id=current.review_id,
            skill_name=current.skill_name,
            version=current.version,
            publisher=current.publisher,
            status=ReviewStatus.REJECTED,
            comments=f"{reason.strip()} by {reviewer}",
            reviewed_at=_utc_now(),
        )
        self._write_record(rejected)
        return rejected

    def list_records(self) -> list[ReviewRecord]:
        """List stored review records sorted by reviewed_at descending."""
        records: list[ReviewRecord] = []
        for file_path in sorted(self.storage_dir.glob("*.json")):
            if file_path.name.endswith(".appeals.json"):
                continue
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            records.append(_record_from_dict(payload))
        records.sort(key=lambda item: item.reviewed_at, reverse=True)
        return records

    def list_pending_records(self) -> list[ReviewRecord]:
        """List pending review records sorted by reviewed_at descending."""
        return [record for record in self.list_records() if record.status == ReviewStatus.PENDING]

    def assign_reviewer(self, *, review_id: str, reviewer: str) -> None:
        """Assign one reviewer to one review record."""
        self._read_record(review_id)
        self.assigned_reviewers[review_id] = reviewer
        self._notify(event_type="review_assigned", review_id=review_id, actor=reviewer)

    def get_assigned_reviewer(self, *, review_id: str) -> str | None:
        """Return assigned reviewer for one review id."""
        return self.assigned_reviewers.get(review_id)

    def appeal(self, *, review_id: str, publisher: str, reason: str) -> AppealRecord:
        """Submit appeal for one rejected review record."""
        current = self._read_record(review_id)
        if current.status != ReviewStatus.REJECTED:
            raise ValueError("only rejected review can be appealed")
        if current.publisher != publisher:
            raise PermissionError("publisher mismatch")
        appeal = AppealRecord(
            review_id=review_id,
            publisher=publisher,
            reason=reason.strip(),
            appealed_at=_utc_now(),
        )
        appeals = self.list_appeals(review_id=review_id)
        appeals.append(appeal)
        self._write_appeals(review_id=review_id, appeals=appeals)
        self._notify(event_type="review_appealed", review_id=review_id, actor=publisher)
        return appeal

    def list_appeals(self, *, review_id: str) -> list[AppealRecord]:
        """List appeals for one review id."""
        path = self._review_path(review_id, suffix=".appeals.json")
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return []
        appeals: list[AppealRecord] = []
        for item in payload:
            if isinstance(item, dict):
                appeals.append(
                    AppealRecord(
                        review_id=str(item.get("review_id", "")),
                        publisher=str(item.get("publisher", "")),
                        reason=str(item.get("reason", "")),
                        appealed_at=str(item.get("appealed_at", "")),
                    )
                )
        appeals.sort(key=lambda item: item.appealed_at)
        return appeals

    def _build_record(
        self,
        *,
        skill_name: str,
        version: str,
        publisher: str,
        status: ReviewStatus,
        comments: str,
    ) -> ReviewRecord:
        _validate_review_component("publisher", publisher)
        _validate_review_component("skill_name", skill_name)
        _validate_review_component("version", version)
        review_id = f"{publisher}-{skill_name}-{version}"
        return ReviewRecord(
            review_id=review_id,
            skill_name=skill_name,
            version=version,
            publisher=publisher,
            status=status,
            comments=comments,
            reviewed_at=_utc_now(),
        )

    def _read_record(self, review_id: str) -> ReviewRecord:
        file_path = self._review_path(review_id, suffix=".json")
        if not file_path.exists():
            raise FileNotFoundError(f"review not found: {review_id}")
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        return _record_from_dict(payload)

    def _write_record(self, record: ReviewRecord) -> None:
        file_path = self._review_path(record.review_id, suffix=".json")
        file_path.write_text(json.dumps(asdict(record), ensure_ascii=False, indent=2), encoding="utf-8")

    def _store_submission(
        self,
        *,
        skill_name: str,
        version: str,
        publisher: str,
        status: ReviewStatus,
        comments: str,
    ) -> ReviewRecord:
        record = self._build_record(
            skill_name=skill_name,
            version=version,
            publisher=publisher,
            status=status,
            comments=comments,
        )
        self._write_record(record)
        return record

    def _write_appeals(self, *, review_id: str, appeals: list[AppealRecord]) -> None:
        path = self._review_path(review_id, suffix=".appeals.json")
        rows = [asdict(item) for item in appeals]
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    def _review_path(self, review_id: str, *, suffix: str) -> Path:
        normalized = _validate_review_id(review_id)
        path = (self.storage_dir / f"{normalized}{suffix}").resolve()
        storage_root = self.storage_dir.resolve()
        try:
            path.relative_to(storage_root)
        except ValueError as exc:
            raise ValueError("invalid review_id") from exc
        return path

    def _notify(self, *, event_type: str, review_id: str, actor: str) -> None:
        self.notifications.append(
            {
                "event_type": event_type,
                "review_id": review_id,
                "actor": actor,
                "timestamp": _utc_now(),
            }
        )


def _record_from_dict(payload: dict[str, str]) -> ReviewRecord:
    return ReviewRecord(
        review_id=str(payload.get("review_id", "")),
        skill_name=str(payload.get("skill_name", "")),
        version=str(payload.get("version", "")),
        publisher=str(payload.get("publisher", "")),
        status=ReviewStatus(str(payload.get("status", ReviewStatus.PENDING.value))),
        comments=str(payload.get("comments", "")),
        reviewed_at=str(payload.get("reviewed_at", "")),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_REVIEW_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9._+-]{1,128}$")
_REVIEW_ID_PATTERN = re.compile(r"^[A-Za-z0-9._+-]{1,128}-[A-Za-z0-9._+-]{1,128}-[A-Za-z0-9._+-]{1,128}$")


def _validate_review_component(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not _REVIEW_COMPONENT_PATTERN.fullmatch(normalized):
        raise ValueError(f"invalid {name}")
    return normalized


def _validate_review_id(review_id: str) -> str:
    normalized = str(review_id).strip()
    if not _REVIEW_ID_PATTERN.fullmatch(normalized):
        raise ValueError("invalid review_id")
    return normalized
