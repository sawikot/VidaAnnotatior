"""The dataset a training run learns from, cut from a project's annotations.

It is an export the app already makes, sorted into the project's train / val / test split and
written into the run's folder instead of a download. Which export depends on the task:

* **detection** and **segmentation** learn from drawn shapes -- the COCO export with patch images::

      dataset.json                          {"task", "format": "coco", "classes", "sets"}
      train/annotations/dataset_coco.json   boxes and polygons, in each image's own pixels
      train/images/<name>.jpg
      val/...   test/...

* **classification** learns one class per patch -- the patch-classification export (a patch's label,
  else the drawn class covering most of it; see services/exporter/classify.py)::

      dataset.json                          {"task", "format": "folders", "classes", "sets"}
      train/labels.csv                      one row per image: file, class, ...
      train/images/<class>/<name>.jpg
      val/...   test/...

Slides in no set, and slides without patches, are left out.
"""
from __future__ import annotations

import csv
import io
import json
import shutil
import zipfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.config_version import AnnotationClass
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide
from app.services import dataset_split
from app.services.exporter import get_exporter
from app.services.exporter.bundle import build_bundle
from app.services.exporter.options import ExportOptions
from app.services.patch_grid import active_grid_filter

# The fewest annotated images a set needs before training on it makes sense at all.
MIN_TRAIN_IMAGES = 2
MIN_VAL_IMAGES = 1

# Classification: only patches that have one of the project's classes (not "Mixed" or "Artifact").
_SCOPE = {"patch_scope": "annotated", "other_labels": False, "unlabeled": "skip"}


def _usable(db: Session, slide: Slide) -> bool:
    return (
        slide.status != "error"
        and slide.active_config_version_id is not None
        and active_grid_filter(db.query(Patch.id).filter(Patch.slide_id == slide.id), slide).first() is not None
    )


def _by_set(db: Session, project: Project) -> dict[str, list[Slide]]:
    slides = sorted(project.slides, key=lambda s: s.id)
    return {name: [s for s in slides if s.split == name and _usable(db, s)] for name in dataset_split.SPLITS}


def _class_ids(db: Session, project: Project) -> dict[str, int]:
    """The project's classes by name: the classification export names a class, runs go by its id."""
    rows = db.query(AnnotationClass).filter(AnnotationClass.config_version_id == project.active_config_version_id)
    return {c.name: c.id for c in rows}


def _count(db: Session, slide: Slide, task: str, ids: dict[str, int], classes: dict[int, dict]) -> tuple[int, dict[int, int]]:
    """What one slide adds to a set: its images, and the examples of each class (by class id)."""
    per_class: dict[int, int] = {}
    if task == "classification":
        rows = list(csv.DictReader(io.StringIO(get_exporter("patch_classification").export(db, slide, ExportOptions(**_SCOPE)))))
        for row in rows:
            class_id = ids.get(row["class"])
            if class_id is not None:
                classes.setdefault(class_id, {"id": class_id, "name": row["class"]})
                per_class[class_id] = per_class.get(class_id, 0) + 1
        return sum(per_class.values()), per_class
    doc = get_exporter("coco").export(db, slide, ExportOptions(patch_scope="annotated"))
    for cat in doc["categories"]:
        classes.setdefault(cat["id"], {"id": cat["id"], "name": cat["name"]})
    for ann in doc["annotations"]:
        per_class[ann["category_id"]] = per_class.get(ann["category_id"], 0) + 1
    return len({ann["image_id"] for ann in doc["annotations"]}), per_class


