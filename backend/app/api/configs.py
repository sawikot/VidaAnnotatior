from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_config_or_404, get_project_or_404
from app.database.session import get_db
from app.models.config_version import AnnotationClass, ProjectConfigVersion
from app.models.project import Project
from app.schemas.config_version import (
    ConfigVersionCreate,
    ConfigVersionForkRequest,
    ConfigVersionOut,
    ConfigVersionUpdate,
)
from app.services.config_versioning import ConfigLockedError, assert_mutable, compute_config_hash, fork_config

router = APIRouter(tags=["configs"])


@router.get("/projects/{project_id}/configs", response_model=list[ConfigVersionOut])
def list_configs(project: Project = Depends(get_project_or_404), db: Session = Depends(get_db)):
    return (
        db.query(ProjectConfigVersion)
        .filter(ProjectConfigVersion.project_id == project.id)
        .order_by(ProjectConfigVersion.created_at.asc())
        .all()
    )


@router.post("/projects/{project_id}/configs", response_model=ConfigVersionOut, status_code=201)
def create_config(
    payload: ConfigVersionCreate,
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
):
    data = payload.model_dump(exclude={"annotation_classes"})
    config = ProjectConfigVersion(project_id=project.id, status="draft", **data)
    config.config_hash = compute_config_hash(config)
    db.add(config)
    db.flush()
    for i, cls in enumerate(payload.annotation_classes):
        db.add(
            AnnotationClass(
                config_version_id=config.id,
                name=cls.name,
                color_hex=cls.color_hex,
                hotkey=cls.hotkey,
                order_index=cls.order_index or i,
            )
        )
    db.commit()
    db.refresh(config)
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
    try:
        assert_mutable(db, config, changes)
    except ConfigLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    for field, value in changes.items():
        setattr(config, field, value)
    config.config_hash = compute_config_hash(config)
    db.commit()
    db.refresh(config)
    return config


@router.post("/configs/{config_id}/lock", response_model=ConfigVersionOut)
def lock_config(config: ProjectConfigVersion = Depends(get_config_or_404), db: Session = Depends(get_db)):
    config.status = "locked"
    db.commit()
    db.refresh(config)
    return config


@router.post("/configs/{config_id}/fork", response_model=ConfigVersionOut, status_code=201)
def fork_config_endpoint(
    payload: ConfigVersionForkRequest,
    config: ProjectConfigVersion = Depends(get_config_or_404),
    db: Session = Depends(get_db),
):
    new_config = fork_config(db, config, payload.overrides, payload.new_version_label, payload.created_by)
    db.commit()
    db.refresh(new_config)
    return new_config
