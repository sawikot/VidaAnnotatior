from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.api.deps import get_patch_or_404, get_slide_or_404
from app.database.session import get_db
from app.models.patch import Patch
from app.models.slide import Slide
from app.services.patch_labels import set_patch_label
from app.services.patch_grid import active_grid_filter
from app.schemas.patch import PatchListResponse, PatchOut, PatchUpdate

router = APIRouter(tags=["patches"])


def _slide_patches(db: Session, slide: Slide):
    """A slide's patches in its *active* config version and grid only -- its other grids' patches stay in
    the DB (with their annotations, which every grid shows) but are not listed."""
    return active_grid_filter(db.query(Patch).filter(Patch.slide_id == slide.id), slide)


@router.get("/slides/{slide_id}/patches", response_model=PatchListResponse)
def list_patches(
    slide: Slide = Depends(get_slide_or_404),
    db: Session = Depends(get_db),
    bbox: str | None = Query(None, description="x0,y0,x1,y1 in Level-0 pixels; viewport filter"),
    status: str | None = Query(None, description="comma-separated status filter"),
    flagged: bool | None = None,
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    q = _slide_patches(db, slide)

    if bbox:
        try:
            x0, y0, x1, y1 = (float(v) for v in bbox.split(","))
        except ValueError:
            raise HTTPException(status_code=400, detail="bbox must be 'x0,y0,x1,y1'")
        q = q.filter(
            and_(
                Patch.x < x1,
                (Patch.x + Patch.width_l0) > x0,
                Patch.y < y1,
                (Patch.y + Patch.height_l0) > y0,
            )
        )

    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if statuses:
            q = q.filter(Patch.status.in_(statuses))

    if flagged is not None:
        q = q.filter(Patch.flagged.is_(flagged))

    total = q.count()
    items = q.order_by(Patch.patch_index.asc()).offset(offset).limit(limit).all()
    return PatchListResponse(total=total, items=items)


@router.get("/slides/{slide_id}/patches/next", response_model=PatchOut | None)
def next_patch(
    slide: Slide = Depends(get_slide_or_404),
    db: Session = Depends(get_db),
    current_index: int = Query(...),
    direction: str = Query("next", pattern="^(next|prev)$"),
    filter: str = Query("any", pattern="^(any|unannotated|flagged|skipped)$"),
):
    q = _slide_patches(db, slide)
    if filter == "unannotated":
        q = q.filter(Patch.status == "unannotated")
    elif filter == "flagged":
        q = q.filter(Patch.flagged.is_(True))
    elif filter == "skipped":
        q = q.filter(Patch.status == "skipped")

    if direction == "next":
        q = q.filter(Patch.patch_index > current_index).order_by(Patch.patch_index.asc())
    else:
        q = q.filter(Patch.patch_index < current_index).order_by(Patch.patch_index.desc())

    return q.first()


@router.get("/patches/{patch_id}", response_model=PatchOut)
def get_patch(patch: Patch = Depends(get_patch_or_404)):
    return patch


@router.put("/patches/{patch_id}", response_model=PatchOut)
def update_patch(
    payload: PatchUpdate,
    patch: Patch = Depends(get_patch_or_404),
    db: Session = Depends(get_db),
):
    changes = payload.model_dump(exclude_unset=True)
    if "patch_label" in changes:
        # A class label also annotates the whole patch with that class (services/patch_labels.py).
        set_patch_label(db, patch, changes.pop("patch_label"), created_by=changes.get("reviewed_by"))
    for field, value in changes.items():
        setattr(patch, field, value)
    if "reviewed_by" in changes and changes["reviewed_by"]:
        from app.database.base import utcnow

        patch.reviewed_at = utcnow()
        if patch.status != "reviewed":
            patch.status = "reviewed"
    db.commit()
    db.refresh(patch)
    return patch
