"""Parallel execution utilities for cli-scan."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ParallelTaskResult:
    file_path: str
    result: Any | None = None
    error: str | None = None


def _safe_execute(args: tuple[str, Callable[[Path], Any]]) -> ParallelTaskResult:
    file_path, worker = args
    path = Path(file_path)
    try:
        return ParallelTaskResult(file_path=file_path, result=worker(path), error=None)
    except Exception as exc:  # pragma: no cover - defensive boundary
        return ParallelTaskResult(file_path=file_path, result=None, error=str(exc))


class ParallelExecutor:
    """Run file-level scanning tasks in parallel with deterministic order."""

    def __init__(self, workers: int | None = None) -> None:
        cpu_count = os.cpu_count() or 1
        self.workers = workers or cpu_count

    def run(self, files: list[Path], worker: Callable[[Path], Any]) -> list[ParallelTaskResult]:
        if not files:
            return []

        payload = [(str(path), worker) for path in files]
        running_under_pytest_cov = bool(os.environ.get("PYTEST_CURRENT_TEST") and os.environ.get("COV_CORE_SOURCE"))
        if self.workers <= 1 or running_under_pytest_cov:
            return [_safe_execute(item) for item in payload]

        with Pool(processes=self.workers) as pool:
            results = pool.map(_safe_execute, payload)
        return results
