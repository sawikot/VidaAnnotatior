"""The synthetic demo slide must render exactly the pixels it always did (annotations
drawn on it stay in place) -- and fast enough to be usable."""
import time

import numpy as np
import pytest

from app.services.wsi_reader import DemoWSIReader


def reference_read_region(reader: DemoWSIReader, x: int, y: int, level: int, width: int, height: int) -> np.ndarray:
    """The original whole-region implementation, kept as the ground truth."""
    downsample = reader._downsamples[level]
    rng = np.random.default_rng(abs(hash((reader._seed, x, y, level, width, height))) % (2**32))
    img = np.full((height, width, 3), 245, dtype=np.uint8)
    noise = rng.integers(-6, 6, size=(height, width, 1))
    img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    x1_l0, y1_l0 = x + width * downsample, y + height * downsample
    yy, xx = np.mgrid[0:height, 0:width]
    global_xx, global_yy = x + xx * downsample, y + yy * downsample
    for b in reader._blobs:
        if b.cx + b.r < x or b.cx - b.r > x1_l0 or b.cy + b.r < y or b.cy - b.r > y1_l0:
            continue
        dist = np.sqrt((global_xx - b.cx) ** 2 + (global_yy - b.cy) ** 2)
        mask = dist < b.r
        if not mask.any():
            continue
        falloff = np.clip(1.0 - dist / b.r, 0, 1) ** 0.5
        for c in range(3):
            img[..., c] = np.where(mask, (img[..., c] * (1 - falloff) + b.color[c] * falloff).astype(np.uint8), img[..., c])
    return img


@pytest.mark.parametrize(
    "x,y,level,width,height",
    [
        (0, 0, 5, 200, 150),  # a wide area at a coarse level: many blobs overlap (the reference is slow, so keep it small)
        (12000, 9000, 0, 256, 256),  # a full-resolution tile
        (5000, 4000, 2, 300, 200),
        (39000, 29000, 0, 900, 900),  # runs off the slide's corner
        (-500, -500, 1, 400, 400),  # starts before the slide
        (20000, 15000, 3, 1, 1),
    ],
)
def test_pixels_are_identical_to_the_original_implementation(x, y, level, width, height):
    reader = DemoWSIReader(seed=16)
    assert np.array_equal(np.asarray(reader.read_region(x, y, level, width, height)), reference_read_region(reader, x, y, level, width, height))


def test_a_full_slide_thumbnail_takes_seconds_not_minutes():
    reader = DemoWSIReader(seed=1)
    started = time.perf_counter()
    thumb = reader.get_thumbnail(512)
    assert thumb.size[0] == 512
    assert time.perf_counter() - started < 5
