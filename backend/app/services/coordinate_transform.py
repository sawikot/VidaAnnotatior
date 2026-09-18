"""Centralized Level-0 <-> patch coordinate transforms.

This module is the SINGLE source of truth for converting between the four
coordinate spaces used across the app:

1. Browser screen coordinates      -- frontend only, never stored.
2. Patch-display coordinates       -- pixels of the image actually rendered
                                       to the user (== what read_region(...)
                                       returns for a patch: `width x height`
                                       at pyramid `level`).
3. Patch-source / Level-0 origin   -- the (x, y) anchor of the patch in the
                                       slide's Level-0 (native, full-res)
                                       pixel grid.
4. WSI Level-0 coordinates         -- the master, absolute coordinate system.
                                       Everything persisted to the database
                                       and every JSON export uses this space.

Core rule: ANNOTATE LOCALLY (space 2), STORE GLOBALLY (space 4).

A mirror of this module's math lives in frontend/src/utils/coordinates.ts;
both are covered by fixture-identical unit tests (see backend/tests and
frontend/src/utils/coordinates.test.ts) so the two can never silently drift.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PatchOrigin:
    """Anchors a patch inside a slide's coordinate pyramid."""

    x: int  # Level-0 origin X
    y: int  # Level-0 origin Y
    level: int  # pyramid level the patch is read/displayed at
    downsample: float  # downsample factor of `level` relative to Level-0


def patch_local_to_level0(
    origin: PatchOrigin, local_x: float, local_y: float
) -> tuple[float, float]:
    """Convert a point drawn in patch-display pixels to absolute Level-0 pixels.

    global = origin + local * downsample(level)
    """
    global_x = origin.x + local_x * origin.downsample
    global_y = origin.y + local_y * origin.downsample
    return global_x, global_y


def level0_to_patch_local(
    origin: PatchOrigin, global_x: float, global_y: float
) -> tuple[float, float]:
    """Inverse of patch_local_to_level0."""
    local_x = (global_x - origin.x) / origin.downsample
    local_y = (global_y - origin.y) / origin.downsample
    return local_x, local_y


def polygon_patch_local_to_level0(
    origin: PatchOrigin, points: list[list[float]]
) -> list[list[float]]:
    return [list(patch_local_to_level0(origin, px, py)) for px, py in points]


def polygon_level0_to_patch_local(
    origin: PatchOrigin, points: list[list[float]]
) -> list[list[float]]:
    return [list(level0_to_patch_local(origin, gx, gy)) for gx, gy in points]


def patch_footprint_l0(width: int, height: int, downsample: float) -> tuple[int, int]:
    """Size, in Level-0 pixels, that a `width x height` patch at a given downsample covers."""
    return round(width * downsample), round(height * downsample)


def level0_bounds_of_patch(origin: PatchOrigin, width: int, height: int) -> tuple[int, int, int, int]:
    """Return (x0, y0, x1, y1) -- the patch's bounding box in Level-0 pixels."""
    w_l0, h_l0 = patch_footprint_l0(width, height, origin.downsample)
    return origin.x, origin.y, origin.x + w_l0, origin.y + h_l0


def downsample_for_level(level_downsamples: list[float], level: int) -> float:
    if level < 0 or level >= len(level_downsamples):
        raise ValueError(f"Invalid pyramid level {level}; slide has {len(level_downsamples)} levels")
    return float(level_downsamples[level])


def best_level_for_magnification(
    objective_magnification: float | None,
    level_downsamples: list[float],
    target_magnification: float,
) -> int:
    """Pick the pyramid level whose effective magnification is closest to (but not
    below, when possible) the requested target magnification."""
    if not objective_magnification:
        objective_magnification = 40.0
    target_downsample = objective_magnification / target_magnification
    best_level = 0
    best_diff = float("inf")
    for i, ds in enumerate(level_downsamples):
        diff = abs(ds - target_downsample)
        if diff < best_diff:
            best_diff = diff
            best_level = i
    return best_level
