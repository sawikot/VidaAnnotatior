from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class ProjectVersion(Base, TimestampMixin):
    """A saved state of a project's work: which slides it has, their tissue and patches, every
    annotation and suggestion, the classes and settings. The tables hold only the state being worked
    on; a version's own copy is a file in ``<storage>/<project id>/_versions/<id>/`` and is never
    changed after it is saved (services/versioning.py). Opening a version puts its state back in the
    tables, and work goes on from there."""

    __tablename__ = "project_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # The version the work was on when this one was saved; None for a first version.
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("project_versions.id", ondelete="SET NULL"), default=None)

    number: Mapped[int] = mapped_column(Integer)  # 1, 2, 3... within the project; never reused
    name: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(String(2000), default=None)
    created_by: Mapped[str | None] = mapped_column(String(120), default=None)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)

    # What it holds, in numbers, for the list: slides, patches, annotated_patches, annotations...
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    # The stored files of its slides (Slide.file_path). A slide removed from the work keeps its
    # files for as long as a version still has it.
    slide_files: Mapped[list] = mapped_column(JSON, default=list)
