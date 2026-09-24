from __future__ import annotations

import io
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import forbid_for_image_project, get_project_or_404, get_slide_or_404
from app.core.config import get_settings
from app.database.session import get_db
from app.models.annotation import GeometryAnnotation
from app.models.config_version import ProjectConfigVersion
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide
from app.schemas.slide import (
    DetectTissueRequest,
    DetectTissueResponse,
    GeneratePatchesRequest,
    GeneratePatchesResponse,
    GridOut,
    AddProjectGridOut,
    AddProjectGridRequest,
    GridRemovalOut,
    ProjectGridOut,
    SetActiveGridRequest,
    SlideOut,
    TissueRegionOut,
    TissueRegionsIn,
    TissueRegionsOut,
)
from app.services import reader_cache
from app.services.geometry import validate_shape
from app.services.patch_generator import generate_patch_grid
from app.services.config_versioning import compute_config_hash
from app.services.patch_grid import GridSpec, grid_key_of, remove_grid
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


class GridCutError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def cut_grid(db: Session, slide: Slide, config: ProjectConfigVersion, spec: GridSpec, activate: bool = True) -> GeneratePatchesResponse:
    """Cut `slide` into patches of `spec` (from its tissue mask), or update that grid if it exists: a patch
    at the same place keeps its id, status and annotations, and an annotated patch that no longer meets
    the threshold is kept. With `activate`, the slide switches to this grid (a slide with no grid always
    does). Not committed; raises GridCutError when nothing can be cut."""
    settings = get_settings()
    try:
        reader = reader_cache.get_reader_for_slide(slide)
        meta = reader.get_metadata()
    except FileNotFoundError as exc:
        raise GridCutError(404, str(exc)) from exc

    tissue_mask = None
    if slide.tissue_mask_path:
        mask_path = settings.wsi_storage_dir / slide.tissue_mask_path
        if mask_path.exists():
            tissue_mask = load_mask(mask_path)

    candidates = generate_patch_grid(meta, spec, tissue_mask, slide.tissue_mask_downsample)
    if not candidates:
        raise GridCutError(
            422,
            "Zero patches were generated. Check patch/stride size against slide dimensions.",
        )

    kept = [c for c in candidates if c.kept]
    if not kept:
        raise GridCutError(
            422,
            (
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
    if activate or slide.active_grid_key is None:
        slide.active_grid_key = key
    slide.status = "patches_generated" if slide.status in ("imported", "tissue_detected") or activate else slide.status

    return GeneratePatchesResponse(
        total_candidates=len(candidates),
        kept=len(kept),
        excluded=len(candidates) - len(kept),
        grid_key=key,
        grid_label=spec.label,
        preserved=preserved,
    )


@router.post("/slides/{slide_id}/generate-patches", response_model=GeneratePatchesResponse)
def generate_patches(
    payload: GeneratePatchesRequest,
    slide: Slide = Depends(get_slide_or_404),
    db: Session = Depends(get_db),
):
    forbid_for_image_project(slide.project, "Patch generation")
    config = db.get(ProjectConfigVersion, payload.config_version_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Configuration not found")
    if not slide.width_l0:
        raise HTTPException(status_code=422, detail="Slide metadata is not available; re-import the slide.")

    spec = GridSpec(**payload.grid.model_dump()) if payload.grid else GridSpec.from_config(config)
    try:
        result = cut_grid(db, slide, config, spec)
    except GridCutError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    db.commit()
    return result


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


@router.delete("/slides/{slide_id}/grids/{grid_key}", response_model=GridRemovalOut)
def remove_slide_grid(grid_key: str, slide: Slide = Depends(get_slide_or_404), db: Session = Depends(get_db)):
    """Remove one patch size from this slide. Its annotations are kept as whole-slide annotations."""
    forbid_for_image_project(slide.project, "Removing a patch grid")
    counts = remove_grid(db, [slide], grid_key)
    if counts["patches"] == 0:
        raise HTTPException(status_code=404, detail="This slide has no patches in that grid.")
    db.commit()
    return counts


@router.get("/projects/{project_id}/grids", response_model=list[ProjectGridOut])
def list_project_grids(project: Project = Depends(get_project_or_404), db: Session = Depends(get_db)):
    """Every patch size used in the project (and the project's own, even if not generated yet)."""
    config = db.get(ProjectConfigVersion, project.active_config_version_id) if project.active_config_version_id else None
    if config is None or project.project_type == "image":
        return []
    slide_ids = [sid for (sid,) in db.query(Slide.id).filter(Slide.project_id == project.id)]
    per_grid = {}
    if slide_ids:
        for key, slides_n, patches_n in (
            db.query(Patch.grid_key, func.count(func.distinct(Patch.slide_id)), func.count(Patch.id))
            .filter(Patch.slide_id.in_(slide_ids))
            .group_by(Patch.grid_key)
        ):
            per_grid[key] = {"slide_count": slides_n, "patch_count": patches_n, "annotated_patch_count": 0, "annotation_count": 0}
        for key, annotated_n, anns_n in (
            db.query(Patch.grid_key, func.count(func.distinct(Patch.id)), func.count(GeometryAnnotation.id))
            .join(GeometryAnnotation, GeometryAnnotation.patch_id == Patch.id)
            .filter(Patch.slide_id.in_(slide_ids))
            .group_by(Patch.grid_key)
        ):
            if key in per_grid:
                per_grid[key].update(annotated_patch_count=annotated_n, annotation_count=anns_n)
    default_key = grid_key_of(config)
    out = []
    for key in sorted(set(k for k in per_grid if k) | {default_key}, key=lambda k: (k != default_key, k)):
        try:
            spec = GridSpec.from_key(key)
        except ValueError:
            continue
        counts = per_grid.get(key, {"slide_count": 0, "patch_count": 0, "annotated_patch_count": 0, "annotation_count": 0})
        out.append(ProjectGridOut(key=key, label=spec.label, spec=spec.as_dict(), is_default=key == default_key, **counts))
    return out


@router.delete("/projects/{project_id}/grids/{grid_key}", response_model=GridRemovalOut)
def remove_project_grid(grid_key: str, project: Project = Depends(get_project_or_404), db: Session = Depends(get_db)):
    """Remove one patch size from every slide of the project. Annotations are kept as whole-slide ones."""
    forbid_for_image_project(project, "Removing a patch grid")
    counts = remove_grid(db, db.query(Slide).filter(Slide.project_id == project.id).all(), grid_key)
    if counts["patches"] == 0:
        raise HTTPException(status_code=404, detail="No slide of this project has patches in that grid.")
    db.commit()
    return counts


@router.post("/projects/{project_id}/grids", response_model=AddProjectGridOut)
def add_project_grid(payload: AddProjectGridRequest, project: Project = Depends(get_project_or_404), db: Session = Depends(get_db)):
    """Cut every slide whose tissue has been found (detected or drawn) into another patch size. Slides keep
    showing the size they are on (one with none yet starts on the new one); the rest are listed as
    skipped, with why. Optionally makes it the project's grid, used by Generate Coords from then on."""
    forbid_for_image_project(project, "Patch grids")
    config = db.get(ProjectConfigVersion, project.active_config_version_id) if project.active_config_version_id else None
    if config is None:
        raise HTTPException(status_code=422, detail="This project has no configuration")
    spec = GridSpec(**payload.grid.model_dump())

    cut, patches, skipped = 0, 0, []
    for slide in db.query(Slide).filter(Slide.project_id == project.id).order_by(Slide.id):
        reason = None
        if slide.status == "error" or not slide.width_l0:
            reason = "the slide could not be read"
        elif not slide.tissue_mask_path:
            reason = "no tissue found yet (detect or draw it on Slide Processing)"
        if reason is None:
            try:
                result = cut_grid(db, slide, config, spec, activate=False)
                cut += 1
                patches += result.kept
                continue
            except GridCutError as exc:
                reason = exc.detail
        skipped.append({"slide_id": slide.id, "slide": slide.filename, "reason": reason})

    if payload.make_default:
        for field in ("patch_width", "patch_height", "stride_x", "stride_y", "target_magnification", "min_tissue_fraction", "include_edge_patches", "allow_partial_patches"):
            setattr(config, field, getattr(spec, field))
        config.config_hash = compute_config_hash(config)
    db.commit()
    return AddProjectGridOut(grid_key=spec.key, grid_label=spec.label, slides=cut, patches=patches, skipped=skipped)
