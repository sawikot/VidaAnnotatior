"""The viewer's tile pyramid must agree with OpenSeadragon's own arithmetic, and
tiles must show the right part of the slide. A previous version got the number
of levels wrong for any slide whose size isn't a power of two, so tiles were
drawn at the wrong scale -- these tests check *content*, not just status codes."""
import math

import numpy as np
import pytest
from PIL import Image

from app.services import deepzoom_service
from app.services.deepzoom_service import (
    TILE_OVERLAP,
    TILE_SIZE,
    DeepZoomAdapter,
    purge_slide_tiles,
    render_thumbnail_bytes,
    render_tile_bytes,
)
from app.services.wsi_reader import WSIMetadata, WSIReader


class GradientReader(WSIReader):
    """Synthetic slide: red = x position, blue = y position (0..255 across the
    whole slide), so a tile's colors reveal exactly which part of the slide it shows."""

    def __init__(self, width: int, height: int, downsamples=(1.0, 4.0, 16.0)):
        self.width, self.height, self.downsamples = width, height, list(downsamples)

    def get_metadata(self) -> WSIMetadata:
        dims = [(math.ceil(self.width / d), math.ceil(self.height / d)) for d in self.downsamples]
        return WSIMetadata(self.width, self.height, len(dims), dims, self.downsamples, None, None, None)

    def read_region(self, x: int, y: int, level: int, width: int, height: int) -> Image.Image:
        ds = self.downsamples[level]
        red = np.clip((x + np.arange(width) * ds) / self.width * 255, 0, 255)
        blue = np.clip((y + np.arange(height) * ds) / self.height * 255, 0, 255)
        arr = np.zeros((height, width, 3), dtype=np.uint8)
        arr[..., 0] = red[None, :]
        arr[..., 2] = blue[:, None]
        return Image.fromarray(arr)

    def get_thumbnail(self, max_size: int = 1024) -> Image.Image:  # pragma: no cover - unused here
        return Image.new("RGB", (1, 1))


SIZES = [(1536, 1024), (2048, 1536), (26687, 23085), (40000, 30000), (1000, 1), (1, 1), (3, 5)]


@pytest.mark.parametrize("width,height", SIZES)
def test_pyramid_matches_the_openseadragon_formula(width, height):
    adapter = DeepZoomAdapter(GradientReader(width, height, downsamples=(1.0,)))
    longest = max(width, height)
    expected_top = math.ceil(math.log2(longest)) if longest > 1 else 0

    assert adapter.max_dzi_level == expected_top
    assert adapter._dzi_level_dimensions(expected_top) == (width, height)  # top level = full resolution
    assert adapter._dzi_level_dimensions(0) == (1, 1)  # bottom level = a single pixel
    for level in range(1, expected_top + 1):
        below_w, below_h = adapter._dzi_level_dimensions(level - 1)
        w, h = adapter._dzi_level_dimensions(level)
        assert (below_w, below_h) == (max(1, math.ceil(w / 2)), max(1, math.ceil(h / 2)))


