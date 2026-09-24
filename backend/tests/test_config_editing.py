import pytest
from fastapi import HTTPException

from app.api.configs import fork_config_endpoint, get_config_usage, update_config
from app.api.patches import list_patches
from app.api.projects import _to_detail
from app.api.slides import set_slide_active_config
from app.models.annotation import GeometryAnnotation
from app.models.config_version import AnnotationClass, ProjectConfigVersion
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide
from app.schemas.config_version import (
    AnnotationClassSync,
    ConfigVersionForkRequest,
    ConfigVersionUpdate,
)
from app.schemas.slide import SlideActiveConfigRequest
from app.services.config_versioning import ClassSyncError, sync_annotation_classes


def _seed(db, with_patch=True):
    project = Project(slug="P", name="P")
    db.add(project)
    db.flush()
    config = ProjectConfigVersion(
        project_id=project.id, version_label="v1.0", status="draft",
        patch_width=512, patch_height=512, stride_x=512, stride_y=512, min_tissue_fraction=0.6,
    )
    db.add(config)
    db.flush()
    tumor = AnnotationClass(config_version_id=config.id, name="Tumor", color_hex="#dc2626", hotkey="1", order_index=0)
    stroma = AnnotationClass(config_version_id=config.id, name="Stroma", color_hex="#16a34a", hotkey="2", order_index=1)
    db.add_all([tumor, stroma])
    slide = Slide(project_id=project.id, filename="s.svs", source_type="upload", active_config_version_id=config.id)
    db.add(slide)
    db.flush()
    project.active_config_version_id = config.id
    patch = None
    if with_patch:
        patch = Patch(slide_id=slide.id, config_version_id=config.id, patch_index=0, x=0, y=0, level=0,
                      width=512, height=512, width_l0=512, height_l0=512)
        db.add(patch)
        db.flush()
        db.add(GeometryAnnotation(
            patch_id=patch.id, slide_id=slide.id, config_version_id=config.id, class_id=tumor.id,
            type="polygon", coordinates_patch_local=[[0, 0]], coordinates_level0=[[0, 0]],
        ))
    db.commit()
    return project, config, slide, patch, tumor, stroma


def _rows(config):
    return [
        AnnotationClassSync(id=c.id, name=c.name, color_hex=c.color_hex, hotkey=c.hotkey)
        for c in config.annotation_classes
    ]


def test_rename_and_recolor_keep_class_id_and_annotations(db):
    _, config, _, _, tumor, _ = _seed(db)
    rows = _rows(config)
    rows[0].name, rows[0].color_hex = "Invasive Tumor", "#111111"
    sync_annotation_classes(db, config, rows)
    db.commit()

    db.refresh(tumor)
    assert tumor.name == "Invasive Tumor" and tumor.color_hex == "#111111"
    assert db.query(GeometryAnnotation).filter(GeometryAnnotation.class_id == tumor.id).count() == 1


def test_add_reorder_and_delete_unused_class(db):
    _, config, _, _, tumor, stroma = _seed(db)
    rows = [
        AnnotationClassSync(name="Normal", color_hex="#2563eb", hotkey="3"),
        AnnotationClassSync(id=tumor.id, name="Tumor", color_hex="#dc2626", hotkey="1"),
    ]  # stroma omitted -> deleted (unused); Normal added and ordered first
    sync_annotation_classes(db, config, rows)
    db.commit()
    db.refresh(config)
    assert [c.name for c in config.annotation_classes] == ["Normal", "Tumor"]
    assert db.get(AnnotationClass, stroma.id) is None


def test_cannot_delete_class_used_by_annotations(db):
    _, config, _, _, tumor, stroma = _seed(db)
    with pytest.raises(ClassSyncError) as exc:
        sync_annotation_classes(db, config, [AnnotationClassSync(id=stroma.id, name="Stroma", color_hex="#16a34a")])
    assert exc.value.status_code == 409 and "Tumor" in str(exc.value)
    assert db.get(AnnotationClass, tumor.id) is not None


@pytest.mark.parametrize(
    "rows,fragment",
    [
        ([], "At least one"),
        ([("A", "1"), ("a", "2")], "unique"),
        ([("A", "1"), ("B", "1")], "Hotkeys"),
    ],
)
def test_invalid_class_lists_rejected(db, rows, fragment):
    _, config, _, _, _, _ = _seed(db, with_patch=False)
    items = [AnnotationClassSync(name=n, hotkey=h) for n, h in rows]
    with pytest.raises(ClassSyncError, match=fragment):
        sync_annotation_classes(db, config, items)


def test_update_is_atomic_when_class_edit_is_rejected(db):
    _, config, _, _, _, stroma = _seed(db)
    payload = ConfigVersionUpdate(
        allow_skip=False,
        title="New title",
        annotation_classes=[AnnotationClassSync(id=stroma.id, name="Stroma", color_hex="#16a34a")],  # drops used Tumor
    )
    with pytest.raises(HTTPException) as exc:
        update_config(payload, config=config, db=db)
    assert exc.value.status_code == 409
    db.refresh(config)
    assert config.allow_skip is True and config.title is None


