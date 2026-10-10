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

Slides in no set, and slides without patches, are left out. How the patches are cut and chosen is
the run's ``DatasetOptions``.
"""
from __future__ import annotations

import csv
import io
import json
import shutil
import zipfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.config_version import AnnotationClass, ProjectConfigVersion
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide
from app.services import dataset_split
from app.services.exporter import get_exporter
from app.services.exporter.bundle import build_bundle
from app.services.exporter.options import ExportOptions
from app.services.patch_grid import GridSpec, active_grid_filter

# The fewest annotated images a set needs before training on it makes sense at all.
MIN_TRAIN_IMAGES = 2
MIN_VAL_IMAGES = 1

# Classification: only patches that have one of the project's classes (not "Mixed" or "Artifact").
_LABELLED = {"other_labels": False, "unlabeled": "skip"}


@dataclass(frozen=True)
class DatasetOptions:
    """How a run's dataset is cut from the slides and which patches go into it."""

    # The side of the patches, in pixels at the project's magnification. None: the patches as they were
    # generated and annotated. A size cuts every slide afresh for this dataset alone (nothing in the
    # project changes), and every annotation is cut into the new patches.
    patch_size: int | None = None
    # How far apart patches start; smaller than the size makes them overlap. None: the size (no overlap).
    stride: int | None = None
    # With a size: cut patches from the "tissue" only, or from the "whole" slide, glass included.
    area: str = "tissue"
    # Which annotated patches to learn from: every "annotated" one, or only those marked "reviewed".
    use: str = "annotated"
    # Patches with nothing in them, as examples of background: this many for every 100 annotated ones.
    empty_percent: float = 0
    # ...taken from "any" patch without annotations, or only "reviewed" ones (confirmed to be empty).
    empty_from: str = "any"
    # The classes to learn; None: all of them. Annotations of other classes are left out.
    class_ids: tuple[int, ...] | None = None

    def as_dict(self) -> dict:
        return {**asdict(self), "class_ids": list(self.class_ids) if self.class_ids is not None else None}


def parse_options(raw: dict | None, project: Project) -> DatasetOptions:
    """The options a person chose, checked. Raises ValueError saying what is wrong."""
    raw = {k: v for k, v in (raw or {}).items() if v is not None}
    unknown = set(raw) - set(DatasetOptions.__dataclass_fields__)
    if unknown:
        raise ValueError(f"Unknown dataset options: {', '.join(sorted(unknown))}")
    try:
        size = int(raw["patch_size"]) if "patch_size" in raw else None
        stride = int(raw["stride"]) if "stride" in raw else None
        empty = float(raw.get("empty_percent", 0))
        classes = tuple(sorted({int(c) for c in raw["class_ids"]})) if "class_ids" in raw else None
    except (TypeError, ValueError):
        raise ValueError("The patch size, stride and share of empty patches must be numbers.") from None
    area, use, empty_from = raw.get("area", "tissue"), raw.get("use", "annotated"), raw.get("empty_from", "any")
    if area not in ("tissue", "whole") or use not in ("annotated", "reviewed") or empty_from not in ("any", "reviewed"):
        raise ValueError("Unknown choice in the dataset options.")
    if size is not None:
        if project.project_type == "image":
            raise ValueError("An image project trains on its images as they are; there is no patch size to choose.")
        if not 32 <= size <= 8192:
            raise ValueError("The patch size must be between 32 and 8192 px.")
        if stride is not None and not 8 <= stride <= 8192:
            raise ValueError("The stride must be between 8 and 8192 px.")
        if use == "reviewed" or (empty > 0 and empty_from == "reviewed"):
            raise ValueError("Patches cut at another size have no Reviewed mark: with a patch size of your own, use all annotated patches and any empty ones.")
    if not 0 <= empty <= 100000:
        raise ValueError("The share of empty patches cannot be negative.")
    if classes is not None and not classes:
        raise ValueError("Choose at least one class to learn.")
    return DatasetOptions(size, stride if size is not None else None, area, use, empty, empty_from, classes)


