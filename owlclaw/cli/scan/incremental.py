"""Incremental scanning utilities for cli-scan."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from owlclaw.cli.scan.models import FileScanResult, ScanMetadata, ScanResult


@dataclass(slots=True)
class ScanCache:
    project_path: Path
    cache_file: str = ".owlclaw-scan-cache.json"

    @property
    def cache_path(self) -> Path:
        return self.project_path / self.cache_file

    def load(self) -> tuple[dict[str, float], dict[str, FileScanResult]]:
        if not self.cache_path.exists():
            return {}, {}
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}, {}

        mtimes_raw = payload.get("mtimes", {})
        files_raw = payload.get("files", {})
        if not isinstance(mtimes_raw, dict) or not isinstance(files_raw, dict):
            return {}, {}

        data = {"metadata": {"project_path": str(self.project_path)}, "files": files_raw}
        results = ScanResult.from_dict(data).files
        mtimes = {str(key): float(value) for key, value in mtimes_raw.items()}
        return mtimes, results

    def save(self, mtimes: dict[str, float], files: dict[str, FileScanResult]) -> None:
        scan = ScanResult(metadata=ScanMetadata(project_path=str(self.project_path)), files=files)
        payload = {
            "mtimes": mtimes,
            "files": scan.to_dict().get("files", {}),
        }
        self.cache_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")


class IncrementalScanner:
    """Resolve changed files and merge incremental scan results."""

    def __init__(self, project_path: Path, cache: ScanCache | None = None) -> None:
        self.project_path = Path(project_path)
        self.cache = cache or ScanCache(self.project_path)

    def get_changed_files(self, files: list[Path]) -> list[Path]:
        git_changed = self._get_changed_files_from_git()
        if git_changed is not None:
            selected = [path for path in files if self._relative(path) in git_changed]
            if selected:
                return sorted(selected)

        mtimes, _ = self.cache.load()
        changed: list[Path] = []
        for path in files:
            key = self._relative(path)
            current_mtime = path.stat().st_mtime
            if key not in mtimes or mtimes[key] != current_mtime:
                changed.append(path)
        return sorted(changed)

    def load_cache(self) -> tuple[dict[str, float], dict[str, FileScanResult]]:
        return self.cache.load()

    def save_cache(self, files: dict[str, FileScanResult]) -> None:
        mtimes: dict[str, float] = {}
        for rel_path in files:
            abs_path = self.project_path / rel_path
            if abs_path.exists():
                mtimes[rel_path] = abs_path.stat().st_mtime
        self.cache.save(mtimes, files)

    def merge_results(
        self,
        cached_results: dict[str, FileScanResult],
        incremental_results: dict[str, FileScanResult],
        current_files: list[Path],
    ) -> dict[str, FileScanResult]:
        existing = {self._relative(path) for path in current_files}
        merged = {key: value for key, value in cached_results.items() if key in existing}
        merged.update(incremental_results)
        return merged

    def _get_changed_files_from_git(self) -> set[str] | None:
        try:
            output = subprocess.check_output(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=self.project_path,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return None

        changed = {line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()}
        return changed

    def _relative(self, path: Path) -> str:
        return str(path.relative_to(self.project_path)).replace("\\", "/")
