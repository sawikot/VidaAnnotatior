from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_config_or_404, get_project_or_404
from app.database.session import get_db
from app.models.config_version import ProjectConfigVersion
from app.models.project import Project
from app.schemas.config_version import ConfigUsageOut, ConfigVersionOut, ConfigVersionUpdate
from app.services.config_versioning import (
    ClassSyncError,
    ConfigLockedError,
    assert_mutable,
    compute_config_hash,
    config_usage,
    sync_annotation_classes,
)

router = APIRouter(tags=["configs"])


@router.get("/projects/{project_id}/config", response_model=ConfigVersionOut)
def get_project_config(project: Project = Depends(get_project_or_404), db: Session = Depends(get_db)):
    """The project's configuration -- a project has exactly one."""
    config = db.get(ProjectConfigVersion, project.active_config_version_id) if project.active_config_version_id else None
    if config is None:
        raise HTTPException(status_code=404, detail="This project has no configuration")
    return config


@router.get("/configs/{config_id}", response_model=ConfigVersionOut)
def get_config(config: ProjectConfigVersion = Depends(get_config_or_404)):
    return config


@router.put("/configs/{config_id}", response_model=ConfigVersionOut)
def update_config(
    payload: ConfigVersionUpdate,
    config: ProjectConfigVersion = Depends(get_config_or_404),
    db: Session = Depends(get_db),
):
    changes = payload.model_dump(exclude_unset=True)
    changes.pop("annotation_classes", None)
    try:
        assert_mutable(db, config, changes)
        if payload.annotation_classes is not None:
            sync_annotation_classes(db, config, payload.annotation_classes)
    except ConfigLockedError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ClassSyncError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    for field, value in changes.items():
        setattr(config, field, value)
    config.config_hash = compute_config_hash(config)
    db.commit()
    db.refresh(config)
    return config


@router.get("/configs/{config_id}/usage", response_model=ConfigUsageOut)
def get_config_usage(config: ProjectConfigVersion = Depends(get_config_or_404), db: Session = Depends(get_db)):
    """How much data hangs off the configuration (patches, annotations, slides)."""
    return config_usage(db, config.id)
