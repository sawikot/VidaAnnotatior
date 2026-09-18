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


class ImportAnnotationEntry(BaseModel):
    """One entry from a previously exported WSI JSON `annotations[]` array."""

    type: str
    label: str | None = None
    unsure: bool = False
    flagged: bool = False
    source_patch: dict
    coordinates: list[list[float]]


class ImportAnnotationsRequest(BaseModel):
    """Body shape matches the WSI JSON export's top level loosely -- only
    `annotations` is required, so a full previously-exported file can be
    posted as-is."""

    config_version_id: int | None = None
    created_by: str | None = None
    annotations: list[ImportAnnotationEntry]


class ImportAnnotationsResponse(BaseModel):
    total: int
    imported: int
    skipped_no_matching_patch: int
    skipped_unknown_class: int
    skipped_duplicate: int


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
