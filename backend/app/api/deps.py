from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.config_version import ProjectConfigVersion
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide


def get_project_or_404(project_id: int, db: Session = Depends(get_db)) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return project


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
        raise HTTPException(status_code=404, detail=f"Config version {config_id} not found")
    return config


def get_patch_or_404(patch_id: int, db: Session = Depends(get_db)) -> Patch:
    patch = db.get(Patch, patch_id)
    if patch is None:
        raise HTTPException(status_code=404, detail=f"Patch {patch_id} not found")
    return patch
