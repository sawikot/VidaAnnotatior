from __future__ import annotations

from collections import defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_patch_or_404, get_slide_or_404
from app.database.session import get_db
from app.models.annotation import GeometryAnnotation
from app.models.config_version import AnnotationClass, ProjectConfigVersion
from app.models.patch import Patch
from app.models.slide import Slide
from app.schemas.annotation import (
    SlideAnnotationCreate,
    GeometryAnnotationCreate,
    GeometryAnnotationOut,
    GeometryAnnotationUpdate,
    ImportAnnotationsRequest,
    ImportAnnotationsResponse,
    OverlappingAnnotationOut,
    OwnerPatchOut,
)
from app.services.patch_labels import on_fill_class_changed, on_fill_deleted
from app.services.geometry import circle_center_radius, polygon_bounds, validate_shape
from app.services.coordinate_transform import PatchOrigin, polygon_level0_to_patch_local, polygon_patch_local_to_level0

router = APIRouter(tags=["annotations"])


@router.get("/slides/{slide_id}/annotations", response_model=list[GeometryAnnotationOut])
def list_slide_annotations(
    slide: Slide = Depends(get_slide_or_404),
    db: Session = Depends(get_db),
    limit: int = Query(5000, ge=1, le=20000),
    scope: Literal["all", "patch", "slide"] = Query("all", description="patch: drawn in a patch; slide: drawn on the whole slide"),
):
    """All geometry annotations for a slide, in Level-0 space -- used by the
    Full WSI Annotation Overview screen. Not bbox-filtered server-side (the
    stitched overlay needs the whole slide's annotations to render correctly
    even when panned out), but capped by `limit` as a safety valve."""
    q = db.query(GeometryAnnotation).options(joinedload(GeometryAnnotation.patch)).filter(GeometryAnnotation.slide_id == slide.id)
    if slide.active_config_version_id is not None:
        q = q.filter(GeometryAnnotation.config_version_id == slide.active_config_version_id)
    if scope == "patch":
        q = q.filter(GeometryAnnotation.patch_id.isnot(None))
    elif scope == "slide":
        q = q.filter(GeometryAnnotation.patch_id.is_(None))
    return q.order_by(GeometryAnnotation.id.asc()).limit(limit).all()


def _require_inside_slide(slide: Slide, coords: list[list[float]]) -> None:
    """Slide-level coordinates are Level-0 pixels of *this* slide; a point outside it is a client bug."""
    if not slide.width_l0 or not slide.height_l0:
        raise HTTPException(status_code=422, detail="Slide dimensions are not known; re-import the slide")
    tolerance = 0.5
    for x, y in coords:
        if not (-tolerance <= x <= slide.width_l0 + tolerance and -tolerance <= y <= slide.height_l0 + tolerance):
            raise HTTPException(
                status_code=422,
                detail=f"Point ({x:g}, {y:g}) is outside the slide ({slide.width_l0} x {slide.height_l0} px)",
            )


@router.post("/slides/{slide_id}/annotations", response_model=GeometryAnnotationOut, status_code=201)
def create_slide_annotation(
    payload: SlideAnnotationCreate,
    slide: Slide = Depends(get_slide_or_404),
    db: Session = Depends(get_db),
):
    """An annotation drawn directly on the whole slide rather than in one patch.

    Its Level-0 coordinates are the only ones stored ("store globally"); it belongs to no patch, so
    it may cross many of them or lie where none was generated. Patch views show it by projecting
    it through each patch's own origin and downsample.
    """
    if slide.project.project_type == "image":
        raise HTTPException(status_code=409, detail="An image is annotated as a whole; use the image's patch.")
    if slide.active_config_version_id is None:
        raise HTTPException(status_code=422, detail="Slide has no active configuration version to annotate under")
    _require_inside_slide(slide, payload.coordinates_level0)
    _require_class_of_config(db, payload.class_id, slide.active_config_version_id)

    annotation = GeometryAnnotation(
        patch_id=None,
        slide_id=slide.id,
        config_version_id=slide.active_config_version_id,
        class_id=payload.class_id,
        type=payload.type,
        coordinates_patch_local=[],
        coordinates_level0=payload.coordinates_level0,
        created_by=payload.created_by,
        notes=payload.notes,
        unsure=payload.unsure,
        flagged=payload.flagged,
    )
    db.add(annotation)
    db.commit()
    db.refresh(annotation)
    return annotation


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
        raise HTTPException(status_code=422, detail="This slide has no configuration to import against")

    classes_by_name = {c.name.strip().lower(): c for c in config.annotation_classes}

    patch_query = db.query(Patch).filter(Patch.slide_id == slide.id, Patch.config_version_id == config.id)
    if config.id == slide.active_config_version_id and slide.active_grid_key is not None:
        patch_query = patch_query.filter(Patch.grid_key == slide.active_grid_key)  # the grid on screen
    patches = patch_query.all()
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

        if entry.source_patch is None:
            # A slide-level annotation: no patch to match, its Level-0 coordinates are used as they are.
            try:
                _require_inside_slide(slide, entry.coordinates)
            except HTTPException:
                skipped_invalid += 1
                continue
            class_obj = classes_by_name.get(entry.label.strip().lower()) if entry.label else None
            if entry.label and class_obj is None:
                skipped_no_class += 1
                continue
            if any(
                existing.type == entry.type and _coords_close(existing.coordinates_level0, entry.coordinates)
                for existing in existing_by_patch.get(None, [])
            ):
                skipped_duplicate += 1
                continue
            annotation = GeometryAnnotation(
                patch_id=None,
                slide_id=slide.id,
                config_version_id=config.id,
                class_id=class_obj.id if class_obj else None,
                type=entry.type,
                coordinates_patch_local=[],
                coordinates_level0=entry.coordinates,
                created_by=payload.created_by or "Imported",
                unsure=entry.unsure,
                flagged=entry.flagged,
            )
            db.add(annotation)
            existing_by_patch[None].append(annotation)
            imported += 1
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


