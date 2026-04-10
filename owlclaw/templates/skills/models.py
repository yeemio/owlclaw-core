"""Core data models for the SKILL.md template library."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class TemplateCategory(str, Enum):
    """Template category for classification."""

    MONITORING = "monitoring"
    ANALYSIS = "analysis"
    WORKFLOW = "workflow"
    INTEGRATION = "integration"
    REPORT = "report"


@dataclass
class TemplateParameter:
    """Template parameter definition."""

    name: str
    type: str  # str, int, bool, list
    description: str
    required: bool = True
    default: Any = None
    choices: list[Any] | None = None


@dataclass
class TemplateMetadata:
    """Template metadata extracted from .md.j2 files."""

    id: str  # e.g. "monitoring/health-check"
    name: str
    category: TemplateCategory
    description: str
    tags: list[str]
    parameters: list[TemplateParameter]
    examples: list[str]
    file_path: Path


@dataclass
class ValidationError:
    """A single validation error (not an exception)."""

    field: str
    message: str
    severity: str  # "error" or "warning"


@dataclass
class SearchResult:
    """Search result with relevance score."""

    template: TemplateMetadata
    score: float  # 0–1
    match_reason: str
