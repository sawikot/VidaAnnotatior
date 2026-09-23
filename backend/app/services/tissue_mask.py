"""The tissue mask patches are generated from: automatic detection, hand-drawn
regions, or both.

A slide's mask *starts* from one of two sources:

- ``auto``   -- the tissue detector's result (HSV + Otsu, see ``tissue_detector``);
- ``manual`` -- empty: only what the user marks counts as tissue.

On top of that, **Add** regions mark areas as tissue (tissue the detector
missed, or everything in manual mode) and **Remove** regions clear areas (pen
marks, folds, bubbles, labels). Remove always wins where the two overlap.

Regions are shapes in Level-0 pixels, drawn with the annotation area tools.
They are not annotations: they only decide where the mask is. The detector's
own result is kept in a separate file so regions can be edited again and again
without re-running detection.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from app.models.slide import Slide
from app.services.geometry import AREA_TYPES, circle_center_radius

TISSUE_SOURCES = ("auto", "manual")
REGION_MODES = ("add", "remove")
REGION_TYPES = AREA_TYPES  # polygon, freehand, rectangle, circle

# Detection runs on a thumbnail this size (its morphology sizes are in these pixels).
DETECTION_MAX_SIZE = 1024
# Hand-drawn regions are rasterised finer, so a region's edge lands close to where it was drawn.
REGION_MAX_SIZE = 4096

_SHIFT = 4  # fixed-point bits for cv2 drawing: sub-pixel accurate edges


@dataclass
class MaskStats:
    tissue_area_mm2: float
    tissue_coverage_pct: float


def mask_dir(storage_root: Path, slide: Slide) -> Path:
    return storage_root / str(slide.project_id) / "_masks"


def auto_mask_path(storage_root: Path, slide: Slide) -> Path:
    return mask_dir(storage_root, slide) / f"{slide.id}_tissue_auto.png"


def final_mask_path(storage_root: Path, slide: Slide) -> Path:
    return mask_dir(storage_root, slide) / f"{slide.id}_tissue_mask.png"


def load_mask(path: Path) -> np.ndarray:
    return np.array(Image.open(path)) > 127


def save_mask(path: Path, mask: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mask.astype(np.uint8) * 255).save(path)


def _load_auto_mask(storage_root: Path, slide: Slide) -> np.ndarray | None:
    auto = auto_mask_path(storage_root, slide)
    if not auto.exists() and slide.tissue_mask_path and slide.tissue_params_used:
        # Detected before hand-drawn regions existed: the saved mask *is* the detector's result.
        # Keep it aside before it is overwritten by a combined mask.
        legacy = storage_root / slide.tissue_mask_path
        if legacy.exists():
            shutil.copyfile(legacy, auto)
    return load_mask(auto) if auto.exists() else None


def _region_canvas_size(width_l0: int, height_l0: int) -> tuple[int, int]:
    scale = min(1.0, REGION_MAX_SIZE / max(width_l0, height_l0))
    return max(1, round(width_l0 * scale)), max(1, round(height_l0 * scale))


def _fill_region(canvas: np.ndarray, region: dict, sx: float, sy: float, value: int) -> None:
    coords = region["coordinates"]
    factor = 1 << _SHIFT
    if region["type"] == "circle":
        (cx, cy), r = circle_center_radius(coords)
        center = (round(cx * sx * factor), round(cy * sy * factor))
        cv2.circle(canvas, center, max(1, round(r * sx * factor)), value, thickness=-1, lineType=cv2.LINE_8, shift=_SHIFT)
    else:
        pts = np.array([[round(x * sx * factor), round(y * sy * factor)] for x, y in coords], dtype=np.int32)
        cv2.fillPoly(canvas, [pts], value, lineType=cv2.LINE_8, shift=_SHIFT)


def compose_mask(
    width_l0: int,
    height_l0: int,
    source: str,
    auto_mask: np.ndarray | None,
    regions: list[dict],
) -> np.ndarray | None:
    """The mask patches are cut from, or None when nothing marks any tissue at all
    (manual mode with no regions, or auto mode before detection with no regions)."""
    base = auto_mask if source == "auto" else None
    if not regions:
        return base.copy() if base is not None else None

    w, h = _region_canvas_size(width_l0, height_l0)
    if base is not None and base.shape[1] > w:
        w, h = base.shape[1], base.shape[0]  # never coarser than the detector's own result
    canvas = np.zeros((h, w), dtype=np.uint8)
    if base is not None:
        canvas = cv2.resize(base.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST)

    sx, sy = w / width_l0, h / height_l0
    for region in regions:
        if region["mode"] == "add":
            _fill_region(canvas, region, sx, sy, 1)
    for region in regions:  # removals last, so they win over any overlapping addition
        if region["mode"] == "remove":
            _fill_region(canvas, region, sx, sy, 0)
    return canvas.astype(bool)


def rebuild_slide_mask(storage_root: Path, slide: Slide) -> MaskStats | None:
    """Recompute the slide's mask from its source and regions, save it, and update the
    slide's mask fields and tissue statistics (not committed). Returns None if no mask."""
    auto = _load_auto_mask(storage_root, slide)
    mask = compose_mask(slide.width_l0, slide.height_l0, slide.tissue_source or "auto", auto, slide.tissue_regions or [])
    final = final_mask_path(storage_root, slide)

    if mask is None:
        final.unlink(missing_ok=True)
        slide.tissue_mask_path = None
        slide.tissue_mask_downsample = None
        slide.tissue_area_mm2 = None
        slide.tissue_coverage_pct = None
        if slide.status == "tissue_detected":
            slide.status = "imported"
        return None

    save_mask(final, mask)
    downsample = slide.width_l0 / mask.shape[1]
    mpp_x = slide.mpp_x or 0.25
    mpp_y = slide.mpp_y or 0.25
    tissue_pixels_l0 = float(mask.sum()) * downsample * (slide.height_l0 / mask.shape[0])
    stats = MaskStats(
        tissue_area_mm2=round(tissue_pixels_l0 * mpp_x * mpp_y / 1_000_000, 4),
        tissue_coverage_pct=round(float(mask.mean()) * 100, 2),
    )
    slide.tissue_mask_path = final.relative_to(storage_root).as_posix()
    slide.tissue_mask_downsample = downsample
    slide.tissue_area_mm2 = stats.tissue_area_mm2
    slide.tissue_coverage_pct = stats.tissue_coverage_pct
    if slide.status == "imported":
        slide.status = "tissue_detected"
    # A slide that already has patches keeps them (and its status) until Generate Coords is run again.
    return stats