def _shape_bounds(shape_type: str, coords: list[list[float]]) -> tuple[float, float, float, float]:
    if shape_type == "circle":
        (cx, cy), r = circle_center_radius(coords)
        return cx - r, cy - r, cx + r, cy + r
    return polygon_bounds(coords)


@router.get("/patches/{patch_id}/overlapping-annotations", response_model=list[OverlappingAnnotationOut])
def list_overlapping_annotations(patch: Patch = Depends(get_patch_or_404), db: Session = Depends(get_db)):
    """Annotations drawn in *other* patches of the same grid that reach into this patch.

    With a stride smaller than the patch size, neighbouring patches share area, so an object drawn in
    one is also (partly) in the next. Each stays owned by the patch it was drawn in; this lists them
    with that patch, so a client can show them here and send edits in the owner's coordinates."""
    x0, y0 = patch.x, patch.y
    x1, y1 = patch.x + patch.width_l0, patch.y + patch.height_l0
    owners = (
        db.query(Patch)
        .filter(
            Patch.slide_id == patch.slide_id,
            Patch.config_version_id == patch.config_version_id,
            Patch.id != patch.id,
            Patch.x < x1,
            Patch.x + Patch.width_l0 > x0,
            Patch.y < y1,
            Patch.y + Patch.height_l0 > y0,
        )
        .all()
    )
    if not owners:
        return []
    by_id = {p.id: p for p in owners}
    annotations = (
        db.query(GeometryAnnotation)
        .filter(GeometryAnnotation.patch_id.in_(list(by_id)))
        .order_by(GeometryAnnotation.created_at.asc())
        .all()
    )
    out = []
    for a in annotations:
        if not a.coordinates_level0:
            continue
        bx0, by0, bx1, by1 = _shape_bounds(a.type, a.coordinates_level0)
        if bx0 < x1 and bx1 > x0 and by0 < y1 and by1 > y0:  # reaches into this patch
            out.append(
                OverlappingAnnotationOut(
                    annotation=GeometryAnnotationOut.model_validate(a), owner=OwnerPatchOut.model_validate(by_id[a.patch_id])
                )
            )
    return out


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
    before = (annotation.coordinates_patch_local, annotation.coordinates_level0)

    if annotation.patch_id is None:
        # Slide-level: only Level-0 coordinates exist.
        if changes.get("coordinates_patch_local") is not None:
            raise HTTPException(status_code=422, detail="A slide-level annotation has no patch-local coordinates; send coordinates_level0")
        if changes.get("coordinates_level0") is not None:
            try:
                validate_shape(annotation.type, changes["coordinates_level0"])
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            _require_inside_slide(db.get(Slide, annotation.slide_id), changes["coordinates_level0"])
            annotation.coordinates_level0 = changes["coordinates_level0"]
        changes.pop("coordinates_level0", None)
        changes.pop("coordinates_patch_local", None)
    elif changes.get("coordinates_level0") is not None and changes.get("coordinates_patch_local") is None:
        # Edited on the whole slide: stored, as always, in the pixels of the patch it belongs to.
        patch = db.get(Patch, annotation.patch_id)
        x0, y0, x1, y1 = patch.x, patch.y, patch.x + patch.width_l0, patch.y + patch.height_l0
        tol = 0.5
        for x, y in changes["coordinates_level0"]:
            if not (x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol):
                raise HTTPException(
                    status_code=422,
                    detail=f"Point ({x:g}, {y:g}) is outside the patch this annotation belongs to ({x0}, {y0} - {x1}, {y1})",
                )
        changes["coordinates_patch_local"] = polygon_level0_to_patch_local(_origin_for_patch(patch), changes["coordinates_level0"])
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

    changes.pop("coordinates_level0", None)
    if annotation.whole_patch and (annotation.coordinates_patch_local, annotation.coordinates_level0) != before:
        annotation.whole_patch = False  # reshaped: an ordinary shape now (the label stays)
    for field, value in changes.items():
        setattr(annotation, field, value)
    if annotation.whole_patch and "class_id" in changes:
        on_fill_class_changed(db, annotation)  # a patch label's fill got another class: so does the label

    db.commit()
    db.refresh(annotation)
    return annotation


@router.delete("/annotations/{annotation_id}", status_code=204, response_model=None)
def delete_annotation(annotation_id: int, db: Session = Depends(get_db)):
    annotation = db.get(GeometryAnnotation, annotation_id)
    if annotation is None:
        raise HTTPException(status_code=404, detail=f"Annotation {annotation_id} not found")
    on_fill_deleted(db, annotation)  # a patch label's fill: the patch loses the label
    db.delete(annotation)
    db.commit()
