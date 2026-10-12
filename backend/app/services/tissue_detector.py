"""Tissue segmentation.

`TissueDetector` is a small strategy interface so additional methods (LAB,
adaptive threshold, a future deep-learning segmenter) can be registered later
without touching callers. `HSVOtsuDetector` is the first, practical
implementation: thumbnail -> HSV -> a threshold on saturation, set from the glass and Otsu's
threshold (`saturation_threshold`) -> morphological open/close -> drop tiny components.
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
        sensitivity = float(params.get("otsu_sensitivity", 0.65))  # the slider: higher is stricter

        rgb = _glass_to_white(np.array(thumbnail.convert("RGB")))
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        saturation = cv2.medianBlur(hsv[:, :, 1], 3)  # single noisy pixels are not tissue
        value = hsv[:, :, 2]

        mask = (saturation > saturation_threshold(saturation, value, sensitivity)).astype(np.uint8)
        mask = mask & (value > 10).astype(np.uint8)  # near-black: the edge of the scan, not tissue

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


# Glass is bright and has no colour: its saturation sits in a narrow peak at or near zero.
_GLASS_MIN_VALUE = 150
_GLASS_MAX_SATURATION = 25
# The slider's positions (the "Otsu sensitivity", 0.1-0.95) and what each means; in between is a
# straight line. Higher is stricter. The default, 0.65, is "everything that is not glass".
_SLIDER = (0.1, 0.65, 0.8, 0.95)


def _glass_to_white(rgb: np.ndarray) -> np.ndarray:
    """The picture with its glass made white. Some scanners (and old slides) give the glass a tint;
    a faint stain on tinted glass has barely more colour than the glass itself until the tint is
    taken out of both. A picture with no glass in view is returned as it is."""
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    saturation, value = hsv[:, :, 1], hsv[:, :, 2]
    above_glass = _above_glass(saturation, value)
    if above_glass == 0:
        return rgb
    colour = np.median(rgb[(value >= _GLASS_MIN_VALUE) & (saturation < above_glass)], axis=0)
    return np.clip(rgb * (255.0 / np.maximum(colour, 1)), 0, 255).astype(np.uint8)


def _above_glass(saturation: np.ndarray, value: np.ndarray) -> int:
    """The first saturation level that is not glass: just past the glass's own narrow peak among the
    bright pixels (a noisier scanner has a wider one). 0 when there is no glass in view."""
    hist = np.bincount(saturation[value >= _GLASS_MIN_VALUE].ravel(), minlength=256)
    low = hist[: _GLASS_MAX_SATURATION + 1]
    if low.sum() < 0.01 * saturation.size:
        return 0
    peak = int(np.argmax(low))
    level = peak + 1
    while level < 2 * _GLASS_MAX_SATURATION and hist[level] > 0.02 * hist[peak]:
        level += 1
    return level


def saturation_threshold(saturation: np.ndarray, value: np.ndarray, sensitivity: float) -> float:
    """The saturation above which a pixel counts as tissue.

    Otsu's threshold alone splits a slide into "strongly stained" and "the rest", so pale tissue --
    fat, loose stroma, a faint stain -- lands with the glass. What separates tissue from glass is
    the glass itself: it is the one part of the picture with no colour at all. So the threshold
    starts just above the glass's own saturation (found from the picture, as scanners differ), which
    keeps every stained pixel however pale, and the slider moves it from there:

    * 0.10 -- the first level above the glass: the most that can be called tissue;
    * 0.65 -- a few levels above the glass: all tissue, without the glass's noise (the default);
    * 0.80 -- Otsu's threshold: only the strongly stained tissue;
    * 0.95 -- well above Otsu's: only the darkest.

    A slide with no glass in view (stained edge to edge) has nothing to start above, so everything
    with any colour is tissue until the slider is raised.
    """
    above_glass = _above_glass(saturation, value)
    otsu, _ = cv2.threshold(saturation, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    everything = above_glass + 4.0
    strong = max(float(otsu), everything + 1)
    threshold = np.interp(sensitivity, _SLIDER, (above_glass + 1.0, everything, strong, 1.6 * strong))
    return float(np.clip(threshold, 1, 250))


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
