"""Core schema models for OwlHub indexing and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class VersionState(str, Enum):
    """Publication state for a skill version."""

    DRAFT = "draft"
    RELEASED = "released"
    DEPRECATED = "deprecated"


@dataclass(frozen=True)
class SkillManifest:
    """Normalized metadata required for one published skill version."""

    name: str
    version: str
    publisher: str
    description: str
    license: str
    tags: list[str] = field(default_factory=list)
    industry: str | None = None
    dependencies: dict[str, str] = field(default_factory=dict)
    repository: str | None = None
    homepage: str | None = None
    version_state: VersionState = VersionState.RELEASED


@dataclass(frozen=True)
class IndexEntry:
    """One index entry used in `index.json`."""

    manifest: SkillManifest
    download_url: str
    checksum: str
    published_at: datetime
    updated_at: datetime
    version_state: VersionState = VersionState.RELEASED


@dataclass(frozen=True)
class ValidationError:
    """One validation issue."""

    field: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class ValidationResult:
    """Validation result for a skill package."""

    is_valid: bool
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)


def utc_now() -> datetime:
    """Return timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)