def readiness(db: Session, project: Project, task: str) -> dict:
    """What a training run would learn from, per set, and what stands in the way of starting one."""
    if dataset_split.fill(project, list(project.slides)):  # slides added since the last random deal
        db.commit()
    ids = _class_ids(db, project)
    classes: dict[int, dict] = {}
    sets: dict[str, dict] = {}
    for name, slides in _by_set(db, project).items():
        images = 0
        per_class: dict[int, int] = {}
        for slide in slides:
            found, counts = _count(db, slide, task, ids, classes)
            images += found
            for class_id, n in counts.items():
                per_class[class_id] = per_class.get(class_id, 0) + n
        sets[name] = {"slides": len(slides), "images": images, "objects": sum(per_class.values()), "per_class": per_class}

    image_project = project.project_type == "image"
    unit = "images" if image_project else "patches"
    what = f"labelled {unit}" if task == "classification" else f"annotated {unit}"
    problems: list[str] = []
    if dataset_split.mode_of(project) == "off":
        problems.append(f"Set up the train / validation / test split first, so the model is tested on {'images' if image_project else 'slides'} it has not seen.")
    else:
        if sets["train"]["images"] < MIN_TRAIN_IMAGES:
            problems.append(f"The training set has too few {what}.")
        if sets["val"]["images"] < MIN_VAL_IMAGES:
            problems.append(f"The validation set has no {what}: the model cannot be scored while it trains.")
        if task == "classification" and not problems and len([c for c in classes if sets["train"]["per_class"].get(c)]) < 2:
            problems.append("The training set has only one class; a classifier needs at least two to tell apart.")

    warnings = []
    if not problems and sets["test"]["images"] == 0:
        warnings.append(f"The test set has no {what}, so there will be no final test score.")
    for cls in classes.values():
        if not problems and sets["train"]["per_class"].get(cls["id"], 0) < 10:
            warnings.append(f"\"{cls['name']}\" has fewer than 10 examples in the training set; expect it to be learned poorly.")

    return {
        "task": task,
        "split_mode": dataset_split.mode_of(project),
        "classes": sorted(classes.values(), key=lambda c: c["id"]),
        "sets": sets,
        "problems": problems,
        "warnings": warnings,
    }


def build_dataset(db: Session, project: Project, run_dir: Path, task: str) -> dict:
    """Write the run's dataset into ``run_dir/dataset`` and return what it holds (kept with the run).
    Raises ValueError when there is nothing to train on."""
    if dataset_split.fill(project, list(project.slides)):
        db.commit()
    by_set = _by_set(db, project)
    slides = [s for group in by_set.values() for s in group]
    if not by_set["train"] or not by_set["val"]:
        raise ValueError("The training and validation sets both need at least one slide with patches.")

    classification = task == "classification"
    target = run_dir / "dataset"
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    bundle = build_bundle(
        db, slides, get_exporter("patch_classification" if classification else "coco"),
        ExportOptions(content="images", image_format="jpg", **(_SCOPE if classification else {"patch_scope": "annotated"})),
        combine=True, label="dataset", max_images=10**9, split_of={s.id: s.split for s in slides},
    )
    try:
        with zipfile.ZipFile(bundle.file) as archive:
            archive.extractall(target)  # names are made by the exporter from safe characters only
    finally:
        bundle.file.close()

    ids = _class_ids(db, project)
    classes: dict[int, dict] = {}
    sets: dict[str, dict] = {}
    layout: dict[str, dict] = {}
    for name, group in by_set.items():
        per_class: dict[str, int] = {}
        if classification:
            file = target / name / "labels.csv"
            rows = list(csv.DictReader(file.open(encoding="utf-8", newline=""))) if file.is_file() else []
            rows = [row for row in rows if row["class"] in ids]
            for row in rows:
                classes.setdefault(ids[row["class"]], {"id": ids[row["class"]], "name": row["class"]})
                per_class[str(ids[row["class"]])] = per_class.get(str(ids[row["class"]]), 0) + 1
            images, objects = len(rows), len(rows)
            where = {"labels": f"{name}/labels.csv", "images": f"{name}"}  # a row's "file" is images/<class>/<name>
        else:
            file = target / name / "annotations" / "dataset_coco.json"
            doc = json.loads(file.read_text(encoding="utf-8")) if file.is_file() else {"images": [], "annotations": [], "categories": []}
            for cat in doc["categories"]:
                classes.setdefault(cat["id"], {"id": cat["id"], "name": cat["name"], "color": cat.get("color")})
            for ann in doc["annotations"]:
                per_class[str(ann["category_id"])] = per_class.get(str(ann["category_id"]), 0) + 1
            images, objects = len(doc["images"]), len(doc["annotations"])
            where = {"annotations": f"{name}/annotations/dataset_coco.json", "images": f"{name}/images"}
        sets[name] = {"slides": [s.filename for s in group] if images else [], "images": images, "objects": objects, "per_class": per_class}
        if images:
            layout[name] = where

    if not sets["train"]["objects"]:
        raise ValueError("The training set has no labelled patches." if classification else "The training set has no annotated objects with a class.")
    if "val" not in layout:
        raise ValueError("The validation set has no labelled patches." if classification else "The validation set has no annotated patches.")

    ordered = sorted(classes.values(), key=lambda c: c["id"])
    (target / "dataset.json").write_text(
        json.dumps({"task": task, "format": "folders" if classification else "coco", "classes": ordered, "sets": layout}, indent=2), encoding="utf-8"
    )
    return {"classes": ordered, "sets": sets}
