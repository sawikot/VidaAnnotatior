from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin


class ProjectConfigVersion(Base, TimestampMixin):
    """A single, versioned snapshot of a project's WSI/patch/tissue/QC configuration.

    Once any Patch or GeometryAnnotation references a version, that version's
    critical geometry parameters (patch size, stride, level, magnification,
    tissue threshold) must never be mutated in place -- create a new version
    (fork) instead. See services/config_versioning.py for the guard.
    """

    __tablename__ = "project_config_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    parent_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_config_versions.id"), default=None
    )

    version_label: Mapped[str] = mapped_column(String(20))  # "v1.0", "v2.0-RC1"
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|locked|experimental|deprecated
    title: Mapped[str | None] = mapped_column(String(200), default=None)
    created_by: Mapped[str | None] = mapped_column(String(120), default=None)
    config_hash: Mapped[str | None] = mapped_column(String(64), default=None)

    # --- WSI configuration ---
    coordinate_system: Mapped[str] = mapped_column(String(20), default="level0")
    target_magnification: Mapped[float | None] = mapped_column(Float, default=20.0)
    target_level: Mapped[int | None] = mapped_column(Integer, default=None)
    mpp_handling: Mapped[str] = mapped_column(String(20), default="auto")

    # --- Patch configuration ---
    patch_width: Mapped[int] = mapped_column(Integer, default=512)
    patch_height: Mapped[int] = mapped_column(Integer, default=512)
    stride_x: Mapped[int] = mapped_column(Integer, default=512)
    stride_y: Mapped[int] = mapped_column(Integer, default=512)
    min_tissue_fraction: Mapped[float] = mapped_column(Float, default=0.6)
    allow_partial_patches: Mapped[bool] = mapped_column(Boolean, default=False)
    include_edge_patches: Mapped[bool] = mapped_column(Boolean, default=True)

    # --- Tissue segmentation ---
    tissue_method: Mapped[str] = mapped_column(String(40), default="hsv_otsu")
    tissue_params: Mapped[dict] = mapped_column(JSON, default=dict)

    # --- Annotation settings ---
    enabled_tools: Mapped[list] = mapped_column(
        JSON, default=lambda: ["polygon", "rectangle", "point", "freehand"]
    )

    # --- Quality control settings ---
    allow_skip: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_unsure: Mapped[bool] = mapped_column(Boolean, default=True)
    require_annotation: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewer_mode: Mapped[bool] = mapped_column(Boolean, default=False)

    project: Mapped["Project"] = relationship(
        "Project", back_populates="config_versions", foreign_keys=[project_id]
    )
    annotation_classes: Mapped[list["AnnotationClass"]] = relationship(
        "AnnotationClass", back_populates="config_version", cascade="all, delete-orphan",
        order_by="AnnotationClass.order_index",
    )


class AnnotationClass(Base, TimestampMixin):
    __tablename__ = "annotation_classes"

    id: Mapped[int] = mapped_column(primary_key=True)
    config_version_id: Mapped[int] = mapped_column(
        ForeignKey("project_config_versions.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    color_hex: Mapped[str] = mapped_column(String(9), default="#2563eb")
    hotkey: Mapped[str | None] = mapped_column(String(4), default=None)
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    config_version: Mapped["ProjectConfigVersion"] = relationship(
        "ProjectConfigVersion", back_populates="annotation_classes"
    )
