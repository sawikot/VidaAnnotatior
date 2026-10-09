"""A project's trained models, and what they suggest in the annotation workspace.

A suggestion is a shape a model proposes in a patch. It stays apart from the annotations -- drawn
differently, never exported -- until a person accepts it, which turns it into an ordinary annotation
recorded as made by that model and accepted by that person.
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from app.api import training
from app.api.access import current_user
from app.api.annotations import _origin_for_patch
from app.api.deps import get_patch_or_404, get_project_or_404, get_slide_or_404
from app.core.config import get_settings
from app.database.session import get_db
from app.models.annotation import GeometryAnnotation
from app.models.config_version import AnnotationClass
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide
from app.models.training import Suggestion, TrainedModel
from app.models.user import User
from app.schemas.annotation import GeometryAnnotationOut
from app.services import prediction_queue, reader_cache, recipes
from app.services.coordinate_transform import polygon_level0_to_patch_local, polygon_patch_local_to_level0
from app.services.exporter.patch_images import encode, render_patch
from app.services.annotation_import import shape_bounds
from app.services.geometry import polygon_bounds
from app.services.patch_grid import active_grid_filter
from app.services.patch_labels import _fill, set_patch_label, whole_patch_rectangle

router = APIRouter(tags=["suggestions"])
log = logging.getLogger(__name__)

# Detections less sure than this are not kept at all; the workspace's slider filters above it.
MIN_SCORE = 0.05
# A detection overlapping an existing shape this much is that shape again, not something new.
SAME_OBJECT_IOU = 0.5
# The kind of suggestion that is no shape but a class for the whole patch (its Patch Label).
LABEL = "patch_label"


class ModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    run_id: int | None
    name: str
    task: str
    source: str = "run"
    classes: list
    class_map: dict | None = None
    score: dict | None


class SuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patch_id: int
    model_id: int
    class_id: int | None
    type: str
    coordinates_patch_local: list
    score: float
    status: str


@router.get("/projects/{project_id}/models", response_model=list[ModelOut])
def list_models(project: Project = Depends(get_project_or_404), db: Session = Depends(get_db)) -> list[TrainedModel]:
    """The models that can make suggestions in this project, newest first."""
    return db.query(TrainedModel).filter(TrainedModel.project_id == project.id).order_by(TrainedModel.id.desc()).all()


def _pending(db: Session, patch: Patch, model_id: int | None = None):
    query = db.query(Suggestion).filter(Suggestion.patch_id == patch.id, Suggestion.status == "pending")
    if model_id is not None:
        query = query.filter(Suggestion.model_id == model_id)
    return query.order_by(Suggestion.score.desc(), Suggestion.id)


@router.get("/patches/{patch_id}/suggestions", response_model=list[SuggestionOut])
def list_suggestions(patch: Patch = Depends(get_patch_or_404), db: Session = Depends(get_db)) -> list[Suggestion]:
    """The suggestions still waiting for a decision in this patch, the surest first."""
    return _pending(db, patch).all()


def _iou(a: tuple, b: tuple) -> float:
    overlap = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - overlap
    return overlap / union if union > 0 else 0.0


def _trainer_running() -> None:
    if time.time() - training._trainer["seen"] > training.TRAINER_TIMEOUT_S:
        raise HTTPException(status_code=503, detail="The trainer is not running, so models cannot be used right now.")


def _ask(job: prediction_queue.Job) -> list:
    """The trainer's answer to a prediction job, its failures put in words a person can act on."""
    try:
        return prediction_queue.wait(job)
    except TimeoutError:
        raise HTTPException(status_code=504, detail="The model took too long to answer. It may still be loading; try again in a moment.") from None
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"The model could not be used: {exc}") from exc


class SuggestRequest(BaseModel):
    model_id: int


