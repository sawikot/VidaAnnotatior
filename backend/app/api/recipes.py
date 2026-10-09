"""The model recipes: listing them, and -- for administrators -- making, editing and checking your own.

Recipe code runs on the training machine with the trainer's rights, so only administrators may change
it; everyone signed in may read it. These routes touch no project, so they are guarded here rather
than by the project rules of api/access.py.
"""
from __future__ import annotations

import shutil
from collections import deque

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api import training
from app.api.access import current_user, require_admin
from app.services import recipes
from app.services.recipes import RecipeError

router = APIRouter(prefix="/recipes", tags=["recipes"], dependencies=[Depends(current_user)])
admin = [Depends(require_admin)]

MAX_ZIP_BYTES = 20 * 1024 * 1024

# Recipes waiting for the trainer to check them (handed out by /trainer/claim, like training runs).
# In memory: a check lost to an app restart is simply asked for again.
_to_check: deque[str] = deque()


def next_check() -> dict | None:
    """The next recipe to check, as the trainer is told it; None when none is waiting."""
    while _to_check:
        recipe = recipes.get_recipe(_to_check.popleft())
        if recipe is None or recipe["builtin"] or recipe["problem"]:
            continue  # deleted or broken since it was asked for
        recipes.set_check(recipes.recipe_path(recipe["id"]), "running")
        return {
            "id": recipe["id"], "folder": recipe["folder"], "hash": recipes.code_hash(recipes.recipe_path(recipe["id"])),
            "trainable": recipe["trainable"], "has_predict": recipe["has_predict"], "settings": recipes.check_settings(recipe), "task": recipe["task"],
        }
    return None


def _found(recipe_id: str) -> dict:
    recipe = recipes.get_recipe(recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail=f"There is no model recipe \"{recipe_id}\".")
    return recipe


def _packages(recipe: dict) -> dict | None:
    """The recipe's extra packages and whether they are installed (in the environment of that name)."""
    if not recipe["requirements"]:
        return None
    env = training.training_dir() / "envs" / recipe["packages_key"]
    installed = (env / ".ready").is_file()
    size = sum(f.stat().st_size for f in env.rglob("*") if f.is_file()) if installed else 0
    return {"wanted": recipe["requirements"], "installed": installed, "bytes": size}


def _out(recipe: dict, with_packages: bool = False) -> dict:
    out = {k: v for k, v in recipe.items() if k not in ("folder", "packages_key", "check_settings")}
    if with_packages:
        out["packages"] = _packages(recipe)
    return out


def _do(action):
    try:
        return action()
    except RecipeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("")
def list_all() -> list[dict]:
    """Every recipe, built-in and your own, usable or not."""
    return [_out(r) for r in recipes.list_recipes()]


@router.get("/{recipe_id}")
def get_one(recipe_id: str) -> dict:
    return _out(_found(recipe_id), with_packages=True)


class NewRecipe(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    task: str | None = None
    # Copy this recipe; left out, start from the blank template.
    source_id: str | None = None


@router.post("", status_code=201, dependencies=admin)
def create(payload: NewRecipe) -> dict:
    """A new recipe of your own: a duplicate of another, or the blank template."""
    return _out(_found(_do(lambda: recipes.create(payload.name, payload.task, payload.source_id))), with_packages=True)


@router.post("/upload", status_code=201, dependencies=admin)
def upload(file: UploadFile = File(...), name: str = Form("")) -> dict:
    """A new recipe from a ZIP of its files."""
    data = file.file.read(MAX_ZIP_BYTES + 1)
    if len(data) > MAX_ZIP_BYTES:
        raise HTTPException(status_code=413, detail="The ZIP is larger than 20 MB. A recipe is code only; add trained models under a project's Training page.")
    return _out(_found(_do(lambda: recipes.create_from_zip(data, name))), with_packages=True)


@router.get("/{recipe_id}/download")
def download(recipe_id: str) -> Response:
    data = _do(lambda: recipes.as_zip(_found(recipe_id)["id"]))
    return Response(content=data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{recipe_id}.zip"'})


@router.delete("/{recipe_id}", status_code=204, response_model=None, dependencies=admin)
def delete(recipe_id: str) -> None:
    """Delete one of your own recipes. Runs and models made with it keep working: each has its own copy of the code."""
    _found(recipe_id)
    _do(lambda: recipes.delete(recipe_id))


@router.get("/{recipe_id}/files/{file_name}")
def read_file(recipe_id: str, file_name: str) -> dict:
    return {"name": file_name, "content": _do(lambda: recipes.read_file(recipe_id, file_name)), "versions": _do(lambda: recipes.versions(recipe_id, file_name))}


class FileContent(BaseModel):
    content: str


@router.put("/{recipe_id}/files/{file_name}", dependencies=admin)
def write_file(recipe_id: str, file_name: str, payload: FileContent) -> dict:
    """Save a file (new or changed). What it held before is kept as an earlier version."""
    _do(lambda: recipes.write_file(recipe_id, file_name, payload.content))
    return _out(_found(recipe_id), with_packages=True)


@router.delete("/{recipe_id}/files/{file_name}", dependencies=admin)
def delete_file(recipe_id: str, file_name: str) -> dict:
    _do(lambda: recipes.delete_file(recipe_id, file_name))
    return _out(_found(recipe_id), with_packages=True)


@router.get("/{recipe_id}/files/{file_name}/versions/{version_id}")
def read_version(recipe_id: str, file_name: str, version_id: str) -> dict:
    return {"content": _do(lambda: recipes.read_version(recipe_id, file_name, version_id))}


@router.post("/{recipe_id}/check", dependencies=admin)
def check(recipe_id: str) -> dict:
    """Ask the trainer to try the recipe as it is now: one epoch on a few made-up images, then a
    prediction. Until it has passed, the recipe cannot be trained with."""
    recipe = _found(recipe_id)
    if recipe["builtin"]:
        raise HTTPException(status_code=409, detail="Built-in recipes need no check.")
    if recipe["problem"]:
        raise HTTPException(status_code=422, detail=recipe["problem"])
    if recipe_id not in _to_check:
        _to_check.append(recipe_id)
    recipes.set_check(recipes.recipe_path(recipe_id), "queued")
    return _out(_found(recipe_id), with_packages=True)


@router.delete("/{recipe_id}/packages", dependencies=admin)
def remove_packages(recipe_id: str) -> dict:
    """Delete the recipe's installed packages to free disk space; they are installed again when next needed."""
    recipe = _found(recipe_id)
    if recipe["packages_key"]:
        shutil.rmtree(training.training_dir() / "envs" / recipe["packages_key"], ignore_errors=True)
    return _out(recipe, with_packages=True)


# -------------------------------------------------------------------------- what the trainer uses

trainer_router = APIRouter(prefix="/trainer", tags=["trainer"], dependencies=[Depends(training.require_trainer)])


class CheckResult(BaseModel):
    hash: str  # of the code that was checked
    ok: bool
    report: dict  # {"steps": [{"name", "ok", "detail"}], "log": "..."}


@trainer_router.post("/checks/{recipe_id}/result")
def check_result(recipe_id: str, payload: CheckResult) -> dict:
    path = recipes.recipe_path(recipe_id)
    if path is None or recipes.is_builtin(path):
        return {"ok": False}  # deleted while it was being checked
    if payload.hash != recipes.code_hash(path):
        return {"ok": False}  # edited meanwhile: that check is of code that no longer exists
    recipes.set_check(path, "passed" if payload.ok else "failed", payload.report)
    return {"ok": True}
