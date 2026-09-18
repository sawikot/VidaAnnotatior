from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class GeometryAnnotationCreate(BaseModel):
    type: str  # polygon|rectangle|point|freehand
    class_id: int | None = None
    coordinates_patch_local: list[list[float]]
    created_by: str | None = None
    notes: str | None = None
    unsure: bool = False
    flagged: bool = False
    excluded: bool = False


class GeometryAnnotationUpdate(BaseModel):
    class_id: int | None = None
    coordinates_patch_local: list[list[float]] | None = None
    notes: str | None = None
    unsure: bool | None = None
    flagged: bool | None = None
    excluded: bool | None = None


class GeometryAnnotationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patch_id: int
    slide_id: int
    config_version_id: int
    class_id: int | None
    type: str
    coordinates_patch_local: list
    coordinates_level0: list
    created_by: str | None
    notes: str | None
    unsure: bool
    flagged: bool
    excluded: bool
    created_at: datetime
    updated_at: datetime
