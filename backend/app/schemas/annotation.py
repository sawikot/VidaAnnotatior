from __future__ import annotations

from datetime import datetime
from typing import Literal

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
    # A shape reworked into another kind: a rectangle or circle the brush added to or cut into is an outline now.
    type: str | None = None
    coordinates_patch_local: list[list[float]] | None = None
    # Only for slide-level annotations (which have no patch-local coordinates).
    coordinates_level0: list[list[float]] | None = None
    notes: str | None = None
    unsure: bool | None = None
    flagged: bool | None = None
    excluded: bool | None = None


class ImportAnnotationEntry(BaseModel):
    """One shape to import, in Level-0 coordinates: an entry of a WSI JSON export's `annotations[]`,
    or what `/import-annotations/parse` made of another format."""

    type: str
    label: str | None = None
    unsure: bool = False
    flagged: bool = False
    # Null for a slide-level annotation (drawn on the whole slide, in no particular patch).
    source_patch: dict | None = None
    coordinates: list[list[float]]


# What to do with every shape carrying one label of the file: give it this class (by id), import it
# without a class, or leave it out.
LabelTarget = int | Literal["skip", "unlabeled"]


class ImportAnnotationsRequest(BaseModel):
    """Body shape matches the WSI JSON export's top level loosely -- only
    `annotations` is required, so a full previously-exported file can be
    posted as-is."""

    config_version_id: int | None = None
    created_by: str | None = None
    annotations: list[ImportAnnotationEntry]
    # File label ("" for shapes without one) -> target. Labels not listed are matched to a class by
    # name, and skipped when no class has that name.
    label_map: dict[str, LabelTarget] | None = None
    # Put each shape that names no patch into the patch that wholly contains it (patch annotations
    # count toward the patch's status); shapes no patch contains stay on the whole slide.
    assign_to_patches: bool = False
    # "patches": leave out shapes lying where the slide has no patch (a grid over the tissue only).
    area: Literal["slide", "patches"] = "slide"


class ImportAnnotationsResponse(BaseModel):
    total: int
    imported: int
    imported_to_patches: int = 0
    imported_on_slide: int = 0
    skipped_no_matching_patch: int
    skipped_unknown_class: int
    skipped_duplicate: int
    skipped_invalid_shape: int = 0
    skipped_outside_slide: int = 0
    skipped_outside_patch_area: int = 0  # only the area covered by patches was asked for
    skipped_by_choice: int = 0  # their label was mapped to "skip"


class ImportLabelOut(BaseModel):
    label: str  # "" for shapes without a label
    count: int
    class_id: int | None  # the class of that name, if the configuration has one


class ImportClassOut(BaseModel):
    id: int
    name: str
    color_hex: str
    code: int | None = None


class ParsedAnnotationsOut(BaseModel):
    """An annotation file read and converted, before anything is saved."""

    format: str
    format_name: str
    annotations: list[ImportAnnotationEntry]
    labels: list[ImportLabelOut]
    classes: list[ImportClassOut]  # the slide configuration's classes, to map labels onto
    shape_counts: dict[str, int]
    linked_to_patches: int  # shapes that name the patch they were drawn in
    outside_slide: int  # shapes reaching outside the slide (usually a wrong scale)
    bounds: list[float] | None  # [min_x, min_y, max_x, max_y] of all shapes, Level-0
    slide_size: list[int | None]  # [width, height], Level-0
    image_project: bool  # every shape must go in the image's patch
    # What the slide's patches cover: all of it, or its tissue only. None: no patches, or an image.
    patch_grid: Literal["whole", "tissue"] | None = None
    outside_patches: dict[str, int] = {}  # label -> shapes on the slide but where it has no patch
    scale: float  # every coordinate was multiplied by this
    scale_auto: bool  # the scale was worked out from the file, not given
    scale_note: str | None = None  # why it is not 1, when worked out
    unreadable: dict[str, int]  # reason -> shapes in the file that could not be converted
    warnings: list[str]


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
    whole_patch: bool = False  # the fill of a patch label
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
