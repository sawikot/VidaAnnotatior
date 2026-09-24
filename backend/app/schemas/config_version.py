from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AnnotationClassIn(BaseModel):
    name: str
    color_hex: str = "#2563eb"
    hotkey: str | None = None
    order_index: int = 0


class AnnotationClassOut(AnnotationClassIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class AnnotationClassSync(BaseModel):
    """One row of the full class list sent when editing a config version.

    `id` present -> update that existing class in place (renames/recolors keep
    every annotation that points at it). `id` absent -> create. Any existing
    class missing from the list is deleted, which is refused if annotations
    still use it."""

    id: int | None = None
    name: str = Field(min_length=1, max_length=100)
    color_hex: str = Field(default="#2563eb", pattern=r"^#[0-9a-fA-F]{6}$")
    hotkey: str | None = Field(default=None, max_length=2)


class ConfigVersionCreate(BaseModel):
    version_label: str = "v1.0"
    title: str | None = None
    created_by: str | None = None

    coordinate_system: str = "level0"
    target_magnification: float = Field(default=20.0, gt=0, le=200)
    target_level: int | None = None
    mpp_handling: str = "auto"

    patch_width: int = Field(default=512, ge=16, le=8192)
    patch_height: int = Field(default=512, ge=16, le=8192)
    stride_x: int = Field(default=512, ge=1, le=8192)
    stride_y: int = Field(default=512, ge=1, le=8192)
    min_tissue_fraction: float = Field(default=0.6, ge=0, le=1)
    allow_partial_patches: bool = False
    include_edge_patches: bool = True

    tissue_method: str = "hsv_otsu"
    tissue_params: dict = {}

    enabled_tools: list[str] = ["polygon", "rectangle", "point", "freehand", "line", "freehand_line", "circle"]

    allow_skip: bool = True
    allow_unsure: bool = True
    require_annotation: bool = False
    reviewer_mode: bool = False

    annotation_classes: list[AnnotationClassIn] = []


class ConfigVersionUpdate(BaseModel):
    """All fields optional; server rejects critical-field changes once patches exist."""

    title: str | None = None
    status: str | None = None
    target_magnification: float | None = Field(default=None, gt=0, le=200)
    target_level: int | None = None
    mpp_handling: str | None = None
    patch_width: int | None = Field(default=None, ge=16, le=8192)
    patch_height: int | None = Field(default=None, ge=16, le=8192)
    stride_x: int | None = Field(default=None, ge=1, le=8192)
    stride_y: int | None = Field(default=None, ge=1, le=8192)
    min_tissue_fraction: float | None = Field(default=None, ge=0, le=1)
    allow_partial_patches: bool | None = None
    include_edge_patches: bool | None = None
    tissue_method: str | None = None
    tissue_params: dict | None = None
    enabled_tools: list[str] | None = None
    allow_skip: bool | None = None
    allow_unsure: bool | None = None
    require_annotation: bool | None = None
    reviewer_mode: bool | None = None
    # Full replacement list (see AnnotationClassSync). Applied in the same
    # transaction as the field changes, so a rejected class edit never leaves
    # the geometry fields half-updated.
    annotation_classes: list[AnnotationClassSync] | None = None


class ConfigUsageOut(BaseModel):
    patch_count: int
    annotation_count: int
    slide_count: int


class ConfigVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    parent_version_id: int | None
    version_label: str
    status: str
    title: str | None
    created_by: str | None
    config_hash: str | None

    coordinate_system: str
    target_magnification: float | None
    target_level: int | None
    mpp_handling: str

    patch_width: int
    patch_height: int
    stride_x: int
    stride_y: int
    min_tissue_fraction: float
    allow_partial_patches: bool
    include_edge_patches: bool

    tissue_method: str
    tissue_params: dict
    enabled_tools: list

    allow_skip: bool
    allow_unsure: bool
    require_annotation: bool
    reviewer_mode: bool

    created_at: datetime
    updated_at: datetime

    annotation_classes: list[AnnotationClassOut] = []
