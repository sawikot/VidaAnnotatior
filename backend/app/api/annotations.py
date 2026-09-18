from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_patch_or_404, get_slide_or_404
from app.database.session import get_db
from app.models.annotation import GeometryAnnotation
from app.models.patch import Patch
from app.models.slide import Slide
from app.schemas.annotation import GeometryAnnotationCreate, GeometryAnnotationOut, GeometryAnnotationUpdate
from app.services.coordinate_transform import PatchOrigin, polygon_patch_local_to_level0

router = APIRouter(tags=["annotations"])


@router.get("/slides/{slide_id}/annotations", response_model=list[GeometryAnnotationOut])
def list_slide_annotations(
    slide: Slide = Depends(get_slide_or_404),
    db: Session = Depends(get_db),
    limit: int = Query(5000, ge=1, le=20000),
):
    """All geometry annotations for a slide, in Level-0 space -- used by the
    Full WSI Annotation Overview screen. Not bbox-filtered server-side (the
    stitched overlay needs the whole slide's annotations to render correctly
    even when panned out), but capped by `limit` as a safety valve."""
    return (
        db.query(GeometryAnnotation)
        .filter(GeometryAnnotation.slide_id == slide.id)
        .order_by(GeometryAnnotation.id.asc())
        .limit(limit)
        .all()
    )


def _origin_for_patch(patch: Patch) -> PatchOrigin:
    downsample = (patch.width_l0 / patch.width) if patch.width else 1.0
    return PatchOrigin(x=patch.x, y=patch.y, level=patch.level, downsample=downsample)


@router.get("/patches/{patch_id}/annotations", response_model=list[GeometryAnnotationOut])
def list_patch_annotations(patch: Patch = Depends(get_patch_or_404), db: Session = Depends(get_db)):
    return (
        db.query(GeometryAnnotation)
        .filter(GeometryAnnotation.patch_id == patch.id)
        .order_by(GeometryAnnotation.created_at.asc())
        .all()
    )


@router.post("/patches/{patch_id}/annotations", response_model=GeometryAnnotationOut, status_code=201)
def create_patch_annotation(
    payload: GeometryAnnotationCreate,
    patch: Patch = Depends(get_patch_or_404),
    db: Session = Depends(get_db),
):
    if len(payload.coordinates_patch_local) < 1:
        raise HTTPException(status_code=422, detail="coordinates_patch_local must not be empty")

    origin = _origin_for_patch(patch)
    coords_l0 = polygon_patch_local_to_level0(origin, payload.coordinates_patch_local)

    annotation = GeometryAnnotation(
        patch_id=patch.id,
        slide_id=patch.slide_id,
        config_version_id=patch.config_version_id,
        class_id=payload.class_id,
        type=payload.type,
        coordinates_patch_local=payload.coordinates_patch_local,
        coordinates_level0=coords_l0,
        created_by=payload.created_by,
        notes=payload.notes,
        unsure=payload.unsure,
        flagged=payload.flagged,
        excluded=payload.excluded,
    )
    db.add(annotation)

    if patch.status == "unannotated":
        patch.status = "annotated"

    db.commit()
    db.refresh(annotation)
    return annotation


@router.put("/annotations/{annotation_id}", response_model=GeometryAnnotationOut)
def update_annotation(
    annotation_id: int,
    payload: GeometryAnnotationUpdate,
    db: Session = Depends(get_db),
):
    annotation = db.get(GeometryAnnotation, annotation_id)
    if annotation is None:
        raise HTTPException(status_code=404, detail=f"Annotation {annotation_id} not found")

    changes = payload.model_dump(exclude_unset=True)
    if "coordinates_patch_local" in changes and changes["coordinates_patch_local"] is not None:
        patch = db.get(Patch, annotation.patch_id)
        origin = _origin_for_patch(patch)
        annotation.coordinates_patch_local = changes["coordinates_patch_local"]
        annotation.coordinates_level0 = polygon_patch_local_to_level0(
            origin, changes["coordinates_patch_local"]
        )
        changes.pop("coordinates_patch_local")

    for field, value in changes.items():
        setattr(annotation, field, value)

    db.commit()
    db.refresh(annotation)
    return annotation


@router.delete("/annotations/{annotation_id}", status_code=204, response_model=None)
def delete_annotation(annotation_id: int, db: Session = Depends(get_db)):
    annotation = db.get(GeometryAnnotation, annotation_id)
    if annotation is None:
        raise HTTPException(status_code=404, detail=f"Annotation {annotation_id} not found")
    db.delete(annotation)
    db.commit()
