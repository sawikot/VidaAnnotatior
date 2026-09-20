"""Config version lock/fork rules.

A ProjectConfigVersion's critical geometry parameters become immutable once
it has any Patch generated against it, or once it has been explicitly locked
("Locked & Certified") -- changing them would silently invalidate every
existing patch/annotation's spatial meaning. Callers must fork a new version
instead. This module is the single place that enforces that rule, plus the
diagnostic-class editing rules.
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
    locked = config.status == "locked"
    if not locked and not has_generated_data(db, config.id):
        return
    touched_critical = [f for f in CRITICAL_FIELDS if f in changes and changes[f] != getattr(config, f)]
    if touched_critical:
        reason = "is locked" if locked else "already has generated patches"
        raise ConfigLockedError(
            f"Version {config.version_label} {reason}; critical field(s) "
            f"{touched_critical} cannot be changed in place. Create a new config version (fork) instead."
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
        raise ClassSyncError(f"Class id(s) {unknown} do not belong to version {config.version_label}.")

    keep_ids = {i.id for i in items if i.id is not None}
    removed = [c for cid, c in existing.items() if cid not in keep_ids]
    in_use = [
        c.name
        for c in removed
        if db.query(GeometryAnnotation.id).filter(GeometryAnnotation.class_id == c.id).first() is not None
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


def fork_config(
    db: Session,
    source: ProjectConfigVersion,
    overrides: dict,
    new_version_label: str,
    created_by: str | None = None,
) -> ProjectConfigVersion:
    data = {
        "project_id": source.project_id,
        "parent_version_id": source.id,
        "version_label": new_version_label,
        "status": "draft",
        "title": overrides.get("title", source.title),
        "created_by": created_by,
        "coordinate_system": source.coordinate_system,
        "target_magnification": source.target_magnification,
        "target_level": source.target_level,
        "mpp_handling": source.mpp_handling,
        "patch_width": source.patch_width,
        "patch_height": source.patch_height,
        "stride_x": source.stride_x,
        "stride_y": source.stride_y,
        "min_tissue_fraction": source.min_tissue_fraction,
        "allow_partial_patches": source.allow_partial_patches,
        "include_edge_patches": source.include_edge_patches,
        "tissue_method": source.tissue_method,
        "tissue_params": dict(source.tissue_params or {}),
        "enabled_tools": list(source.enabled_tools or []),
        "allow_skip": source.allow_skip,
        "allow_unsure": source.allow_unsure,
        "require_annotation": source.require_annotation,
        "reviewer_mode": source.reviewer_mode,
    }
    data.update({k: v for k, v in overrides.items() if k in data})

    new_config = ProjectConfigVersion(**data)
    new_config.config_hash = compute_config_hash(new_config)
    db.add(new_config)
    db.flush()

    for cls in source.annotation_classes:
        db.add(
            AnnotationClass(
                config_version_id=new_config.id,
                name=cls.name,
                color_hex=cls.color_hex,
                hotkey=cls.hotkey,
                order_index=cls.order_index,
            )
        )
    db.flush()
    return new_config
