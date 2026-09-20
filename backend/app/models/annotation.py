from __future__ import annotations

from sqlalchemy import JSON, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin

from app.services.geometry import SHAPE_TYPES as GEOMETRY_TYPES  # noqa: E402,F401


class GeometryAnnotation(Base, TimestampMixin):
    """A drawn shape. Stored in BOTH patch-local (provenance) and Level-0 (canonical) space."""

    __tablename__ = "geometry_annotations"

    id: Mapped[int] = mapped_column(primary_key=True)
    # NULL for a *slide-level* annotation: one drawn directly on the whole slide, which belongs to no
    # single patch (it may cross many, or lie where no patch was generated). Its Level-0 coordinates
    # are then the only ones that exist; ``coordinates_patch_local`` is empty.
    patch_id: Mapped[int | None] = mapped_column(ForeignKey("patches.id", ondelete="CASCADE"), index=True, default=None)
    slide_id: Mapped[int] = mapped_column(ForeignKey("slides.id", ondelete="CASCADE"), index=True)
    config_version_id: Mapped[int] = mapped_column(ForeignKey("project_config_versions.id"), index=True)
    class_id: Mapped[int | None] = mapped_column(ForeignKey("annotation_classes.id"), default=None)

    type: Mapped[str] = mapped_column(String(20))  # point|line|freehand_line|rectangle|circle|polygon|freehand (see services/geometry.py)

    # Provenance: coordinates as drawn, in the patch's *display* pixel space.
    coordinates_patch_local: Mapped[list] = mapped_column(JSON)
    # Canonical: coordinates transformed into Level-0 absolute pixel space. Export uses this.
    coordinates_level0: Mapped[list] = mapped_column(JSON)

    created_by: Mapped[str | None] = mapped_column(String(120), default=None)
    notes: Mapped[str | None] = mapped_column(String(2000), default=None)
    unsure: Mapped[bool] = mapped_column(Boolean, default=False)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)

    patch: Mapped["Patch | None"] = relationship("Patch", back_populates="geometry_annotations")
    annotation_class: Mapped["AnnotationClass | None"] = relationship("AnnotationClass")
