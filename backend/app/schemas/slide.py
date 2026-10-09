from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SlideImportPathRequest(BaseModel):
    """Register a WSI file already sitting under the configured watch directory."""

    path: str
    config_version_id: int | None = None


class SlideOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    filename: str
    source_type: str
    format: str | None
    status: str
    error_message: str | None

    width_l0: int | None
    height_l0: int | None
    level_count: int | None
    level_dimensions: list | None
    level_downsamples: list | None
    mpp_x: float | None
    mpp_y: float | None
    magnification: float | None

    tissue_area_mm2: float | None
    tissue_coverage_pct: float | None
    tissue_mask_path: str | None
    tissue_source: str = "auto"
    image_version: str = "0"  # add as ?v= to tile/patch/thumbnail URLs so the browser may cache them
    active_config_version_id: int | None
    active_grid_key: str | None = None
    split: str | None = None  # train | val | test
    # Progress in the active grid; filled in by the slide list (None elsewhere).
    patch_count: int | None = None
    annotated_patch_count: int | None = None
    reviewed_patch_count: int | None = None

    created_at: datetime
    updated_at: datetime


class SkippedItemOut(BaseModel):
    name: str
    reason: str


class SlideBatchImportResult(BaseModel):
    """Outcome of importing one or more slides from uploads, zips or a folder.

    Partial success is normal (e.g. a zip holding two slides and a mask), so
    every file that wasn't imported is listed with the reason."""

    slides: list[SlideOut]
    skipped: list[SkippedItemOut]
    ignored_file_count: int = 0  # companion/other files that aren't slides themselves
    warnings: list[str] = []


class WsiFormatOut(BaseModel):
    extension: str
    description: str


class WsiFormatsOut(BaseModel):
    formats: list[WsiFormatOut]
    archives: list[str]
    max_upload_bytes: int
    max_upload_files: int


class SlideStatsOut(BaseModel):
    total_patches: int
    kept_patches: int
    annotated_patches: int
    reviewed_patches: int
    skipped_patches: int
    flagged_patches: int
    coverage_pct: float


class DetectTissueRequest(BaseModel):
    method: str = "hsv_otsu"
    otsu_sensitivity: float = 0.65
    morph_open_px: int = 3
    morph_close_px: int = 5
    min_component_px: int = 400


class DetectTissueResponse(BaseModel):
    tissue_area_mm2: float | None
    tissue_coverage_pct: float
    mask_url: str


class TissueRegion(BaseModel):
    """A hand-drawn area that adds tissue to the mask or removes it, in Level-0 pixels."""

    mode: Literal["add", "remove"]
    type: Literal["polygon", "freehand", "rectangle", "circle"]
    coordinates: list[list[float]]


class TissueRegionOut(TissueRegion):
    id: int


class TissueRegionsIn(BaseModel):
    source: Literal["auto", "manual"]
    regions: list[TissueRegion] = Field(default_factory=list, max_length=500)


class TissueRegionsOut(BaseModel):
    source: str
    regions: list[TissueRegionOut]
    tissue_area_mm2: float | None
    tissue_coverage_pct: float | None
    has_mask: bool


class GridSpecIn(BaseModel):
    """A patch grid: size, stride, magnification and tissue threshold (see services/patch_grid.py)."""

    patch_width: int = Field(ge=16, le=8192)
    patch_height: int = Field(ge=16, le=8192)
    stride_x: int = Field(ge=1, le=8192)
    stride_y: int = Field(ge=1, le=8192)
    target_magnification: float | None = Field(default=None, gt=0, le=200)
    min_tissue_fraction: float = Field(default=0.5, ge=0, le=1)
    include_edge_patches: bool = False
    allow_partial_patches: bool = False


class GridOut(BaseModel):
    key: str
    label: str
    spec: GridSpecIn
    patch_count: int
    annotated_patch_count: int
    active: bool
    is_default: bool  # the configuration's own grid


class ProjectGridOut(BaseModel):
    """A patch size used somewhere in the project, with how much of it exists."""

    key: str
    label: str
    spec: GridSpecIn
    slide_count: int
    patch_count: int
    annotated_patch_count: int
    annotation_count: int  # drawn in its patches (kept, as whole-slide annotations, if it is removed)
    is_default: bool  # the project's grid, used by Generate Coords


class AddProjectGridRequest(BaseModel):
    grid: GridSpecIn
    # Also make it the project's grid (what Generate Coords cuts from now on).
    make_default: bool = False


class SkippedSlideOut(BaseModel):
    slide_id: int
    slide: str
    reason: str


class AddProjectGridOut(BaseModel):
    grid_key: str
    grid_label: str
    slides: int  # slides cut into it
    patches: int
    skipped: list[SkippedSlideOut]


class GridRemovalOut(BaseModel):
    slides: int
    patches: int
    annotations_kept: int


class GeneratePatchesRequest(BaseModel):
    config_version_id: int
    # Another grid than the configuration's own (a different patch size, stride ...). The slide
    # switches to it; its other grids -- and every annotation -- stay as they are.
    grid: GridSpecIn | None = None
    # True: cover the whole slide (every patch up to its edges, whatever the tissue; needs no tissue
    # detection). False: only the tissue, by the configuration's threshold. None: as the grid is.
    whole_slide: bool | None = None


class GeneratePatchesResponse(BaseModel):
    total_candidates: int
    kept: int
    excluded: int
    grid_key: str | None = None
    grid_label: str | None = None
    # Annotated patches of an earlier run of this grid that no longer meet the threshold: kept, so no
    # annotation is ever lost by regenerating.
    preserved: int = 0
    # The other patch grids this one replaced, their patches, and the annotations drawn in those
    # patches -- kept, as whole-slide annotations at the same place.
    replaced_grids: int = 0
    replaced_patches: int = 0
    annotations_moved_to_slide: int = 0


class SetActiveGridRequest(BaseModel):
    grid_key: str