def ask_model(slide: Slide, model: TrainedModel, patches: list[Patch]) -> list[list[dict]]:
    """What the model finds in each of these patches of one slide (one list per patch), asked of the
    trainer in a single request. Raises HTTPException in words a person can act on."""
    # The trainer cannot read slides: it is handed the patches as images, in the folder the two share.
    folder = training.training_dir() / "predict" / uuid.uuid4().hex
    folder.mkdir(parents=True)
    try:
        relative = folder.relative_to(training.training_dir()).as_posix()
        try:
            reader = reader_cache.get_reader_for_slide(slide)
            for i, patch in enumerate(patches):
                (folder / f"{i}.jpg").write_bytes(encode(render_patch(reader, patch).convert("RGB"), "jpg"))
        except Exception as exc:  # noqa: BLE001 - e.g. the slide file is gone from disk
            raise HTTPException(status_code=422, detail=f"The patch image could not be read: {exc}") from exc
        job = prediction_queue.submit({
            "model_id": model.id, "code": model.code_path, "weights": model.weights_path,
            "images": [f"{relative}/{i}.jpg" for i in range(len(patches))], "min_score": MIN_SCORE,
        })
        answers = _ask(job)
        if len(answers) != len(patches):
            raise HTTPException(status_code=502, detail="The model did not answer for every patch.")
        return answers
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def slide_level_bounds(db: Session, slide_id: int) -> list[tuple]:
    """Where the annotations drawn on the whole slide are (Level-0 bounds): they are annotated objects
    in every patch they reach, though they belong to none."""
    rows = db.query(GeometryAnnotation).filter(GeometryAnnotation.slide_id == slide_id, GeometryAnnotation.patch_id.is_(None))
    return [shape_bounds(a.type, a.coordinates_level0) for a in rows if a.coordinates_level0]


def store_suggestions(
    db: Session, patch: Patch, model: TrainedModel, found_here: list[dict], known: set[int], names: dict[int, str], on_slide: list[tuple] | None = None
) -> int:
    """Keep what a model found in a patch as suggestions, replacing its earlier undecided ones there.
    Left out: what is already annotated (in the patch, or on the whole slide where it reaches this
    patch -- pass ``on_slide`` when storing for many patches), what was rejected before, classes the project does not have,
    and the model's own repeats of one object. Not committed. Returns how many were kept.

    A detection is a box ({"box": [x0, y0, x1, y1]}), an outline ({"polygon": [[x, y], ...]}) or, with
    neither, a class for the whole patch -- a suggested patch label."""
    taken = [shape_bounds(a.type, a.coordinates_patch_local) for a in db.query(GeometryAnnotation).filter(GeometryAnnotation.patch_id == patch.id) if a.coordinates_patch_local and not a.whole_patch]
    origin = _origin_for_patch(patch)
    right, bottom = patch.x + patch.width_l0, patch.y + patch.height_l0
    for x0, y0, x1, y1 in slide_level_bounds(db, patch.slide_id) if on_slide is None else on_slide:
        if x1 >= patch.x and x0 <= right and y1 >= patch.y and y0 <= bottom:
            (ax, ay), (bx, by) = polygon_level0_to_patch_local(origin, [[x0, y0], [x1, y1]])
            taken.append((ax, ay, bx, by))
    rejected = db.query(Suggestion).filter(Suggestion.patch_id == patch.id, Suggestion.model_id == model.id, Suggestion.status == "rejected").all()
    taken += [polygon_bounds(s.coordinates_patch_local) for s in rejected if s.type != LABEL]
    refused_labels = {s.class_id for s in rejected if s.type == LABEL}
    _pending(db, patch, model.id).order_by(None).delete(synchronize_session=False)

    width, height = float(patch.width), float(patch.height)
    kept, labelled = 0, False
    for found in sorted((d for d in found_here if isinstance(d, dict)), key=lambda d: -float(d.get("score") or 0)):
        try:
            score = float(found["score"])
            # A model added from elsewhere names its own classes; the map says which of the project's each is.
            class_id = found.get("class_id") if model.class_map is None else model.class_map.get(str(found.get("class")))
            if score < MIN_SCORE or class_id not in known:
                continue
            if "polygon" in found:
                kind = "polygon"
                local = [[min(width, max(0.0, float(x))), min(height, max(0.0, float(y)))] for x, y in found["polygon"]]
                if len(local) < 3:
                    continue
            elif "box" in found:
                kind = "rectangle"
                x0, y0, x1, y1 = (float(v) for v in found["box"])
                x0, x1 = max(0.0, min(x0, x1)), min(width, max(x0, x1))
                y0, y1 = max(0.0, min(y0, y1)), min(height, max(y0, y1))
                local = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
            else:
                # A label for the whole patch: only the surest, and not the one it already has or was refused.
                if labelled or class_id in refused_labels or (patch.patch_label or "").strip().lower() == names.get(class_id, "").strip().lower():
                    labelled = True
                    continue
                labelled = True
                local, level0 = whole_patch_rectangle(patch)
                db.add(Suggestion(patch_id=patch.id, slide_id=patch.slide_id, model_id=model.id, class_id=class_id, type=LABEL,
                                  coordinates_patch_local=local, coordinates_level0=level0, score=round(score, 4)))
                kept += 1
                continue
        except (KeyError, TypeError, ValueError):
            continue  # a malformed detection must not lose the others
        box = polygon_bounds(local)
        if box[2] - box[0] < 1 or box[3] - box[1] < 1 or any(_iou(box, other) >= SAME_OBJECT_IOU for other in taken):
            continue
        taken.append(box)  # the model's own duplicates of one object collapse into the surest
        db.add(Suggestion(
            patch_id=patch.id, slide_id=patch.slide_id, model_id=model.id, class_id=class_id, type=kind,
            coordinates_patch_local=local, coordinates_level0=polygon_patch_local_to_level0(origin, local), score=round(score, 4),
        ))
        kept += 1
    return kept


