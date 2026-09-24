"""Patch labels: saying what a whole patch is.

Setting a patch's label to one of the project's classes also *annotates the whole patch* with that class:
a rectangle covering the patch (clipped to the slide), marked ``whole_patch``. So the label is a real
annotation -- it reaches masks, COCO, statistics and every other patch size like any drawn shape -- and
the two stay linked:

* changing the label changes the rectangle's class; clearing it removes the rectangle;
* deleting the rectangle clears the label; changing its class changes the label;
* reshaping or moving it makes it an ordinary shape (the label stays, without a fill).

A label that is not a class ("Mixed", "Artifact / Background") is stored as text only, with no shape.
Class labels are kept by class id, so renaming a class renames its labels.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.annotation import GeometryAnnotation
from app.models.config_version import AnnotationClass
from app.models.patch import Patch
from app.services.coordinate_transform import PatchOrigin, polygon_patch_local_to_level0


def whole_patch_rectangle(patch: Patch) -> tuple[list[list[float]], list[list[float]]]:
    """(patch-local, Level-0) corners of the patch, clipped to the slide (an edge patch can reach past it)."""
    sx = patch.width_l0 / patch.width if patch.width else 1.0
    sy = patch.height_l0 / patch.height if patch.height else 1.0
    slide = patch.slide
    w = float(patch.width)
    h = float(patch.height)
    if slide is not None and slide.width_l0:
        w = min(w, (slide.width_l0 - patch.x) / sx)
    if slide is not None and slide.height_l0:
        h = min(h, (slide.height_l0 - patch.y) / sy)
    local = [[0.0, 0.0], [w, 0.0], [w, h], [0.0, h]]
    origin = PatchOrigin(x=patch.x, y=patch.y, level=patch.level, downsample=sx)
    return local, polygon_patch_local_to_level0(origin, local)


def _fill(db: Session, patch: Patch) -> GeometryAnnotation | None:
    return (
        db.query(GeometryAnnotation)
        .filter(GeometryAnnotation.patch_id == patch.id, GeometryAnnotation.whole_patch.is_(True))
        .first()
    )


def set_patch_label(db: Session, patch: Patch, label: str | None, created_by: str | None = None) -> None:
    """Set (or clear, with None/"") the patch's label, keeping its whole-patch annotation in step. Not committed."""
    label = (label or "").strip() or None
    fill = _fill(db, patch)
    cls = None
    if label is not None:
        cls = (
            db.query(AnnotationClass)
            .filter(AnnotationClass.config_version_id == patch.config_version_id)
            .all()
        )
        cls = next((c for c in cls if c.name.strip().lower() == label.lower()), None)

    if cls is not None:
        patch.label_class_id = cls.id
        patch.patch_label = cls.name
        if fill is None:
            local, level0 = whole_patch_rectangle(patch)
            db.add(
                GeometryAnnotation(
                    patch_id=patch.id,
                    slide_id=patch.slide_id,
                    config_version_id=patch.config_version_id,
                    class_id=cls.id,
                    type="rectangle",
                    coordinates_patch_local=local,
                    coordinates_level0=level0,
                    whole_patch=True,
                    created_by=created_by,
                )
            )
        else:
            fill.class_id = cls.id
    else:
        patch.label_class_id = None
        patch.patch_label = label  # a label that is not a class, or None
        if fill is not None:
            db.delete(fill)

    if label is not None and patch.status == "unannotated":
        patch.status = "annotated"
    db.flush()


def on_fill_deleted(db: Session, annotation: GeometryAnnotation) -> None:
    """The whole-patch shape was deleted: the patch no longer has that label."""
    if not annotation.whole_patch or annotation.patch_id is None:
        return
    patch = db.get(Patch, annotation.patch_id)
    if patch is not None and patch.label_class_id == annotation.class_id:
        patch.label_class_id = None
        patch.patch_label = None


def on_fill_class_changed(db: Session, annotation: GeometryAnnotation) -> None:
    """The whole-patch shape was given another class: so was the patch's label."""
    if not annotation.whole_patch or annotation.patch_id is None:
        return
    patch = db.get(Patch, annotation.patch_id)
    cls = db.get(AnnotationClass, annotation.class_id) if annotation.class_id else None
    if patch is None:
        return
    patch.label_class_id = cls.id if cls else None
    patch.patch_label = cls.name if cls else None
