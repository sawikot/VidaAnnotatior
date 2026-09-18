import numpy as np

from app.models.config_version import ProjectConfigVersion
from app.services.patch_generator import generate_patch_grid
from app.services.wsi_reader import WSIMetadata


def make_config(**overrides) -> ProjectConfigVersion:
    cfg = ProjectConfigVersion(
        patch_width=512,
        patch_height=512,
        stride_x=512,
        stride_y=512,
        min_tissue_fraction=0.6,
        target_magnification=20.0,
        allow_partial_patches=False,
        include_edge_patches=True,
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def make_metadata(width=2048, height=2048) -> WSIMetadata:
    return WSIMetadata(
        width=width,
        height=height,
        level_count=1,
        level_dimensions=[(width, height)],
        level_downsamples=[1.0],
        mpp_x=0.25,
        mpp_y=0.25,
        magnification=20.0,
    )


def test_grid_covers_full_slide_with_no_tissue_mask():
    meta = make_metadata(2048, 2048)
    cfg = make_config()
    candidates = generate_patch_grid(meta, cfg, tissue_mask=None, tissue_mask_downsample=None)
    # 2048 / 512 = 4 -> 4x4 grid, all kept since no mask => tissue_fraction=1.0
    assert len(candidates) == 16
    assert all(c.kept for c in candidates)
    xs = sorted({c.x for c in candidates})
    assert xs == [0, 512, 1024, 1536]


def test_patches_outside_tissue_mask_are_excluded():
    meta = make_metadata(1024, 1024)
    cfg = make_config(min_tissue_fraction=0.6)
    # mask covers only the top-left quadrant
    mask = np.zeros((1024, 1024), dtype=bool)
    mask[:512, :512] = True
    candidates = generate_patch_grid(meta, cfg, tissue_mask=mask, tissue_mask_downsample=1.0)
    kept = [c for c in candidates if c.kept]
    assert len(kept) == 1
    assert (kept[0].x, kept[0].y) == (0, 0)


def test_patch_footprint_scales_with_downsampled_level():
    # Native magnification 40x, target 20x -> level with downsample 2 chosen,
    # so a "512x512 @ 20x" patch covers 1024x1024 Level-0 pixels.
    meta = WSIMetadata(
        width=4096,
        height=4096,
        level_count=2,
        level_dimensions=[(4096, 4096), (2048, 2048)],
        level_downsamples=[1.0, 2.0],
        mpp_x=0.25,
        mpp_y=0.25,
        magnification=40.0,
    )
    cfg = make_config(target_magnification=20.0)
    candidates = generate_patch_grid(meta, cfg, tissue_mask=None, tissue_mask_downsample=None)
    assert candidates[0].level == 1
    assert candidates[0].width == 512 and candidates[0].height == 512
    assert candidates[0].width_l0 == 1024 and candidates[0].height_l0 == 1024


def test_zero_patches_when_slide_smaller_than_patch_and_edges_excluded():
    meta = make_metadata(300, 300)
    cfg = make_config(patch_width=512, patch_height=512, include_edge_patches=False)
    candidates = generate_patch_grid(meta, cfg, tissue_mask=None, tissue_mask_downsample=None)
    assert candidates == []
