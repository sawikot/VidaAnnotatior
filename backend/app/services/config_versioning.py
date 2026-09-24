"""Configuration editing rules. A project has exactly one configuration (a single
ProjectConfigVersion row, kept as a row because patches, annotations and classes point at it).

The patch-grid fields (size, stride, magnification, tissue threshold) can always be changed: every
patch records the grid it was cut with (services/patch_grid.py), so existing patches and annotations
keep their meaning and the new values describe the grid used next. The tissue method and coordinate
system are fixed once patches exist. This module enforces that, plus the diagnostic-class rules.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.models.annotation import GeometryAnnotation
from app.models.config_version import AnnotationClass, ProjectConfigVersion
from app.models.patch import Patch
from app.models.slide import Slide

CRITICAL_FIELDS = (
    "patch_width",
    "patch_height",
    "stride_x",
    "stride_y",
    "target_level",
    "target_magnification",
    "min_tissue_fraction",
    "tissue_method",
    "coordinate_system",
)


# Describe the grid patches are cut with; each patch keeps its own grid, so these stay editable.
GRID_FIELDS = (
    "patch_width",
    "patch_height",
    "stride_x",
    "stride_y",
    "target_level",
    "target_magnification",
    "min_tissue_fraction",
)


class ConfigLockedError(Exception):
    """Raised when an update would mutate a critical field of a config version
    that is locked or already has generated patches. Callers should fork."""


class ClassSyncError(Exception):
    """A rejected diagnostic-class edit. `status_code` is the HTTP status the
    API layer should answer with (422 invalid list, 409 conflicts with data)."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def compute_config_hash(config: ProjectConfigVersion) -> str:
    payload = {field: getattr(config, field) for field in CRITICAL_FIELDS}
    encoded = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def has_generated_data(db: Session, config_version_id: int) -> bool:
    return db.query(Patch.id).filter(Patch.config_version_id == config_version_id).first() is not None


def assert_mutable(db: Session, config: ProjectConfigVersion, changes: dict) -> None:
    if not has_generated_data(db, config.id):
        return
    guarded = tuple(f for f in CRITICAL_FIELDS if f not in GRID_FIELDS)
    touched_critical = [f for f in guarded if f in changes and changes[f] != getattr(config, f)]
    if touched_critical:
        raise ConfigLockedError(
            f"{', '.join(touched_critical)} cannot be changed once patches exist: existing patches and "
            "annotations were made with it."
        )


def config_usage(db: Session, config_id: int) -> dict:
    return {
        "patch_count": db.query(Patch).filter(Patch.config_version_id == config_id).count(),
        "annotation_count": db.query(GeometryAnnotation).filter(GeometryAnnotation.config_version_id == config_id).count(),
        "slide_count": db.query(Slide).filter(Slide.active_config_version_id == config_id).count(),
    }


def sync_annotation_classes(db: Session, config: ProjectConfigVersion, items: list) -> None:
    """Replace `config`'s diagnostic classes with `items` (objects with
    id/name/color_hex/hotkey), preserving ids so existing annotations keep
    pointing at the same class row across renames and recolors.

    Validates the whole list before touching anything. Deleting a class that
    annotations still use is refused rather than orphaning them."""
    if not items:
        raise ClassSyncError("At least one diagnostic class is required.")

    names = [i.name.strip() for i in items]
    if any(not n for n in names):
        raise ClassSyncError("Class names cannot be blank.")
    lowered = [n.lower() for n in names]
    dupes = sorted({n for n in names if lowered.count(n.lower()) > 1})
    if dupes:
        raise ClassSyncError(f"Class names must be unique (duplicated: {', '.join(dupes)}).")

    hotkeys = [i.hotkey.strip() for i in items if i.hotkey and i.hotkey.strip()]
    dupe_keys = sorted({k for k in hotkeys if hotkeys.count(k) > 1})
    if dupe_keys:
        raise ClassSyncError(f"Hotkeys must be unique (duplicated: {', '.join(dupe_keys)}).")

    existing = {c.id: c for c in config.annotation_classes}
    unknown = [i.id for i in items if i.id is not None and i.id not in existing]
    if unknown:
        raise ClassSyncError(f"Class id(s) {unknown} do not belong to this configuration.")

    keep_ids = {i.id for i in items if i.id is not None}
    removed = [c for cid, c in existing.items() if cid not in keep_ids]
    in_use = [
        c.name
        for c in removed
        if db.query(GeometryAnnotation.id).filter(GeometryAnnotation.class_id == c.id).first() is not None
        or db.query(Patch.id).filter(Patch.label_class_id == c.id).first() is not None
    ]
    if in_use:
        raise ClassSyncError(
            f"Cannot delete class(es) still used by annotations: {', '.join(in_use)}. "
            "Delete or reclassify those annotations first.",
            status_code=409,
        )

    for c in removed:
        db.delete(c)
    for position, (item, name) in enumerate(zip(items, names)):
        hotkey = item.hotkey.strip() if item.hotkey and item.hotkey.strip() else None
        if item.id is not None:
            cls = existing[item.id]
            if cls.name != name:  # the patch labels that are this class follow the rename
                db.query(Patch).filter(Patch.label_class_id == cls.id).update({Patch.patch_label: name}, synchronize_session=False)
            cls.name, cls.color_hex, cls.hotkey, cls.order_index = name, item.color_hex, hotkey, position
        else:
            db.add(
                AnnotationClass(
                    config_version_id=config.id,
                    name=name,
                    color_hex=item.color_hex,
                    hotkey=hotkey,
                    order_index=position,
                )
            )
    db.flush()
    db.expire(config, ["annotation_classes"])
