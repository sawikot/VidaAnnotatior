"""Automatic tissue detection: everything stained counts, however pale, and the glass does not."""
import numpy as np
from PIL import Image

from app.services.tissue_detector import HSVOtsuDetector

GLASS = (255, 255, 255)
STRONG = (120, 40, 140)  # dense, dark purple
PALE = (243, 232, 240)  # fat or loose stroma: barely pink


def slide(glass=GLASS, noise=0, size=400) -> np.ndarray:
    """Glass with a strongly stained square (top left) and a pale one (bottom right)."""
    rng = np.random.default_rng(0)
    pixels = np.empty((size, size, 3), dtype=np.int16)
    pixels[:] = glass
    pixels[40:160, 40:160] = STRONG
    pixels[240:360, 240:360] = PALE
    if noise:
        pixels += rng.integers(-noise, noise + 1, pixels.shape, dtype=np.int16)
    return np.clip(pixels, 0, 255).astype(np.uint8)


def detect(pixels: np.ndarray, **params):
    return HSVOtsuDetector().detect(Image.fromarray(pixels), {"morph_open_px": 0, "morph_close_px": 0, "min_component_px": 0, **params}).mask


def covered(mask: np.ndarray) -> tuple[float, float, float]:
    """The share marked as tissue of: the strong square, the pale square, the glass."""
    glass = np.ones_like(mask)
    glass[30:170, 30:170] = glass[230:370, 230:370] = False
    return float(mask[40:160, 40:160].mean()), float(mask[240:360, 240:360].mean()), float(mask[glass].mean())


def test_pale_tissue_is_tissue_and_glass_is_not():
    strong, pale, glass = covered(detect(slide()))
    assert strong > 0.99 and pale > 0.99 and glass == 0


def test_the_slider_goes_from_everything_stained_to_only_the_strongly_stained():
    strong, pale, _ = covered(detect(slide(), otsu_sensitivity=0.1))
    assert strong > 0.99 and pale > 0.99
    strong, pale, glass = covered(detect(slide(), otsu_sensitivity=0.8))  # Otsu's own threshold
    assert strong > 0.99 and pale == 0 and glass == 0
    areas = [detect(slide(), otsu_sensitivity=s).sum() for s in (0.1, 0.4, 0.65, 0.8, 0.95)]
    assert areas == sorted(areas, reverse=True)  # higher is never more


def test_a_scanners_noisy_or_tinted_glass_is_still_glass():
    for pixels in (slide(noise=3), slide(glass=(250, 249, 244)), slide(glass=(250, 249, 244), noise=2)):
        strong, pale, glass = covered(detect(pixels))
        assert strong > 0.99 and pale > 0.9 and glass < 0.002


def test_a_slide_stained_edge_to_edge_is_all_tissue():
    pixels = np.empty((200, 200, 3), dtype=np.uint8)
    pixels[:] = (215, 200, 235)  # a smear covering the whole scan: no glass in view
    assert detect(pixels).mean() == 1.0


def test_the_black_edge_of_a_scan_is_not_tissue():
    pixels = slide()
    pixels[:, :20] = (0, 0, 0)
    mask = detect(pixels)
    assert not mask[:, :20].any() and mask[40:160, 40:160].mean() > 0.99
