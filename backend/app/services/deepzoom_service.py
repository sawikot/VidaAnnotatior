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

from app.core.config import get_settings
from app.services.wsi_reader import WSIReader

# 510 + 1 px overlap on each side = 512 px tiles. Larger tiles mean far fewer requests per screen
# (a browser runs only 6 at once to one server), which is what dominates how fast a view fills in.
TILE_SIZE = 510
TILE_OVERLAP = 1
TILE_FORMAT = "jpeg"
_DOWNSAMPLE_TOLERANCE = 1.01


class DeepZoomAdapter:
    """Minimal DZI generator that works directly against our WSIReader
    interface (rather than requiring an openslide.OpenSlide instance, so it
    also works transparently over ImageReader).
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
        requested scale, so we read as little data as possible without upsampling.

        Real slides report their downsamples as ratios of pixel counts, so a "16x" level
        is typically 16.002 -- strictly comparing would skip it and read the next finer
        level instead (about 16x more data to decode). The tolerance treats those as equal;
        any residual size difference is absorbed by the resize in get_tile."""
        best, best_diff = 0, float("inf")
        for i, ds in enumerate(self.meta.level_downsamples):
            if ds <= scale * _DOWNSAMPLE_TOLERANCE and abs(scale - ds) < best_diff:
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
    """Encoded tiles and thumbnails, least recently used dropped first once `max_bytes` is exceeded."""

    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self._bytes = 0
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
            old = self._data.pop(key, None)
            if old is not None:
                self._bytes -= len(old)
            self._data[key] = value
            self._bytes += len(value)
            while self._bytes > self.max_bytes and len(self._data) > 1:
                _, dropped = self._data.popitem(last=False)
                self._bytes -= len(dropped)

    def purge(self, slide_id: int) -> None:
        with self._lock:
            for key in [k for k in self._data if k[0] == slide_id]:
                self._bytes -= len(self._data.pop(key))


# About 256 MB: several thousand 512 px tiles -- every tile of a few slides' commonly viewed levels.
_tile_cache = _LRUTileCache(max_bytes=get_settings().tile_cache_mb * 1024 * 1024)


def purge_slide_tiles(slide_id: int) -> None:
    """Forget cached tiles for a slide. Required when a slide is deleted:
    database ids get reused, so a stale entry would otherwise be served as the
    tiles of whatever slide is next given the same id."""
    _tile_cache.purge(slide_id)


def render_thumbnail_bytes(reader: WSIReader, slide_id: int, max_size: int) -> bytes:
    """A slide's overview JPEG. Cut from the slide once, then served from memory: every
    dashboard and gallery card asks for one, and each cut reads the file again (seconds
    for a large slide that is not in the disk cache). Purged with the slide's tiles."""
    cache_key = (slide_id, "thumbnail", max_size)
    cached = _tile_cache.get(cache_key)
    if cached is not None:
        return cached
    buf = io.BytesIO()
    reader.get_thumbnail(max_size).save(buf, format="JPEG", quality=88)
    data = buf.getvalue()
    _tile_cache.put(cache_key, data)
    return data


_PREVIEW_MAX_READ = 2048  # px: a wider read (a big area of a slide with no pyramid) is cut from the overview instead


def render_region_preview_bytes(reader: WSIReader, slide_id: int, x: int, y: int, side: int, size: int) -> bytes:
    """A small square JPEG of the Level-0 square at (x, y), for lists of annotations. Read from the coarsest
    level still sharp enough. Any part of the square beyond the slide stays dark, so the picture always
    maps exactly onto the square that was asked for. Kept in memory and purged with the slide's tiles."""
    cache_key = (slide_id, "preview", x, y, side, size)
    cached = _tile_cache.get(cache_key)
    if cached is not None:
        return cached

    meta = reader.get_metadata()
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + side, meta.width), min(y + side, meta.height)
    if x1 <= x0 or y1 <= y0:
        raise ValueError("The area is outside the slide")

    wanted = side / size
    level = max((i for i, d in enumerate(meta.level_downsamples) if d <= wanted), key=lambda i: meta.level_downsamples[i], default=0)
    ds = meta.level_downsamples[level]
    read_w, read_h = max(1, math.ceil((x1 - x0) / ds)), max(1, math.ceil((y1 - y0) / ds))
    if max(read_w, read_h) > _PREVIEW_MAX_READ:
        overview = reader.get_thumbnail(1024).convert("RGB")
        sx, sy = overview.width / meta.width, overview.height / meta.height
        region = overview.crop((int(x0 * sx), int(y0 * sy), max(math.ceil(x1 * sx), int(x0 * sx) + 1), max(math.ceil(y1 * sy), int(y0 * sy) + 1)))
    else:
        region = reader.read_region(x0, y0, level, read_w, read_h).convert("RGB")

    scale = size / side
    canvas = Image.new("RGB", (size, size), (15, 23, 42))
    fitted = region.resize((max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale))), Image.Resampling.BILINEAR)
    canvas.paste(fitted, (round((x0 - x) * scale), round((y0 - y) * scale)))
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=80)
    data = buf.getvalue()
    _tile_cache.put(cache_key, data)
    return data


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
