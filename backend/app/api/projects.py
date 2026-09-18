from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.database.session import get_db
from app.models.config_version import ProjectConfigVersion
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide
from app.schemas.project import ProjectCreate, ProjectDetailOut, ProjectOut, ProjectStats, ProjectUpdate
from app.services.config_versioning import compute_config_hash
from app.services.slugify import slugify, unique_project_slug

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)) -> list[Project]:
    return db.query(Project).order_by(Project.updated_at.desc()).all()


@router.post("", response_model=ProjectDetailOut, status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> Project:
    base_slug = slugify(payload.slug or payload.name)
    slug = unique_project_slug(db, base_slug)

    project = Project(
        slug=slug,
        name=payload.name,
        organ=payload.organ,
        description=payload.description,
        team=payload.team,
        status="active",
    )
    db.add(project)
    db.flush()

    cfg_data = payload.config.model_dump(exclude={"annotation_classes"})
    config = ProjectConfigVersion(project_id=project.id, status="draft", **cfg_data)
    config.config_hash = compute_config_hash(config)
    db.add(config)
    db.flush()

    from app.models.config_version import AnnotationClass

    for i, cls in enumerate(payload.config.annotation_classes):
        db.add(
            AnnotationClass(
                config_version_id=config.id,
                name=cls.name,
                color_hex=cls.color_hex,
                hotkey=cls.hotkey,
                order_index=cls.order_index or i,
            )
        )

    project.active_config_version_id = config.id
    db.commit()
    db.refresh(project)
    return _to_detail(db, project)


@router.get("/{project_id}", response_model=ProjectDetailOut)
def get_project(project: Project = Depends(get_project_or_404), db: Session = Depends(get_db)) -> Project:
    return _to_detail(db, project)


@router.put("/{project_id}", response_model=ProjectDetailOut)
def update_project(
    payload: ProjectUpdate,
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
) -> Project:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return _to_detail(db, project)


@router.delete("/{project_id}", status_code=204, response_model=None)
def delete_project(project: Project = Depends(get_project_or_404), db: Session = Depends(get_db)) -> None:
    db.delete(project)
    db.commit()


def _to_detail(db: Session, project: Project) -> ProjectDetailOut:
    slide_count = db.query(func.count(Slide.id)).filter(Slide.project_id == project.id).scalar() or 0
    processed = (
        db.query(func.count(Slide.id))
        .filter(Slide.project_id == project.id, Slide.status.in_(["patches_generated", "annotating", "reviewed"]))
        .scalar()
        or 0
    )
    slide_ids = [s.id for s in db.query(Slide.id).filter(Slide.project_id == project.id).all()]
    total_patches = 0
    annotated = 0
    reviewed = 0
    flagged = 0
    tissue_area = 0.0
    if slide_ids:
        total_patches = (
            db.query(func.count(Patch.id)).filter(Patch.slide_id.in_(slide_ids)).scalar() or 0
        )
        annotated = (
            db.query(func.count(Patch.id))
            .filter(Patch.slide_id.in_(slide_ids), Patch.status.in_(["annotated", "reviewed"]))
            .scalar()
            or 0
        )
        reviewed = (
            db.query(func.count(Patch.id))
            .filter(Patch.slide_id.in_(slide_ids), Patch.status == "reviewed")
            .scalar()
            or 0
        )
        flagged = (
            db.query(func.count(Patch.id)).filter(Patch.slide_id.in_(slide_ids), Patch.flagged.is_(True)).scalar()
            or 0
        )
        tissue_area = (
            db.query(func.coalesce(func.sum(Slide.tissue_area_mm2), 0.0))
            .filter(Slide.project_id == project.id)
            .scalar()
            or 0.0
        )

    active_config = db.get(ProjectConfigVersion, project.active_config_version_id) if project.active_config_version_id else None

    stats = ProjectStats(
        slide_count=slide_count,
        processed_slide_count=processed,
        total_patches=total_patches,
        annotated_patches=annotated,
        reviewed_patches=reviewed,
        flagged_patches=flagged,
        tissue_area_mm2=round(tissue_area, 2),
    )
    base = ProjectOut.model_validate(project, from_attributes=True).model_dump()
    return ProjectDetailOut(**base, stats=stats, active_config=active_config)