def test_with_data_the_grid_classes_and_settings_are_editable_but_not_the_tissue_method(db):
    _, config, _, _, _, _ = _seed(db)
    # Existing patches keep the grid they were cut with, so the grid can change in place.
    out = update_config(ConfigVersionUpdate(patch_width=1024, stride_x=512), config=config, db=db)
    assert out.patch_width == 1024 and out.stride_x == 512
    with pytest.raises(HTTPException) as exc:
        update_config(ConfigVersionUpdate(tissue_method="other"), config=config, db=db)
    assert exc.value.status_code == 409

    rows = _rows(config)
    rows[0].name = "Carcinoma"
    out = update_config(ConfigVersionUpdate(allow_skip=False, annotation_classes=rows), config=config, db=db)
    assert out.allow_skip is False
    assert {c.name for c in out.annotation_classes} == {"Carcinoma", "Stroma"}


def test_critical_edit_allowed_without_data_and_hash_changes(db):
    _, config, _, _, _, _ = _seed(db, with_patch=False)
    before = config.config_hash
    out = update_config(ConfigVersionUpdate(patch_width=1024, patch_height=1024), config=config, db=db)
    assert out.patch_width == 1024 and out.config_hash != before


def test_usage_counts(db):
    _, config, _, _, _, _ = _seed(db)
    assert get_config_usage(config=config, db=db) == {"patch_count": 1, "annotation_count": 1, "slide_count": 1}


def test_fork_with_edited_classes_leaves_original_untouched(db):
    _, config, _, _, _, _ = _seed(db)
    req = ConfigVersionForkRequest(
        new_version_label="v2.0",
        overrides={"patch_width": 1024, "stride_x": 1024, "bogus": 1},
        annotation_classes=[AnnotationClassSync(id=999, name="OnlyOne", color_hex="#000000")],
    )
    forked = fork_config_endpoint(req, config=config, db=db)
    assert forked.patch_width == 1024 and forked.parent_version_id == config.id
    assert [c.name for c in forked.annotation_classes] == ["OnlyOne"]
    db.refresh(config)
    assert config.patch_width == 512 and {c.name for c in config.annotation_classes} == {"Tumor", "Stroma"}


def test_fork_rejects_duplicate_label_and_bad_override(db):
    _, config, _, _, _, _ = _seed(db)
    with pytest.raises(HTTPException) as exc:
        fork_config_endpoint(ConfigVersionForkRequest(new_version_label="v1.0"), config=config, db=db)
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        fork_config_endpoint(
            ConfigVersionForkRequest(new_version_label="v3", overrides={"patch_width": -5}), config=config, db=db
        )
    assert exc.value.status_code == 422


def test_switching_slide_version_changes_which_patches_are_listed(db):
    project, config, slide, _, _, _ = _seed(db)
    v2 = fork_config_endpoint(ConfigVersionForkRequest(new_version_label="v2.0"), config=config, db=db)

    def listed():
        return list_patches(slide=slide, db=db, bbox=None, status=None, flagged=None, limit=500, offset=0).total

    assert listed() == 1
    out = set_slide_active_config(SlideActiveConfigRequest(config_version_id=v2.id), slide=slide, db=db)
    assert out.active_config_version_id == v2.id and out.status == "imported"  # no patches under v2 yet
    assert listed() == 0
    assert _to_detail(db, project).stats.total_patches == 0

    set_slide_active_config(SlideActiveConfigRequest(config_version_id=config.id), slide=slide, db=db)
    assert listed() == 1 and slide.status == "patches_generated"
    assert _to_detail(db, project).stats.total_patches == 1


def test_cannot_switch_slide_to_another_projects_version(db):
    _, _, slide, _, _, _ = _seed(db)
    other = Project(slug="O", name="O")
    db.add(other)
    db.flush()
    foreign = ProjectConfigVersion(project_id=other.id, version_label="v1.0")
    db.add(foreign)
    db.commit()
    with pytest.raises(HTTPException) as exc:
        set_slide_active_config(SlideActiveConfigRequest(config_version_id=foreign.id), slide=slide, db=db)
    assert exc.value.status_code == 404


def test_project_default_version_must_belong_to_the_project(db):
    from app.api.projects import update_project
    from app.schemas.project import ProjectUpdate

    project, config, _, _, _, _ = _seed(db, with_patch=False)
    v2 = fork_config_endpoint(ConfigVersionForkRequest(new_version_label="v2.0"), config=config, db=db)
    out = update_project(ProjectUpdate(active_config_version_id=v2.id), project=project, db=db)
    assert out.active_config.id == v2.id

    other = Project(slug="O2", name="O2")
    db.add(other)
    db.flush()
    foreign = ProjectConfigVersion(project_id=other.id, version_label="v1.0")
    db.add(foreign)
    db.commit()
    with pytest.raises(HTTPException) as exc:
        update_project(ProjectUpdate(active_config_version_id=foreign.id), project=project, db=db)
    assert exc.value.status_code == 422
