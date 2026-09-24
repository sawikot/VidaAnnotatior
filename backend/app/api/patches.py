from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.api.deps import get_patch_or_404, get_slide_or_404
from app.database.session import get_db
from app.models.patch import Patch
from app.models.slide import Slide
from app.api.access import current_user
from app.models.user import User
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
    label: str | None = Query(None, description="patch label to match (any case); '-' for patches with no label"),
    sort: str = Query("index", description="index | tissue_desc | tissue_asc"),
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

    if label is not None:
        if label == NO_LABEL:
            q = q.filter(Patch.patch_label.is_(None))
        else:
            q = q.filter(func.lower(Patch.patch_label) == label.strip().lower())

    orders = {
        "index": [Patch.patch_index.asc()],
        "tissue_desc": [Patch.tissue_fraction.desc(), Patch.patch_index.asc()],
        "tissue_asc": [Patch.tissue_fraction.asc(), Patch.patch_index.asc()],
    }
    if sort not in orders:
        raise HTTPException(status_code=422, detail=f"sort must be one of: {', '.join(orders)}")

    total = q.count()
    items = q.order_by(*orders[sort]).offset(offset).limit(limit).all()
    return PatchListResponse(total=total, items=items)


NO_LABEL = "-"


class LabelCount(BaseModel):
    label: str | None  # None: patches with no label
    count: int


@router.get("/slides/{slide_id}/patches/label-counts", response_model=list[LabelCount])
def patch_label_counts(slide: Slide = Depends(get_slide_or_404), db: Session = Depends(get_db)) -> list[LabelCount]:
    """How many patches of the slide's current patch size carry each label (for the gallery's filter)."""
    rows = (
        _slide_patches(db, slide)
        .with_entities(Patch.patch_label, func.count(Patch.id))
        .group_by(Patch.patch_label)
        .all()
    )
    return sorted((LabelCount(label=l, count=n) for l, n in rows), key=lambda r: (r.label is None, (r.label or "").lower()))


class BatchLabelIn(BaseModel):
    patch_ids: list[int] = Field(min_length=1, max_length=5000)
    label: str | None = None  # None or "": remove the label


class BatchLabelOut(BaseModel):
    updated: int


@router.post("/slides/{slide_id}/patches/label", response_model=BatchLabelOut)
def label_patches(
    payload: BatchLabelIn,
    slide: Slide = Depends(get_slide_or_404),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> BatchLabelOut:
    """Give many patches of this slide the same label at once -- exactly as setting it on each one
    (a class label fills the patch; see services/patch_labels.py). Ids of other slides are refused."""
    ids = set(payload.patch_ids)
    patches = db.query(Patch).filter(Patch.slide_id == slide.id, Patch.id.in_(ids)).all()
    if len(patches) != len(ids):
        raise HTTPException(status_code=422, detail="Some of these patches are not on this slide")
    for patch in patches:
        set_patch_label(db, patch, payload.label, created_by=user.name, created_by_id=user.id)
    db.commit()
    return BatchLabelOut(updated=len(patches))


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
    user: User = Depends(current_user),
):
    changes = payload.model_dump(exclude_unset=True)
    if "patch_label" in changes:
        # A class label also annotates the whole patch with that class (services/patch_labels.py).
        set_patch_label(db, patch, changes.pop("patch_label"), created_by=user.name, created_by_id=user.id)
    # The reviewer is the signed-in person, whatever the client says.
    reviewing = bool(changes.pop("reviewed_by", None)) or changes.get("status") == "reviewed"
    for field, value in changes.items():
        setattr(patch, field, value)
    if reviewing:
        from app.database.base import utcnow

        patch.reviewed_by, patch.reviewed_by_id = user.name, user.id
        patch.reviewed_at = utcnow()
        if patch.status != "reviewed":
            patch.status = "reviewed"
    elif "status" in changes and patch.status != "reviewed":
        # A validation undone (or the patch skipped instead): it is no longer anyone's review.
        patch.reviewed_by = patch.reviewed_by_id = patch.reviewed_at = None
    db.commit()
    db.refresh(patch)
    return patch
