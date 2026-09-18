from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slide_id: int
    config_version_id: int
    patch_index: int
    x: int
    y: int
    level: int
    width: int
    height: int
    width_l0: int
    height_l0: int
    tissue_fraction: float
    status: str
    patch_label: str | None
    unsure: bool
    flagged: bool
    excluded: bool
    notes: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PatchUpdate(BaseModel):
    status: str | None = None
    patch_label: str | None = None
    unsure: bool | None = None
    flagged: bool | None = None
    excluded: bool | None = None
    notes: str | None = None
    reviewed_by: str | None = None


class PatchListResponse(BaseModel):
    total: int
    items: list[PatchOut]
