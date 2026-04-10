"""CLI-facing OwlHub API client with static-index fallback."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from owlclaw.owlhub import OwlHubClient, SearchResult
from owlclaw.owlhub.validator import Validator


class SkillHubApiClient:
    """Unified client supporting API mode, index mode, and auto fallback."""

    def __init__(
        self,
        *,
        index_client: OwlHubClient,
        api_base_url: str = "",
        api_token: str = "",
        mode: str = "auto",
        timeout_seconds: int = 15,
        cache_ttl_seconds: int = 3600,
        no_cache: bool = False,
    ) -> None:
        self.index_client = index_client
        self.api_base_url = api_base_url.strip().rstrip("/")
        self.api_token = api_token.strip()
        self.mode = mode.strip().lower() if mode.strip() else "auto"
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = max(0, cache_ttl_seconds)
        self.no_cache = no_cache
        self.validator = Validator()
        self.last_install_warning: str | None = None

    def search(
        self,
        *,
        query: str = "",
        tags: list[str] | None = None,
        tag_mode: str = "and",
        include_draft: bool = False,
        industry: str = "",
        sort_by: str = "name",
    ) -> list[SearchResult]:
        """Search skills via API when configured; fallback to static index as needed."""
        if self.mode == "index":
            return self.index_client.search(
                query=query,
                tags=tags,
                tag_mode=tag_mode,
                include_draft=include_draft,
                industry=industry,
                sort_by=sort_by,
            )
        if self.api_base_url:
            try:
                return self._search_via_api(query=query, tags=tags or [], sort_by=sort_by)
            except Exception:
                if self.mode == "api":
                    raise
        return self.index_client.search(
            query=query,
            tags=tags,
            tag_mode=tag_mode,
            include_draft=include_draft,
            industry=industry,
            sort_by=sort_by,
        )

    def install(
        self,
        *,
        name: str,
        version: str | None = None,
        no_deps: bool = False,
        force: bool = False,
    ) -> Path:
        """Install skill using static index client."""
        target = self.index_client.install(name=name, version=version, no_deps=no_deps, force=force)
        self.last_install_warning = self.index_client.last_install_warning
        return target

    def update(self, name: str | None = None) -> list[dict[str, str]]:
        """Update skill(s) using static index client."""
        return self.index_client.update(name=name)

    def list_installed(self) -> list[dict[str, Any]]:
        """List installed skills from lock file."""
        return self.index_client.list_installed()

    def publish(self, *, skill_path: Path) -> dict[str, Any]:
        """Publish one local skill package via OwlHub API."""
        if not self.api_base_url:
            raise ValueError("api base url is required for publish")
        if not self.validator.validate_structure(skill_path).is_valid:
            raise ValueError("invalid skill package structure")
        manifest = _read_manifest(skill_path / "SKILL.md")
        payload: dict[str, Any] = {
            "publisher": str(manifest.get("publisher", "")).strip(),
            "skill_name": str(manifest.get("name", "")).strip(),
            "version": str(manifest.get("version", "")).strip(),
            "metadata": {
                "description": str(manifest.get("description", "")).strip(),
                "license": str(manifest.get("license", "")).strip(),
                "tags": manifest.get("tags", []),
                "dependencies": manifest.get("dependencies", {}),
                "download_url": str((skill_path.resolve()).as_posix()),
            },
        }
        skill_name = str(payload.get("skill_name", "")).strip()
        quality_payload = _load_anonymized_quality(skill_name=skill_name, root=skill_path.parent)
        if quality_payload:
            metadata = payload.get("metadata", {})
            if isinstance(metadata, dict):
                metadata["quality"] = quality_payload
        response = self._request_json("POST", "/api/v1/skills", body=payload)
        return response if isinstance(response, dict) else {}

    def _search_via_api(self, *, query: str, tags: list[str], sort_by: str) -> list[SearchResult]:
        params = {"query": query, "tags": ",".join(tags), "sort_by": sort_by}
        query_text = urllib.parse.urlencode(params)
        payload = self._request_json("GET", f"/api/v1/skills?{query_text}")
        if not isinstance(payload, dict):
            return []
        items = payload.get("items", [])
        results: list[SearchResult] = []
        if not isinstance(items, list):
            return results
        for item in items:
            if not isinstance(item, dict):
                continue
            results.append(
                SearchResult(
                    name=str(item.get("name", "")),
                    publisher=str(item.get("publisher", "")),
                    version=str(item.get("version", "")),
                    description=str(item.get("description", "")),
                    tags=[str(tag) for tag in item.get("tags", []) if isinstance(tag, str)],
                    version_state=str(item.get("version_state", "released")),
                    download_url="",
                    checksum="",
                    dependencies={},
                    quality_score=float(item["quality_score"])
                    if isinstance(item.get("quality_score"), int | float)
                    else None,
                    low_quality_warning=bool(item.get("low_quality_warning", False)),
                )
            )
        return results

    def _request_json(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        if not self.api_base_url:
            raise ValueError("api base url is not configured")
        target = f"{self.api_base_url}{path}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        request = urllib.request.Request(target, data=data, method=method, headers=headers)
        cache_file = self.index_client.cache_dir / f"api-{_sha256(target)}.json"
        if method.upper() == "GET" and not self.no_cache and _is_cache_fresh(cache_file, ttl_seconds=self.cache_ttl_seconds):
            return json.loads(cache_file.read_text(encoding="utf-8"))
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
            if method.upper() == "GET" and not self.no_cache:
                cache_file.write_text(raw, encoding="utf-8")
            return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise ValueError(f"api request failed: {exc.code} {detail}") from exc


def _read_manifest(path: Path) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        raise ValueError("SKILL.md missing frontmatter")
    parts = content.split("---", 2)
    if len(parts) < 3:
        raise ValueError("SKILL.md frontmatter parse failed")
    frontmatter = yaml.safe_load(parts[1]) if parts[1].strip() else {}
    if not isinstance(frontmatter, dict):
        raise ValueError("SKILL.md frontmatter must be a mapping")
    metadata = frontmatter.get("metadata", {})
    if isinstance(metadata, dict) and "version" in metadata and "version" not in frontmatter:
        frontmatter["version"] = metadata["version"]
    return frontmatter


def _is_cache_fresh(path: Path, *, ttl_seconds: int) -> bool:
    if not path.exists() or ttl_seconds <= 0:
        return False
    age = time.time() - path.stat().st_mtime
    return age <= ttl_seconds


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_anonymized_quality(*, skill_name: str, root: Path) -> dict[str, Any] | None:
    """Load local aggregated quality payload for publish metadata, if present."""
    path = root / ".owlclaw" / "quality" / "snapshots.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    rows = raw if isinstance(raw, list) else []
    candidates = [row for row in rows if isinstance(row, dict) and str(row.get("skill_name", "")).strip() == skill_name]
    if not candidates:
        return None
    latest = sorted(candidates, key=lambda row: str(row.get("computed_at", "")))[-1]
    raw_score = latest.get("quality_score")
    if not isinstance(raw_score, int | float):
        return None
    sample_size = latest.get("sample_size", latest.get("total_runs", 0))
    if not isinstance(sample_size, int):
        sample_size = 0
    return {
        "quality_score": float(raw_score),
        "sample_size": max(0, sample_size),
        "computed_at": str(latest.get("computed_at", "")),
        "source": "anonymous_aggregate",
    }
