from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.config_version import ConfigVersionCreate, ConfigVersionOut


class SplitSettings(BaseModel):
    """How a project's slides are split into train / validation / test (services/dataset_split.py)."""

    mode: Literal["off", "random", "manual"] = "off"
    train: int = Field(default=70, ge=0, le=100)  # shares in percent; they add up to 100
    val: int = Field(default=15, ge=0, le=100)
    test: int = Field(default=15, ge=0, le=100)


class ProjectCreate(BaseModel):
    name: str
    project_type: Literal["wsi", "image"] = "wsi"
    slug: str | None = None
    organ: str | None = None
    description: str | None = None
    team: str | None = None
    config: ConfigVersionCreate = ConfigVersionCreate()
    split: SplitSettings | None = None


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
    project_type: str
    split_config: dict | None = None
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
