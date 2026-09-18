from __future__ import annotations

from sqlalchemy import JSON, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin

GEOMETRY_TYPES = ("polygon", "rectangle", "point", "freehand")


class GeometryAnnotation(Base, TimestampMixin):
    """A drawn shape. Stored in BOTH patch-local (provenance) and Level-0 (canonical) space."""

    __tablename__ = "geometry_annotations"

    id: Mapped[int] = mapped_column(primary_key=True)
    patch_id: Mapped[int] = mapped_column(ForeignKey("patches.id", ondelete="CASCADE"), index=True)
    slide_id: Mapped[int] = mapped_column(ForeignKey("slides.id", ondelete="CASCADE"), index=True)
    config_version_id: Mapped[int] = mapped_column(ForeignKey("project_config_versions.id"), index=True)
    class_id: Mapped[int | None] = mapped_column(ForeignKey("annotation_classes.id"), default=None)

    type: Mapped[str] = mapped_column(String(20))  # polygon|rectangle|point|freehand

    # Provenance: coordinates as drawn, in the patch's *display* pixel space.
    coordinates_patch_local: Mapped[list] = mapped_column(JSON)
    # Canonical: coordinates transformed into Level-0 absolute pixel space. Export uses this.
    coordinates_level0: Mapped[list] = mapped_column(JSON)

    created_by: Mapped[str | None] = mapped_column(String(120), default=None)
    notes: Mapped[str | None] = mapped_column(String(2000), default=None)
    unsure: Mapped[bool] = mapped_column(Boolean, default=False)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)

    patch: Mapped["Patch"] = relationship("Patch", back_populates="geometry_annotations")
    annotation_class: Mapped["AnnotationClass | None"] = relationship("AnnotationClass")