def _export_options(db: Session, project: Project, task: str, opts: DatasetOptions, seed: int = 0, **more) -> ExportOptions:
    grid = None
    if opts.patch_size is not None:
        config = db.get(ProjectConfigVersion, project.active_config_version_id)
        spec = replace(GridSpec.from_config(config), patch_width=opts.patch_size, patch_height=opts.patch_size, stride_x=opts.stride or opts.patch_size, stride_y=opts.stride or opts.patch_size)
        grid = spec.over_whole_slide() if opts.area == "whole" else spec.over_tissue(config)
    classification = task == "classification"
    return ExportOptions(
        patch_scope="annotated", grid=grid, only_reviewed=opts.use == "reviewed", seed=seed,
        # A classifier has nothing to learn from a patch without a class; its classes are chosen by label, below.
        empty_ratio=None if classification or opts.empty_percent <= 0 else opts.empty_percent / 100,
        empty_from=opts.empty_from,
        class_ids=None if classification or opts.class_ids is None else frozenset(opts.class_ids),
        # A Patch Label fills its patch with one big shape: a label, not an object to find.
        skip_patch_fills=task == "detection",
        **(_LABELLED if classification else {}), **more,
    )


def _usable(db: Session, slide: Slide, opts: DatasetOptions) -> bool:
    if slide.status == "error" or slide.active_config_version_id is None:
        return False
    if opts.patch_size is not None:
        return bool(slide.width_l0)  # cut afresh: it needs no patches of its own
    return active_grid_filter(db.query(Patch.id).filter(Patch.slide_id == slide.id), slide).first() is not None


def _by_set(db: Session, project: Project, opts: DatasetOptions) -> dict[str, list[Slide]]:
    slides = sorted(project.slides, key=lambda s: s.id)
    return {name: [s for s in slides if s.split == name and _usable(db, s, opts)] for name in dataset_split.SPLITS}


def _class_ids(db: Session, project: Project) -> dict[str, int]:
    """The project's classes by name: the classification export names a class, runs go by its id."""
    rows = db.query(AnnotationClass).filter(AnnotationClass.config_version_id == project.active_config_version_id)
    return {c.name: c.id for c in rows}


def _wanted(class_id: int | None, opts: DatasetOptions) -> bool:
    return class_id is not None and (opts.class_ids is None or class_id in opts.class_ids)


def _count(db: Session, slide: Slide, task: str, export: ExportOptions, opts: DatasetOptions, ids: dict[str, int], classes: dict[int, dict]) -> tuple[int, int, dict[int, int]]:
    """What one slide adds to a set: its images with something in them, its empty ones, and the
    examples of each class (by class id)."""
    per_class: dict[int, int] = {}
    if task == "classification":
        for row in csv.DictReader(io.StringIO(get_exporter("patch_classification").export(db, slide, export))):
            class_id = ids.get(row["class"])
            if _wanted(class_id, opts):
                classes.setdefault(class_id, {"id": class_id, "name": row["class"]})
                per_class[class_id] = per_class.get(class_id, 0) + 1
        return sum(per_class.values()), 0, per_class
    doc = get_exporter("coco").export(db, slide, export)
    names = {cat["id"]: cat["name"] for cat in doc["categories"]}
    for ann in doc["annotations"]:
        classes.setdefault(ann["category_id"], {"id": ann["category_id"], "name": names.get(ann["category_id"], "?")})
        per_class[ann["category_id"]] = per_class.get(ann["category_id"], 0) + 1
    with_objects = len({ann["image_id"] for ann in doc["annotations"]})
    return with_objects, len(doc["images"]) - with_objects, per_class


