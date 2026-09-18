import pytest

from app.models.config_version import AnnotationClass, ProjectConfigVersion
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide
from app.services.config_versioning import ConfigLockedError, assert_mutable, compute_config_hash, fork_config


def _seed(db):
    project = Project(slug="BCA_2026", name="Breast Cancer Annotation")
    db.add(project)
    db.flush()
    config = ProjectConfigVersion(
        project_id=project.id,
        version_label="v1.0",
        status="locked",
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


def test_locked_once_patches_exist(db):
    project, config = _seed(db)
    slide = Slide(project_id=project.id, filename="Patient_001.svs", source_type="demo")
    db.add(slide)
    db.flush()
    db.add(
        Patch(
            slide_id=slide.id, config_version_id=config.id, patch_index=0,
            x=0, y=0, level=0, width=512, height=512, width_l0=512, height_l0=512,
        )
    )
    db.commit()

    with pytest.raises(ConfigLockedError):
        assert_mutable(db, config, {"patch_width": 1024})

    # Non-critical fields (e.g. QC flags) remain freely editable even after lock.
    assert_mutable(db, config, {"allow_skip": False})


def test_fork_creates_new_version_with_parent_link_and_copies_classes(db):
    project, config = _seed(db)
    new_config = fork_config(db, config, {"patch_width": 1024, "target_magnification": 40.0}, "v2.0-RC1")
    db.commit()

    assert new_config.id != config.id
    assert new_config.parent_version_id == config.id
    assert new_config.version_label == "v2.0-RC1"
    assert new_config.status == "draft"
    assert new_config.patch_width == 1024
    assert new_config.target_magnification == 40.0
    # untouched fields carried over from the parent
    assert new_config.min_tissue_fraction == config.min_tissue_fraction
    assert len(new_config.annotation_classes) == 1
    assert new_config.annotation_classes[0].name == "Tumor"

    # original version is untouched
    assert config.patch_width == 512


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
