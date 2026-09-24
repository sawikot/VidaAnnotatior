from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.core.config import get_settings
from app.database.session import get_db
from app.models.annotation import GeometryAnnotation
from app.models.config_version import AnnotationClass, ProjectConfigVersion
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide
from app.schemas.project import ProjectCreate, ProjectDetailOut, ProjectOut, ProjectStats, ProjectUpdate
from app.services import project_storage, reader_cache
from app.services.deepzoom_service import purge_slide_tiles
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
        project_type=payload.project_type,
    )
    db.add(project)
    db.flush()
    # SQLite can hand a deleted project's id to a new one; never let the new
    # project inherit files a failed delete left behind under that id.
    project_storage.remove_tree(project_storage.project_dir(get_settings().wsi_storage_dir, project.id))

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
    changes = payload.model_dump(exclude_unset=True)
    new_active = changes.get("active_config_version_id")
    if new_active is not None:
        config = db.get(ProjectConfigVersion, new_active)
        if config is None or config.project_id != project.id:
            raise HTTPException(status_code=422, detail="That configuration does not belong to this project")
    for field, value in changes.items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return _to_detail(db, project)


@router.delete("/{project_id}", status_code=204, response_model=None)
def delete_project(project: Project = Depends(get_project_or_404), db: Session = Depends(get_db)) -> None:
    slide_ids = [row[0] for row in db.query(Slide.id).filter(Slide.project_id == project.id)]
    config_ids = [row[0] for row in db.query(ProjectConfigVersion.id).filter(ProjectConfigVersion.project_id == project.id)]

    for slide_id in slide_ids:
        reader_cache.invalidate(slide_id)
        purge_slide_tiles(slide_id)

    # Delete in explicit dependency order. Project.config_versions and
    # Project.slides both cascade via SQLAlchemy relationships, but
    # Patch.config_version_id, GeometryAnnotation.config_version_id, and
    # Slide.active_config_version_id are plain FK columns (no relationship
    # mapped the other way), so the ORM's automatic cascade doesn't know to
    # clear them before deleting project_config_versions -- SQLite's
    # foreign_keys=ON then rejects the delete. Do it by hand instead of
    # relying on cascade ordering.
    if slide_ids:
        db.query(GeometryAnnotation).filter(GeometryAnnotation.slide_id.in_(slide_ids)).delete(synchronize_session=False)
        db.query(Patch).filter(Patch.slide_id.in_(slide_ids)).delete(synchronize_session=False)
        db.query(Slide).filter(Slide.id.in_(slide_ids)).update(
            {Slide.active_config_version_id: None}, synchronize_session=False
        )
        db.query(Slide).filter(Slide.id.in_(slide_ids)).delete(synchronize_session=False)
    if config_ids:
        db.query(AnnotationClass).filter(AnnotationClass.config_version_id.in_(config_ids)).delete(
            synchronize_session=False
        )
        db.query(ProjectConfigVersion).filter(ProjectConfigVersion.id.in_(config_ids)).update(
            {ProjectConfigVersion.parent_version_id: None}, synchronize_session=False
        )
    project.active_config_version_id = None
    db.flush()
    if config_ids:
        db.query(ProjectConfigVersion).filter(ProjectConfigVersion.id.in_(config_ids)).delete(synchronize_session=False)

    project_dir = project_storage.project_dir(get_settings().wsi_storage_dir, project.id)
    db.delete(project)
    db.commit()

    # Only once the rows are gone: a failed commit must not leave a project
    # whose files were already deleted. This removes every slide/image file and
    # tissue mask the project stored (they all live in its directory).
    project_storage.remove_tree(project_dir)


def _to_detail(db: Session, project: Project) -> ProjectDetailOut:
    slide_count = db.query(func.count(Slide.id)).filter(Slide.project_id == project.id).scalar() or 0
    processed = (
        db.query(func.count(Slide.id))
        .filter(Slide.project_id == project.id, Slide.status.in_(["patches_generated", "annotating", "reviewed"]))
        .scalar()
        or 0
    )
    # Count each slide's patches under its *active* config version only, so a
    # forked version doesn't double-count the slide's older patches.
    def patch_count(*criteria) -> int:
        return (
            db.query(func.count(Patch.id))
            .join(Slide, Patch.slide_id == Slide.id)
            .filter(
                Slide.project_id == project.id,
                Patch.config_version_id == Slide.active_config_version_id,
                or_(Slide.active_grid_key.is_(None), Patch.grid_key == Slide.active_grid_key),
                *criteria,
            )
            .scalar()
            or 0
        )

    total_patches = patch_count()
    annotated = patch_count(Patch.status.in_(["annotated", "reviewed"]))
    reviewed = patch_count(Patch.status == "reviewed")
    flagged = patch_count(Patch.flagged.is_(True))
    tissue_area = (
        db.query(func.coalesce(func.sum(Slide.tissue_area_mm2), 0.0)).filter(Slide.project_id == project.id).scalar()
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
