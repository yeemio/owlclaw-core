"""Request/response schemas for OwlHub API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class VersionInfo(BaseModel):
    """Version info for one skill release."""

    version: str
    version_state: str = "released"
    published_at: datetime | None = None
    updated_at: datetime | None = None


class SkillDetail(BaseModel):
    """Skill detail payload."""

    name: str
    publisher: str
    description: str
    tags: list[str] = Field(default_factory=list)
    dependencies: dict[str, str] = Field(default_factory=dict)
    versions: list[VersionInfo] = Field(default_factory=list)
    statistics: dict[str, Any] = Field(default_factory=dict)


class SkillStatisticsResponse(BaseModel):
    """Aggregated statistics payload for one skill."""

    skill_name: str
    publisher: str
    total_downloads: int
    downloads_last_30d: int
    total_installs: int
    active_installs: int
    last_updated: datetime


class SkillSearchItem(BaseModel):
    """One skill row in search results."""

    name: str
    publisher: str
    version: str
    description: str
    tags: list[str] = Field(default_factory=list)
    version_state: str = "released"
    quality_score: float | None = None
    low_quality_warning: bool = False


class SkillSearchResponse(BaseModel):
    """Paginated search response."""

    total: int
    page: int = 1
    page_size: int = 20
    items: list[SkillSearchItem] = Field(default_factory=list)


class PublishRequest(BaseModel):
    """Request body for publish endpoint."""

    publisher: str
    skill_name: str
    version: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class PublishResponse(BaseModel):
    """Publish endpoint response."""

    accepted: bool
    review_id: str
    status: str


class UpdateStateRequest(BaseModel):
    """Request body for version state updates."""

    state: str


class RejectRequest(BaseModel):
    """Request body for review rejection."""

    reason: str


class AppealRequest(BaseModel):
    """Request body for review appeal."""

    reason: str


class ReviewRecordResponse(BaseModel):
    """Review record payload used by review endpoints."""

    review_id: str
    skill_name: str
    version: str
    publisher: str
    status: str
    comments: str
    reviewed_at: datetime


class TakedownRequest(BaseModel):
    """Request body for skill takedown operations."""

    reason: str
