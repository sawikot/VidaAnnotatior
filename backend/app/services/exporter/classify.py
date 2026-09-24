"""One class per patch, for patch-classification datasets.

A patch's class is, in order:

1. its **Patch Label** (set in the workspace) -- a project class, or "Mixed" / "Artifact / Background"
   and the like (``source = "label"``);
2. otherwise the drawn class covering at least ``ExportOptions.min_coverage`` of the patch
   (``source = "drawn"``). Drawn means every annotation reaching the patch: its own, those of other
   patch sizes and whole-slide ones -- including the whole-patch fills labels create, which is how
   labels reach a custom export grid;
3. otherwise none: the patch is left out, or put in ``unlabeled`` (``ExportOptions.unlabeled``).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from app.services.geometry import polygon_area, shape_area

from .base import ExportData
from .options import ExportOptions

UNLABELED = "unlabeled"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def folder_name(class_name: str) -> str:
    """The class's folder under images/: its name, reduced to characters safe on every OS."""
    return _UNSAFE.sub("_", class_name).strip("._") or "class"


@dataclass(frozen=True)
class PatchClass:
    name: str
    source: str  # "label" | "drawn" | "none"
    coverage: float | None  # share of the patch the class covers (drawn classes only)


def class_areas(data: ExportData, patch) -> dict[str, float]:
    """Level-0 area of each class drawn inside ``patch`` (overlapping shapes are summed)."""
    areas: dict[str, float] = defaultdict(float)
    for a in data.own_annotations(patch.id):
        name = data.class_name(a)
        if name:
            areas[name] += shape_area(a.type, a.coordinates_level0)
    scale = (patch.width_l0 / patch.width) * (patch.height_l0 / patch.height)  # patch px^2 -> Level-0 px^2
    for piece in data.projections.get(patch.id, []):
        name = data.class_name(piece.annotation)
        if name and piece.is_area:
            local = sum(shape_area(piece.type, part) if piece.type == "circle" else polygon_area(part) for part in piece.parts)
            areas[name] += local * scale
    return areas


def _patch_area(data: ExportData, patch) -> float:
    """Level-0 area of the patch that lies on the slide (an edge patch can reach past it)."""
    slide = data.slide
    w = min(patch.width_l0, slide.width_l0 - patch.x) if slide.width_l0 else patch.width_l0
    h = min(patch.height_l0, slide.height_l0 - patch.y) if slide.height_l0 else patch.height_l0
    return float(max(w, 0) * max(h, 0))


def classify(data: ExportData, patch, options: ExportOptions) -> PatchClass | None:
    """The patch's class, or None when it is left out of the dataset."""
    class_names = {c.name for c in data.classes.values()}
    label = (patch.patch_label or "").strip()
    if label:
        if label in class_names or options.other_labels:
            return PatchClass(label, "label", None)
        return None  # "Mixed" and the like, left out on request

    areas = class_areas(data, patch)
    total = _patch_area(data, patch)
    if areas and total > 0:
        name, area = max(areas.items(), key=lambda kv: (kv[1], kv[0]))
        coverage = min(area / total, 1.0)
        if coverage >= options.min_coverage - 1e-9:
            return PatchClass(name, "drawn", round(coverage, 4))
    if options.unlabeled == "folder":
        return PatchClass(UNLABELED, "none", None)
    return None


def classify_all(data: ExportData, options: ExportOptions) -> dict[int, PatchClass]:
    """Patch id -> class, for the patches in scope that get an image (never excluded ones)."""
    out: dict[int, PatchClass] = {}
    for p in data.patches:
        if p.excluded:
            continue
        cls = classify(data, p, options)
        if cls is not None:
            out[p.id] = cls
    return out
