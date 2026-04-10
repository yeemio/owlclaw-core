"""Read-only skill endpoints for OwlHub API."""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
from datetime import datetime, timezone
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from owlclaw.owlhub.api.auth import Principal, get_current_principal
from owlclaw.owlhub.api.schemas import (
    PublishRequest,
    PublishResponse,
    SkillDetail,
    SkillSearchItem,
    SkillSearchResponse,
    SkillStatisticsResponse,
    TakedownRequest,
    UpdateStateRequest,
    VersionInfo,
)
from owlclaw.owlhub.indexer import IndexBuilder
from owlclaw.owlhub.review import ReviewStatus
from owlclaw.owlhub.schema import VersionState

router = APIRouter(prefix="/api/v1/skills", tags=["skills"])
current_principal_type = Annotated[Principal, Depends(get_current_principal)]
logger = logging.getLogger(__name__)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_CHECKSUM_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


@lru_cache(maxsize=1)
def _load_index() -> dict[str, Any]:
    index_path = Path(os.getenv("OWLHUB_INDEX_PATH", "./index.json")).resolve()
    if not index_path.exists():
        return {"skills": []}
    return cast(dict[str, Any], json.loads(index_path.read_text(encoding="utf-8")))


def _iter_skills() -> list[dict]:
    data = _load_index()
    skills = data.get("skills", [])
    return skills if isinstance(skills, list) else []


def _save_index(data: dict[str, Any]) -> None:
    index_path = Path(os.getenv("OWLHUB_INDEX_PATH", "./index.json")).resolve()
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _load_index.cache_clear()


