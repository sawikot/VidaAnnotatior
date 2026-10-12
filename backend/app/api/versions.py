"""Versions of a project's work (services/versioning.py): save the work as a version, open another
one and go on from there."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api import suggestions
from app.api.access import current_user
from app.api.deps import get_project_or_404
from app.core.config import get_settings
from app.database.session import get_db
from app.models.project import Project
from app.models.slide import Slide
from app.models.training import TrainingRun
from app.models.user import User
from app.models.version import ProjectVersion
from app.services import versioning

router = APIRouter(tags=["versions"])


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: int
    name: str
    note: str | None
    parent_id: int | None
    created_by: str | None
    created_at: datetime
    summary: dict


class VersionsOut(BaseModel):
    # The version the work is on (None: nothing saved yet, or that version was deleted) and whether
    # the work has changed since it was saved or opened.
    current_version_id: int | None
    has_unsaved_changes: bool
    versions: list[VersionOut]


class VersionSave(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)


class VersionUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=2000)


class VersionSwitch(BaseModel):
    # Opening a version replaces the work; unsaved changes are lost, so that must be asked for.
    discard_changes: bool = False


# Saving and opening read or replace a whole project's rows: one at a time.
_one_at_a_time = threading.Lock()


@contextmanager
def _exclusive():
    if not _one_at_a_time.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A version is being saved or opened right now. Try again in a moment.")
    try:
        yield
    finally:
        _one_at_a_time.release()


def get_version_or_404(version_id: int, project: Project = Depends(get_project_or_404), db: Session = Depends(get_db)) -> ProjectVersion:
    version = db.get(ProjectVersion, version_id)
    if version is None or version.project_id != project.id:
        raise HTTPException(status_code=404, detail=f"Version {version_id} not found")
    return version


def _out(db: Session, project: Project) -> VersionsOut:
    versions = db.query(ProjectVersion).filter(ProjectVersion.project_id == project.id).order_by(ProjectVersion.number.desc()).all()
    return VersionsOut(
        current_version_id=project.current_version_id,
        has_unsaved_changes=versioning.has_unsaved_changes(db, project),
        versions=versions,
    )


def _slide_ids(db: Session, project: Project) -> set[int]:
    return {row[0] for row in db.query(Slide.id).filter(Slide.project_id == project.id)}


def _refuse_while_busy(db: Session, project: Project) -> None:
    """Opening a version replaces the rows other work is reading and writing."""
    slide_ids = _slide_ids(db, project)
    with suggestions._slide_jobs_lock:
        suggesting = any(job.status == "running" and job.slide_id in slide_ids for job in suggestions._slide_jobs.values())
    if suggesting:
        raise HTTPException(status_code=409, detail="A model is still making suggestions on a slide of this project. Wait for it to finish, or stop it, then open the version.")
    if db.query(TrainingRun.id).filter(TrainingRun.project_id == project.id, TrainingRun.status == "preparing").first() is not None:
        raise HTTPException(status_code=409, detail="A training run is cutting its dataset from this project right now. Open the version once it has started training.")


@router.get("/projects/{project_id}/versions", response_model=VersionsOut)
def list_versions(project: Project = Depends(get_project_or_404), db: Session = Depends(get_db)) -> VersionsOut:
    return _out(db, project)


@router.post("/projects/{project_id}/versions", response_model=VersionsOut, status_code=201)
def save_version(
    payload: VersionSave,
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> VersionsOut:
    """Save the work as it is now as a new version; the work is then on that version."""
    with _exclusive():
        versioning.save_version(db, project, get_settings().wsi_storage_dir, name=payload.name, note=payload.note, user=user)
    return _out(db, project)


@router.post("/projects/{project_id}/versions/{version_id}/switch", response_model=VersionsOut)
def switch_version(
    payload: VersionSwitch,
    version: ProjectVersion = Depends(get_version_or_404),
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
) -> VersionsOut:
    """Open a version: the project then shows exactly what that version holds, and work goes on from
    it. Opening the version the work is already on puts it back as it was saved."""
    with _exclusive():
        _refuse_while_busy(db, project)
        if not payload.discard_changes and versioning.has_unsaved_changes(db, project):
            raise HTTPException(status_code=409, detail="There are unsaved changes. Save them as a new version first, or discard them.")
        before = _slide_ids(db, project)
        try:
            versioning.switch_to(db, project, version, get_settings().wsi_storage_dir)
        except versioning.VersionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        with suggestions._slide_jobs_lock:  # what a finished job said is about a slide that may be another now
            for slide_id in before | _slide_ids(db, project):
                suggestions._slide_jobs.pop(slide_id, None)
    return _out(db, project)


@router.put("/projects/{project_id}/versions/{version_id}", response_model=VersionsOut)
def update_version(
    payload: VersionUpdate,
    version: ProjectVersion = Depends(get_version_or_404),
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
) -> VersionsOut:
    """Rename a version or change its note. What it holds never changes."""
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="A version needs a name.")
    version.name = payload.name.strip()
    version.note = (payload.note or "").strip() or None
    db.commit()
    return _out(db, project)


@router.delete("/projects/{project_id}/versions/{version_id}", response_model=VersionsOut)
def delete_version(
    version: ProjectVersion = Depends(get_version_or_404),
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
) -> VersionsOut:
    """Delete a saved version, and the files of slides only it still had. The work itself is left as
    it is."""
    with _exclusive():
        versioning.delete_version(db, project, version, get_settings().wsi_storage_dir)
    return _out(db, project)
