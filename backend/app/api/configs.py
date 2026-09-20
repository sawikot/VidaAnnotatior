from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import forbid_for_image_project, get_config_or_404, get_project_or_404
from app.database.session import get_db
from app.models.config_version import AnnotationClass, ProjectConfigVersion
from app.models.project import Project
from app.schemas.config_version import (
    ConfigUsageOut,
    ConfigVersionCreate,
    ConfigVersionForkRequest,
    ConfigVersionOut,
    ConfigVersionUpdate,
)
from app.services.config_versioning import (
    ClassSyncError,
    ConfigLockedError,
    assert_mutable,
    compute_config_hash,
    config_usage,
    fork_config,
    sync_annotation_classes,
)

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
    forbid_for_image_project(project, "Creating another configuration version")
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
    """How much data hangs off this version -- the UI uses it to decide whether
    critical-field edits must become a fork instead of an in-place change."""
    return config_usage(db, config.id)


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
    forbid_for_image_project(db.get(Project, config.project_id), "Forking a configuration version")
    label = payload.new_version_label.strip()
    taken = {
        row[0]
        for row in db.query(ProjectConfigVersion.version_label).filter(
            ProjectConfigVersion.project_id == config.project_id
        )
    }
    if label in taken:
        raise HTTPException(status_code=409, detail=f"Version label '{label}' already exists in this project.")

    # Validate overrides with the same constraints as an in-place edit; unknown
    # keys are ignored by fork_config.
    known = ConfigVersionUpdate.model_fields.keys() - {"annotation_classes"}
    try:
        overrides = ConfigVersionUpdate.model_validate(
            {k: v for k, v in payload.overrides.items() if k in known}
        ).model_dump(exclude_unset=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    new_config = fork_config(db, config, overrides, label, payload.created_by)
    if payload.annotation_classes is not None:
        # Class ids in the request belong to the source version; the fork owns
        # fresh copies, so treat every row as new.
        try:
            sync_annotation_classes(
                db, new_config, [c.model_copy(update={"id": None}) for c in payload.annotation_classes]
            )
        except ClassSyncError as exc:
            db.rollback()
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    new_config.config_hash = compute_config_hash(new_config)
    db.commit()
    db.refresh(new_config)
    return new_config
