"""Dependency resolver for OwlHub skill installation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from packaging.version import InvalidVersion, Version


@dataclass(frozen=True)
class ResolvedNode:
    """Resolved node in install plan."""

    name: str
    version: str
    publisher: str
    dependencies: dict[str, str]
    result: Any


class DependencyResolver:
    """Resolve skill dependencies with topological ordering."""

    def __init__(self, *, get_candidates: Callable[[str], list[Any]]) -> None:
        self._get_candidates = get_candidates

    def resolve(self, *, root: Any) -> list[ResolvedNode]:
        visiting: set[str] = set()
        visited: set[str] = set()
        resolved: dict[str, ResolvedNode] = {}
        order: list[str] = []

        def visit(skill: Any, constraint: str | None = None) -> None:
            name = skill.name
            if name in visited:
                self._assert_constraint(resolved[name].result, constraint)
                return
            if name in visiting:
                raise ValueError(f"circular dependency detected: {name}")
            visiting.add(name)
            self._assert_constraint(skill, constraint)

            dependencies = skill.dependencies if isinstance(skill.dependencies, dict) else {}
            for dep_name, dep_constraint in dependencies.items():
                candidate = self._select_candidate(dep_name, str(dep_constraint))
                visit(candidate, str(dep_constraint))

            visiting.remove(name)
            visited.add(name)
            resolved[name] = ResolvedNode(
                name=skill.name,
                version=skill.version,
                publisher=skill.publisher,
                dependencies=dependencies,
                result=skill,
            )
            order.append(name)

        visit(root, None)
        return [resolved[name] for name in order]

    def _select_candidate(self, name: str, constraint: str) -> Any:
        candidates = [item for item in self._get_candidates(name) if item.name == name]
        if not candidates:
            raise ValueError(f"missing dependency: {name}")
        valid = [item for item in candidates if _match_constraint(item.version, constraint)]
        if not valid:
            raise ValueError(f"no version of {name} satisfies constraint {constraint}")
        valid.sort(key=lambda item: _version_sort_key(item.version))
        return valid[-1]

    @staticmethod
    def _assert_constraint(skill: Any, constraint: str | None) -> None:
        if constraint and not _match_constraint(skill.version, constraint):
            raise ValueError(f"dependency conflict: {skill.name}@{skill.version} does not satisfy {constraint}")


def _match_constraint(version: str, constraint: str) -> bool:
    text = constraint.strip()
    if not text:
        return True
    if text.startswith("^"):
        base = text[1:].strip()
        base_v = _to_version(base)
        current = _to_version(version)
        if base_v is None or current is None:
            return version == base
        upper_bound = Version(f"{base_v.major + 1}.0.0")
        return base_v <= current < upper_bound
    if text.startswith("~"):
        base = text[1:].strip()
        base_v = _to_version(base)
        current = _to_version(version)
        if base_v is None or current is None:
            return version == base
        upper_bound = Version(f"{base_v.major}.{base_v.minor + 1}.0")
        return base_v <= current < upper_bound
    if text.startswith(">=") and ",<" in text:
        lower_text, upper_text = text.split(",", 1)
        lower = lower_text.replace(">=", "").strip()
        upper_raw = upper_text.replace("<", "").strip()
        lower_v = _to_version(lower)
        upper_v = _to_version(upper_raw)
        current = _to_version(version)
        if lower_v is None or upper_v is None or current is None:
            return version == lower
        return lower_v <= current < upper_v
    return version == text


def _to_version(raw: str) -> Version | None:
    try:
        return Version(raw)
    except InvalidVersion:
        return None


def _version_sort_key(raw: str) -> tuple[int, Version | str]:
    parsed = _to_version(raw)
    if parsed is None:
        return (0, raw)
    return (1, parsed)
