"""Tissue segmentation.

`TissueDetector` is a small strategy interface so additional methods (LAB,
adaptive threshold, a future deep-learning segmenter) can be registered later
without touching callers. `HSVOtsuDetector` is the first, practical
implementation: thumbnail -> HSV -> Otsu threshold on saturation -> morphological
open/close -> drop tiny components.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image


@dataclass
class TissueMaskResult:
    mask: np.ndarray  # bool array, shape (h, w), True == tissue. Same size as the input thumbnail.
    tissue_fraction: float  # fraction of the thumbnail classified as tissue
    thumbnail_size: tuple[int, int]  # (w, h) the mask corresponds to


class TissueDetector(ABC):
    method_name: str

    @abstractmethod
    def detect(self, thumbnail: Image.Image, params: dict) -> TissueMaskResult: ...


class HSVOtsuDetector(TissueDetector):
    method_name = "hsv_otsu"

    def detect(self, thumbnail: Image.Image, params: dict) -> TissueMaskResult:
        open_px = int(params.get("morph_open_px", 3))
        close_px = int(params.get("morph_close_px", 5))
        min_component_px = int(params.get("min_component_px", 400))
        sensitivity = float(params.get("otsu_sensitivity", 0.65))  # blended with the Otsu result

        rgb = np.array(thumbnail.convert("RGB"))
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        saturation = hsv[:, :, 1]

        otsu_thresh, _ = cv2.threshold(saturation, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        # Blend the data-driven Otsu threshold with the user's sensitivity slider
        # (0..1, mapped onto the 0..255 saturation range) so the UI slider has real effect.
        effective_thresh = otsu_thresh * (1 - sensitivity) + (sensitivity * 255) * sensitivity
        effective_thresh = float(np.clip(effective_thresh, 5, 250))

        mask = (saturation > effective_thresh).astype(np.uint8)

        # Also reject near-white (glass) and near-black (vignette/edge) pixels outright.
        value = hsv[:, :, 2]
        not_glass = ~((saturation < 15) & (value > 200))
        not_black = value > 10
        mask = mask & not_glass.astype(np.uint8) & not_black.astype(np.uint8)

        if open_px > 0:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_px, open_px))
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        if close_px > 0:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_px, close_px))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        mask = _remove_small_components(mask, min_component_px)

        bool_mask = mask.astype(bool)
        tissue_fraction = float(bool_mask.sum()) / float(bool_mask.size) if bool_mask.size else 0.0
        h, w = bool_mask.shape
        return TissueMaskResult(mask=bool_mask, tissue_fraction=tissue_fraction, thumbnail_size=(w, h))


def _remove_small_components(mask: np.ndarray, min_size: int) -> np.ndarray:
    if min_size <= 0:
        return mask
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    cleaned = np.zeros_like(mask)
    for label in range(1, n_labels):
        if stats[label, cv2.CC_STAT_AREA] >= min_size:
            cleaned[labels == label] = 1
    return cleaned


DETECTORS: dict[str, TissueDetector] = {
    "hsv_otsu": HSVOtsuDetector(),
}


def get_detector(method: str) -> TissueDetector:
    if method not in DETECTORS:
        raise ValueError(f"Unknown tissue detection method '{method}'. Available: {list(DETECTORS)}")
    return DETECTORS[method]


def tissue_fraction_in_region(mask: np.ndarray, mask_scale: float, region_x0: int, region_y0: int,
                               region_w_l0: int, region_h_l0: int) -> float:
    """Fraction of tissue within a Level-0 region, sampled against a thumbnail-resolution mask.

    `mask_scale` = Level-0 pixels per mask pixel (i.e. the downsample of the thumbnail the mask
    was computed on, relative to Level-0).
    """
    h, w = mask.shape
    mx0 = int(region_x0 / mask_scale)
    my0 = int(region_y0 / mask_scale)
    mx1 = max(mx0 + 1, int((region_x0 + region_w_l0) / mask_scale))
    my1 = max(my0 + 1, int((region_y0 + region_h_l0) / mask_scale))

    mx0, my0 = max(0, mx0), max(0, my0)
    mx1, my1 = min(w, mx1), min(h, my1)
    if mx1 <= mx0 or my1 <= my0:
        return 0.0

    region = mask[my0:my1, mx0:mx1]
    if region.size == 0:
        return 0.0
    return float(region.sum()) / float(region.size)
