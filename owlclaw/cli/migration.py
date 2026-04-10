"""Migration and approval CLI command implementations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from owlclaw.config.loader import YAMLConfigLoader


def _resolve_config_path(config: str) -> Path:
    if config.strip():
        return Path(config.strip())
    return YAMLConfigLoader.resolve_path()


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _save_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def status_command(config: str = "") -> None:
    """Show migration weights by skill."""
    path = _resolve_config_path(config)
    payload = _load_yaml(path)
    skills = payload.get("skills", {})
    rows: list[dict[str, Any]] = []
    if isinstance(skills, dict):
        for skill_name, skill_cfg in skills.items():
            if not isinstance(skill_name, str) or not isinstance(skill_cfg, dict):
                continue
            weight = skill_cfg.get("migration_weight", 100)
            try:
                weight_int = int(weight)
            except (TypeError, ValueError):
                weight_int = 100
            rows.append({"skill": skill_name, "migration_weight": max(0, min(100, weight_int))})
    rows.sort(key=lambda item: str(item["skill"]))
    print(json.dumps({"config": str(path), "skills": rows}, ensure_ascii=False))


def set_command(skill: str, weight: int, config: str = "") -> None:
    """Set migration weight for one skill in owlclaw.yaml."""
    normalized_skill = skill.strip()
    if not normalized_skill:
        raise ValueError("skill must be a non-empty string")
    weight_int = int(weight)
    if weight_int < 0 or weight_int > 100:
        raise ValueError("weight must be between 0 and 100")
    path = _resolve_config_path(config)
    payload = _load_yaml(path)
    skills = payload.get("skills")
    if not isinstance(skills, dict):
        skills = {}
        payload["skills"] = skills
    entry = skills.get(normalized_skill)
    if not isinstance(entry, dict):
        entry = {}
        skills[normalized_skill] = entry
    entry["migration_weight"] = weight_int
    _save_yaml(path, payload)
    print(json.dumps({"status": "ok", "skill": normalized_skill, "migration_weight": weight_int}, ensure_ascii=False))


def suggest_command(config: str = "") -> None:
    """Suggest next migration weight based on simple confidence heuristics."""
    path = _resolve_config_path(config)
    payload = _load_yaml(path)
    skills = payload.get("skills", {})
    suggestions: list[dict[str, Any]] = []
    if isinstance(skills, dict):
        for skill_name, skill_cfg in skills.items():
            if not isinstance(skill_name, str) or not isinstance(skill_cfg, dict):
                continue
            current = int(skill_cfg.get("migration_weight", 100))
            if current >= 100:
                continue
            suggested = min(100, current + 20)
            suggestions.append(
                {
                    "skill": skill_name,
                    "current_weight": current,
                    "suggested_weight": suggested,
                    "reason": "progressive rollout recommendation",
                }
            )
    suggestions.sort(key=lambda row: str(row["skill"]))
    print(json.dumps({"config": str(path), "suggestions": suggestions}, ensure_ascii=False))


def _approval_store_path(store: str) -> Path:
    return Path(store.strip()) if store.strip() else Path(".owlclaw") / "approval_queue.json"


def _load_approval_store(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _save_approval_store(path: Path, payload: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def approval_list_command(status: str = "", store: str = "") -> None:
    """List approval requests from local store."""
    path = _approval_store_path(store)
    rows = _load_approval_store(path)
    normalized = status.strip().lower()
    if normalized:
        rows = [row for row in rows if str(row.get("status", "")).lower() == normalized]
    print(json.dumps({"store": str(path), "items": rows}, ensure_ascii=False))


def approval_approve_command(request_id: str, approver: str = "cli", store: str = "") -> None:
    """Approve one pending request in local store."""
    normalized_id = request_id.strip()
    if not normalized_id:
        raise ValueError("request_id must be a non-empty string")
    path = _approval_store_path(store)
    rows = _load_approval_store(path)
    updated = False
    for row in rows:
        if str(row.get("id", "")).strip() != normalized_id:
            continue
        row["status"] = "approved"
        row["approver"] = approver
        updated = True
        break
    if updated:
        _save_approval_store(path, rows)
    print(json.dumps({"status": "ok" if updated else "not_found", "request_id": normalized_id}, ensure_ascii=False))
