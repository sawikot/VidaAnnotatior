from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.config_version import ConfigVersionCreate, ConfigVersionOut


class ProjectCreate(BaseModel):
    name: str
    slug: str | None = None
    organ: str | None = None
    description: str | None = None
    team: str | None = None
    config: ConfigVersionCreate = ConfigVersionCreate()


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    organ: str | None = None
    description: str | None = None
    team: str | None = None
    status: str | None = None
    active_config_version_id: int | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    organ: str | None
    description: str | None
    team: str | None
    status: str
    active_config_version_id: int | None
    created_at: datetime
    updated_at: datetime


class ProjectStats(BaseModel):
    slide_count: int
    processed_slide_count: int
    total_patches: int
    annotated_patches: int
    reviewed_patches: int
    flagged_patches: int
    tissue_area_mm2: float


class ProjectDetailOut(ProjectOut):
    stats: ProjectStats
    active_config: ConfigVersionOut | None = None