@pytest.mark.parametrize("width,height,downsamples", [(1536, 1024, (1.0, 4.0, 16.0)), (26687, 23085, (1.0, 4.000311, 16.009006))])
def test_tiles_show_the_correct_part_of_the_slide_at_every_zoom(width, height, downsamples):
    adapter = DeepZoomAdapter(GradientReader(width, height, downsamples))
    for level in range(0, adapter.max_dzi_level + 1, 2):
        scale = adapter._scale(level)
        level_w, level_h = adapter._dzi_level_dimensions(level)
        tol = 255 * scale / width + 4  # one pixel of this level, plus resampling slack

        # A tile in the middle of the level (or the only tile, at coarse levels).
        col = min(2, (level_w - 1) // TILE_SIZE)
        row = min(1, (level_h - 1) // TILE_SIZE)
        tile = np.asarray(adapter.get_tile(level, col, row))
        x0 = col * TILE_SIZE - (TILE_OVERLAP if col else 0)
        y0 = row * TILE_SIZE - (TILE_OVERLAP if row else 0)

        assert abs(int(tile[0, 0, 0]) - min(255, x0 * scale / width * 255)) <= tol, f"level {level}: left edge misplaced"
        assert abs(int(tile[0, 0, 2]) - min(255, y0 * scale / height * 255)) <= 255 * scale / height + 4, f"level {level}: top edge misplaced"
        last_x = (tile.shape[1] - 1) * scale + x0 * scale
        assert abs(int(tile[0, -1, 0]) - min(255, last_x / width * 255)) <= tol, f"level {level}: right edge misplaced (wrong scale)"


def test_a_level_a_hair_over_the_requested_scale_is_still_the_one_read():
    # Real slides report "16x" as 16.002 (a ratio of pixel counts). Skipping such a level
    # reads the next finer one instead -- ~16x more data to decode per tile, which made
    # mid-zoom tiles several times slower than any other zoom.
    adapter = DeepZoomAdapter(GradientReader(76160, 56510, (1.0, 4.000070786437318, 16.00198244123478, 32.008498583569406)))

    assert adapter._best_native_level(1)[0] == 0
    assert adapter._best_native_level(4)[0] == 1
    assert adapter._best_native_level(16)[0] == 2
    assert adapter._best_native_level(32)[0] == 3
    assert adapter._best_native_level(8)[0] == 1  # between levels: the finer one, never upsampled
    assert adapter._best_native_level(2)[0] == 0
    assert adapter._best_native_level(1024)[0] == 3


def test_full_resolution_tiles_are_true_size_and_edge_tiles_are_clipped():
    adapter = DeepZoomAdapter(GradientReader(1536, 1024))
    top = adapter.max_dzi_level
    assert adapter.get_tile(top, 0, 0).size == (TILE_SIZE + TILE_OVERLAP, TILE_SIZE + TILE_OVERLAP)
    cols = math.ceil(1536 / TILE_SIZE)
    last = adapter.get_tile(top, cols - 1, 0)
    assert last.size[0] == 1536 - ((cols - 1) * TILE_SIZE - TILE_OVERLAP)


def test_out_of_range_requests_are_rejected():
    adapter = DeepZoomAdapter(GradientReader(1536, 1024))
    for args in [(-1, 0, 0), (adapter.max_dzi_level + 1, 0, 0), (adapter.max_dzi_level, 99, 0), (adapter.max_dzi_level, 0, 99)]:
        with pytest.raises(ValueError):
            adapter.get_tile(*args)


def test_tile_cache_is_purged_per_slide():
    adapter = DeepZoomAdapter(GradientReader(1536, 1024))
    render_tile_bytes(adapter, 901, adapter.max_dzi_level, 0, 0)
    render_tile_bytes(adapter, 902, adapter.max_dzi_level, 0, 0)
    assert any(k[0] == 901 for k in deepzoom_service._tile_cache._data)
    purge_slide_tiles(901)
    keys = list(deepzoom_service._tile_cache._data)
    assert not any(k[0] == 901 for k in keys) and any(k[0] == 902 for k in keys)
    purge_slide_tiles(902)


def test_thumbnails_are_cut_once_and_forgotten_with_their_slide():
    class Counting(GradientReader):
        cuts = 0

        def get_thumbnail(self, max_size: int = 1024) -> Image.Image:
            Counting.cuts += 1
            return Image.new("RGB", (max_size, max_size // 2), (10, 20, 30))

    reader = Counting(1536, 1024)
    first = render_thumbnail_bytes(reader, 901, 256)
    assert render_thumbnail_bytes(reader, 901, 256) == first and Counting.cuts == 1
    render_thumbnail_bytes(reader, 901, 512)  # another size is another picture
    assert Counting.cuts == 2

    purge_slide_tiles(901)  # e.g. the slide was deleted and its id will be reused
    render_thumbnail_bytes(reader, 901, 256)
    assert Counting.cuts == 3
    purge_slide_tiles(901)
