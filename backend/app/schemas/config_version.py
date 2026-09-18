from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AnnotationClassIn(BaseModel):
    name: str
    color_hex: str = "#2563eb"
    hotkey: str | None = None
    order_index: int = 0


class AnnotationClassOut(AnnotationClassIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class ConfigVersionCreate(BaseModel):
    version_label: str = "v1.0"
    title: str | None = None
    created_by: str | None = None

    coordinate_system: str = "level0"
    target_magnification: float = 20.0
    target_level: int | None = None
    mpp_handling: str = "auto"

    patch_width: int = 512
    patch_height: int = 512
    stride_x: int = 512
    stride_y: int = 512
    min_tissue_fraction: float = 0.6
    allow_partial_patches: bool = False
    include_edge_patches: bool = True

    tissue_method: str = "hsv_otsu"
    tissue_params: dict = {}

    enabled_tools: list[str] = ["polygon", "rectangle", "point", "freehand"]

    allow_skip: bool = True
    allow_unsure: bool = True
    require_annotation: bool = False
    reviewer_mode: bool = False

    annotation_classes: list[AnnotationClassIn] = []


class ConfigVersionUpdate(BaseModel):
    """All fields optional; server rejects critical-field changes once patches exist."""

    title: str | None = None
    status: str | None = None
    target_magnification: float | None = None
    target_level: int | None = None
    mpp_handling: str | None = None
    patch_width: int | None = None
    patch_height: int | None = None
    stride_x: int | None = None
    stride_y: int | None = None
    min_tissue_fraction: float | None = None
    allow_partial_patches: bool | None = None
    include_edge_patches: bool | None = None
    tissue_method: str | None = None
    tissue_params: dict | None = None
    enabled_tools: list[str] | None = None
    allow_skip: bool | None = None
    allow_unsure: bool | None = None
    require_annotation: bool | None = None
    reviewer_mode: bool | None = None


class ConfigVersionForkRequest(BaseModel):
    new_version_label: str
    overrides: dict = {}
    created_by: str | None = None


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