def project_classes_of(db: Session, config_version_id: int) -> tuple[set[int], dict[int, str]]:
    rows = db.query(AnnotationClass).filter(AnnotationClass.config_version_id == config_version_id).all()
    return {c.id for c in rows}, {c.id: c.name for c in rows}


def model_of(db: Session, model_id: int, slide: Slide) -> TrainedModel:
    model = db.get(TrainedModel, model_id)
    if model is None or model.project_id != slide.project_id:
        raise HTTPException(status_code=404, detail="That model does not belong to this project.")
    return model


@router.post("/patches/{patch_id}/suggest", response_model=list[SuggestionOut])
def suggest(payload: SuggestRequest, patch: Patch = Depends(get_patch_or_404), db: Session = Depends(get_db)) -> list[Suggestion]:
    """Ask a model what it sees in this patch. Its earlier undecided suggestions here are replaced;
    whatever it finds that is already annotated, or was rejected before, is left out."""
    slide = db.get(Slide, patch.slide_id)
    model = model_of(db, payload.model_id, slide)
    _trainer_running()
    found = ask_model(slide, model, [patch])[0]
    known, names = project_classes_of(db, patch.config_version_id)
    store_suggestions(db, patch, model, found, known, names)
    db.commit()
    return _pending(db, patch).all()


def _accept(db: Session, suggestion: Suggestion, patch: Patch, user: User, model_names: dict[int, str]) -> GeometryAnnotation:
    if suggestion.model_id not in model_names:
        model = db.get(TrainedModel, suggestion.model_id)
        model_names[suggestion.model_id] = model.name if model else "a model"
    by = f"{user.name} (suggested by {model_names[suggestion.model_id]})"[:120]
    if suggestion.type == LABEL:
        # Accepting a label is labelling the patch, which fills it with that class (services/patch_labels.py).
        cls = db.get(AnnotationClass, suggestion.class_id) if suggestion.class_id else None
        if cls is None:
            raise HTTPException(status_code=409, detail="The class this label names no longer exists.")
        set_patch_label(db, patch, cls.name, created_by=by, created_by_id=user.id)
        annotation = _fill(db, patch)
        suggestion.status, suggestion.annotation_id, suggestion.decided_by_id = "accepted", annotation.id if annotation else None, user.id
        return annotation
    annotation = GeometryAnnotation(
        patch_id=patch.id, slide_id=patch.slide_id, config_version_id=patch.config_version_id, class_id=suggestion.class_id,
        type=suggestion.type, coordinates_patch_local=suggestion.coordinates_patch_local, coordinates_level0=suggestion.coordinates_level0,
        # Who is answerable for it: the person who accepted it. What proposed it is kept alongside.
        created_by=by, created_by_id=user.id,
    )
    db.add(annotation)
    db.flush()
    suggestion.status, suggestion.annotation_id, suggestion.decided_by_id = "accepted", annotation.id, user.id
    if patch.status == "unannotated":
        patch.status = "annotated"
    return annotation


