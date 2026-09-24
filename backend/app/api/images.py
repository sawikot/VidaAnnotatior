from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.database.session import get_db
from app.models.annotation import GeometryAnnotation
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide

router = APIRouter(tags=["images"])


class ImageSummary(BaseModel):
    """One image of an image project, with the state of its single patch."""

    slide_id: int
    patch_id: int
    filename: str
    width: int
    height: int
    status: str
    unsure: bool
    flagged: bool
    excluded: bool
    annotation_count: int


class ImageListResponse(BaseModel):
    total: int
    items: list[ImageSummary]


@router.get("/projects/{project_id}/images", response_model=ImageListResponse)
def list_images(
    status: str | None = Query(None, description="Comma-separated patch statuses to keep"),
    limit: int = Query(20_000, ge=1, le=50_000),
    offset: int = Query(0, ge=0),
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
) -> ImageListResponse:
    """Every image of an image project in a stable order (import order), each with
    its annotation state. Deliberately light -- no pixels -- so the workspace can hold
    the whole list and move between images without a request per step."""
    if project.project_type != "image":
        raise HTTPException(status_code=409, detail="This is a WSI project; use the slide and patch endpoints.")

    counts = (
        db.query(GeometryAnnotation.patch_id.label("patch_id"), func.count(GeometryAnnotation.id).label("n"))
        .group_by(GeometryAnnotation.patch_id)
        .subquery()
    )
    query = (
        db.query(Slide, Patch, func.coalesce(counts.c.n, 0))
        .join(
            Patch,
            and_(
                Patch.slide_id == Slide.id,
                Patch.config_version_id == Slide.active_config_version_id,
                or_(Slide.active_grid_key.is_(None), Patch.grid_key == Slide.active_grid_key),
            ),
        )
        .outerjoin(counts, counts.c.patch_id == Patch.id)
        .filter(Slide.project_id == project.id)
    )
    if status:
        query = query.filter(Patch.status.in_([s.strip() for s in status.split(",") if s.strip()]))

    total = query.count()
    rows = query.order_by(Slide.id.asc()).offset(offset).limit(limit).all()
    return ImageListResponse(
        total=total,
        items=[
            ImageSummary(
                slide_id=slide.id,
                patch_id=patch.id,
                filename=slide.filename,
                width=patch.width,
                height=patch.height,
                status=patch.status,
                unsure=patch.unsure,
                flagged=patch.flagged,
                excluded=patch.excluded,
                annotation_count=n,
            )
            for slide, patch, n in rows
        ],
    )
