from __future__ import annotations

from sqlalchemy import JSON, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin


class Slide(Base, TimestampMixin):
    __tablename__ = "slides"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)

    filename: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str | None] = mapped_column(String(1000), default=None)  # relative to wsi_storage_dir
    source_type: Mapped[str] = mapped_column(String(20), default="upload")  # upload|path|demo
    format: Mapped[str | None] = mapped_column(String(20), default=None)

    status: Mapped[str] = mapped_column(String(30), default="imported")
    # imported -> tissue_detected -> patches_generated -> annotating -> reviewed | error
    error_message: Mapped[str | None] = mapped_column(String(2000), default=None)

    width_l0: Mapped[int | None] = mapped_column(Integer, default=None)
    height_l0: Mapped[int | None] = mapped_column(Integer, default=None)
    level_count: Mapped[int | None] = mapped_column(Integer, default=None)
    level_dimensions: Mapped[list | None] = mapped_column(JSON, default=None)
    level_downsamples: Mapped[list | None] = mapped_column(JSON, default=None)
    mpp_x: Mapped[float | None] = mapped_column(Float, default=None)
    mpp_y: Mapped[float | None] = mapped_column(Float, default=None)
    magnification: Mapped[float | None] = mapped_column(Float, default=None)

    tissue_area_mm2: Mapped[float | None] = mapped_column(Float, default=None)
    tissue_coverage_pct: Mapped[float | None] = mapped_column(Float, default=None)
    # Cached tissue mask (a single small PNG per slide -- a visualization/computation
    # cache, NOT a patch image dataset) + the Level-0-pixels-per-mask-pixel scale.
    tissue_mask_path: Mapped[str | None] = mapped_column(String(1000), default=None)
    tissue_mask_downsample: Mapped[float | None] = mapped_column(Float, default=None)
    tissue_params_used: Mapped[dict | None] = mapped_column(JSON, default=None)

    active_config_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_config_versions.id"), default=None
    )

    project: Mapped["Project"] = relationship("Project", back_populates="slides")
    patches: Mapped[list["Patch"]] = relationship(
        "Patch", back_populates="slide", cascade="all, delete-orphan"
    )
