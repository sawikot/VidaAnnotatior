"""WSI reading abstraction.

`WSIReader` is the interface every part of the app (deepzoom, patch endpoint,
tissue detector, metadata extraction) reads through. `OpenSlideReader` is the
real implementation over openslide-python. `DemoWSIReader` synthesizes a
procedural tissue-like pyramidal image on the fly -- same interface, no file
needed -- so the whole app can be exercised without a real slide (spec section 30).
"""
from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import openslide
from PIL import Image


@dataclass
class WSIMetadata:
    width: int
    height: int
    level_count: int
    level_dimensions: list[tuple[int, int]]
    level_downsamples: list[float]
    mpp_x: float | None
    mpp_y: float | None
    magnification: float | None
    vendor: str | None = None


class WSIReader(ABC):
    @abstractmethod
    def get_metadata(self) -> WSIMetadata: ...

    @abstractmethod
    def read_region(self, x: int, y: int, level: int, width: int, height: int) -> Image.Image:
        """x, y are Level-0 absolute coordinates. width/height are pixels *at `level`*."""
        ...

    @abstractmethod
    def get_thumbnail(self, max_size: int = 1024) -> Image.Image: ...

    def close(self) -> None:  # pragma: no cover - default no-op
        pass


class OpenSlideReader(WSIReader):
    def __init__(self, file_path: str | Path):
        self._path = str(file_path)
        self._slide = openslide.OpenSlide(self._path)

    def get_metadata(self) -> WSIMetadata:
        s = self._slide
        props = s.properties
        mpp_x = _safe_float(props.get(openslide.PROPERTY_NAME_MPP_X))
        mpp_y = _safe_float(props.get(openslide.PROPERTY_NAME_MPP_Y))
        magnification = _safe_float(props.get("openslide.objective-power"))
        return WSIMetadata(
            width=s.dimensions[0],
            height=s.dimensions[1],
            level_count=s.level_count,
            level_dimensions=list(s.level_dimensions),
            level_downsamples=[float(d) for d in s.level_downsamples],
            mpp_x=mpp_x,
            mpp_y=mpp_y,
            magnification=magnification,
            vendor=props.get(openslide.PROPERTY_NAME_VENDOR),
        )

    def read_region(self, x: int, y: int, level: int, width: int, height: int) -> Image.Image:
        region = self._slide.read_region((x, y), level, (width, height))
        return region.convert("RGB")

    def get_thumbnail(self, max_size: int = 1024) -> Image.Image:
        return self._slide.get_thumbnail((max_size, max_size)).convert("RGB")

    def close(self) -> None:
        self._slide.close()


def _safe_float(v) -> float | None:  # noqa: ANN001
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass
class _Blob:
    cx: float
    cy: float
    r: float
    color: tuple[int, int, int]


class DemoWSIReader(WSIReader):
    """Procedurally generates a deterministic, tissue-mockup pyramidal image.

    Deterministic per (seed, region) so the same tile/patch always renders the
    same pixels without ever persisting an image file. Used for
    Slide.source_type == "demo".
    """

    NATIVE_WIDTH = 40000
    NATIVE_HEIGHT = 30000

    def __init__(self, seed: int = 42):
        self._seed = seed
        self._downsamples = [1.0, 2.0, 4.0, 8.0, 16.0, 32.0]
        self._level_dims = [
            (max(1, round(self.NATIVE_WIDTH / d)), max(1, round(self.NATIVE_HEIGHT / d)))
            for d in self._downsamples
        ]
        rng = np.random.default_rng(seed)
        n_blobs = 220
        self._blobs = [
            _Blob(
                cx=float(rng.uniform(0, self.NATIVE_WIDTH)),
                cy=float(rng.uniform(0, self.NATIVE_HEIGHT)),
                r=float(rng.uniform(400, 2600)),
                color=(
                    int(rng.uniform(120, 210)),
                    int(rng.uniform(60, 150)),
                    int(rng.uniform(120, 200)),
                ),
            )
            for _ in range(n_blobs)
        ]

    def get_metadata(self) -> WSIMetadata:
        return WSIMetadata(
            width=self.NATIVE_WIDTH,
            height=self.NATIVE_HEIGHT,
            level_count=len(self._downsamples),
            level_dimensions=self._level_dims,
            level_downsamples=self._downsamples,
            mpp_x=0.25,
            mpp_y=0.25,
            magnification=40.0,
            vendor="demo-synthetic",
        )

    def read_region(self, x: int, y: int, level: int, width: int, height: int) -> Image.Image:
        downsample = self._downsamples[level]
        # Background: pale glass with subtle per-pixel-block noise, deterministic via hash-seeded rng.
        block_seed = (self._seed, x, y, level, width, height)
        rng = np.random.default_rng(abs(hash(block_seed)) % (2**32))
        img = np.full((height, width, 3), 245, dtype=np.uint8)
        noise = rng.integers(-6, 6, size=(height, width, 1))
        img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)

        # Stamp tissue blobs that intersect this region.
        x0_l0, y0_l0 = x, y
        x1_l0, y1_l0 = x + width * downsample, y + height * downsample
        yy, xx = np.mgrid[0:height, 0:width]
        global_xx = x0_l0 + xx * downsample
        global_yy = y0_l0 + yy * downsample

        for b in self._blobs:
            if b.cx + b.r < x0_l0 or b.cx - b.r > x1_l0 or b.cy + b.r < y0_l0 or b.cy - b.r > y1_l0:
                continue
            dist = np.sqrt((global_xx - b.cx) ** 2 + (global_yy - b.cy) ** 2)
            mask = dist < b.r
            if not mask.any():
                continue
            falloff = np.clip(1.0 - dist / b.r, 0, 1) ** 0.5
            for c in range(3):
                img[..., c] = np.where(
                    mask,
                    (img[..., c] * (1 - falloff) + b.color[c] * falloff).astype(np.uint8),
                    img[..., c],
                )
        return Image.fromarray(img, mode="RGB")

    def get_thumbnail(self, max_size: int = 1024) -> Image.Image:
        scale = max(self.NATIVE_WIDTH, self.NATIVE_HEIGHT) / max_size
        w, h = round(self.NATIVE_WIDTH / scale), round(self.NATIVE_HEIGHT / scale)
        # Find the closest pyramid level to read at, then resize.
        level = min(range(len(self._downsamples)), key=lambda i: abs(self._downsamples[i] - scale))
        ds = self._downsamples[level]
        lw, lh = self._level_dims[level]
        region = self.read_region(0, 0, level, lw, lh)
        return region.resize((w, h))
