from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
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
    ImportClassOut,
    ImportLabelOut,
    ParsedAnnotationsOut,
    OverlappingAnnotationOut,
    OwnerPatchOut,
)
from app.api.access import current_user
from app.models.user import User
from app.services.annotation_import import FORMAT_NAMES, ImportFormatError, parse_annotation_file, shape_bounds
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
    user: User = Depends(current_user),
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
        created_by=user.name,  # the signed-in person, whatever the client says
        created_by_id=user.id,
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


MAX_IMPORT_FILE_BYTES = 200 * 1024 * 1024


def _import_config(db: Session, slide: Slide, config_version_id: int | None = None) -> ProjectConfigVersion:
    config_id = config_version_id or slide.active_config_version_id
    config = db.get(ProjectConfigVersion, config_id) if config_id else None
    if config is None:
        raise HTTPException(status_code=422, detail="This slide has no configuration to import against")
    return config


def _outside_slide(slide: Slide, coords: list[list[float]]) -> bool:
    if not slide.width_l0 or not slide.height_l0:
        return False
    tolerance = 0.5
    min_x, min_y, max_x, max_y = polygon_bounds(coords)  # for a circle: its centre and edge point
    return min_x < -tolerance or min_y < -tolerance or max_x > slide.width_l0 + tolerance or max_y > slide.height_l0 + tolerance


