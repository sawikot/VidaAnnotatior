"""Patch grids: which patch size / stride / magnification / tissue threshold a set of patches was cut with.

A slide can hold several grids at once under the same configuration version (and so the same classes):
annotate at 2048 px, switch to 512 px, and everything drawn so far is still there, because every
annotation also lives in Level-0 pixels. Each patch records the key of the grid it belongs to; a slide
shows and exports the patches of its *active* grid.

The key is a short readable string that identifies a grid exactly, e.g. ``2048x2048_s1024x1024_m40_t0.04``.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

IMAGE_GRID_KEY = "image"  # image projects: every image is one whole-image patch

_KEY = re.compile(
    r"^(?P<pw>\d+)x(?P<ph>\d+)_s(?P<sx>\d+)x(?P<sy>\d+)_m(?P<mag>[0-9.]+|auto)_t(?P<t>[0-9.]+)(?P<edge>_e)?(?P<partial>_p)?$"
)


@dataclass(frozen=True)
class GridSpec:
    patch_width: int
    patch_height: int
    stride_x: int
    stride_y: int
    target_magnification: float | None
    min_tissue_fraction: float
    include_edge_patches: bool = False
    allow_partial_patches: bool = False

    @classmethod
    def from_config(cls, config) -> "GridSpec":  # noqa: ANN001 - a ProjectConfigVersion or anything shaped like one
        return cls(
            patch_width=int(config.patch_width),
            patch_height=int(config.patch_height),
            stride_x=int(config.stride_x),
            stride_y=int(config.stride_y),
            target_magnification=float(config.target_magnification) if config.target_magnification else None,
            min_tissue_fraction=round(float(config.min_tissue_fraction), 4),
            include_edge_patches=bool(getattr(config, "include_edge_patches", False)),
            allow_partial_patches=bool(getattr(config, "allow_partial_patches", False)),
        )

    @classmethod
    def from_key(cls, key: str) -> "GridSpec":
        m = _KEY.match(key)
        if not m:
            raise ValueError(f"Not a patch grid key: {key!r}")
        mag = m["mag"]
        return cls(
            patch_width=int(m["pw"]),
            patch_height=int(m["ph"]),
            stride_x=int(m["sx"]),
            stride_y=int(m["sy"]),
            target_magnification=None if mag == "auto" else float(mag),
            min_tissue_fraction=float(m["t"]),
            include_edge_patches=bool(m["edge"]),
            allow_partial_patches=bool(m["partial"]),
        )

    @property
    def key(self) -> str:
        mag = "auto" if not self.target_magnification else f"{self.target_magnification:g}"
        return (
            f"{self.patch_width}x{self.patch_height}_s{self.stride_x}x{self.stride_y}_m{mag}_t{self.min_tissue_fraction:g}"
            + ("_e" if self.include_edge_patches else "")
            + ("_p" if self.allow_partial_patches else "")
        )

    @property
    def label(self) -> str:
        size = f"{self.patch_width}" if self.patch_width == self.patch_height else f"{self.patch_width}x{self.patch_height}"
        stride = f"{self.stride_x}" if self.stride_x == self.stride_y else f"{self.stride_x}x{self.stride_y}"
        mag = f"{self.target_magnification:g}x" if self.target_magnification else "default magnification"
        return f"{size} px, stride {stride}, {mag}, tissue >= {self.min_tissue_fraction:.0%}"

    # generate_patch_grid reads these attribute names (the same as a config version's).
    target_level = None

    def as_dict(self) -> dict:
        return asdict(self)


def grid_key_of(config) -> str:  # noqa: ANN001
    return GridSpec.from_config(config).key


def active_grid_filter(query, slide):  # noqa: ANN001, ANN201
    """Narrow a Patch query to the slide's active config version and active grid.

    A slide with no active grid yet (patches made before grids existed, or none made) is not narrowed
    by grid, so nothing it has disappears.
    """
    from app.models.patch import Patch

    if slide.active_config_version_id is not None:
        query = query.filter(Patch.config_version_id == slide.active_config_version_id)
    if slide.active_grid_key is not None:
        query = query.filter(Patch.grid_key == slide.active_grid_key)
    return query