def readiness(db: Session, project: Project, task: str, opts: DatasetOptions | None = None) -> dict:
    """What a training run would learn from, per set, and what stands in the way of starting one."""
    opts = opts or DatasetOptions()
    if dataset_split.fill(project, list(project.slides)):  # slides added since the last random deal
        db.commit()
    ids = _class_ids(db, project)
    export = _export_options(db, project, task, opts)
    classes: dict[int, dict] = {}
    sets: dict[str, dict] = {}
    problems: list[str] = []
    for name, slides in _by_set(db, project, opts).items():
        images = empty = 0
        per_class: dict[int, int] = {}
        for slide in slides:
            try:
                found, bare, counts = _count(db, slide, task, export, opts, ids, classes)
            except ValueError as exc:  # e.g. a patch size that cuts far too many patches from a slide
                if str(exc) not in problems:
                    problems.append(str(exc))
                continue
            images, empty = images + found, empty + bare
            for class_id, n in counts.items():
                per_class[class_id] = per_class.get(class_id, 0) + n
        sets[name] = {"slides": len(slides), "images": images, "empty": empty, "objects": sum(per_class.values()), "per_class": per_class}

    image_project = project.project_type == "image"
    unit = "images" if image_project else "patches"
    what = f"labelled {unit}" if task == "classification" else f"annotated {unit}"
    if dataset_split.mode_of(project) == "off":
        problems.insert(0, f"Set up the train / validation / test split first, so the model is tested on {'images' if image_project else 'slides'} it has not seen.")
    elif not problems:
        if sets["train"]["images"] < MIN_TRAIN_IMAGES:
            problems.append(f"The training set has too few {what}.")
        if sets["val"]["images"] < MIN_VAL_IMAGES:
            problems.append(f"The validation set has no {what}: the model cannot be scored while it trains.")
        if task == "classification" and not problems and len([c for c in classes if sets["train"]["per_class"].get(c)]) < 2:
            problems.append("The training set has only one class; a classifier needs at least two to tell apart.")

    warnings = []
    if not problems and sets["test"]["images"] == 0:
        warnings.append(f"The test set has no {what}, so there will be no final test score.")
    if not problems and opts.empty_percent > 0 and task != "classification" and sets["train"]["empty"] == 0:
        warnings.append("No empty patches were found to add" + (" among the reviewed ones." if opts.empty_from == "reviewed" else "."))
    for cls in classes.values():
        if not problems and sets["train"]["per_class"].get(cls["id"], 0) < 10:
            warnings.append(f"\"{cls['name']}\" has fewer than 10 examples in the training set; expect it to be learned poorly.")

    return {
        "task": task,
        "split_mode": dataset_split.mode_of(project),
        "classes": sorted(classes.values(), key=lambda c: c["id"]),
        # Every class of the project, for choosing which to learn.
        "project_classes": [{"id": i, "name": n} for n, i in sorted(ids.items(), key=lambda item: item[1])],
        "sets": sets,
        "problems": problems,
        "warnings": warnings,
        "options": opts.as_dict(),
    }


def build_dataset(db: Session, project: Project, run_dir: Path, task: str, opts: DatasetOptions | None = None, seed: int = 0) -> dict:
    """Write the run's dataset into ``run_dir/dataset`` and return what it holds (kept with the run).
    Raises ValueError when there is nothing to train on."""
    opts = opts or DatasetOptions()
    if dataset_split.fill(project, list(project.slides)):
        db.commit()
    by_set = _by_set(db, project, opts)
    slides = [s for group in by_set.values() for s in group]
    if not by_set["train"] or not by_set["val"]:
        raise ValueError("The training and validation sets both need at least one slide with patches.")

    classification = task == "classification"
    target = run_dir / "dataset"
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    bundle = build_bundle(
        db, slides, get_exporter("patch_classification" if classification else "coco"),
        _export_options(db, project, task, opts, seed, content="images", image_format="jpg"),
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
        empty = 0
        if classification:
            file = target / name / "labels.csv"
            rows = list(csv.DictReader(file.open(encoding="utf-8", newline=""))) if file.is_file() else []
            kept = [row for row in rows if _wanted(ids.get(row["class"]), opts)]
            if file.is_file() and len(kept) != len(rows):  # only the chosen classes are learned
                with file.open("w", encoding="utf-8", newline="") as out:
                    writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(kept)
            for row in kept:
                classes.setdefault(ids[row["class"]], {"id": ids[row["class"]], "name": row["class"]})
                per_class[str(ids[row["class"]])] = per_class.get(str(ids[row["class"]]), 0) + 1
            images, objects = len(kept), len(kept)
            where = {"labels": f"{name}/labels.csv", "images": f"{name}"}  # a row's "file" is images/<class>/<name>
        else:
            file = target / name / "annotations" / "dataset_coco.json"
            doc = json.loads(file.read_text(encoding="utf-8")) if file.is_file() else {"images": [], "annotations": [], "categories": []}
            names = {cat["id"]: cat for cat in doc["categories"]}
            for ann in doc["annotations"]:
                cat = names.get(ann["category_id"], {})
                classes.setdefault(ann["category_id"], {"id": ann["category_id"], "name": cat.get("name", "?"), "color": cat.get("color")})
                per_class[str(ann["category_id"])] = per_class.get(str(ann["category_id"]), 0) + 1
            images, objects = len(doc["images"]), len(doc["annotations"])
            empty = images - len({ann["image_id"] for ann in doc["annotations"]})
            where = {"annotations": f"{name}/annotations/dataset_coco.json", "images": f"{name}/images"}
        sets[name] = {"slides": [s.filename for s in group] if images else [], "images": images, "empty": empty, "objects": objects, "per_class": per_class}
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
    return {"classes": ordered, "sets": sets, "options": opts.as_dict()}