def _open_suggestion(suggestion_id: int, db: Session) -> tuple[Suggestion, Patch]:
    suggestion = db.get(Suggestion, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail=f"Suggestion {suggestion_id} not found")
    if suggestion.status != "pending":
        raise HTTPException(status_code=409, detail=f"This suggestion was already {suggestion.status}.")
    return suggestion, db.get(Patch, suggestion.patch_id)


@router.post("/suggestions/{suggestion_id}/accept", response_model=GeometryAnnotationOut)
def accept_suggestion(suggestion_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> GeometryAnnotation:
    """Make an annotation of the suggestion."""
    suggestion, patch = _open_suggestion(suggestion_id, db)
    annotation = _accept(db, suggestion, patch, user, {})
    db.commit()
    db.refresh(annotation)
    return annotation


@router.post("/suggestions/{suggestion_id}/reject", status_code=204, response_model=None)
def reject_suggestion(suggestion_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> None:
    """Turn the suggestion down; the model does not propose the same object here again."""
    suggestion, _ = _open_suggestion(suggestion_id, db)
    suggestion.status, suggestion.decided_by_id = "rejected", user.id
    db.commit()


class AcceptMany(BaseModel):
    min_score: float = Field(default=0.5, ge=0, le=1)
    model_id: int | None = None


@router.post("/patches/{patch_id}/suggestions/accept", response_model=list[GeometryAnnotationOut])
def accept_suggestions(
    payload: AcceptMany, patch: Patch = Depends(get_patch_or_404), db: Session = Depends(get_db), user: User = Depends(current_user)
) -> list[GeometryAnnotation]:
    """Accept every waiting suggestion in the patch that is at least this sure."""
    names: dict[int, str] = {}
    made = [_accept(db, s, patch, user, names) for s in _pending(db, patch, payload.model_id).filter(Suggestion.score >= payload.min_score).all()]
    db.commit()
    for annotation in made:
        db.refresh(annotation)
    return made


@router.delete("/patches/{patch_id}/suggestions", status_code=204, response_model=None)
def clear_suggestions(patch: Patch = Depends(get_patch_or_404), db: Session = Depends(get_db)) -> None:
    """Drop the patch's waiting suggestions without deciding on them (they may be suggested again)."""
    _pending(db, patch).order_by(None).delete(synchronize_session=False)
    db.commit()


# ------------------------------------------------------------------- a whole slide at a time

# How many patches are sent to the model in one request.
BATCH = 4


@dataclass
class SlideJob:
    """A model working through a slide's patches in the background. Kept in memory: if the app
    restarts, the suggestions made so far are there and the rest is simply asked for again."""

    slide_id: int
    model_id: int
    total: int
    status: str = "running"  # running | done | failed | stopped
    done: int = 0  # patches looked at
    found: int = 0  # suggestions kept
    error: str | None = None
    stop: bool = False


_slide_jobs: dict[int, SlideJob] = {}
_slide_jobs_lock = threading.Lock()


class SlideSuggestRequest(BaseModel):
    model_id: int
    # "unannotated": only patches nobody has annotated yet (the work still to do). "all": every patch.
    scope: Literal["unannotated", "all"] = "unannotated"


class SlideSuggestionOut(SuggestionOut):
    coordinates_level0: list


def _slide_state(db: Session, slide: Slide) -> dict:
    job = _slide_jobs.get(slide.id)
    pending = db.query(Suggestion).filter(Suggestion.slide_id == slide.id, Suggestion.status == "pending")
    return {
        "job": None if job is None else {"model_id": job.model_id, "status": job.status, "done": job.done, "total": job.total, "found": job.found, "error": job.error},
        "pending": pending.count(),
        "patches": pending.with_entities(Suggestion.patch_id).distinct().count(),
    }


def _work_through(job: SlideJob, patch_ids: list[int], make_session) -> None:
    db: Session = make_session()
    try:
        slide = db.get(Slide, job.slide_id)
        model = db.get(TrainedModel, job.model_id)
        known, names = project_classes_of(db, slide.active_config_version_id)
        on_slide = slide_level_bounds(db, slide.id)
        for start in range(0, len(patch_ids), BATCH):
            if job.stop:
                job.status = "stopped"
                return
            patches = [p for p in (db.get(Patch, i) for i in patch_ids[start:start + BATCH]) if p is not None]
            if patches:
                for patch, found in zip(patches, ask_model(slide, model, patches)):
                    job.found += store_suggestions(db, patch, model, found, known, names, on_slide)
                db.commit()
            job.done = min(job.total, start + BATCH)
        job.status = "done"
    except HTTPException as exc:
        job.status, job.error = "failed", str(exc.detail)
    except Exception as exc:  # noqa: BLE001 - the person watching must be told, whatever it was
        log.exception("Suggesting on slide %s failed", job.slide_id)
        job.status, job.error = "failed", str(exc)
    finally:
        db.close()


@router.post("/slides/{slide_id}/suggest")
def suggest_on_slide(payload: SlideSuggestRequest, slide: Slide = Depends(get_slide_or_404), db: Session = Depends(get_db)) -> dict:
    """Have a model go through the slide's patches in the background, leaving suggestions in each."""
    model_of(db, payload.model_id, slide)
    _trainer_running()
    query = active_grid_filter(db.query(Patch.id).filter(Patch.slide_id == slide.id, Patch.excluded.is_(False)), slide)
    if payload.scope == "unannotated":
        query = query.filter(Patch.status == "unannotated")
    patch_ids = [row[0] for row in query.order_by(Patch.patch_index)]
    if not patch_ids:
        raise HTTPException(status_code=422, detail="There are no patches to look at: every patch is already annotated." if payload.scope == "unannotated" else "This slide has no patches.")
    with _slide_jobs_lock:
        running = _slide_jobs.get(slide.id)
        if running is not None and running.status == "running":
            raise HTTPException(status_code=409, detail="A model is already working through this slide.")
        job = _slide_jobs[slide.id] = SlideJob(slide_id=slide.id, model_id=payload.model_id, total=len(patch_ids))
    make_session = sessionmaker(bind=db.get_bind())  # a session of its own, on the same database
    threading.Thread(target=_work_through, args=(job, patch_ids, make_session), daemon=True).start()
    return _slide_state(db, slide)


@router.get("/slides/{slide_id}/suggest")
def slide_suggest_state(slide: Slide = Depends(get_slide_or_404), db: Session = Depends(get_db)) -> dict:
    """How far a model has got through the slide, and how many suggestions are waiting on it."""
    return _slide_state(db, slide)


@router.delete("/slides/{slide_id}/suggest")
def stop_slide_suggest(slide: Slide = Depends(get_slide_or_404), db: Session = Depends(get_db)) -> dict:
    """Stop the model after the patches it is looking at now; what it has suggested so far stays."""
    job = _slide_jobs.get(slide.id)
    if job is not None and job.status == "running":
        job.stop = True
    return _slide_state(db, slide)


def _slide_pending(db: Session, slide: Slide):
    return db.query(Suggestion).filter(Suggestion.slide_id == slide.id, Suggestion.status == "pending")


@router.get("/slides/{slide_id}/suggestions", response_model=list[SlideSuggestionOut])
def list_slide_suggestions(limit: int = Query(5000, ge=1, le=20000), slide: Slide = Depends(get_slide_or_404), db: Session = Depends(get_db)) -> list[Suggestion]:
    """The suggestions waiting anywhere on the slide, in Level-0 pixels, the surest first."""
    return _slide_pending(db, slide).order_by(Suggestion.score.desc(), Suggestion.id).limit(limit).all()


@router.get("/slides/{slide_id}/suggestions/next")
def next_patch_with_suggestions(after: int = Query(-1, description="the patch_index to continue from"), slide: Slide = Depends(get_slide_or_404), db: Session = Depends(get_db)) -> dict:
    """The next patch (by its number, wrapping round to the first) that has suggestions waiting."""
    waiting = db.query(Patch.id, Patch.patch_index).join(Suggestion, Suggestion.patch_id == Patch.id).filter(Patch.slide_id == slide.id, Suggestion.status == "pending").distinct()
    row = waiting.filter(Patch.patch_index > after).order_by(Patch.patch_index).first() or waiting.order_by(Patch.patch_index).first()
    return {"patch_id": row[0] if row else None}


@router.post("/slides/{slide_id}/suggestions/accept")
def accept_slide_suggestions(payload: AcceptMany, slide: Slide = Depends(get_slide_or_404), db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """Accept every waiting suggestion on the slide that is at least this sure."""
    query = _slide_pending(db, slide).filter(Suggestion.score >= payload.min_score)
    if payload.model_id is not None:
        query = query.filter(Suggestion.model_id == payload.model_id)
    names: dict[int, str] = {}
    patches: dict[int, Patch] = {}
    accepted = 0
    for suggestion in query.order_by(Suggestion.id).all():
        patch = patches.get(suggestion.patch_id) or patches.setdefault(suggestion.patch_id, db.get(Patch, suggestion.patch_id))
        _accept(db, suggestion, patch, user, names)
        accepted += 1
    db.commit()
    return {"accepted": accepted, **_slide_state(db, slide)}


@router.delete("/slides/{slide_id}/suggestions")
def clear_slide_suggestions(slide: Slide = Depends(get_slide_or_404), db: Session = Depends(get_db)) -> dict:
    """Drop every waiting suggestion on the slide without deciding on them."""
    _slide_pending(db, slide).delete(synchronize_session=False)
    db.commit()
    return _slide_state(db, slide)


# ------------------------------------------------------------ models trained elsewhere, added as a file


def _project_classes(db: Session, project: Project) -> list[AnnotationClass]:
    return db.query(AnnotationClass).filter(AnnotationClass.config_version_id == project.active_config_version_id).order_by(AnnotationClass.order_index, AnnotationClass.id).all()


@router.post("/projects/{project_id}/models/import", response_model=ModelOut, status_code=201)
def import_model(
    file: UploadFile = File(...),
    importer_id: str = Form(..., description="which kind of file it is (GET /training/importers)"),
    name: str = Form(""),
    settings: str = Form("{}", description="the importer's settings, as JSON"),
    class_names: str = Form("", description="the model's classes in its own order, one per line -- needed when the file does not carry them"),
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
) -> TrainedModel:
    """Add a model that was trained outside the app. The trainer loads it once to make sure it can be
    used and to read its classes; each class is matched to the project's class of the same name, which
    can be corrected afterwards (PUT /models/{id})."""
    importer = recipes.get_recipe(importer_id)
    if importer is None or not importer["import"] or not importer["usable"]:
        raise HTTPException(status_code=422, detail=f"Unknown kind of model file: {importer_id}")
    extension = Path(file.filename or "").suffix.lower()
    allowed = importer["import"].get("extensions", [])
    if extension not in allowed:
        raise HTTPException(status_code=422, detail=f"{importer['name']} takes {', '.join(allowed)} files, not \"{extension or file.filename}\".")
    try:
        chosen = recipes.resolve_settings(importer, json.loads(settings or "{}"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _trainer_running()  # it is the trainer that can open the file

    relative = f"models/{uuid.uuid4().hex}"
    folder = training.training_dir() / relative
    try:
        shutil.copytree(recipes.recipe_path(importer_id), folder / "code", ignore=shutil.ignore_patterns("__pycache__", ".*"))
        limit = get_settings().max_model_bytes
        written = 0
        with (folder / f"model{extension}").open("wb") as target:
            while chunk := file.file.read(1 << 20):
                written += len(chunk)
                if written > limit:
                    raise HTTPException(status_code=413, detail=f"The file is larger than {limit // (1024 ** 3)} GB.")
                target.write(chunk)
        if written == 0:
            raise HTTPException(status_code=422, detail="The file is empty.")
        (folder / "config.json").write_text(json.dumps(chosen, indent=2), encoding="utf-8")

        # Loading it is the check: a file the importer cannot run is refused here, with the reason.
        job = prediction_queue.submit({"model_id": relative, "code": f"{relative}/code", "weights": f"{relative}/model{extension}", "images": [], "min_score": MIN_SCORE})
        _ask(job)
        info = job.info or {}
        typed = [line.strip() for line in class_names.splitlines() if line.strip()]
        names = typed or [str(n) for n in info.get("classes") or []]
        count = info.get("class_count")
        if count and names and len(names) != count:
            raise HTTPException(status_code=422, detail=f"The model has {count} classes, but {len(names)} names were given.")
        if not names and count:
            names = [f"Class {i + 1}" for i in range(int(count))]
        if not names:
            raise HTTPException(status_code=422, detail="This file does not say what its classes are. Type their names, one per line, in the model's own order.")
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise

    by_name = {c.name.strip().lower(): c.id for c in _project_classes(db, project)}
    model = TrainedModel(
        project_id=project.id, source="import", name=(name.strip() or Path(file.filename or "model").stem)[:200], task=importer["task"], recipe_id=importer_id,
        classes=[{"index": i, "name": n} for i, n in enumerate(names)],
        class_map={str(i): by_name.get(n.strip().lower()) for i, n in enumerate(names)},
        code_path=f"{relative}/code", weights_path=f"{relative}/model{extension}",
    )
    db.add(model)
    db.commit()
    db.refresh(model)
    return model


def get_model_or_404(model_id: int, db: Session = Depends(get_db)) -> TrainedModel:
    model = db.get(TrainedModel, model_id)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found")
    return model


class ModelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    # A model added from elsewhere: its class (by place, "0", "1", ...) -> the project's class id, or null to leave it out.
    class_map: dict[str, int | None] | None = None


@router.put("/models/{model_id}", response_model=ModelOut)
def update_model(payload: ModelUpdate, model: TrainedModel = Depends(get_model_or_404), db: Session = Depends(get_db)) -> TrainedModel:
    """Rename a model, or say which class of the project each of its own classes is."""
    if payload.name is not None:
        model.name = payload.name.strip()
    if payload.class_map is not None:
        if model.class_map is None:
            raise HTTPException(status_code=409, detail="A model trained in this project already uses the project's classes.")
        known = {c.id for c in _project_classes(db, db.get(Project, model.project_id))}
        places = {str(c["index"]) for c in model.classes}
        for place, class_id in payload.class_map.items():
            if place not in places:
                raise HTTPException(status_code=422, detail=f"The model has no class number {place}.")
            if class_id is not None and class_id not in known:
                raise HTTPException(status_code=422, detail=f"Class {class_id} is not a class of this project.")
        model.class_map = {place: payload.class_map.get(place) for place in sorted(places, key=int)}
    db.commit()
    db.refresh(model)
    return model


@router.delete("/models/{model_id}", status_code=204, response_model=None)
def delete_model(model: TrainedModel = Depends(get_model_or_404), db: Session = Depends(get_db)) -> None:
    """Remove a model from the project, with the suggestions still waiting from it. Annotations made by
    accepting its suggestions stay. A model added as a file is deleted; one from a training run stays
    with its run (delete the run to delete its files)."""
    folder = training.training_dir() / Path(model.code_path).parent if model.source == "import" else None
    db.delete(model)
    db.commit()
    if folder is not None:
        shutil.rmtree(folder, ignore_errors=True)


# -------------------------------------------------------------------------- what the trainer uses

trainer_router = APIRouter(prefix="/trainer", tags=["trainer"], dependencies=[Depends(training.require_trainer)])

# How long the trainer's question "any prediction to make?" is held open when there is none.
CLAIM_WAIT_S = 10  # well under the time after which a silent trainer is taken to be off


@trainer_router.post("/predictions/claim")
def claim_prediction() -> dict:
    """The next prediction to make; answers at once when one is waiting, else after a short wait."""
    training._seen(None)
    job = prediction_queue.claim(CLAIM_WAIT_S)
    training._seen(None)
    return {"job": job.payload if job else None}


class PredictionResult(BaseModel):
    results: list | None = None  # per image: [{"box" or "polygon" (or neither: a label), "class_id" or "class", "score"}]
    error: str | None = None
    info: dict | None = None  # what the model said about itself when it loaded


@trainer_router.post("/predictions/{job_id}/result")
def prediction_result(job_id: int, payload: PredictionResult) -> dict:
    return {"ok": prediction_queue.finish(job_id, payload.results, payload.error, payload.info)}
