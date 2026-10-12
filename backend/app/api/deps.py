from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.session import get_db
from app.models.config_version import ProjectConfigVersion
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide
from app.models.version import ProjectVersion
from app.services import versioning


def get_project_or_404(project_id: int, db: Session = Depends(get_db)) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return project


def project_view(
    version: int | None = Query(None, description="read this saved version of the project instead of the work as it is now"),
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
) -> Iterator[tuple[Session, Project]]:
    """The session and project a route reads from: the work, or -- with ``?version=`` -- a saved
    version, read beside the work without opening it (services/versioning.read_only). In a version,
    slide and patch ids are the version's own; they only mean something in requests naming it too."""
    if version is None:
        yield db, project
        return
    saved = db.get(ProjectVersion, version)
    if saved is None or saved.project_id != project.id:
        raise HTTPException(status_code=404, detail=f"Version {version} not found")
    try:
        with versioning.read_only(db, project, saved, get_settings().wsi_storage_dir) as view:
            yield view
    except versioning.VersionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def forbid_for_image_project(project: Project, what: str) -> None:
    """Image projects annotate each image as it is: one slide, one patch covering it,
    one configuration. Tiling or forking would strand or delete that patch."""
    if project.project_type == "image":
        raise HTTPException(status_code=409, detail=f"{what} is not available in an image project.")


def get_slide_or_404(slide_id: int, db: Session = Depends(get_db)) -> Slide:
    slide = db.get(Slide, slide_id)
    if slide is None:
        raise HTTPException(status_code=404, detail=f"Slide {slide_id} not found")
    return slide


def get_config_or_404(config_id: int, db: Session = Depends(get_db)) -> ProjectConfigVersion:
    config = db.get(ProjectConfigVersion, config_id)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Configuration {config_id} not found")
    return config


def get_patch_or_404(patch_id: int, db: Session = Depends(get_db)) -> Patch:
    patch = db.get(Patch, patch_id)
    if patch is None:
        raise HTTPException(status_code=404, detail=f"Patch {patch_id} not found")
    return patch
