"""WSI reading abstraction.

`WSIReader` is the interface every part of the app (deepzoom, patch endpoint,
tissue detector, metadata extraction) reads through. `OpenSlideReader` is the
real implementation over openslide-python; `ImageReader` presents a plain image
as a single-level slide.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import openslide
from PIL import Image, ImageOps


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


class ImageReader(WSIReader):
    """An ordinary image (PNG, JPEG, small TIFF ...) presented as a one-level "slide".

    Level 0 *is* the image: pixel (0, 0) is its top-left corner and there is no
    pyramid. Everything downstream (patch endpoint, viewer tiles, annotation
    coordinates, export) then works exactly as it does for a real slide.

    The image is normalised the same way on every read -- EXIF rotation applied,
    converted to RGB -- so annotation coordinates always refer to the pixels the
    annotator saw, and the file on disk is never modified.
    """

    def __init__(self, file_path: str | Path):
        self._path = Path(file_path)
        self._lock = threading.Lock()
        self._image: Image.Image | None = None

    def _pixels(self) -> Image.Image:
        with self._lock:
            if self._image is None:
                self._image = load_rgb(self._path)
            return self._image

    def get_metadata(self) -> WSIMetadata:
        width, height = self._pixels().size
        return WSIMetadata(
            width=width,
            height=height,
            level_count=1,
            level_dimensions=[(width, height)],
            level_downsamples=[1.0],
            mpp_x=None,
            mpp_y=None,
            magnification=None,
            vendor="image",
        )

    def read_region(self, x: int, y: int, level: int, width: int, height: int) -> Image.Image:
        if level != 0:
            raise ValueError("Images have a single level (0)")
        image = self._pixels()
        canvas = Image.new("RGB", (width, height))  # black where the request leaves the image, as OpenSlide does
        left, top = max(x, 0), max(y, 0)
        right, bottom = min(x + width, image.width), min(y + height, image.height)
        if right > left and bottom > top:
            canvas.paste(image.crop((left, top, right, bottom)), (left - x, top - y))
        return canvas

    def get_thumbnail(self, max_size: int = 1024) -> Image.Image:
        thumb = self._pixels().copy()
        thumb.thumbnail((max_size, max_size), Image.LANCZOS)
        return thumb

    def close(self) -> None:
        with self._lock:
            self._image = None


def load_rgb(path: Path) -> Image.Image:
    """Decode an image file into upright RGB pixels (the canonical form annotations refer to)."""
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened)  # returns a copy, already loaded
        if image.mode in ("I;16", "I;16B", "I;16L", "I"):
            # 16-bit grayscale would otherwise be clipped to white by a plain convert().
            array = np.asarray(image, dtype=np.float64)
            top = array.max() or 1.0
            image = Image.fromarray((array / top * 255).astype(np.uint8))
        elif image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
            rgba = image.convert("RGBA")
            background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            image = Image.alpha_composite(background, rgba)
        return image.convert("RGB")


def _safe_float(v) -> float | None:  # noqa: ANN001
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
