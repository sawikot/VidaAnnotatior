"""Virtual patch coordinate generation.

Walks a slide's Level-0 grid at the config's patch/stride size, samples the
cached tissue mask for each candidate patch, and keeps only patches meeting
`min_tissue_fraction`. Nothing is written except coordinates + metadata rows --
no patch images are ever extracted to disk.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.models.config_version import ProjectConfigVersion
from app.services.coordinate_transform import best_level_for_magnification
from app.services.tissue_detector import tissue_fraction_in_region
from app.services.wsi_reader import WSIMetadata


@dataclass
class CandidatePatch:
    patch_index: int
    x: int
    y: int
    level: int
    width: int
    height: int
    width_l0: int
    height_l0: int
    tissue_fraction: float
    kept: bool


def generate_patch_grid(
    metadata: WSIMetadata,
    config: ProjectConfigVersion,
    tissue_mask: np.ndarray | None,
    tissue_mask_downsample: float | None,
) -> list[CandidatePatch]:
    level = best_level_for_magnification(
        metadata.magnification, metadata.level_downsamples, config.target_magnification or 20.0
    )
    downsample = metadata.level_downsamples[level]

    stride_l0_x = config.stride_x * downsample
    stride_l0_y = config.stride_y * downsample
    patch_l0_w = config.patch_width * downsample
    patch_l0_h = config.patch_height * downsample

    if stride_l0_x <= 0 or stride_l0_y <= 0:
        raise ValueError("Stride must be positive")

    candidates: list[CandidatePatch] = []
    idx = 0
    y = 0.0
    while y < metadata.height:
        x = 0.0
        while x < metadata.width:
            x0, y0 = round(x), round(y)
            w_l0, h_l0 = round(patch_l0_w), round(patch_l0_h)

            is_edge = (x0 + w_l0 > metadata.width) or (y0 + h_l0 > metadata.height)
            if is_edge and not config.include_edge_patches:
                x += stride_l0_x
                continue
            if is_edge and not config.allow_partial_patches:
                # Clip the read footprint to the slide bounds but keep the nominal patch
                # width/height for grid regularity metadata; the dynamic patch endpoint
                # itself always clamps reads to slide bounds.
                pass

            if tissue_mask is not None and tissue_mask_downsample:
                frac = tissue_fraction_in_region(
                    tissue_mask, tissue_mask_downsample, x0, y0, w_l0, h_l0
                )
            else:
                frac = 1.0

            kept = frac >= config.min_tissue_fraction
            candidates.append(
                CandidatePatch(
                    patch_index=idx,
                    x=x0,
                    y=y0,
                    level=level,
                    width=config.patch_width,
                    height=config.patch_height,
                    width_l0=w_l0,
                    height_l0=h_l0,
                    tissue_fraction=round(frac, 4),
                    kept=kept,
                )
            )
            idx += 1
            x += stride_l0_x
        y += stride_l0_y

    return candidates
