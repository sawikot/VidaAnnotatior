"""Training models on a project's annotations.

Two sides:

* ``router`` -- what people use: the recipes, whether a project is ready, starting / watching /
  stopping runs. Guarded like every other route (api/access.py).
* ``trainer_router`` -- what the trainer program uses (trainer/trainer.py): take the next run, report
  progress, finish. Guarded by a secret the two share through the training folder; the trainer never
  touches the database itself.
"""
from __future__ import annotations

import logging
import secrets
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.access import current_user
from app.api.deps import get_project_or_404
from app.core.config import get_settings
from app.database.session import get_db
from app.models.project import Project
from app.models.training import ACTIVE_STATUSES, TrainedModel, TrainingRun
from app.models.user import User
from app.services import recipes, training_data

router = APIRouter(tags=["training"])
trainer_router = APIRouter(prefix="/trainer", tags=["trainer"])
log = logging.getLogger(__name__)

# The trainer says hello every few seconds; without one for this long it is taken to be off.
TRAINER_TIMEOUT_S = 20
_trainer: dict = {"seen": 0.0, "hardware": None}
_claim_lock = threading.Lock()  # one run is handed out at a time


def training_dir() -> Path:
    path = get_settings().training_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_dir(run_id: int) -> Path:
    return training_dir() / "runs" / str(run_id)


def trainer_secret() -> str:
    """The secret the trainer must send, kept in the folder the two share (made on first use)."""
    file = training_dir() / ".trainer-secret"
    if not file.is_file() or not file.read_text().strip():
        file.write_text(secrets.token_urlsafe(32))
    return file.read_text().strip()


def require_trainer(x_trainer_secret: str = Header("")) -> None:
    if not secrets.compare_digest(x_trainer_secret, trainer_secret()):
        raise HTTPException(status_code=403, detail="Wrong or missing trainer secret.")


