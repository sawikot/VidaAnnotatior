from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.database.session import get_db
from app.models.project import Project
from app.schemas.project import SplitSettings
from app.services import dataset_split

router = APIRouter(tags=["splits"])

SplitName = Literal["train", "val", "test"]


class SplitUpdate(SplitSettings):
    # Random mode: deal every slide out again (changing the shares does that by itself).
    reshuffle: bool = False
    # Manual mode: slide id -> its set, or null to take it out of every set. Slides not named keep theirs.
    assignments: dict[int, SplitName | None] | None = None


class SplitSlideOut(BaseModel):
    slide_id: int
    filename: str
    split: SplitName | None


class SplitOut(SplitSettings):
    slides: list[SplitSlideOut]
    counts: dict[str, int]


def _out(project: Project) -> SplitOut:
    slides = sorted(project.slides, key=lambda s: s.id)
    config = project.split_config or {"mode": "off", **dataset_split.DEFAULT_SHARES}
    return SplitOut(
        mode=config["mode"],
        train=config["train"],
        val=config["val"],
        test=config["test"],
        slides=[
            SplitSlideOut(slide_id=s.id, filename=s.filename, split=s.split if s.split in dataset_split.SPLITS else None)
            for s in slides
        ],
        counts=dataset_split.counts(slides),
    )


@router.get("/projects/{project_id}/split", response_model=SplitOut)
def get_split(project: Project = Depends(get_project_or_404), db: Session = Depends(get_db)) -> SplitOut:
    """How the project's slides are split into train / validation / test, and where each slide is."""
    if dataset_split.fill(project, list(project.slides)):  # slides added since the last random deal
        db.commit()
    return _out(project)


@router.put("/projects/{project_id}/split", response_model=SplitOut)
def set_split(payload: SplitUpdate, project: Project = Depends(get_project_or_404), db: Session = Depends(get_db)) -> SplitOut:
    """Turn the split on or off, change how it is made, deal the slides out again, or assign them by hand.

    Turning the split off keeps each slide's set, so turning it back on (or to manual) starts from there."""
    before = project.split_config or {}
    try:
        config = dataset_split.make_config(payload.mode, payload.train, payload.val, payload.test, seed=before.get("seed"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    slides = list(project.slides)
    project.split_config = config

    if payload.mode == "manual" and payload.assignments:
        by_id = {s.id: s for s in slides}
        unknown = [i for i in payload.assignments if i not in by_id]
        if unknown:
            raise HTTPException(status_code=422, detail=f"Not slides of this project: {', '.join(map(str, unknown))}")
        for slide_id, name in payload.assignments.items():
            by_id[slide_id].split = name
    elif payload.mode == "random":
        shares_changed = any(before.get(name) != config[name] for name in dataset_split.SPLITS)
        if payload.reshuffle or before.get("mode") != "random" or shares_changed:
            dataset_split.reshuffle(project, slides)
        else:
            dataset_split.fill(project, slides)

    db.commit()
    db.refresh(project)
    return _out(project)
