"""File discovery utilities for cli-scan."""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path


class FileDiscovery:
    """Discover Python files with include/exclude filtering."""

    DEFAULT_INCLUDE = ("*.py",)
    DEFAULT_EXCLUDE = ("*/venv/*", "*/.venv/*", "*/env/*", "*/site-packages/*")

    def __init__(self, include_patterns: list[str] | None = None, exclude_patterns: list[str] | None = None) -> None:
        self.include_patterns = include_patterns or list(self.DEFAULT_INCLUDE)
        self.exclude_patterns = exclude_patterns or list(self.DEFAULT_EXCLUDE)

    def discover(self, project_path: Path) -> list[Path]:
        root = Path(project_path)
        if not root.exists():
            return []

        discovered: list[Path] = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.is_symlink():
                continue
            normalized = path.as_posix()
            if self._is_excluded(normalized):
                continue
            if self._is_included(path.name):
                discovered.append(path)
        return sorted(discovered)

    def _is_included(self, filename: str) -> bool:
        return any(fnmatch(filename, pattern) for pattern in self.include_patterns)

    def _is_excluded(self, normalized_path: str) -> bool:
        if "/venv/" in normalized_path or "/.venv/" in normalized_path or "/env/" in normalized_path:
            return True
        if "/site-packages/" in normalized_path:
            return True
        return any(fnmatch(normalized_path, pattern) for pattern in self.exclude_patterns)
