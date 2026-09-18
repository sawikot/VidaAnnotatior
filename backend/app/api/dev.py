"""Dev-mode helpers: seed a fully-formed demo project (synthetic slide, tissue
mask, generated patches, a couple of sample annotations) so the frontend can be
exercised without a real WSI file (spec section 30)."""
from __future__ import annotations

import numpy as np
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.annotation import GeometryAnnotation
from app.models.config_version import AnnotationClass, ProjectConfigVersion
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide
from app.schemas.project import ProjectOut
from app.services import reader_cache
from app.services.config_versioning import compute_config_hash
from app.services.coordinate_transform import PatchOrigin, polygon_patch_local_to_level0
from app.services.patch_generator import generate_patch_grid
from app.services.slugify import slugify, unique_project_slug
from app.services.tissue_detector import get_detector

router = APIRouter(prefix="/dev", tags=["dev"])

DEFAULT_CLASSES = [
    {"name": "Tumor", "color_hex": "#dc2626", "hotkey": "1"},
    {"name": "Stroma", "color_hex": "#16a34a", "hotkey": "2"},
    {"name": "Necrosis", "color_hex": "#eab308", "hotkey": "3"},
    {"name": "Normal", "color_hex": "#2563eb", "hotkey": "4"},
]


@router.post("/seed-demo", response_model=ProjectOut, status_code=201)
def seed_demo_project(db: Session = Depends(get_db)):
    base_slug = slugify("Breast Cancer Annotation Demo")
    slug = unique_project_slug(db, base_slug)

    project = Project(
        slug=slug,
        name="Breast Cancer Annotation (Demo)",
        organ="Breast",
        description="Synthetic demo project seeded for exercising the app without a real WSI.",
        team="Demo",
        status="active",
    )
    db.add(project)
    db.flush()

    config = ProjectConfigVersion(
        project_id=project.id,
        version_label="v1.0",
        status="locked",
        title="Standard Clinical 20x Tile Lattice (Demo)",
        target_magnification=20.0,
        patch_width=512,
        patch_height=512,
        stride_x=512,
        stride_y=512,
        min_tissue_fraction=0.6,
    )
    config.config_hash = compute_config_hash(config)
    db.add(config)
    db.flush()

    for i, cls in enumerate(DEFAULT_CLASSES):
        db.add(AnnotationClass(config_version_id=config.id, order_index=i, **cls))
    db.flush()

    project.active_config_version_id = config.id
    db.flush()

    slide = Slide(
        project_id=project.id,
        filename="demo_slide_001.svs",
        source_type="demo",
        format="demo",
        status="imported",
        active_config_version_id=config.id,
    )
    db.add(slide)
    db.flush()

    reader = reader_cache.get_reader_for_slide(slide)
    meta = reader.get_metadata()
    slide.width_l0 = meta.width
    slide.height_l0 = meta.height
    slide.level_count = meta.level_count
    slide.level_dimensions = [list(d) for d in meta.level_dimensions]
    slide.level_downsamples = meta.level_downsamples
    slide.mpp_x = meta.mpp_x
    slide.mpp_y = meta.mpp_y
    slide.magnification = meta.magnification
    db.flush()

    thumbnail = reader.get_thumbnail(1024)
    detector = get_detector("hsv_otsu")
    result = detector.detect(thumbnail, {})
    mask_downsample = slide.width_l0 / result.thumbnail_size[0]

    from pathlib import Path

    from app.core.config import get_settings

    settings = get_settings()
    mask_dir = settings.wsi_storage_dir / str(project.id) / "_masks"
    mask_dir.mkdir(parents=True, exist_ok=True)
    mask_path = mask_dir / f"{slide.id}_tissue_mask.png"
    from PIL import Image

    Image.fromarray((result.mask * 255).astype(np.uint8)).save(mask_path)

    slide.tissue_mask_path = str(mask_path.relative_to(settings.wsi_storage_dir).as_posix())
    slide.tissue_mask_downsample = mask_downsample
    tissue_area_mm2 = result.mask.sum() * (mask_downsample**2) * (meta.mpp_x * meta.mpp_y) / 1_000_000
    slide.tissue_area_mm2 = round(float(tissue_area_mm2), 4)
    slide.tissue_coverage_pct = round(result.tissue_fraction * 100, 2)
    slide.status = "tissue_detected"
    db.flush()

    candidates = generate_patch_grid(meta, config, result.mask, mask_downsample)
    kept = [c for c in candidates if c.kept]
    patch_rows = []
    for c in kept:
        p = Patch(
            slide_id=slide.id,
            config_version_id=config.id,
            patch_index=c.patch_index,
            x=c.x,
            y=c.y,
            level=c.level,
            width=c.width,
            height=c.height,
            width_l0=c.width_l0,
            height_l0=c.height_l0,
            tissue_fraction=c.tissue_fraction,
            status="unannotated",
        )
        db.add(p)
        patch_rows.append(p)
    slide.status = "patches_generated"
    db.flush()

    tumor_class = next(c for c in config.annotation_classes if c.name == "Tumor")
    for p in patch_rows[:3]:
        origin = PatchOrigin(x=p.x, y=p.y, level=p.level, downsample=p.width_l0 / p.width)
        local_pts = [[80, 80], [400, 120], [420, 400], [100, 420]]
        db.add(
            GeometryAnnotation(
                patch_id=p.id,
                slide_id=slide.id,
                config_version_id=config.id,
                class_id=tumor_class.id,
                type="polygon",
                coordinates_patch_local=local_pts,
                coordinates_level0=polygon_patch_local_to_level0(origin, local_pts),
                created_by="Demo Seed",
            )
        )
        p.status = "annotated"

    db.commit()
    db.refresh(project)
    return project
