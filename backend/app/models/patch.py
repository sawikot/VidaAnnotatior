from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin

PATCH_STATUSES = ("unannotated", "active", "annotated", "reviewed", "skipped", "flagged")


class Patch(Base, TimestampMixin):
    """A *virtual* patch: pure coordinates into a Slide. No image bytes are ever stored here."""

    __tablename__ = "patches"

    id: Mapped[int] = mapped_column(primary_key=True)
    slide_id: Mapped[int] = mapped_column(ForeignKey("slides.id", ondelete="CASCADE"), index=True)
    config_version_id: Mapped[int] = mapped_column(
        ForeignKey("project_config_versions.id"), index=True
    )

    patch_index: Mapped[int] = mapped_column(Integer)  # stable ordering within the slide's grid
    # Which grid (patch size, stride, magnification, tissue threshold) it was cut with -- see
    # services/patch_grid.py. A slide can hold several grids; it shows its active one.
    grid_key: Mapped[str | None] = mapped_column(String(80), default=None, index=True)

    # Level-0 absolute origin -- the master coordinate.
    x: Mapped[int] = mapped_column(Integer)
    y: Mapped[int] = mapped_column(Integer)
    # Pyramid level this patch is read at, and its pixel size *at that level*.
    level: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    # Footprint in Level-0 pixels (= width/height * downsample(level)).
    width_l0: Mapped[int] = mapped_column(Integer)
    height_l0: Mapped[int] = mapped_column(Integer)

    tissue_fraction: Mapped[float] = mapped_column(Float, default=0.0)

    status: Mapped[str] = mapped_column(String(20), default="unannotated")
    patch_label: Mapped[str | None] = mapped_column(String(100), default=None)  # patch-level classification
    # The class the label is, when it is one of the project's classes (see services/patch_labels.py):
    # kept by id so renaming the class renames the label. None for "Mixed" and the like.
    label_class_id: Mapped[int | None] = mapped_column(ForeignKey("annotation_classes.id"), default=None)
    unsure: Mapped[bool] = mapped_column(Boolean, default=False)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(String(4000), default=None)

    reviewed_by: Mapped[str | None] = mapped_column(String(120), default=None)
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)  # the account
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    slide: Mapped["Slide"] = relationship("Slide", back_populates="patches")
    geometry_annotations: Mapped[list["GeometryAnnotation"]] = relationship(
        "GeometryAnnotation", back_populates="patch", cascade="all, delete-orphan"
    )