@router.get("", response_model=SkillSearchResponse)
def search_skills(
    request: Request,
    query: str = "",
    tags: str = "",
    sort_by: str = Query("name", pattern="^(name|updated_at|downloads|quality_score)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> SkillSearchResponse:
    requested_tags = {tag.strip().lower() for tag in tags.split(",") if tag.strip()}
    normalized_query = query.strip().lower()

    items: list[SkillSearchItem] = []
    sort_values: dict[tuple[str, str, str], int | float | str] = {}
    for entry in _iter_skills():
        manifest = entry.get("manifest", {})
        name = str(manifest.get("name", "")).strip()
        publisher = str(manifest.get("publisher", "")).strip()
        if _is_hidden(entry=entry, request=request):
            continue
        version = str(manifest.get("version", "")).strip()
        description = str(manifest.get("description", "")).strip()
        skill_tags = [tag for tag in manifest.get("tags", []) if isinstance(tag, str)]
        lowered_tags = {tag.lower() for tag in skill_tags}
        if normalized_query and normalized_query not in f"{name} {description}".lower():
            continue
        if requested_tags and not requested_tags.issubset(lowered_tags):
            continue
        skill_key = (publisher, name, version)
        if sort_by == "downloads":
            stats = entry.get("statistics", {})
            downloads = int(stats.get("total_downloads", 0)) if isinstance(stats, dict) else 0
            sort_values[skill_key] = downloads
        elif sort_by == "quality_score":
            stats = entry.get("statistics", {})
            quality = float(stats.get("quality_score", 0.0)) if isinstance(stats, dict) and isinstance(stats.get("quality_score"), int | float) else 0.0
            sort_values[skill_key] = quality
        elif sort_by == "updated_at":
            sort_values[skill_key] = str(entry.get("updated_at", ""))
        stats_payload = entry.get("statistics", {})
        quality_score: float | None = None
        if isinstance(stats_payload, dict):
            raw_quality = stats_payload.get("quality_score")
            if isinstance(raw_quality, int | float):
                quality_score = float(raw_quality)
        items.append(
            SkillSearchItem(
                name=name,
                publisher=publisher,
                version=version,
                description=description,
                tags=skill_tags,
                version_state=str(entry.get("version_state", "released")),
                quality_score=quality_score,
                low_quality_warning=quality_score is not None and quality_score < 0.5,
            )
        )

    if sort_by == "downloads":
        items.sort(key=lambda item: int(sort_values.get((item.publisher, item.name, item.version), 0)), reverse=True)
    elif sort_by == "quality_score":
        items.sort(key=lambda item: float(sort_values.get((item.publisher, item.name, item.version), 0.0)), reverse=True)
    elif sort_by == "updated_at":
        items.sort(key=lambda item: str(sort_values.get((item.publisher, item.name, item.version), "")), reverse=True)
    else:
        items.sort(key=lambda item: (item.name, item.version))

    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    return SkillSearchResponse(total=total, page=page, page_size=page_size, items=items[start:end])


@router.get("/{publisher}/{name}", response_model=SkillDetail)
def get_skill_detail(publisher: str, name: str, request: Request) -> SkillDetail:
    entries = [entry for entry in _iter_skills() if _is_skill(entry, publisher, name) and not _is_hidden(entry=entry, request=request)]
    if not entries:
        raise HTTPException(status_code=404, detail="skill not found")

    entries.sort(key=lambda entry: str(entry.get("manifest", {}).get("version", "")))
    latest = entries[-1]
    manifest = latest.get("manifest", {})
    versions = [
        VersionInfo(
            version=str(entry.get("manifest", {}).get("version", "")),
            version_state=str(entry.get("version_state", "released")),
            published_at=entry.get("published_at"),
            updated_at=entry.get("updated_at"),
        )
        for entry in entries
    ]
    return SkillDetail(
        name=str(manifest.get("name", "")),
        publisher=str(manifest.get("publisher", "")),
        description=str(manifest.get("description", "")),
        tags=[tag for tag in manifest.get("tags", []) if isinstance(tag, str)],
        dependencies=manifest.get("dependencies", {}) if isinstance(manifest.get("dependencies", {}), dict) else {},
        versions=versions,
        statistics=latest.get("statistics", {}) if isinstance(latest.get("statistics", {}), dict) else {},
    )


@router.get("/{publisher}/{name}/versions", response_model=list[VersionInfo])
def get_skill_versions(publisher: str, name: str, request: Request) -> list[VersionInfo]:
    entries = [entry for entry in _iter_skills() if _is_skill(entry, publisher, name) and not _is_hidden(entry=entry, request=request)]
    if not entries:
        raise HTTPException(status_code=404, detail="skill not found")
    entries.sort(key=lambda entry: str(entry.get("manifest", {}).get("version", "")))
    return [
        VersionInfo(
            version=str(entry.get("manifest", {}).get("version", "")),
            version_state=str(entry.get("version_state", "released")),
            published_at=entry.get("published_at"),
            updated_at=entry.get("updated_at"),
        )
        for entry in entries
    ]


@router.get("/{publisher}/{name}/statistics", response_model=SkillStatisticsResponse)
def get_skill_statistics(publisher: str, name: str, request: Request) -> SkillStatisticsResponse:
    entries = [entry for entry in _iter_skills() if _is_skill(entry, publisher, name) and not _is_hidden(entry=entry, request=request)]
    if not entries:
        raise HTTPException(status_code=404, detail="skill not found")
    entries.sort(key=lambda entry: str(entry.get("manifest", {}).get("version", "")))
    latest = entries[-1]
    manifest = latest.get("manifest", {})
    tracker = request.app.state.statistics_tracker
    stats = tracker.get_statistics(
        skill_name=name,
        publisher=publisher,
        repository=str(manifest.get("repository", "")).strip() or None,
    )
    return SkillStatisticsResponse(
        skill_name=stats.skill_name,
        publisher=stats.publisher,
        total_downloads=stats.total_downloads,
        downloads_last_30d=stats.downloads_last_30d,
        total_installs=stats.total_installs,
        active_installs=stats.active_installs,
        last_updated=stats.last_updated,
    )


@router.post("", response_model=PublishResponse)
def publish_skill(
    payload: dict[str, Any],
    request: Request,
    principal: current_principal_type,
) -> PublishResponse:
    try:
        request_payload = PublishRequest.model_validate(payload)
    except ValidationError as exc:
        logger.debug("Publish request validation failed: %s", exc)
        raise HTTPException(status_code=422, detail="validation_error") from exc
    publisher = _sanitize_text(request_payload.publisher)
    skill_name = _sanitize_text(request_payload.skill_name)
    version = _sanitize_text(request_payload.version)
    metadata_dict = request_payload.metadata if isinstance(request_payload.metadata, dict) else {}
    if not _is_safe_identifier(publisher):
        raise HTTPException(status_code=422, detail="invalid publisher format")
    if not _is_safe_identifier(skill_name):
        raise HTTPException(status_code=422, detail="invalid skill name format")

    if not _principal_allowed_for_publisher(principal, publisher):
        raise HTTPException(status_code=403, detail="publisher does not match authenticated user")
    if request.app.state.blacklist_manager.is_blocked(publisher=publisher, skill_name=skill_name):
        raise HTTPException(status_code=403, detail="publisher or skill is blacklisted")

    manifest_payload = {
        "name": skill_name,
        "version": version,
        "publisher": publisher,
        "description": _sanitize_text(str(metadata_dict.get("description", "")), max_len=512),
        "license": _sanitize_text(str(metadata_dict.get("license", "")), max_len=64),
        "tags": [_sanitize_text(str(tag), max_len=32) for tag in metadata_dict.get("tags", []) if isinstance(tag, str)],
        "dependencies": metadata_dict.get("dependencies", {}),
    }

    validator = request.app.state.validator
    validation = validator.validate_manifest(manifest_payload)
    review_system = request.app.state.review_system
    review = review_system.submit_manifest_for_review(
        manifest=manifest_payload,
        skill_name=skill_name,
        version=version,
        publisher=publisher,
    )
    if not validation.is_valid or review.status == ReviewStatus.REJECTED:
        errors = [{"field": err.field, "message": err.message} for err in validation.errors]
        raise HTTPException(
            status_code=422,
            detail={"message": "manifest validation failed", "review_id": review.review_id, "errors": errors},
        )

    download_url = _sanitize_text(str(metadata_dict.get("download_url", "")), max_len=2048)
    checksum = _resolve_publish_checksum(
        download_url=download_url,
        provided_checksum=_sanitize_text(str(metadata_dict.get("checksum", "")), max_len=80),
        manifest_payload=manifest_payload,
    )

    now = datetime.now(timezone.utc).isoformat()
    state = str(metadata_dict.get("version_state", VersionState.RELEASED.value)).strip().lower()
    if state not in {member.value for member in VersionState}:
        raise HTTPException(status_code=422, detail="invalid version state")

    index_data = _load_index()
    skills = index_data.get("skills", [])
    if not isinstance(skills, list):
        skills = []
    statistics_payload: dict[str, Any] = (
        cast(dict[str, Any], metadata_dict.get("statistics", {}))
        if isinstance(metadata_dict.get("statistics", {}), dict)
        else {"total_downloads": 0, "downloads_last_30d": 0}
    )
    entry: dict[str, Any] = {
        "manifest": manifest_payload,
        "version_state": state,
        "published_at": now,
        "updated_at": now,
        "download_url": download_url,
        "checksum": checksum,
        "statistics": statistics_payload,
    }
    quality_payload = metadata_dict.get("quality", {})
    if isinstance(quality_payload, dict):
        raw_score = quality_payload.get("quality_score")
        raw_samples = quality_payload.get("sample_size", 0)
        if isinstance(raw_score, int | float):
            entry["statistics"]["quality_score"] = float(raw_score)
            entry["statistics"]["quality_samples"] = int(raw_samples) if isinstance(raw_samples, int) else 0
            entry["statistics"]["quality_source"] = "anonymous_aggregate"
    replaced = False
    for idx, existing in enumerate(skills):
        manifest = existing.get("manifest", {})
        if (
            str(manifest.get("publisher", "")) == publisher
            and str(manifest.get("name", "")) == skill_name
            and str(manifest.get("version", "")) == version
        ):
            skills[idx] = entry
            replaced = True
            break
    if not replaced:
        skills.append(entry)
    index_data["skills"] = skills
    index_data["total_skills"] = len(skills)
    index_data["generated_at"] = now
    _save_index(index_data)

    audit = request.app.state.audit_logger
    audit.log(
        event_type="publish",
        principal=principal,
        details={
            "publisher": publisher,
            "skill_name": skill_name,
            "version": version,
            "review_id": review.review_id,
        },
    )
    logger.info(
        "%s",
        json.dumps(
            {
                "event": "skill_publish",
                "publisher": publisher,
                "skill_name": skill_name,
                "version": version,
                "review_id": review.review_id,
                "principal": principal.user_id,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    return PublishResponse(accepted=True, review_id=review.review_id, status=review.status.value)


@router.put("/{publisher}/{name}/versions/{version}/state", response_model=dict[str, Any])
def update_skill_state(
    publisher: str,
    name: str,
    version: str,
    payload: dict[str, Any],
    request: Request,
    principal: current_principal_type,
) -> dict[str, Any]:
    if not _is_safe_identifier(publisher) or not _is_safe_identifier(name):
        raise HTTPException(status_code=422, detail="invalid publisher or skill name format")
    if not _principal_allowed_for_publisher(principal, publisher):
        raise HTTPException(status_code=403, detail="publisher does not match authenticated user")
    try:
        request_payload = UpdateStateRequest.model_validate(payload)
    except ValidationError as exc:
        logger.debug("Update state validation failed: %s", exc)
        raise HTTPException(status_code=422, detail="validation_error") from exc
    state = request_payload.state.strip().lower()
    if state not in {member.value for member in VersionState}:
        raise HTTPException(status_code=422, detail="invalid version state")

    index_data = _load_index()
    skills = index_data.get("skills", [])
    if not isinstance(skills, list):
        skills = []
    target: dict[str, Any] | None = None
    for entry in skills:
        manifest = entry.get("manifest", {})
        if (
            str(manifest.get("publisher", "")) == publisher
            and str(manifest.get("name", "")) == name
            and str(manifest.get("version", "")) == version
        ):
            target = entry
            break
    if target is None:
        raise HTTPException(status_code=404, detail="skill version not found")

    old_state = str(target.get("version_state", VersionState.RELEASED.value))
    target["version_state"] = state
    target["updated_at"] = datetime.now(timezone.utc).isoformat()
    index_data["skills"] = skills
    index_data["total_skills"] = len(skills)
    _save_index(index_data)

    audit = request.app.state.audit_logger
    audit.log(
        event_type="state_update",
        principal=principal,
        details={
            "publisher": publisher,
            "skill_name": name,
            "version": version,
            "from_state": old_state,
            "to_state": state,
        },
    )
    logger.info(
        "%s",
        json.dumps(
            {
                "event": "skill_state_update",
                "publisher": publisher,
                "skill_name": name,
                "version": version,
                "from_state": old_state,
                "to_state": state,
                "principal": principal.user_id,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    return {"updated": True, "publisher": publisher, "skill_name": name, "version": version, "state": state}


@router.post("/{publisher}/{name}/takedown", response_model=dict[str, Any])
def takedown_skill(
    publisher: str,
    name: str,
    payload: dict[str, Any],
    request: Request,
    principal: current_principal_type,
) -> dict[str, Any]:
    if not _is_safe_identifier(publisher) or not _is_safe_identifier(name):
        raise HTTPException(status_code=422, detail="invalid publisher or skill name format")
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    try:
        request_payload = TakedownRequest.model_validate(payload)
    except ValidationError as exc:
        logger.debug("Takedown request validation failed: %s", exc)
        raise HTTPException(status_code=422, detail="validation_error") from exc

    index_data = _load_index()
    skills = index_data.get("skills", [])
    if not isinstance(skills, list):
        skills = []
    now = datetime.now(timezone.utc).isoformat()
    affected = 0
    for entry in skills:
        manifest = entry.get("manifest", {})
        if str(manifest.get("publisher", "")) == publisher and str(manifest.get("name", "")) == name:
            entry["takedown"] = {"is_taken_down": True, "reason": request_payload.reason.strip(), "timestamp": now}
            entry["updated_at"] = now
            affected += 1
    if affected == 0:
        raise HTTPException(status_code=404, detail="skill not found")
    index_data["skills"] = skills
    index_data["total_skills"] = len(skills)
    _save_index(index_data)
    request.app.state.audit_logger.log(
        event_type="takedown",
        principal=principal,
        details={"publisher": publisher, "skill_name": name, "reason": request_payload.reason.strip()},
    )
    logger.info(
        "%s",
        json.dumps(
            {
                "event": "skill_takedown",
                "publisher": publisher,
                "skill_name": name,
                "reason": request_payload.reason.strip(),
                "principal": principal.user_id,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    return {"takedown": True, "publisher": publisher, "skill_name": name, "reason": request_payload.reason.strip()}


def _is_skill(entry: dict, publisher: str, name: str) -> bool:
    manifest = entry.get("manifest", {})
    return str(manifest.get("publisher", "")) == publisher and str(manifest.get("name", "")) == name


def _principal_allowed_for_publisher(principal: Principal, publisher: str) -> bool:
    if principal.role == "admin":
        return True
    target = publisher.strip().lower()
    identity = principal.user_id.strip().lower()
    if identity == target:
        return True
    return ":" in identity and identity.split(":", 1)[1] == target


def _is_hidden(*, entry: dict[str, Any], request: Request | None = None) -> bool:
    manifest = entry.get("manifest", {})
    publisher = str(manifest.get("publisher", ""))
    skill_name = str(manifest.get("name", ""))
    takedown = entry.get("takedown", {})
    if isinstance(takedown, dict) and bool(takedown.get("is_taken_down", False)):
        return True
    if bool(entry.get("is_taken_down", False)):
        return True
    if request is None:
        return False
    manager = request.app.state.blacklist_manager
    return bool(manager.is_blocked(publisher=publisher, skill_name=skill_name))


def _sanitize_text(value: str, *, max_len: int = 128) -> str:
    filtered = "".join(ch for ch in value if ch >= " " and ch != "\x7f")
    return filtered.strip()[:max_len]


def _is_safe_identifier(value: str) -> bool:
    return bool(_SAFE_IDENTIFIER.fullmatch(value))


def _resolve_publish_checksum(*, download_url: str, provided_checksum: str, manifest_payload: dict[str, Any]) -> str:
    if provided_checksum and not _CHECKSUM_PATTERN.fullmatch(provided_checksum):
        raise HTTPException(status_code=422, detail="invalid checksum format")

    local_file = _resolve_local_file_path(download_url)
    if local_file is not None and local_file.exists() and local_file.is_file():
        actual = IndexBuilder().calculate_checksum(local_file)
        if provided_checksum and provided_checksum != actual:
            raise HTTPException(status_code=422, detail="checksum does not match package content")
        return provided_checksum or actual

    if provided_checksum:
        return provided_checksum
    if download_url:
        raise HTTPException(status_code=422, detail="checksum is required when package file is not locally accessible")

    payload = json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{sha256(payload).hexdigest()}"


def _resolve_local_file_path(download_url: str) -> Path | None:
    if not download_url:
        return None
    parsed = urllib.parse.urlparse(download_url)
    if parsed.scheme in {"http", "https"}:
        return None
    if parsed.scheme == "file":
        return Path(download_url.replace("file://", "")).expanduser().resolve()
    return Path(download_url).expanduser().resolve()
