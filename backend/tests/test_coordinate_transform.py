from app.services.coordinate_transform import (
    PatchOrigin,
    best_level_for_magnification,
    level0_bounds_of_patch,
    level0_to_patch_local,
    patch_footprint_l0,
    patch_local_to_level0,
    polygon_level0_to_patch_local,
    polygon_patch_local_to_level0,
)


def test_same_resolution_matches_spec_worked_example():
    # From the spec: patch origin (20000, 15000), local (100, 80), same-resolution
    # display (downsample=1) -> global (20100, 15080).
    origin = PatchOrigin(x=20000, y=15000, level=0, downsample=1.0)
    gx, gy = patch_local_to_level0(origin, 100, 80)
    assert (gx, gy) == (20100, 15080)


def test_downsampled_patch_scales_local_offset():
    # Patch fetched at a pyramid level with downsample=2: a local offset of
    # (100, 80) covers twice as many Level-0 pixels.
    origin = PatchOrigin(x=20000, y=15000, level=1, downsample=2.0)
    gx, gy = patch_local_to_level0(origin, 100, 80)
    assert (gx, gy) == (20200, 15160)


def test_roundtrip_local_to_global_to_local():
    origin = PatchOrigin(x=48213, y=91007, level=2, downsample=4.0)
    for lx, ly in [(0, 0), (511, 511), (37.5, 402.2)]:
        gx, gy = patch_local_to_level0(origin, lx, ly)
        back_x, back_y = level0_to_patch_local(origin, gx, gy)
        assert abs(back_x - lx) < 1e-9
        assert abs(back_y - ly) < 1e-9


def test_polygon_transform_matches_pointwise_transform():
    origin = PatchOrigin(x=1000, y=2000, level=0, downsample=1.5)
    points = [[10, 20], [100, 200], [0, 0]]
    poly_l0 = polygon_patch_local_to_level0(origin, points)
    expected = [list(patch_local_to_level0(origin, x, y)) for x, y in points]
    assert poly_l0 == expected

    back = polygon_level0_to_patch_local(origin, poly_l0)
    for (ox, oy), (bx, by) in zip(points, back):
        assert abs(ox - bx) < 1e-9
        assert abs(oy - by) < 1e-9


def test_patch_footprint_and_bounds():
    w_l0, h_l0 = patch_footprint_l0(512, 512, 2.0)
    assert (w_l0, h_l0) == (1024, 1024)

    origin = PatchOrigin(x=20000, y=15000, level=1, downsample=2.0)
    x0, y0, x1, y1 = level0_bounds_of_patch(origin, 512, 512)
    assert (x0, y0, x1, y1) == (20000, 15000, 21024, 16024)


def test_best_level_for_magnification_picks_matching_downsample():
    # Native (objective) magnification 40x, levels at downsample 1,2,4,8 -> effective
    # magnifications 40x,20x,10x,5x. Requesting 20x should land on level 1.
    level = best_level_for_magnification(40.0, [1.0, 2.0, 4.0, 8.0], target_magnification=20.0)
    assert level == 1

    level_40x = best_level_for_magnification(40.0, [1.0, 2.0, 4.0, 8.0], target_magnification=40.0)
    assert level_40x == 0
