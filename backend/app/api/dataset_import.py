"""Importing a dataset -- images or slides with their annotations, in whatever layout and formats
they come (services/dataset_import.py, services/dataset_formats.py) -- in steps:

1. ``POST   /projects/{id}/imports``                 start an import: a folder of its own for the files;
2. ``POST   /projects/{id}/imports/{import}/files``  send files into it, in as many requests as it takes;
3. ``POST   /projects/{id}/imports/{import}/analyse`` what was found, and the labels to match to classes;
4. ``POST   /projects/{id}/imports/{import}/commit``  import it as shown, with the classes chosen;
   ``DELETE /projects/{id}/imports/{import}``         or drop it.

Nothing in the project changes before step 4. Annotations can be sent without images: they are
matched to the images the project already has.
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from collections import Counter
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api import slides as slides_api
from app.api.access import current_user
from app.api.annotations import import_entries
from app.api.deps import get_project_or_404
from app.core.config import get_settings
from app.database.session import get_db
from app.models.config_version import AnnotationClass, ProjectConfigVersion
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide
from app.models.user import User
from app.schemas.annotation import ImportAnnotationEntry
from app.services import dataset_import
from app.services.image_import import ImageItem
from app.services.multipart_stream import receive_multipart
from app.services.patch_labels import set_patch_label
from app.services.wsi_bundles import ARCHIVE_EXTENSIONS, ArchiveError, UnsafePathError, discover_bundles, extract_zip, safe_parts

router = APIRouter(tags=["dataset import"])

# Colours for classes made from a file's labels, told apart at a glance.
_PALETTE = ["#dc2626", "#2563eb", "#16a34a", "#d97706", "#9333ea", "#0891b2", "#db2777", "#65a30d", "#4f46e5", "#ea580c", "#0d9488", "#be123c"]


def _folder(project: Project, import_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", import_id):
        raise HTTPException(status_code=404, detail="Import not found")
    folder = get_settings().wsi_storage_dir / "_staging" / f"import-{import_id}"
    meta = folder / "import.json"
    if not meta.is_file() or json.loads(meta.read_text()).get("project_id") != project.id:
        raise HTTPException(status_code=404, detail="Import not found (it may have been finished, cancelled, or cleared by a restart). Start again.")
    return folder


@router.post("/projects/{project_id}/imports", status_code=201)
def start_import(project: Project = Depends(get_project_or_404)) -> dict:
    import_id = uuid.uuid4().hex
    folder = get_settings().wsi_storage_dir / "_staging" / f"import-{import_id}"
    (folder / "tree").mkdir(parents=True)
    (folder / "import.json").write_text(json.dumps({"project_id": project.id}))
    return {"id": import_id}


@router.delete("/projects/{project_id}/imports/{import_id}", status_code=204, response_model=None)
def cancel_import(import_id: str, project: Project = Depends(get_project_or_404)) -> None:
    shutil.rmtree(_folder(project, import_id), ignore_errors=True)


def _place(folder: Path, uploads: list, names: list[str]) -> dict:
    """Put received files into the import's tree under the paths they were picked with; a .zip is
    unpacked into a folder named after it (``labels.zip`` becomes ``labels/``)."""
    settings = get_settings()
    tree = folder / "tree"
    skipped: list[dict] = []
    warnings: list[str] = []
    placed = 0
    for upload, raw_name in zip(uploads, names):
        try:
            parts = safe_parts(raw_name)
        except UnsafePathError as exc:
            skipped.append({"name": raw_name, "reason": f"unsafe file name ({exc})"})
            continue
        if Path(parts[-1]).suffix.lower() in ARCHIVE_EXTENSIONS:
            target = tree.joinpath(*parts[:-1], Path(parts[-1]).stem)
            if target.exists():
                target = target.with_name(f"{target.name}-{uuid.uuid4().hex[:6]}")
            try:
                warnings += [f"{raw_name}: {w}" for w in extract_zip(upload.path, target, max_bytes=settings.max_upload_bytes, max_files=settings.max_upload_files)]
                placed += 1
            except ArchiveError as exc:
                skipped.append({"name": raw_name, "reason": f"archive {exc}"})
            continue
        target = tree.joinpath(*parts)
        if target.exists():
            skipped.append({"name": raw_name, "reason": "a file with this name was already sent"})
            continue
        slides_api._place_upload(upload, target)
        placed += 1
    return {"files": placed, "skipped": skipped, "warnings": warnings}


@router.post("/projects/{project_id}/imports/{import_id}/files")
async def add_import_files(import_id: str, request: Request, project: Project = Depends(get_project_or_404)) -> dict:
    """Send files into the import: multipart ``files`` (repeatable) and a ``manifest`` -- a JSON list
    of their paths in the picked folder, one per file, in the same order (see the slide upload)."""
    settings = get_settings()
    folder = _folder(project, import_id)
    slides_api._check_free_space(settings.wsi_storage_dir, int(request.headers.get("content-length") or 0))
    received = folder / "received" / uuid.uuid4().hex
    try:
        try:
            form = await receive_multipart(request, received, max_files=settings.max_upload_files, max_bytes=settings.max_upload_bytes)
        except OSError as exc:
            raise slides_api._storage_error(settings.wsi_storage_dir, exc) from exc
        if not form.files:
            raise HTTPException(status_code=422, detail="No files were uploaded")
        names = [u.filename or f"file-{i}" for i, u in enumerate(form.files)]
        if form.fields.get("manifest"):
            try:
                manifest = json.loads(form.fields["manifest"])
            except json.JSONDecodeError:
                raise HTTPException(status_code=422, detail="manifest must be a JSON list of paths") from None
            if not (isinstance(manifest, list) and all(isinstance(m, str) for m in manifest)) or len(manifest) != len(form.files):
                raise HTTPException(status_code=422, detail="manifest must list one path per uploaded file")
            names = manifest
        try:
            return await run_in_threadpool(_place, folder, form.files, names)
        except OSError as exc:  # unpacking a zip can fill the disk too
            raise slides_api._storage_error(settings.wsi_storage_dir, exc) from exc
    finally:
        shutil.rmtree(received, ignore_errors=True)


@router.post("/projects/{project_id}/imports/{import_id}/analyse")
def analyse_import(import_id: str, project: Project = Depends(get_project_or_404), db: Session = Depends(get_db)) -> dict:
    """What the files sent so far hold: images, annotations and their formats, which image each
    annotation is for, the labels used -- and what could not be used, with the reason."""
    folder = _folder(project, import_id)
    plan = dataset_import.analyse(db, project, folder / "tree", get_settings().max_image_pixels)
    dataset_import.save_plan(folder, plan)
    return plan["preview"]


class ImportCommit(BaseModel):
    # What to do with each label of the files ("" for shapes without one): give it this class (by
    # id), make a class of that name ("new"), import it without a class ("unlabeled", shapes only), or
    # leave it out ("skip"). A label not listed gets the class of its name, or is left out.
    label_map: dict[str, int | Literal["new", "skip", "unlabeled"]] = {}
    # In an image project whose folders name the classes: label each image by its folder.
    folder_labels: bool = True
    # For labels made into classes ("new"): the class's name, when it is not to be the label itself
    # (a mask's "class 1" is better called what it is).
    new_names: dict[str, str] = {}


def _new_class(db: Session, config: ProjectConfigVersion, label: str) -> AnnotationClass:
    used = {c.color_hex.lower() for c in config.annotation_classes}
    colour = next((c for c in _PALETTE if c not in used), _PALETTE[len(config.annotation_classes) % len(_PALETTE)])
    cls = AnnotationClass(config_version_id=config.id, name=label[:100], color_hex=colour, order_index=max((c.order_index for c in config.annotation_classes), default=-1) + 1)
    config.annotation_classes.append(cls)
    db.flush()
    return cls


@router.post("/projects/{project_id}/imports/{import_id}/commit")
def commit_import(
    import_id: str,
    payload: ImportCommit,
    project: Project = Depends(get_project_or_404),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Import what ``analyse`` showed: add the new images or slides, make the classes asked for, and
    put every annotation on its image. Annotations already there are not added twice."""
    folder = _folder(project, import_id)
    plan = dataset_import.load_plan(folder)
    if plan is None:
        raise HTTPException(status_code=409, detail="Look at what the import holds first (analyse).")
    config = db.get(ProjectConfigVersion, project.active_config_version_id) if project.active_config_version_id else None
    if config is None:
        raise HTTPException(status_code=422, detail="This project has no configuration to import against")
    tree = folder / "tree"
    image_project = project.project_type == "image"

    # Classes: those chosen, those made now, and -- for labels nobody decided on -- those of the same name.
    by_id = {c.id: c for c in config.annotation_classes}
    by_name = {c.name.strip().lower(): c for c in config.annotation_classes}
    created: list[str] = []
    label_map: dict[str, int | str] = {}
    for item in plan["preview"]["labels"]:
        label = item["label"]
        choice = payload.label_map.get(label)
        if isinstance(choice, int) and choice not in by_id:
            raise HTTPException(status_code=422, detail=f"Class {choice} is not a class of this project")
        if choice == "new" and label.strip():
            name = (payload.new_names.get(label) or "").strip() or label.strip()
            cls = by_name.get(name.lower())
            if cls is None:
                cls = by_name[name.lower()] = _new_class(db, config, name)
                by_id[cls.id] = cls
                created.append(cls.name)
            choice = cls.id
        elif choice is None or choice == "new":
            cls = by_name.get(label.strip().lower()) if label.strip() else None
            choice = cls.id if cls else "skip"
        label_map[label] = choice
    db.commit()

    # The new images or slides.
    slide_of: dict[str, Slide] = {}
    failed: list[dict] = []
    bundles = {} if image_project else {b.primary.relative_to(tree).as_posix(): b for b in discover_bundles(tree).bundles}
    for item in plan["new"]:
        if image_project:
            outcome = slides_api._register_image(db, project, ImageItem(tree / item["rel"], item["name"], item["width"], item["height"]), "upload", True)
        elif item["rel"] in bundles:
            outcome = slides_api._register_bundle(db, project, bundles[item["rel"]], None, "upload", True)
        else:
            failed.append({"name": item["name"], "reason": "its files are no longer in the import"})
            continue
        if isinstance(outcome, Slide):
            slide_of[item["key"]] = outcome
        else:
            failed.append({"name": outcome.name, "reason": outcome.reason})

    # Their annotations, and those for images the project already had.
    counts: Counter = Counter()
    labelled = 0
    targets = set(plan["work"]) | (set(plan["folder_labels"]) if payload.folder_labels else set())
    for n, key in enumerate(sorted(targets)):
        slide = slide_of.get(key) if key.startswith("new:") else db.get(Slide, int(key.split(":")[1]))
        if slide is None or slide.project_id != project.id:
            counts["image not imported"] += len(plan["work"].get(key, {}).get("entries", []))
            continue
        slot = plan["work"].get(key, {"entries": [], "label": None})
        if slot["entries"]:
            entries = [ImportAnnotationEntry(**entry) for entry in slot["entries"]]
            counts.update(import_entries(db, slide, config, entries, label_map, user, assign_to_patches=True))
        label = slot["label"] or (plan["folder_labels"].get(key) if payload.folder_labels else None)
        if label and image_project:
            choice = label_map.get(label)
            patch = db.query(Patch).filter(Patch.slide_id == slide.id).order_by(Patch.id).first()
            if isinstance(choice, int) and patch is not None:
                if patch.label_class_id != choice:
                    set_patch_label(db, patch, by_id[choice].name, f"Imported by {user.name}", user.id)
                    labelled += 1
            else:
                counts["by_choice" if choice == "skip" else "no_class"] += 1
        if n % 50 == 49:
            db.commit()
    db.commit()
    shutil.rmtree(folder, ignore_errors=True)

    reasons = {
        "no_class": "their label has no class",
        "by_choice": "their label was left out",
        "duplicate": "already there",
        "outside": "reaching outside the image",
        "invalid": "not a valid shape",
        "no_patch": "no patch to put them in",
        "image not imported": "their image could not be imported",
    }
    return {
        "images_imported": len(slide_of),
        "images_failed": failed,
        "annotations_imported": counts["to_patches"] + counts["on_slide"],
        "image_labels_set": labelled,
        "classes_created": created,
        "left_out": {text: counts[key] for key, text in reasons.items() if counts[key]},
    }
