"""Serves a standard Deep Zoom Image (DZI) pyramid over any WSIReader, generated
on request -- nothing is ever written to disk. This is purely a *display*
concern for OpenSeadragon; it is unrelated to (and must not be confused with)
the virtual annotation patches, which are pure coordinates.
"""
from __future__ import annotations

import io
import threading
from collections import OrderedDict

from PIL import Image

from app.services.wsi_reader import WSIReader

TILE_SIZE = 254
TILE_OVERLAP = 1
TILE_FORMAT = "jpeg"


class DeepZoomAdapter:
    """Minimal DZI generator that works directly against our WSIReader
    interface (rather than requiring an openslide.OpenSlide instance, so it
    also works transparently over DemoWSIReader).
    """

    def __init__(self, reader: WSIReader):
        self.reader = reader
        self.meta = reader.get_metadata()
        # DZI levels are defined top-down by successive halving of the full-res image,
        # independent of the slide's own pyramid levels. We map each DZI level to the
        # closest native level for an efficient read, then resize.
        self._dzi_level_count = 1
        dim = max(self.meta.width, self.meta.height)
        while dim > 1:
            dim = dim // 2
            self._dzi_level_count += 1

    def get_dzi_xml(self) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<Image TileSize="{TILE_SIZE}" Overlap="{TILE_OVERLAP}" Format="{TILE_FORMAT}" '
            'xmlns="http://schemas.microsoft.com/deepzoom/2008">'
            f'<Size Width="{self.meta.width}" Height="{self.meta.height}"/>'
            "</Image>"
        )

    @property
    def max_dzi_level(self) -> int:
        return self._dzi_level_count - 1

    def _dzi_level_dimensions(self, dzi_level: int) -> tuple[int, int]:
        scale = 2 ** (self.max_dzi_level - dzi_level)
        w = max(1, round(self.meta.width / scale))
        h = max(1, round(self.meta.height / scale))
        return w, h

    def _best_native_level(self, dzi_level: int) -> tuple[int, float]:
        target_w, _ = self._dzi_level_dimensions(dzi_level)
        target_downsample = self.meta.width / target_w
        best = 0
        best_diff = float("inf")
        for i, ds in enumerate(self.meta.level_downsamples):
            if ds <= target_downsample:
                diff = target_downsample - ds
                if diff < best_diff:
                    best_diff = diff
                    best = i
        return best, self.meta.level_downsamples[best]

    def get_tile(self, dzi_level: int, col: int, row: int) -> Image.Image:
        if dzi_level < 0 or dzi_level > self.max_dzi_level:
            raise ValueError(f"Invalid DZI level {dzi_level}")

        level_w, level_h = self._dzi_level_dimensions(dzi_level)
        dzi_to_level0 = self.meta.width / level_w

        x = col * TILE_SIZE - TILE_OVERLAP if col else 0
        y = row * TILE_SIZE - TILE_OVERLAP if row else 0
        w = min(TILE_SIZE + (2 * TILE_OVERLAP if col else TILE_OVERLAP), level_w - x)
        h = min(TILE_SIZE + (2 * TILE_OVERLAP if row else TILE_OVERLAP), level_h - y)
        if w <= 0 or h <= 0:
            raise ValueError("Tile out of bounds")

        native_level, native_downsample = self._best_native_level(dzi_level)
        read_scale = dzi_to_level0 / native_downsample  # native px per DZI-level px

        native_x = round(x * dzi_to_level0)
        native_y = round(y * dzi_to_level0)
        native_w = max(1, round(w * read_scale))
        native_h = max(1, round(h * read_scale))

        region = self.reader.read_region(native_x, native_y, native_level, native_w, native_h)
        if (native_w, native_h) != (w, h):
            region = region.resize((max(1, round(w)), max(1, round(h))), Image.LANCZOS)
        return region


class _LRUTileCache:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self._data: OrderedDict[tuple, bytes] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple) -> bytes | None:
        with self._lock:
            if key not in self._data:
                return None
            self._data.move_to_end(key)
            return self._data[key]

    def put(self, key: tuple, value: bytes) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            if len(self._data) > self.capacity:
                self._data.popitem(last=False)


_tile_cache = _LRUTileCache(capacity=512)


def render_tile_bytes(adapter: DeepZoomAdapter, slide_id: int, dzi_level: int, col: int, row: int) -> bytes:
    cache_key = (slide_id, dzi_level, col, row)
    cached = _tile_cache.get(cache_key)
    if cached is not None:
        return cached
    tile = adapter.get_tile(dzi_level, col, row)
    buf = io.BytesIO()
    tile.save(buf, format="JPEG", quality=85)
    data = buf.getvalue()
    _tile_cache.put(cache_key, data)
    return data