def get_run_or_404(run_id: int, db: Session = Depends(get_db)) -> TrainingRun:
    run = db.get(TrainingRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Training run {run_id} not found")
    return run


# ------------------------------------------------------------------------------ what people use


class RunOut(BaseModel):
    id: int
    project_id: int
    recipe_id: str
    recipe_name: str
    task: str
    settings: dict
    dataset_options: dict | None = None
    status: str
    stop_requested: bool
    error: str | None
    stage: str | None = None
    dataset: dict | None
    epoch: int
    epochs: int
    result: dict | None
    device: str | None
    created_by: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    has_model: bool
    metrics: list | None = None  # only on a single run; the list of runs leaves it out, and the result's figures too


def _headline(result: dict | None) -> dict | None:
    """A run's result without its figures and per-class table: all that a list of runs shows."""
    if not isinstance(result, dict):
        return result
    return {
        key: {k: v for k, v in value.items() if k not in ("figures", "classes")} if isinstance(value, dict) else value
        for key, value in result.items()
    }


def _out(run: TrainingRun, with_metrics: bool = False) -> RunOut:
    return RunOut(
        id=run.id, project_id=run.project_id, recipe_id=run.recipe_id, recipe_name=run.recipe_name, task=run.task,
        settings=run.settings or {}, dataset_options=run.dataset_options, status=run.status, stop_requested=run.stop_requested, error=run.error, stage=run.stage if run.status == "running" else None,
        dataset=run.dataset, epoch=run.epoch, epochs=run.epochs, result=run.result if with_metrics else _headline(run.result), device=run.device,
        created_by=run.created_by, created_at=run.created_at, started_at=run.started_at, finished_at=run.finished_at,
        has_model=(run_dir(run.id) / "output" / "model.pt").is_file(),
        metrics=list(run.metrics or []) if with_metrics else None,
    )


@router.get("/training/recipes")
def list_recipes() -> list[dict]:
    """The models that can be trained: the built-in ones, and your own that have passed their check."""
    return [r for r in recipes.list_recipes() if r["trainable"] and r["usable"]]


@router.get("/training/importers")
def list_importers() -> list[dict]:
    """The kinds of model file, trained elsewhere, that can be added to a project."""
    return [r for r in recipes.list_recipes() if r["import"] and r["usable"]]


@router.get("/training/status")
def trainer_status() -> dict:
    """Whether the trainer is running, and the hardware it found."""
    trainer_secret()  # a trainer started before the app finds its secret as soon as anyone looks
    online = time.time() - _trainer["seen"] < TRAINER_TIMEOUT_S
    return {"online": online, "hardware": _trainer["hardware"] if online else None}


@router.get("/projects/{project_id}/training/readiness")
def get_readiness(
    task: str = Query("detection"),
    patch_size: int | None = Query(None, description="cut the slides afresh at this patch size (px); left out: the patches as annotated"),
    stride: int | None = Query(None, description="with patch_size: how far apart patches start (default: the size)"),
    area: str = Query("tissue", description="with patch_size: tissue | whole (the whole slide)"),
    use: str = Query("annotated", description="annotated | reviewed (only patches marked Reviewed)"),
    empty_percent: float = Query(0, description="empty patches to add for every 100 annotated ones"),
    empty_from: str = Query("any", description="any | reviewed (only empty patches marked Reviewed)"),
    classes: str | None = Query(None, description="the class ids to learn, comma-separated (default: all)"),
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
) -> dict:
    """What a run with these dataset options would learn from, and what stands in the way of starting it."""
    if task not in recipes.TASKS:
        raise HTTPException(status_code=422, detail=f"task must be one of: {', '.join(recipes.TASKS)}")
    try:
        class_ids = [int(c) for c in classes.split(",") if c.strip()] if classes is not None else None
        opts = training_data.parse_options(
            {"patch_size": patch_size, "stride": stride, "area": area, "use": use, "empty_percent": empty_percent, "empty_from": empty_from, "class_ids": class_ids}, project
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return training_data.readiness(db, project, task, opts)


@router.get("/projects/{project_id}/training/runs", response_model=list[RunOut])
def list_runs(project: Project = Depends(get_project_or_404), db: Session = Depends(get_db)) -> list[RunOut]:
    runs = db.query(TrainingRun).filter(TrainingRun.project_id == project.id).order_by(TrainingRun.id.desc()).all()
    return [_out(run) for run in runs]


class RunCreate(BaseModel):
    recipe_id: str
    settings: dict = {}
    # How the dataset is cut and which patches go into it (services/training_data.DatasetOptions).
    dataset: dict = {}


@router.post("/projects/{project_id}/training/runs", response_model=RunOut, status_code=201)
def start_run(
    payload: RunCreate,
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> RunOut:
    """Queue a training run; the trainer picks it up. Refused while the project is not ready."""
    recipe = recipes.get_recipe(payload.recipe_id)
    if recipe is None or not recipe["trainable"]:
        raise HTTPException(status_code=422, detail=f"Unknown model recipe: {payload.recipe_id}")
    if not recipe["usable"]:
        raise HTTPException(status_code=422, detail=f"\"{recipe['name']}\" has not passed its check as it is now. An administrator runs the check on the Model recipes page.")
    try:
        settings = recipes.resolve_settings(recipe, payload.settings)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        opts = training_data.parse_options(payload.dataset, project)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    problems = training_data.readiness(db, project, recipe["task"], opts)["problems"]
    if problems:
        raise HTTPException(status_code=422, detail=problems[0])

    run = TrainingRun(
        project_id=project.id, recipe_id=recipe["id"], recipe_name=recipe["name"], task=recipe["task"], settings=settings,
        dataset_options=opts.as_dict(),
        status="queued", epochs=int(settings.get("epochs", 0) or 0), created_by=user.name, created_by_id=user.id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return _out(run)


@router.get("/training/runs/{run_id}", response_model=RunOut)
def get_run(run: TrainingRun = Depends(get_run_or_404)) -> RunOut:
    return _out(run, with_metrics=True)


@router.post("/training/runs/{run_id}/stop", response_model=RunOut)
def stop_run(run: TrainingRun = Depends(get_run_or_404), db: Session = Depends(get_db)) -> RunOut:
    """Stop a run: one still waiting ends at once, one in progress when the trainer next reports."""
    if run.status == "queued":
        run.status, run.finished_at = "stopped", datetime.utcnow()
    elif run.status in ACTIVE_STATUSES:
        run.stop_requested = True
    db.commit()
    return _out(run, with_metrics=True)


@router.delete("/training/runs/{run_id}", status_code=204, response_model=None)
def delete_run(run: TrainingRun = Depends(get_run_or_404), db: Session = Depends(get_db)) -> None:
    """Remove a finished run with its files (log, trained model)."""
    if run.status in ("preparing", "running"):
        raise HTTPException(status_code=409, detail="Stop the run before deleting it.")
    folder = run_dir(run.id)
    db.delete(run)
    db.commit()
    shutil.rmtree(folder, ignore_errors=True)


@router.get("/training/runs/{run_id}/log", response_class=PlainTextResponse)
def get_log(tail: int = Query(200, ge=1, le=5000), run: TrainingRun = Depends(get_run_or_404)) -> str:
    """The last lines the recipe printed."""
    file = run_dir(run.id) / "output" / "train.log"
    if not file.is_file():
        return ""
    with file.open("rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 400 * tail))
        lines = f.read().decode("utf-8", errors="replace").splitlines()
    return "\n".join(lines[-tail:])


@router.get("/training/runs/{run_id}/model")
def download_model(run: TrainingRun = Depends(get_run_or_404)) -> FileResponse:
    file = run_dir(run.id) / "output" / "model.pt"
    if not file.is_file():
        raise HTTPException(status_code=404, detail="This run has no trained model.")
    return FileResponse(file, filename=f"run{run.id}_{run.recipe_id}.pt", media_type="application/octet-stream")


# -------------------------------------------------------------------------- what the trainer uses


class Hello(BaseModel):
    hardware: dict | None = None
    # Sent once when the trainer starts: whatever it was running before did not survive.
    fresh: bool = False


def _seen(hardware: dict | None) -> None:
    _trainer["seen"] = time.time()
    if hardware is not None:
        _trainer["hardware"] = hardware


@trainer_router.post("/hello", dependencies=[Depends(require_trainer)])
def trainer_hello(payload: Hello, db: Session = Depends(get_db)) -> dict:
    _seen(payload.hardware)
    if payload.fresh:
        lost = db.query(TrainingRun).filter(TrainingRun.status.in_(("preparing", "running"))).all()
        for run in lost:
            run.status, run.finished_at = "failed", datetime.utcnow()
            run.error = "The trainer was restarted while this run was in progress."
        db.commit()
    return {"ok": True}


@trainer_router.post("/claim", dependencies=[Depends(require_trainer)])
def trainer_claim(payload: Hello, db: Session = Depends(get_db)) -> dict:
    """Hand the oldest waiting run to the trainer, with its dataset cut into the run's folder. This
    answers only once the dataset is written, which takes as long as cutting the patch images does."""
    _seen(payload.hardware)
    with _claim_lock:
        run = db.query(TrainingRun).filter(TrainingRun.status == "queued").order_by(TrainingRun.id).first()
        if run is None:
            from app.api.recipes import next_check  # (that module builds on this one)

            return {"run": None, "check": next_check()}
        run.status, run.started_at = "preparing", datetime.utcnow()
        db.commit()

    folder = run_dir(run.id)
    recipe = recipes.get_recipe(run.recipe_id)
    try:
        if recipe is None:
            raise ValueError("The model recipe was deleted before the run started.")
        project = db.get(Project, run.project_id)
        opts = training_data.parse_options(run.dataset_options, project)
        run.dataset = training_data.build_dataset(db, project, folder, run.task, opts, seed=run.id)
    except Exception as exc:  # noqa: BLE001 - whatever went wrong, the run must not stay "preparing"
        if not isinstance(exc, ValueError):
            log.exception("Cutting the dataset of training run %s failed", run.id)
        run.status, run.finished_at = "failed", datetime.utcnow()
        run.error = str(exc) if isinstance(exc, ValueError) else f"The dataset could not be made: {exc}"
        db.commit()
        return {"run": None}

    if run.stop_requested:
        run.status, run.finished_at = "stopped", datetime.utcnow()
        db.commit()
        return {"run": None}
    run.status = "running"
    db.commit()
    return {"run": {
        "id": run.id, "recipe_id": run.recipe_id, "task": run.task, "settings": run.settings, "folder": f"runs/{run.id}",
        "recipe_folder": recipe["folder"],  # one of your own, in the shared folder; None: one of the trainer's built-in ones
    }}


class Progress(BaseModel):
    metrics: dict | None = None  # one finished epoch, as the recipe reported it
    device: str | None = None
    stage: str | None = None  # what is going on before the first epoch (installing packages, loading the network)


@trainer_router.post("/runs/{run_id}/progress", dependencies=[Depends(require_trainer)])
def trainer_progress(payload: Progress, run: TrainingRun = Depends(get_run_or_404), db: Session = Depends(get_db)) -> dict:
    """A heartbeat from a run in progress, carrying an epoch's numbers when one has finished. The
    answer tells the trainer whether to stop."""
    _seen(None)
    if payload.device:
        run.device = payload.device
    if payload.stage is not None:
        run.stage = payload.stage[:400] or None
    if payload.metrics:
        run.stage = None  # the first epoch is in: it is training
        run.metrics = [*(run.metrics or []), payload.metrics]  # a new list, so the change is saved
        run.epoch = int(payload.metrics.get("epoch", run.epoch) or 0)
        run.epochs = int(payload.metrics.get("epochs", run.epochs) or 0)
    db.commit()
    return {"stop": run.stop_requested or run.status != "running"}


class Finish(BaseModel):
    status: str  # done | failed | stopped
    result: dict | None = None
    error: str | None = None


@trainer_router.post("/runs/{run_id}/finish", dependencies=[Depends(require_trainer)])
def trainer_finish(payload: Finish, run: TrainingRun = Depends(get_run_or_404), db: Session = Depends(get_db)) -> dict:
    _seen(None)
    if payload.status not in ("done", "failed", "stopped"):
        raise HTTPException(status_code=422, detail="status must be done, failed or stopped")
    run.status, run.finished_at = payload.status, datetime.utcnow()
    run.result, run.error = payload.result, payload.error
    if payload.status == "done" and (run_dir(run.id) / "code" / "predict.py").is_file():
        # The trained model can now make suggestions in the project's annotation workspace.
        result = payload.result or {}
        metric = result.get("primary_metric")
        db.add(TrainedModel(
            project_id=run.project_id, run_id=run.id, name=f"{run.recipe_name} (run #{run.id})", task=run.task, recipe_id=run.recipe_id,
            classes=[{"id": c["id"], "name": c["name"]} for c in (run.dataset or {}).get("classes", [])],
            score={"metric": metric, **{name: result[name].get(metric) for name in ("val", "test") if isinstance(result.get(name), dict)}} if metric else None,
            code_path=f"runs/{run.id}/code", weights_path=f"runs/{run.id}/output/model.pt",
        ))
    db.commit()
    # The dataset is a copy the app can cut again; the code, log and trained model are kept.
    shutil.rmtree(run_dir(run.id) / "dataset", ignore_errors=True)
    return {"ok": True}
