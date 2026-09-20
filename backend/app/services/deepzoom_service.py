"""Serves a standard Deep Zoom Image (DZI) pyramid over any WSIReader, generated
on request -- nothing is ever written to disk. This is purely a *display*
concern for OpenSeadragon; it is unrelated to (and must not be confused with)
the virtual annotation patches, which are pure coordinates.
"""
from __future__ import annotations

import io
import math
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
        # Standard Deep Zoom pyramid: the top level is the full-resolution image
        # and each level below halves it, down to 1x1 -- ceil(log2(longest side))
        # halvings. OpenSeadragon derives its own top level from the <Size> in
        # the descriptor with exactly this formula, so ours must agree to the
        # digit: an off-by-one here makes every tile land at half or double scale.
        longest = max(self.meta.width, self.meta.height)
        self._max_level = math.ceil(math.log2(longest)) if longest > 1 else 0

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
        return self._max_level

    def _scale(self, dzi_level: int) -> int:
        """Level-0 pixels covered by one pixel of this DZI level (a power of two)."""
        return 2 ** (self._max_level - dzi_level)

    def _dzi_level_dimensions(self, dzi_level: int) -> tuple[int, int]:
        scale = self._scale(dzi_level)
        return max(1, -(-self.meta.width // scale)), max(1, -(-self.meta.height // scale))

    def _best_native_level(self, scale: int) -> tuple[int, float]:
        """The slide's own pyramid level closest to (but not coarser than) the
        requested scale, so we read as little data as possible without upsampling."""
        best, best_diff = 0, float("inf")
        for i, ds in enumerate(self.meta.level_downsamples):
            if ds <= scale and scale - ds < best_diff:
                best, best_diff = i, scale - ds
        return best, self.meta.level_downsamples[best]

    def get_tile(self, dzi_level: int, col: int, row: int) -> Image.Image:
        if dzi_level < 0 or dzi_level > self._max_level:
            raise ValueError(f"Invalid DZI level {dzi_level}")

        level_w, level_h = self._dzi_level_dimensions(dzi_level)
        scale = self._scale(dzi_level)

        # Tile rectangle in this DZI level's own pixels, including the overlap border.
        x = col * TILE_SIZE - TILE_OVERLAP if col else 0
        y = row * TILE_SIZE - TILE_OVERLAP if row else 0
        w = min(TILE_SIZE + (2 * TILE_OVERLAP if col else TILE_OVERLAP), level_w - x)
        h = min(TILE_SIZE + (2 * TILE_OVERLAP if row else TILE_OVERLAP), level_h - y)
        if w <= 0 or h <= 0:
            raise ValueError("Tile out of bounds")

        # The same rectangle in Level-0 pixels, clipped to the slide (the last
        # row/column of a level can reach a few pixels past the edge).
        native_x, native_y = x * scale, y * scale
        span_w = min(w * scale, self.meta.width - native_x)
        span_h = min(h * scale, self.meta.height - native_y)
        if span_w <= 0 or span_h <= 0:
            raise ValueError("Tile out of bounds")

        native_level, native_downsample = self._best_native_level(scale)
        region = self.reader.read_region(
            native_x,
            native_y,
            native_level,
            max(1, round(span_w / native_downsample)),
            max(1, round(span_h / native_downsample)),
        )
        out_size = (max(1, round(span_w / scale)), max(1, round(span_h / scale)))
        if region.size != out_size:
            region = region.resize(out_size, Image.LANCZOS)
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

    def purge(self, slide_id: int) -> None:
        with self._lock:
            for key in [k for k in self._data if k[0] == slide_id]:
                del self._data[key]


_tile_cache = _LRUTileCache(capacity=512)


def purge_slide_tiles(slide_id: int) -> None:
    """Forget cached tiles for a slide. Required when a slide is deleted:
    database ids get reused, so a stale entry would otherwise be served as the
    tiles of whatever slide is next given the same id."""
    _tile_cache.purge(slide_id)


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
