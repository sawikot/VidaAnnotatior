from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SlideImportPathRequest(BaseModel):
    """Register a WSI file already sitting under the configured watch directory."""

    path: str
    config_version_id: int | None = None


class SlideActiveConfigRequest(BaseModel):
    config_version_id: int


class SlideCreateDemo(BaseModel):
    filename: str = "demo_slide.svs"
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
    active_config_version_id: int | None

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


class GeneratePatchesRequest(BaseModel):
    config_version_id: int


class GeneratePatchesResponse(BaseModel):
    total_candidates: int
    kept: int
    excluded: int
