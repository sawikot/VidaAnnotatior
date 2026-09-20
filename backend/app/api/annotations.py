from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_patch_or_404, get_slide_or_404
from app.database.session import get_db
from app.models.annotation import GeometryAnnotation
from app.models.config_version import AnnotationClass, ProjectConfigVersion
from app.models.patch import Patch
from app.models.slide import Slide
from app.schemas.annotation import (
    GeometryAnnotationCreate,
    GeometryAnnotationOut,
    GeometryAnnotationUpdate,
    ImportAnnotationsRequest,
    ImportAnnotationsResponse,
)
from app.services.geometry import validate_shape
from app.services.coordinate_transform import PatchOrigin, polygon_level0_to_patch_local, polygon_patch_local_to_level0

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
    q = db.query(GeometryAnnotation).filter(GeometryAnnotation.slide_id == slide.id)
    if slide.active_config_version_id is not None:
        q = q.filter(GeometryAnnotation.config_version_id == slide.active_config_version_id)
    return q.order_by(GeometryAnnotation.id.asc()).limit(limit).all()


def _origin_for_patch(patch: Patch) -> PatchOrigin:
    downsample = (patch.width_l0 / patch.width) if patch.width else 1.0
    return PatchOrigin(x=patch.x, y=patch.y, level=patch.level, downsample=downsample)


def _coords_close(a: list[list[float]], b: list[list[float]], tol: float = 0.5) -> bool:
    if len(a) != len(b):
        return False
    return all(abs(ax - bx) <= tol and abs(ay - by) <= tol for (ax, ay), (bx, by) in zip(a, b))


@router.post("/slides/{slide_id}/import-annotations", response_model=ImportAnnotationsResponse)
def import_annotations(
    payload: ImportAnnotationsRequest,
    slide: Slide = Depends(get_slide_or_404),
    db: Session = Depends(get_db),
):
    """Re-import annotations from a previously exported WSI JSON file (or any
    payload shaped like its `annotations[]` array).

    Annotations are matched to *existing* patches in this slide by exact
    Level-0 origin (`source_patch.x`, `source_patch.y`) -- patches must already
    be generated (via tissue detection + "Generate Coords") with a config that
    produces the same grid before importing. This is deliberate: fabricating a
    patch from unverified import data (unknown tissue_fraction, no re-run
    tissue check) would silently corrupt the coordinate-generation provenance
    the rest of the app relies on.

    Diagnostic classes are matched by exact (case-insensitive) name against
    the target config version's classes; an unrecognized label is skipped
    rather than inventing a new class. Already-present annotations (same
    patch, type, and near-identical Level-0 coordinates) are skipped so
    re-running an import is safe.
    """
    config_id = payload.config_version_id or slide.active_config_version_id
    config = db.get(ProjectConfigVersion, config_id) if config_id else None
    if config is None:
        raise HTTPException(status_code=422, detail="Slide has no active config version to import against")

    classes_by_name = {c.name.strip().lower(): c for c in config.annotation_classes}

    patches = db.query(Patch).filter(Patch.slide_id == slide.id, Patch.config_version_id == config.id).all()
    patch_by_origin = {(p.x, p.y): p for p in patches}

    existing_by_patch: dict[int, list[GeometryAnnotation]] = defaultdict(list)
    for ann in db.query(GeometryAnnotation).filter(GeometryAnnotation.slide_id == slide.id):
        existing_by_patch[ann.patch_id].append(ann)

    imported = skipped_no_patch = skipped_no_class = skipped_duplicate = skipped_invalid = 0

    for entry in payload.annotations:
        try:
            validate_shape(entry.type, entry.coordinates)
        except ValueError:
            skipped_invalid += 1  # a malformed entry must not sink the rest of the file
            continue

        x = entry.source_patch.get("x")
        y = entry.source_patch.get("y")
        patch = patch_by_origin.get((x, y)) if x is not None and y is not None else None
        if patch is None:
            skipped_no_patch += 1
            continue

        class_obj = None
        if entry.label:
            class_obj = classes_by_name.get(entry.label.strip().lower())
            if class_obj is None:
                skipped_no_class += 1
                continue

        if any(
            existing.type == entry.type and _coords_close(existing.coordinates_level0, entry.coordinates)
            for existing in existing_by_patch.get(patch.id, [])
        ):
            skipped_duplicate += 1
            continue

        origin = _origin_for_patch(patch)
        local_coords = polygon_level0_to_patch_local(origin, entry.coordinates)

        annotation = GeometryAnnotation(
            patch_id=patch.id,
            slide_id=slide.id,
            config_version_id=config.id,
            class_id=class_obj.id if class_obj else None,
            type=entry.type,
            coordinates_patch_local=local_coords,
            coordinates_level0=entry.coordinates,
            created_by=payload.created_by or "Imported",
            unsure=entry.unsure,
            flagged=entry.flagged,
        )
        db.add(annotation)
        existing_by_patch[patch.id].append(annotation)
        if patch.status == "unannotated":
            patch.status = "annotated"
        imported += 1

    db.commit()

    return ImportAnnotationsResponse(
        total=len(payload.annotations),
        imported=imported,
        skipped_no_matching_patch=skipped_no_patch,
        skipped_unknown_class=skipped_no_class,
        skipped_duplicate=skipped_duplicate,
        skipped_invalid_shape=skipped_invalid,
    )


@router.get("/patches/{patch_id}/annotations", response_model=list[GeometryAnnotationOut])
def list_patch_annotations(patch: Patch = Depends(get_patch_or_404), db: Session = Depends(get_db)):
    return (
        db.query(GeometryAnnotation)
        .filter(GeometryAnnotation.patch_id == patch.id)
        .order_by(GeometryAnnotation.created_at.asc())
        .all()
    )


def _require_class_of_config(db: Session, class_id: int | None, config_version_id: int) -> None:
    """A shape may only carry a class of the config version it was drawn under: a class from
    another version (or another project) would export under a name the file doesn't declare."""
    if class_id is None:
        return
    owner = db.query(AnnotationClass.config_version_id).filter(AnnotationClass.id == class_id).scalar()
    if owner != config_version_id:
        raise HTTPException(status_code=422, detail=f"Class {class_id} does not belong to this configuration version")


@router.post("/patches/{patch_id}/annotations", response_model=GeometryAnnotationOut, status_code=201)
def create_patch_annotation(
    payload: GeometryAnnotationCreate,
    patch: Patch = Depends(get_patch_or_404),
    db: Session = Depends(get_db),
):
    if len(payload.coordinates_patch_local) < 1:
        raise HTTPException(status_code=422, detail="coordinates_patch_local must not be empty")

    _require_class_of_config(db, payload.class_id, patch.config_version_id)
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
    if changes.get("class_id") is not None:
        _require_class_of_config(db, changes["class_id"], annotation.config_version_id)
    if "coordinates_patch_local" in changes and changes["coordinates_patch_local"] is not None:
        try:
            validate_shape(annotation.type, changes["coordinates_patch_local"])  # the shape keeps its type
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
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
