from __future__ import annotations

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    organ: Mapped[str | None] = mapped_column(String(100), default=None)
    description: Mapped[str | None] = mapped_column(String(2000), default=None)
    team: Mapped[str | None] = mapped_column(String(200), default=None)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|review|completed

    active_config_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_config_versions.id", use_alter=True, name="fk_project_active_config"),
        default=None,
    )

    config_versions: Mapped[list["ProjectConfigVersion"]] = relationship(
        "ProjectConfigVersion",
        back_populates="project",
        cascade="all, delete-orphan",
        foreign_keys="ProjectConfigVersion.project_id",
    )
    slides: Mapped[list["Slide"]] = relationship(
        "Slide", back_populates="project", cascade="all, delete-orphan"
    )
