import pytest

from app.models.config_version import AnnotationClass, ProjectConfigVersion
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide
from app.services.config_versioning import ConfigLockedError, assert_mutable, compute_config_hash


def _seed(db, status="draft"):
    project = Project(slug="BCA_2026", name="Breast Cancer Annotation")
    db.add(project)
    db.flush()
    config = ProjectConfigVersion(
        project_id=project.id,
        version_label="v1.0",
        status=status,
        patch_width=512,
        patch_height=512,
        stride_x=512,
        stride_y=512,
        min_tissue_fraction=0.6,
    )
    db.add(config)
    db.flush()
    db.add(AnnotationClass(config_version_id=config.id, name="Tumor", color_hex="#dc2626"))
    db.commit()
    return project, config


def test_mutable_when_no_patches_generated(db):
    project, config = _seed(db)
    assert_mutable(db, config, {"patch_width": 1024})  # should not raise


def test_once_patches_exist_only_the_grid_stays_editable(db):
    project, config = _seed(db)
    slide = Slide(project_id=project.id, filename="Patient_001.svs", source_type="upload")
    db.add(slide)
    db.flush()
    db.add(
        Patch(
            slide_id=slide.id, config_version_id=config.id, patch_index=0,
            x=0, y=0, level=0, width=512, height=512, width_l0=512, height_l0=512,
        )
    )
    db.commit()

    # The grid can change: the existing patches keep the grid they were cut with.
    assert_mutable(db, config, {"patch_width": 1024, "stride_x": 256, "min_tissue_fraction": 0.2})
    # What the existing patches' meaning depends on cannot.
    with pytest.raises(ConfigLockedError):
        assert_mutable(db, config, {"tissue_method": "other"})
    with pytest.raises(ConfigLockedError):
        assert_mutable(db, config, {"coordinate_system": "level1"})

    # Non-critical fields (e.g. QC flags) remain freely editable.
    assert_mutable(db, config, {"allow_skip": False})


def test_config_hash_changes_when_critical_fields_change(db):
    project, config = _seed(db)
    h1 = compute_config_hash(config)
    config.patch_width = 1024
    h2 = compute_config_hash(config)
    assert h1 != h2


def test_config_hash_stable_for_noncritical_field_changes(db):
    project, config = _seed(db)
    h1 = compute_config_hash(config)
    config.allow_skip = False
    h2 = compute_config_hash(config)
    assert h1 == h2
