"""OwlHub CLI client for search/install/update workflows."""

from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from packaging.version import InvalidVersion, Version

from owlclaw.owlhub.indexer.builder import IndexBuilder
from owlclaw.owlhub.validator import Validator


@dataclass(frozen=True)
class SearchResult:
    """One search hit from OwlHub index."""

    name: str
    publisher: str
    version: str
    description: str
    tags: list[str]
    version_state: str
    download_url: str
    checksum: str
    dependencies: dict[str, str]
    industry: str = ""
    source: str = "owlhub"
    score: float | None = None
    quality_score: float | None = None
    low_quality_warning: bool = False


class OwlHubClient:
    """Read OwlHub index and perform local install/update operations."""

    def __init__(
        self,
        *,
        index_url: str,
        install_dir: Path,
        lock_file: Path,
        cache_dir: Path | None = None,
        cache_ttl_seconds: int = 3600,
        no_cache: bool = False,
    ):
        self.index_url = index_url
        self.install_dir = install_dir
        self.lock_file = lock_file
        self.validator = Validator()
        self.index_builder = IndexBuilder()
        self.last_install_warning: str | None = None
        self.retry_attempts = 3
        self.retry_backoff_seconds = 0.1
        self.cache_dir = cache_dir or (self.lock_file.parent / ".owlhub-cache")
        self.cache_ttl_seconds = max(0, cache_ttl_seconds)
        self.no_cache = no_cache
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def search(
        self,
        query: str = "",
        tags: list[str] | None = None,
        tag_mode: str = "and",
        include_draft: bool = False,
        include_hidden: bool = False,
        industry: str = "",
        sort_by: str = "name",
    ) -> list[SearchResult]:
        """Search skills by name/description and optional tags."""
        data = self._load_index()
        normalized_query = query.strip().lower()
        normalized_industry = industry.strip().lower()
        requested_tags = {tag.strip().lower() for tag in (tags or []) if tag.strip()}
        normalized_mode = tag_mode.strip().lower()
        if normalized_mode not in {"and", "or"}:
            normalized_mode = "and"

        results: list[SearchResult] = []
        for entry in data.get("skills", []):
            manifest = entry.get("manifest", {})
            name = str(manifest.get("name", "")).strip()
            description = str(manifest.get("description", "")).strip()
            publisher = str(manifest.get("publisher", "")).strip()
            if not include_hidden and _is_hidden_entry(entry):
                continue
            version = str(manifest.get("version", "")).strip()
            version_state = str(entry.get("version_state", "released")).strip().lower()
            skill_industry = str(manifest.get("industry", "")).strip().lower()
            if not skill_industry:
                metadata = manifest.get("metadata", {})
                if isinstance(metadata, dict):
                    skill_industry = str(metadata.get("industry", "")).strip().lower()
            skill_tags = {
                str(tag).strip().lower()
                for tag in manifest.get("tags", [])
                if isinstance(tag, str) and tag.strip()
            }

            if normalized_query and normalized_query not in f"{name} {description}".lower():
                continue
            if not include_draft and version_state == "draft":
                continue
            if normalized_industry and skill_industry != normalized_industry:
                continue
            if requested_tags:
                if normalized_mode == "and" and not requested_tags.issubset(skill_tags):
                    continue
                if normalized_mode == "or" and requested_tags.isdisjoint(skill_tags):
                    continue
            statistics = entry.get("statistics", {})
            quality_score = None
            if isinstance(statistics, dict):
                raw_quality = statistics.get("quality_score")
                if isinstance(raw_quality, int | float):
                    quality_score = float(raw_quality)
            results.append(
                SearchResult(
                    name=name,
                    publisher=publisher,
                    version=version,
                    description=description,
                    tags=sorted(skill_tags),
                    version_state=version_state,
                    download_url=str(entry.get("download_url", "")),
                    checksum=str(entry.get("checksum", "")),
                    dependencies=manifest.get("dependencies", {})
                    if isinstance(manifest.get("dependencies", {}), dict)
                    else {},
                    industry=skill_industry,
                    quality_score=quality_score,
                    low_quality_warning=quality_score is not None and quality_score < 0.5,
                )
            )

        normalized_sort = sort_by.strip().lower()
        if normalized_sort == "quality_score":
            results.sort(
                key=lambda item: (
                    item.quality_score if item.quality_score is not None else -1.0,
                    item.name,
                    item.version,
                ),
                reverse=True,
            )
        else:
            results.sort(key=lambda item: (item.name, item.version), reverse=False)
        return results

    def install(
        self,
        *,
        name: str,
        version: str | None = None,
        no_deps: bool = False,
        force: bool = False,
    ) -> Path:
        """Install one skill by name and optional exact version."""
        candidates = self.search(query=name, include_hidden=force)
        matched = [item for item in candidates if item.name == name]
        if version is not None:
            matched = [item for item in matched if item.version == version]
        if not matched:
            raise ValueError(f"skill not found: {name}{'@' + version if version else ''}")
        selected = sorted(matched, key=lambda item: item.version)[-1]
        plan = [selected]
        if not no_deps:
            from owlclaw.cli.resolver import DependencyResolver

            resolver = DependencyResolver(get_candidates=lambda dep_name: self._list_candidates_by_name(dep_name, include_hidden=force))
            plan = [node.result for node in resolver.resolve(root=selected)]
        target = self.install_dir / selected.name / selected.version
        for item in plan:
            target = self._install_one(item, force=force)
        return target

    def list_installed(self) -> list[dict[str, Any]]:
        """List installed skills from lock file."""
        if not self.lock_file.exists():
            return []
        data = json.loads(self.lock_file.read_text(encoding="utf-8"))
        skills = data.get("skills", [])
        return skills if isinstance(skills, list) else []

    def update(self, name: str | None = None) -> list[dict[str, str]]:
        """Update one installed skill (or all) to latest indexed version."""
        installed = self.list_installed()
        if not installed:
            return []

        updates: list[dict[str, str]] = []
        for item in installed:
            skill_name = str(item.get("name", "")).strip()
            current_version = str(item.get("version", "")).strip()
            if not skill_name:
                continue
            if name and skill_name != name:
                continue

            latest = self._resolve_latest_version(skill_name)
            if latest is None:
                continue
            if _compare_version(latest.version, current_version) <= 0:
                continue

            self.install(name=skill_name, version=latest.version)
            updates.append(
                {
                    "name": skill_name,
                    "from_version": current_version,
                    "to_version": latest.version,
                }
            )

        return updates

    def validate_local(self, path: Path) -> bool:
        """Validate a local skill package path."""
        structure = self.validator.validate_structure(path)
        return structure.is_valid

    def _load_index(self) -> dict[str, Any]:
        parsed = urllib.parse.urlparse(self.index_url)
        if parsed.scheme in {"http", "https"}:
            cache_file = self.cache_dir / f"index-{_sha256(self.index_url)}.json"
            if not self.no_cache and _is_cache_fresh(cache_file, ttl_seconds=self.cache_ttl_seconds):
                return cast(dict[str, Any], json.loads(cache_file.read_text(encoding="utf-8")))
            with self._urlopen_with_retry(self.index_url, timeout=30) as response:
                payload = response.read().decode("utf-8")
            if not self.no_cache:
                cache_file.write_text(payload, encoding="utf-8")
            return cast(dict[str, Any], json.loads(payload))
        path = Path(self.index_url.replace("file://", "")).resolve()
        return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))

    def _download(self, url: str) -> Path:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme in {"http", "https"}:
            cache_file = self.cache_dir / f"pkg-{_sha256(url)}.pkg"
            if not self.no_cache and _is_cache_fresh(cache_file, ttl_seconds=self.cache_ttl_seconds):
                return cache_file
            with self._urlopen_with_retry(url, timeout=60) as response:
                data = response.read()
            if not self.no_cache:
                cache_file.write_bytes(data)
                return cache_file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pkg") as handle:
                handle.write(data)
                return Path(handle.name)
        if parsed.scheme == "file":
            return Path(parsed.path).resolve()
        return Path(url).resolve()

    def _validate_install(self, installed_path: Path) -> None:
        if not any(installed_path.rglob("SKILL.md")):
            raise ValueError("installed package missing SKILL.md")

    def _resolve_latest_version(self, name: str) -> SearchResult | None:
        candidates = self.search(query=name)
        matched = [item for item in candidates if item.name == name]
        if not matched:
            return None
        matched.sort(key=lambda item: _version_sort_key(item.version))
        return matched[-1]

    def _list_candidates_by_name(self, name: str, *, include_hidden: bool = False) -> list[SearchResult]:
        return self.search(query=name, include_draft=True, include_hidden=include_hidden)

    def _install_one(self, selected: SearchResult, *, force: bool = False) -> Path:
        self.last_install_warning = None
        source_entry = _find_source_entry(self._load_index(), selected)
        if not force and source_entry is not None and _is_hidden_entry(source_entry):
            raise ValueError(f"skill {selected.publisher}/{selected.name} is blocked by moderation policy")
        if selected.version_state == "deprecated":
            self.last_install_warning = f"skill {selected.name}@{selected.version} is deprecated"

        downloaded = self._download(selected.download_url)
        actual_checksum = self.index_builder.calculate_checksum(downloaded)
        if not force and selected.checksum and actual_checksum != selected.checksum:
            raise ValueError("checksum verification failed")
        if force and selected.checksum and actual_checksum != selected.checksum:
            self.last_install_warning = "checksum mismatch ignored due to --force"

        target = self.install_dir / selected.name / selected.version
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)

        try:
            if tarfile.is_tarfile(downloaded):
                with tarfile.open(downloaded, "r:*") as archive:
                    _safe_extract_tar(archive, target)
            else:
                if downloaded.is_dir():
                    shutil.copytree(downloaded, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(downloaded, target / downloaded.name)

            self._validate_install(target)
            self._write_lock(selected, target)
        except Exception as exc:
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            raise ValueError(f"installation failed for {selected.name}@{selected.version}: {exc}") from exc
        return target

    def _urlopen_with_retry(self, target: str, *, timeout: int) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                return urllib.request.urlopen(target, timeout=timeout)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                if attempt >= self.retry_attempts:
                    break
                time.sleep(self.retry_backoff_seconds * attempt)
        raise ValueError(f"network request failed after retries: {target}") from last_error

    def clear_cache(self) -> int:
        """Clear all cached index/package files and return removed count."""
        removed = 0
        if not self.cache_dir.exists():
            return removed
        for item in self.cache_dir.glob("*"):
            if item.is_file():
                item.unlink(missing_ok=True)
                removed += 1
        return removed

    def _write_lock(self, selected: SearchResult, target: Path) -> None:
        existing = {"version": "1.0", "generated_at": "", "skills": []}
        if self.lock_file.exists():
            loaded = json.loads(self.lock_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing.update(loaded)

        skills: list[dict[str, Any]] = []
        raw_skills = existing.get("skills", [])
        if isinstance(raw_skills, list):
            for item in raw_skills:
                if isinstance(item, dict) and item.get("name") != selected.name:
                    skills.append(item)
        skills.append(
            {
                "name": selected.name,
                "publisher": selected.publisher,
                "version": selected.version,
                "download_url": selected.download_url,
                "checksum": selected.checksum,
                "install_path": str(target),
                "version_state": selected.version_state,
            }
        )
        payload = {
            "version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "skills": sorted(skills, key=lambda item: str(item.get("name", ""))),
        }
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        self.lock_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _version_sort_key(version_text: str) -> tuple[int, Version | str]:
    try:
        return (1, Version(version_text))
    except InvalidVersion:
        return (0, version_text)


def _compare_version(left: str, right: str) -> int:
    left_key = _version_sort_key(left)
    right_key = _version_sort_key(right)
    if left_key > right_key:
        return 1
    if left_key < right_key:
        return -1
    return 0


def _is_hidden_entry(entry: dict[str, Any]) -> bool:
    takedown = entry.get("takedown", {})
    if isinstance(takedown, dict) and bool(takedown.get("is_taken_down", False)):
        return True
    if bool(entry.get("is_taken_down", False)):
        return True
    return bool(entry.get("blacklisted", False))


def _find_source_entry(index_data: dict[str, Any], selected: SearchResult) -> dict[str, Any] | None:
    skills = index_data.get("skills", [])
    if not isinstance(skills, list):
        return None
    for entry in skills:
        manifest = entry.get("manifest", {})
        if (
            str(manifest.get("publisher", "")) == selected.publisher
            and str(manifest.get("name", "")) == selected.name
            and str(manifest.get("version", "")) == selected.version
        ):
            return entry if isinstance(entry, dict) else None
    return None


def _is_cache_fresh(path: Path, *, ttl_seconds: int) -> bool:
    if not path.exists():
        return False
    if ttl_seconds <= 0:
        return False
    age = time.time() - path.stat().st_mtime
    return age <= ttl_seconds


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_extract_tar(archive: tarfile.TarFile, target: Path) -> None:
    target_root = target.resolve()
    members = archive.getmembers()
    for member in members:
        # Reject link entries to avoid archive-based filesystem escapes.
        if member.islnk() or member.issym():
            raise ValueError(f"unsafe archive member: {member.name}")
        member_path = (target_root / member.name).resolve()
        try:
            member_path.relative_to(target_root)
        except ValueError as exc:
            raise ValueError(f"unsafe archive member path: {member.name}") from exc
    for member in members:
        archive.extract(member, path=target_root)
