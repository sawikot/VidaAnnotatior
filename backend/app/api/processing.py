from __future__ import annotations

import io
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import forbid_for_image_project, get_slide_or_404
from app.core.config import get_settings
from app.database.session import get_db
from app.models.annotation import GeometryAnnotation
from app.models.config_version import ProjectConfigVersion
from app.models.patch import Patch
from app.models.slide import Slide
from app.schemas.slide import (
    DetectTissueRequest,
    DetectTissueResponse,
    GeneratePatchesRequest,
    GeneratePatchesResponse,
    GridOut,
    SetActiveGridRequest,
    SlideOut,
    TissueRegionOut,
    TissueRegionsIn,
    TissueRegionsOut,
)
from app.services import reader_cache
from app.services.geometry import validate_shape
from app.services.patch_generator import generate_patch_grid
from app.services.patch_grid import GridSpec, grid_key_of
from app.services.tissue_detector import get_detector
from app.services.tissue_mask import DETECTION_MAX_SIZE, auto_mask_path, load_mask, mask_outline, rebuild_slide_mask, save_mask

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


@router.get("/slides/{slide_id}/tissue-mask/outline")
def get_tissue_mask_outline(slide: Slide = Depends(get_slide_or_404)):
    """The slide's tissue mask as outline rings in Level-0 pixels (see ``mask_outline``), so a viewer can
    draw it as a crisp filled outline at any zoom instead of stretching the mask image."""
    if not slide.tissue_mask_path:
        raise HTTPException(status_code=404, detail="Tissue has not been detected for this slide yet.")
    mask_path = get_settings().wsi_storage_dir / slide.tissue_mask_path
    if not mask_path.exists():
        raise HTTPException(status_code=404, detail="Tissue mask file is missing on disk.")
    # Encoded directly: tens of thousands of points through the generic response encoder take seconds.
    rings = mask_outline(load_mask(mask_path), slide.width_l0, slide.height_l0)
    return Response(content=json.dumps({"rings": rings}, separators=(",", ":")), media_type="application/json")


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

    spec = GridSpec(**payload.grid.model_dump()) if payload.grid else GridSpec.from_config(config)
    candidates = generate_patch_grid(meta, spec, tissue_mask, slide.tissue_mask_downsample)
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
                f"({spec.min_tissue_fraction:.0%}). Run tissue detection first or lower the threshold."
            ),
        )

    # Regenerating a grid (after the tissue mask changed, say) updates it in place: a patch at the same
    # place keeps its id, status and annotations; an annotated patch that no longer meets the threshold
    # is kept too. Only empty patches that fell out are removed. Other grids are not touched.
    key = spec.key
    existing = {
        (p.x, p.y, p.width_l0, p.height_l0): p
        for p in db.query(Patch).filter(Patch.slide_id == slide.id, Patch.config_version_id == config.id, Patch.grid_key == key)
    }
    annotated_ids = {
        pid
        for (pid,) in db.query(GeometryAnnotation.patch_id).filter(
            GeometryAnnotation.patch_id.in_([p.id for p in existing.values()])
        )
    } if existing else set()

    seen = set()
    for c in kept:
        spot = (c.x, c.y, c.width_l0, c.height_l0)
        seen.add(spot)
        patch = existing.get(spot)
        if patch is not None:
            patch.patch_index, patch.tissue_fraction, patch.level = c.patch_index, c.tissue_fraction, c.level
            patch.width, patch.height = c.width, c.height
            continue
        db.add(
            Patch(
                slide_id=slide.id,
                config_version_id=config.id,
                grid_key=key,
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
    preserved = 0
    next_index = max((c.patch_index for c in candidates), default=0) + 1
    for spot, patch in existing.items():
        if spot in seen:
            continue
        if patch.id in annotated_ids:
            patch.patch_index = next_index  # after the grid's own, so indexes stay unique
            next_index += 1
            preserved += 1
        else:
            db.delete(patch)

    slide.active_config_version_id = config.id
    slide.active_grid_key = key
    slide.status = "patches_generated"
    db.commit()

    return GeneratePatchesResponse(
        total_candidates=len(candidates),
        kept=len(kept),
        excluded=len(candidates) - len(kept),
        grid_key=key,
        grid_label=spec.label,
        preserved=preserved,
    )


@router.get("/slides/{slide_id}/grids", response_model=list[GridOut])
def list_grids(slide: Slide = Depends(get_slide_or_404), db: Session = Depends(get_db)):
    """The patch grids this slide has under its active configuration (plus the configuration's own
    grid, even before it is generated), with how many patches and annotated patches each has."""
    config = db.get(ProjectConfigVersion, slide.active_config_version_id) if slide.active_config_version_id else None
    if config is None:
        return []
    default_key = grid_key_of(config)
    rows = (
        db.query(Patch.grid_key, func.count(Patch.id))
        .filter(Patch.slide_id == slide.id, Patch.config_version_id == config.id)
        .group_by(Patch.grid_key)
        .all()
    )
    annotated = dict(
        db.query(Patch.grid_key, func.count(func.distinct(Patch.id)))
        .join(GeometryAnnotation, GeometryAnnotation.patch_id == Patch.id)
        .filter(Patch.slide_id == slide.id, Patch.config_version_id == config.id)
        .group_by(Patch.grid_key)
        .all()
    )
    counts = {k: n for k, n in rows if k}
    keys = sorted(set(counts) | {default_key}, key=lambda k: (k != default_key, k))
    out = []
    for k in keys:
        try:
            spec = GridSpec.from_key(k)
        except ValueError:
            continue  # e.g. an image project's whole-image grid
        out.append(
            GridOut(
                key=k,
                label=spec.label,
                spec=spec.as_dict(),
                patch_count=counts.get(k, 0),
                annotated_patch_count=annotated.get(k, 0),
                active=k == slide.active_grid_key,
                is_default=k == default_key,
            )
        )
    return out


@router.put("/slides/{slide_id}/active-grid", response_model=SlideOut)
def set_active_grid(payload: SetActiveGridRequest, slide: Slide = Depends(get_slide_or_404), db: Session = Depends(get_db)):
    """Show (annotate, export) another grid this slide already has. To make a new one, generate it."""
    forbid_for_image_project(slide.project, "Switching patch grids")
    has = (
        db.query(Patch.id)
        .filter(Patch.slide_id == slide.id, Patch.config_version_id == slide.active_config_version_id, Patch.grid_key == payload.grid_key)
        .first()
    )
    if has is None:
        raise HTTPException(status_code=404, detail="This slide has no patches in that grid yet; generate it first.")
    slide.active_grid_key = payload.grid_key
    slide.status = "patches_generated" if slide.status in ("imported", "tissue_detected") else slide.status
    db.commit()
    db.refresh(slide)
    return slide
