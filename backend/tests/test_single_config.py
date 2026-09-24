"""A project has one configuration. Projects from when several versions were possible are merged into
the one they use, losing nothing."""
from app.database.session import _merge_config_versions
from app.models.annotation import GeometryAnnotation
from app.models.config_version import AnnotationClass, ProjectConfigVersion
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide


def _config(db, project, label, **extra):
    config = ProjectConfigVersion(project_id=project.id, version_label=label, patch_width=512, patch_height=512, stride_x=512, stride_y=512, **extra)
    db.add(config)
    db.flush()
    return config


def _patch(db, slide, config, x, grid="512x512_s512x512_m20_t0.5"):
    patch = Patch(slide_id=slide.id, config_version_id=config.id, grid_key=grid, patch_index=x, x=x, y=0, level=0, width=512, height=512, width_l0=512, height_l0=512)
    db.add(patch)
    db.flush()
    return patch


def _ann(db, slide, config, patch, cls):
    ann = GeometryAnnotation(
        slide_id=slide.id, patch_id=patch.id if patch else None, config_version_id=config.id, class_id=cls.id if cls else None,
        type="point", coordinates_patch_local=[[1, 1]] if patch else [], coordinates_level0=[[1, 1]],
    )  # fmt: skip
    db.add(ann)
    db.flush()
    return ann


def test_other_versions_are_merged_into_the_one_in_use(db):
    project = Project(slug="P", name="P")
    db.add(project)
    db.flush()
    v1 = _config(db, project, "v1.0", status="locked")
    v2 = _config(db, project, "v2.0", parent_version_id=v1.id)
    tumor1 = AnnotationClass(config_version_id=v1.id, name="Tumor", color_hex="#f00", hotkey="1")
    tumor2 = AnnotationClass(config_version_id=v2.id, name="tumor", color_hex="#0f0", hotkey="1")
    necrosis2 = AnnotationClass(config_version_id=v2.id, name="Necrosis", color_hex="#00f", hotkey="1")
    db.add_all([tumor1, tumor2, necrosis2])
    project.active_config_version_id = v1.id

    a = Slide(project_id=project.id, filename="a.svs", active_config_version_id=v1.id)
    b = Slide(project_id=project.id, filename="b.svs", active_config_version_id=v2.id)
    db.add_all([a, b])
    db.flush()
    p1 = _patch(db, a, v1, 0)
    twin = _patch(db, a, v2, 0)  # the same patch, cut under v2 too
    own = _patch(db, b, v2, 512)
    moved_to_twin = _ann(db, a, v2, twin, tumor2)
    on_own = _ann(db, b, v2, own, necrosis2)
    slide_level = _ann(db, b, v2, None, None)
    db.commit()
    ids = {"p1": p1.id, "twin": twin.id, "own": own.id, "v1": v1.id, "v2": v2.id}
    ann_ids = (moved_to_twin.id, on_own.id, slide_level.id)

    _merge_config_versions(db.get_bind())
    db.expire_all()

    assert [c.id for c in db.query(ProjectConfigVersion)] == [ids["v1"]]
    keep = db.get(ProjectConfigVersion, ids["v1"])
    assert keep.status != "locked" and keep.parent_version_id is None
    names = {c.name: c for c in keep.annotation_classes}
    assert set(names) == {"Tumor", "Necrosis"}  # matched by name; the missing one added
    assert names["Necrosis"].hotkey is None  # "1" is already Tumor's

    assert db.get(Patch, ids["twin"]) is None and db.get(Patch, ids["own"]).config_version_id == ids["v1"]
    first, second, third = (db.get(GeometryAnnotation, i) for i in ann_ids)
    assert first.patch_id == ids["p1"] and first.class_id == names["Tumor"].id
    assert second.patch_id == ids["own"] and second.class_id == names["Necrosis"].id
    assert third.patch_id is None and third.config_version_id == ids["v1"]
    assert {s.active_config_version_id for s in db.query(Slide)} == {ids["v1"]}


def test_a_project_with_one_configuration_is_left_alone(db):
    project = Project(slug="Q", name="Q")
    db.add(project)
    db.flush()
    only = _config(db, project, "v1.0")
    project.active_config_version_id = only.id
    db.commit()
    before = (only.id, only.version_label, only.status)
    _merge_config_versions(db.get_bind())
    db.expire_all()
    config = db.query(ProjectConfigVersion).one()
    assert (config.id, config.version_label, config.status) == before


def test_there_is_no_way_to_add_versions_any_more():
    from app.main import app

    paths = {route.path for route in app.routes}
    assert "/api/projects/{project_id}/config" in paths
    assert not {"/api/configs/{config_id}/fork", "/api/configs/{config_id}/lock", "/api/slides/{slide_id}/active-config"} & paths
    assert not any(p == "/api/projects/{project_id}/configs" for p in paths)
