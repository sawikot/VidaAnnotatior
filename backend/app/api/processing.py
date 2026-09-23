from __future__ import annotations

import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import forbid_for_image_project, get_slide_or_404
from app.core.config import get_settings
from app.database.session import get_db
from app.models.config_version import ProjectConfigVersion
from app.models.patch import Patch
from app.models.slide import Slide
from app.schemas.slide import (
    DetectTissueRequest,
    DetectTissueResponse,
    GeneratePatchesRequest,
    GeneratePatchesResponse,
    TissueRegionOut,
    TissueRegionsIn,
    TissueRegionsOut,
)
from app.services import reader_cache
from app.services.geometry import validate_shape
from app.services.patch_generator import generate_patch_grid
from app.services.tissue_detector import get_detector
from app.services.tissue_mask import DETECTION_MAX_SIZE, auto_mask_path, load_mask, rebuild_slide_mask, save_mask

router = APIRouter(tags=["processing"])


@router.post("/slides/{slide_id}/detect-tissue", response_model=DetectTissueResponse)
def detect_tissue(
    payload: DetectTissueRequest,
    slide: Slide = Depends(get_slide_or_404),
    db: Session = Depends(get_db),
):
    forbid_for_image_project(slide.project, "Tissue detection")
    if slide.status == "error" or not slide.width_l0:
        raise HTTPException(status_code=422, detail="Slide metadata is not available; re-import the slide.")

    settings = get_settings()
    try:
        reader = reader_cache.get_reader_for_slide(slide)
        thumbnail = reader.get_thumbnail(DETECTION_MAX_SIZE)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    detector = get_detector(payload.method)
    params = payload.model_dump(exclude={"method"})
    result = detector.detect(thumbnail, params)

    # The detector's own result is kept apart; the mask patches use is it plus any hand-drawn regions.
    save_mask(auto_mask_path(settings.wsi_storage_dir, slide), result.mask)
    slide.tissue_params_used = {"method": payload.method, **params}
    slide.tissue_source = "auto"  # running detection means the mask starts from its result
    rebuild_slide_mask(settings.wsi_storage_dir, slide)
    db.commit()

    return DetectTissueResponse(
        tissue_area_mm2=slide.tissue_area_mm2,
        tissue_coverage_pct=slide.tissue_coverage_pct,
        mask_url=f"/api/slides/{slide.id}/tissue-mask.png",
    )


@router.get("/slides/{slide_id}/tissue-mask.png")
def get_tissue_mask(slide: Slide = Depends(get_slide_or_404)):
    if not slide.tissue_mask_path:
        raise HTTPException(status_code=404, detail="Tissue has not been detected for this slide yet.")
    settings = get_settings()
    mask_path = settings.wsi_storage_dir / slide.tissue_mask_path
    if not mask_path.exists():
        raise HTTPException(status_code=404, detail="Tissue mask file is missing on disk.")
    return Response(content=mask_path.read_bytes(), media_type="image/png")


def _regions_out(slide: Slide) -> TissueRegionsOut:
    return TissueRegionsOut(
        source=slide.tissue_source or "auto",
        regions=[TissueRegionOut(id=i + 1, **r) for i, r in enumerate(slide.tissue_regions or [])],
        tissue_area_mm2=slide.tissue_area_mm2,
        tissue_coverage_pct=slide.tissue_coverage_pct,
        has_mask=slide.tissue_mask_path is not None,
    )


@router.get("/slides/{slide_id}/tissue-regions", response_model=TissueRegionsOut)
def get_tissue_regions(slide: Slide = Depends(get_slide_or_404)):
    return _regions_out(slide)


@router.put("/slides/{slide_id}/tissue-regions", response_model=TissueRegionsOut)
def set_tissue_regions(
    payload: TissueRegionsIn,
    slide: Slide = Depends(get_slide_or_404),
    db: Session = Depends(get_db),
):
    """Replace the slide's hand-drawn tissue regions (and where its mask starts), then
    rebuild the mask patches are generated from. Existing patches are kept until
    Generate Coords is run again."""
    forbid_for_image_project(slide.project, "Tissue regions")
    if not slide.width_l0 or not slide.height_l0:
        raise HTTPException(status_code=422, detail="Slide metadata is not available; re-import the slide.")
    for i, region in enumerate(payload.regions, start=1):
        try:
            validate_shape(region.type, region.coordinates)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Region {i}: {exc}") from exc

    slide.tissue_source = payload.source
    slide.tissue_regions = [r.model_dump() for r in payload.regions]
    rebuild_slide_mask(get_settings().wsi_storage_dir, slide)
    db.commit()
    db.refresh(slide)
    return _regions_out(slide)


@router.post("/slides/{slide_id}/generate-patches", response_model=GeneratePatchesResponse)
def generate_patches(
    payload: GeneratePatchesRequest,
    slide: Slide = Depends(get_slide_or_404),
    db: Session = Depends(get_db),
):
    forbid_for_image_project(slide.project, "Patch generation")
    config = db.get(ProjectConfigVersion, payload.config_version_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Config version not found")
    if not slide.width_l0:
        raise HTTPException(status_code=422, detail="Slide metadata is not available; re-import the slide.")

    settings = get_settings()
    try:
        reader = reader_cache.get_reader_for_slide(slide)
        meta = reader.get_metadata()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    tissue_mask = None
    if slide.tissue_mask_path:
        mask_path = settings.wsi_storage_dir / slide.tissue_mask_path
        if mask_path.exists():
            tissue_mask = load_mask(mask_path)

    candidates = generate_patch_grid(meta, config, tissue_mask, slide.tissue_mask_downsample)
    if not candidates:
        raise HTTPException(
            status_code=422,
            detail="Zero patches were generated. Check patch/stride size against slide dimensions.",
        )

    kept = [c for c in candidates if c.kept]
    if not kept:
        raise HTTPException(
            status_code=422,
            detail=(
                f"0 of {len(candidates)} candidate patches met the minimum tissue fraction "
                f"({config.min_tissue_fraction:.0%}). Run tissue detection first or lower the threshold."
            ),
        )

    # Clear any previous patch grid for this (slide, config) pair before regenerating.
    db.query(Patch).filter(
        Patch.slide_id == slide.id, Patch.config_version_id == config.id
    ).delete(synchronize_session=False)

    for c in kept:
        db.add(
            Patch(
                slide_id=slide.id,
                config_version_id=config.id,
                patch_index=c.patch_index,
                x=c.x,
                y=c.y,
                level=c.level,
                width=c.width,
                height=c.height,
                width_l0=c.width_l0,
                height_l0=c.height_l0,
                tissue_fraction=c.tissue_fraction,
                status="unannotated",
            )
        )

    slide.active_config_version_id = config.id
    slide.status = "patches_generated"
    db.commit()

    return GeneratePatchesResponse(
        total_candidates=len(candidates), kept=len(kept), excluded=len(candidates) - len(kept)
    )