@router.post("/slides/{slide_id}/import-annotations/parse", response_model=ParsedAnnotationsOut)
async def parse_annotation_import(
    file: UploadFile = File(...),
    scale: float | None = Form(None),  # none: worked out from the file
    slide: Slide = Depends(get_slide_or_404),
    db: Session = Depends(get_db),
):
    """Reads an annotation file of any supported format (WSI JSON, GeoJSON, COCO, ASAP XML, Aperio
    XML, CSV) and returns its shapes as import entries in Level-0 pixels, with the labels it uses,
    so the person can map them to classes before `/import-annotations` saves anything."""
    raw = await file.read(MAX_IMPORT_FILE_BYTES + 1)
    if len(raw) > MAX_IMPORT_FILE_BYTES:
        raise HTTPException(status_code=413, detail="The file is larger than 200 MB")
    config = _import_config(db, slide)
    classes = sorted(config.annotation_classes, key=lambda c: (c.order_index, c.id))
    try:
        parsed = parse_annotation_file(
            file.filename or "",
            raw,
            scale=scale,
            mpp=(slide.mpp_x, slide.mpp_y),
            slide_size=(slide.width_l0, slide.height_l0),
            class_codes=[(c.code, c.name) for c in classes if c.code is not None],
        )
    except ImportFormatError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None

    class_by_name = {c.name.strip().lower(): c.id for c in classes}
    label_counts = Counter(entry["label"] or "" for entry in parsed.entries)
    bounds = None
    outside = 0
    for entry in parsed.entries:
        b = shape_bounds(entry["type"], entry["coordinates"])
        bounds = b if bounds is None else (min(bounds[0], b[0]), min(bounds[1], b[1]), max(bounds[2], b[2]), max(bounds[3], b[3]))
        outside += _outside_slide(slide, entry["coordinates"])

    return ParsedAnnotationsOut(
        format=parsed.format,
        format_name=FORMAT_NAMES[parsed.format],
        annotations=parsed.entries,
        labels=[
            ImportLabelOut(label=label, count=count, class_id=class_by_name.get(label.lower()) if label else None)
            for label, count in sorted(label_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        classes=[ImportClassOut(id=c.id, name=c.name, color_hex=c.color_hex, code=c.code) for c in classes],
        shape_counts=dict(Counter(entry["type"] for entry in parsed.entries)),
        linked_to_patches=sum(1 for entry in parsed.entries if entry["source_patch"]),
        outside_slide=outside,
        bounds=[round(v, 2) for v in bounds] if bounds else None,
        slide_size=[slide.width_l0, slide.height_l0],
        image_project=slide.project.project_type == "image",
        scale=parsed.scale,
        scale_auto=parsed.auto_scale,
        scale_note=parsed.scale_note,
        unreadable=dict(parsed.unreadable),
        warnings=parsed.warnings,
    )


class _PatchFinder:
    """Finds the patch that wholly contains a shape, quickly: every patch is filed under each cell
    of a coarse grid it overlaps, and a patch containing the shape contains its top-left corner."""

    def __init__(self, patches: list[Patch]):
        self.cell_w = max((p.width_l0 for p in patches), default=1) or 1
        self.cell_h = max((p.height_l0 for p in patches), default=1) or 1
        self.cells: dict[tuple[int, int], list[Patch]] = defaultdict(list)
        for p in patches:
            for cx in range(p.x // self.cell_w, (p.x + p.width_l0 - 1) // self.cell_w + 1):
                for cy in range(p.y // self.cell_h, (p.y + p.height_l0 - 1) // self.cell_h + 1):
                    self.cells[(cx, cy)].append(p)

    def containing(self, bounds: tuple[float, float, float, float]) -> Patch | None:
        min_x, min_y, max_x, max_y = bounds
        mid_x, mid_y = (min_x + max_x) / 2, (min_y + max_y) / 2
        inside = [
            p
            for p in self.cells.get((int(min_x // self.cell_w), int(min_y // self.cell_h)), [])
            if p.x <= min_x and p.y <= min_y and max_x <= p.x + p.width_l0 and max_y <= p.y + p.height_l0
        ]
        # Patches overlap when the stride is smaller than the patch: take the one the shape is most
        # central in, preferring patches still in use.
        return min(
            inside,
            key=lambda p: (p.excluded, math.hypot(p.x + p.width_l0 / 2 - mid_x, p.y + p.height_l0 / 2 - mid_y)),
            default=None,
        )


class _Duplicates:
    """Existing shapes by patch, type and rounded first point, so a re-import is checked in constant time."""

    def __init__(self):
        self.index: dict[tuple, list[list[list[float]]]] = defaultdict(list)

    @staticmethod
    def _key(patch_id: int | None, shape_type: str, coords: list[list[float]], dx: int = 0, dy: int = 0) -> tuple:
        return (patch_id, shape_type, len(coords), round(coords[0][0]) + dx, round(coords[0][1]) + dy)

    def add(self, patch_id: int | None, shape_type: str, coords: list[list[float]]) -> None:
        if coords:
            self.index[self._key(patch_id, shape_type, coords)].append(coords)

    def has(self, patch_id: int | None, shape_type: str, coords: list[list[float]]) -> bool:
        return any(
            _coords_close(existing, coords)
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for existing in self.index.get(self._key(patch_id, shape_type, coords, dx, dy), [])
        )


@router.post("/slides/{slide_id}/import-annotations", response_model=ImportAnnotationsResponse)
def import_annotations(
    payload: ImportAnnotationsRequest,
    slide: Slide = Depends(get_slide_or_404),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Imports annotations in Level-0 coordinates: a previously exported WSI JSON file (or any
    payload shaped like its `annotations[]` array), or what `/import-annotations/parse` made of
    another format.

    An entry naming a `source_patch` is matched to an *existing* patch of this slide by exact
    Level-0 origin -- patches must already be generated with a config that produces the same grid.
    This is deliberate: fabricating a patch from unverified import data (unknown tissue_fraction,
    no re-run tissue check) would silently corrupt the coordinate-generation provenance the rest of
    the app relies on. An entry without one becomes a slide-level annotation, or -- with
    `assign_to_patches`, and always in an image project -- goes into the patch that contains it.

    Labels are mapped by `label_map` where it names them, else matched by exact (case-insensitive)
    class name; an unrecognized label is skipped rather than inventing a new class.
    Already-present annotations (same patch, type, and near-identical Level-0 coordinates) are
    skipped so re-running an import is safe.
    """
    config = _import_config(db, slide, payload.config_version_id)
    classes_by_id = {c.id: c for c in config.annotation_classes}
    classes_by_name = {c.name.strip().lower(): c for c in config.annotation_classes}
    label_map = {label.strip(): target for label, target in (payload.label_map or {}).items()}
    for target in label_map.values():
        if isinstance(target, int) and target not in classes_by_id:
            raise HTTPException(status_code=422, detail=f"Class {target} is not a class of this slide's configuration")
    image_project = slide.project.project_type == "image"

    patch_query = db.query(Patch).filter(Patch.slide_id == slide.id, Patch.config_version_id == config.id)
    if config.id == slide.active_config_version_id and slide.active_grid_key is not None:
        patch_query = patch_query.filter(Patch.grid_key == slide.active_grid_key)  # the grid on screen
    patches = patch_query.all()
    patch_by_origin = {(p.x, p.y): p for p in patches}
    finder = _PatchFinder(patches) if payload.assign_to_patches or image_project else None

    duplicates = _Duplicates()
    for ann in db.query(GeometryAnnotation).filter(GeometryAnnotation.slide_id == slide.id):
        duplicates.add(ann.patch_id, ann.type, ann.coordinates_level0)

    counts: Counter = Counter()
    created_by = payload.created_by or f"Imported by {user.name}"

    for entry in payload.annotations:
        try:
            validate_shape(entry.type, entry.coordinates)
        except ValueError:
            counts["invalid"] += 1  # a malformed entry must not sink the rest of the file
            continue

        label = (entry.label or "").strip()
        target = label_map.get(label)
        if target == "skip":
            counts["by_choice"] += 1
            continue
        if isinstance(target, int):
            class_obj = classes_by_id[target]
        elif target == "unlabeled" or not label:
            class_obj = None
        else:
            class_obj = classes_by_name.get(label.lower())
            if class_obj is None:
                counts["no_class"] += 1
                continue

        if entry.source_patch is not None:
            x, y = entry.source_patch.get("x"), entry.source_patch.get("y")
            patch = patch_by_origin.get((x, y)) if x is not None and y is not None else None
            if patch is None:
                counts["no_patch"] += 1
                continue
        else:
            if _outside_slide(slide, entry.coordinates):
                counts["outside"] += 1
                continue
            patch = finder.containing(shape_bounds(entry.type, entry.coordinates)) if finder else None
            if patch is None and image_project:
                counts["no_patch"] += 1  # an image is annotated only through its patch
                continue

        patch_id = patch.id if patch is not None else None
        if duplicates.has(patch_id, entry.type, entry.coordinates):
            counts["duplicate"] += 1
            continue

        annotation = GeometryAnnotation(
            patch_id=patch_id,
            slide_id=slide.id,
            config_version_id=config.id,
            class_id=class_obj.id if class_obj else None,
            type=entry.type,
            coordinates_patch_local=polygon_level0_to_patch_local(_origin_for_patch(patch), entry.coordinates) if patch else [],
            coordinates_level0=entry.coordinates,
            created_by=created_by,
            created_by_id=user.id,
            unsure=entry.unsure,
            flagged=entry.flagged,
        )
        db.add(annotation)
        duplicates.add(patch_id, entry.type, entry.coordinates)
        if patch is not None:
            if patch.status == "unannotated":
                patch.status = "annotated"
            counts["to_patches"] += 1
        else:
            counts["on_slide"] += 1

    db.commit()

    return ImportAnnotationsResponse(
        total=len(payload.annotations),
        imported=counts["to_patches"] + counts["on_slide"],
        imported_to_patches=counts["to_patches"],
        imported_on_slide=counts["on_slide"],
        skipped_no_matching_patch=counts["no_patch"],
        skipped_unknown_class=counts["no_class"],
        skipped_duplicate=counts["duplicate"],
        skipped_invalid_shape=counts["invalid"],
        skipped_outside_slide=counts["outside"],
        skipped_by_choice=counts["by_choice"],
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
    user: User = Depends(current_user),
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
        created_by=user.name,  # the signed-in person, whatever the client says
        created_by_id=user.id,
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
    shape_type = changes.pop("type", None) or annotation.type

    if annotation.patch_id is None:
        # Slide-level: only Level-0 coordinates exist.
        if changes.get("coordinates_patch_local") is not None:
            raise HTTPException(status_code=422, detail="A slide-level annotation has no patch-local coordinates; send coordinates_level0")
        if changes.get("coordinates_level0") is not None:
            try:
                validate_shape(shape_type, changes["coordinates_level0"])
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
            validate_shape(shape_type, changes["coordinates_patch_local"])
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
    if shape_type != annotation.type:
        try:
            validate_shape(shape_type, annotation.coordinates_level0)  # also when the points stayed as they were
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        annotation.type = shape_type
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
