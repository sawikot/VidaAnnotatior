from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator

from app.services.geometry import validate_shape


class GeometryAnnotationCreate(BaseModel):
    type: str  # point|line|freehand_line|rectangle|circle|polygon|freehand
    class_id: int | None = None
    coordinates_patch_local: list[list[float]]
    created_by: str | None = None
    notes: str | None = None
    unsure: bool = False
    flagged: bool = False
    excluded: bool = False

    @model_validator(mode="after")
    def _well_formed(self) -> "GeometryAnnotationCreate":
        validate_shape(self.type, self.coordinates_patch_local)  # ValueError -> HTTP 422
        return self


class SlideAnnotationCreate(BaseModel):
    """A shape drawn directly on the whole slide: its coordinates are Level-0 pixels, the master space."""

    type: str
    class_id: int | None = None
    coordinates_level0: list[list[float]]
    created_by: str | None = None
    notes: str | None = None
    unsure: bool = False
    flagged: bool = False

    @model_validator(mode="after")
    def _well_formed(self) -> "SlideAnnotationCreate":
        validate_shape(self.type, self.coordinates_level0)
        return self


class GeometryAnnotationUpdate(BaseModel):
    class_id: int | None = None
    coordinates_patch_local: list[list[float]] | None = None
    # Only for slide-level annotations (which have no patch-local coordinates).
    coordinates_level0: list[list[float]] | None = None
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
    # Null for a slide-level annotation (drawn on the whole slide, in no particular patch).
    source_patch: dict | None = None
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
    skipped_invalid_shape: int = 0


class OwnerPatchOut(BaseModel):
    """Where the patch an annotation belongs to lies on the slide."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    patch_index: int
    x: int
    y: int
    width: int
    height: int
    width_l0: int
    height_l0: int


class GeometryAnnotationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patch_id: int | None  # None: a slide-level annotation
    slide_id: int
    config_version_id: int
    class_id: int | None
    type: str
    coordinates_patch_local: list
    coordinates_level0: list
    patch_bounds_l0: list[int] | None = None  # the owning patch on the slide: [x0, y0, x1, y1]
    created_by: str | None
    notes: str | None
    unsure: bool
    flagged: bool
    excluded: bool
    created_at: datetime
    updated_at: datetime


class OverlappingAnnotationOut(BaseModel):
    """An annotation drawn in another patch that reaches into this one (patches overlap when the
    stride is smaller than the patch), with the patch it belongs to."""

    annotation: GeometryAnnotationOut
    owner: OwnerPatchOut
