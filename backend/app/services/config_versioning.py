"""Config version lock/fork rules.

Once a ProjectConfigVersion has any Patch generated against it, its critical
geometry parameters become immutable -- changing them would silently
invalidate every existing patch/annotation's spatial meaning. Callers must
fork a new version instead. This module is the single place that enforces
that rule.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.models.config_version import AnnotationClass, ProjectConfigVersion
from app.models.patch import Patch

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
    that already has generated patches. Callers should fork instead."""


def compute_config_hash(config: ProjectConfigVersion) -> str:
    payload = {field: getattr(config, field) for field in CRITICAL_FIELDS}
    encoded = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def has_generated_data(db: Session, config_version_id: int) -> bool:
    return db.query(Patch.id).filter(Patch.config_version_id == config_version_id).first() is not None


def assert_mutable(db: Session, config: ProjectConfigVersion, changes: dict) -> None:
    if not has_generated_data(db, config.id):
        return
    touched_critical = [f for f in CRITICAL_FIELDS if f in changes and changes[f] != getattr(config, f)]
    if touched_critical:
        raise ConfigLockedError(
            "Version "
            f"{config.version_label} already has generated patches; critical field(s) "
            f"{touched_critical} cannot be changed in place. Create a new config version (fork) instead."
        )


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
