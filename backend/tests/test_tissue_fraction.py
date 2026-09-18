import numpy as np

from app.services.tissue_detector import tissue_fraction_in_region


def test_full_tissue_region_returns_one():
    mask = np.ones((100, 100), dtype=bool)
    frac = tissue_fraction_in_region(mask, mask_scale=10.0, region_x0=0, region_y0=0,
                                      region_w_l0=500, region_h_l0=500)
    assert frac == 1.0


def test_empty_tissue_region_returns_zero():
    mask = np.zeros((100, 100), dtype=bool)
    frac = tissue_fraction_in_region(mask, mask_scale=10.0, region_x0=0, region_y0=0,
                                      region_w_l0=500, region_h_l0=500)
    assert frac == 0.0


def test_half_tissue_region():
    mask = np.zeros((100, 100), dtype=bool)
    mask[:, :50] = True  # left half is tissue
    # mask_scale=10 -> mask is 1000x1000 L0 px total. Sample the left half exactly.
    frac_left = tissue_fraction_in_region(mask, mask_scale=10.0, region_x0=0, region_y0=0,
                                           region_w_l0=500, region_h_l0=1000)
    assert frac_left == 1.0

    frac_right = tissue_fraction_in_region(mask, mask_scale=10.0, region_x0=500, region_y0=0,
                                            region_w_l0=500, region_h_l0=1000)
    assert frac_right == 0.0


def test_region_outside_mask_bounds_returns_zero():
    mask = np.ones((10, 10), dtype=bool)
    frac = tissue_fraction_in_region(mask, mask_scale=1.0, region_x0=1000, region_y0=1000,
                                      region_w_l0=50, region_h_l0=50)
    assert frac == 0.0
