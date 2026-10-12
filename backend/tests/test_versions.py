"""Versions of a project's work: save the work as a version, open another one, go on from there.
Real HTTP against the real app, with small real image and slide files."""
import pytest

from tests.test_image_projects import SQUARE, add_images, classes_of, image_bytes, only_patch
from tests.test_slide_import_api import HEIGHT as H, SLIDE, WIDTH as W, env, upload  # noqa: F401  (env is a fixture)
from tests.test_tissue_regions import generated, put_regions, rect


@pytest.fixture()
def proj(env, monkeypatch):  # noqa: F811
    client, wsi_pid, settings = env
    for module in ("app.api.versions", "app.api.processing", "app.api.deps"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    res = client.post(
        "/api/projects",
        json={"name": "Cells", "project_type": "image", "config": {"annotation_classes": [{"name": "Tumor", "color_hex": "#dc2626"}, {"name": "Normal", "color_hex": "#2563eb"}]}},
    )
    assert res.status_code == 201, res.text
    return client, res.json()["id"], settings, wsi_pid


def versions(client, pid):
    return client.get(f"/api/projects/{pid}/versions").json()


def save(client, pid, name=None):
    res = client.post(f"/api/projects/{pid}/versions", json={"name": name})
    assert res.status_code == 201, res.text
    body = res.json()
    return next(v for v in body["versions"] if v["id"] == body["current_version_id"])


def switch(client, pid, version_id, discard=False):
    return client.post(f"/api/projects/{pid}/versions/{version_id}/switch", json={"discard_changes": discard})


def names(client, pid):
    return sorted(s["filename"] for s in client.get(f"/api/projects/{pid}/slides").json())


def slide_named(client, pid, filename):
    return next(s for s in client.get(f"/api/projects/{pid}/slides").json() if s["filename"] == filename)


def annotate(client, pid, filename, class_name="Tumor"):
    patch = only_patch(client, slide_named(client, pid, filename)["id"])
    res = client.post(f"/api/patches/{patch['id']}/annotations", json={"type": "polygon", "class_id": classes_of(client, pid)[class_name], "coordinates_patch_local": SQUARE})
    assert res.status_code == 201, res.text
    return res.json()


def annotations(client, pid, filename):
    return client.get(f"/api/slides/{slide_named(client, pid, filename)['id']}/annotations").json()


def stored(settings, slide):
    return settings.wsi_storage_dir / slide["file_path"] if "file_path" in slide else None


def test_each_version_shows_only_what_it_had_and_work_goes_on_from_the_one_opened(proj):
    client, pid, _, _ = proj
    assert versions(client, pid) == {"current_version_id": None, "has_unsaved_changes": True, "versions": []}

    add_images(client, pid, [("a.png", image_bytes()), ("b.png", image_bytes())])
    annotate(client, pid, "a.png")
    v1 = save(client, pid, "First pass")
    assert (v1["number"], v1["name"], v1["parent_id"]) == (1, "First pass", None)
    assert v1["summary"] == {"slides": 2, "patches": 2, "annotated_patches": 1, "annotations": 1, "suggestions": 0, "classes": 2}

    # More work: another image, more annotations, and an image removed.
    add_images(client, pid, [("c.png", image_bytes())])
    annotate(client, pid, "b.png", "Normal")
    annotate(client, pid, "c.png")
    assert client.delete(f"/api/slides/{slide_named(client, pid, 'a.png')['id']}").status_code == 204
    v2 = save(client, pid)
    assert (v2["number"], v2["name"], v2["parent_id"]) == (2, "Version 2", v1["id"])

    body = switch(client, pid, v1["id"]).json()
    assert (body["current_version_id"], body["has_unsaved_changes"]) == (v1["id"], False)
    assert names(client, pid) == ["a.png", "b.png"]  # c is not in it; a is back
    assert len(annotations(client, pid, "a.png")) == 1 and annotations(client, pid, "b.png") == []
    a = slide_named(client, pid, "a.png")
    assert client.get(f"/api/slides/{a['id']}/thumbnail").status_code == 200  # its file was kept for this version
    stats = client.get(f"/api/projects/{pid}").json()["stats"]
    assert (stats["slide_count"], stats["total_patches"], stats["annotated_patches"]) == (2, 2, 1)

    # Going on from version 1 makes a version beside version 2, not after it.
    annotate(client, pid, "b.png")
    v3 = save(client, pid, "Other direction")
    assert (v3["number"], v3["parent_id"]) == (3, v1["id"])

    assert switch(client, pid, v2["id"]).status_code == 200
    assert names(client, pid) == ["b.png", "c.png"]
    assert [a["class_id"] for a in annotations(client, pid, "b.png")] == [classes_of(client, pid)["Normal"]]
    assert len(annotations(client, pid, "c.png")) == 1

    assert switch(client, pid, v3["id"]).status_code == 200
    assert names(client, pid) == ["a.png", "b.png"] and len(annotations(client, pid, "b.png")) == 1


def test_unsaved_changes_are_kept_from_being_lost_unless_discarded(proj):
    client, pid, settings, _ = proj
    add_images(client, pid, [("a.png", image_bytes())])
    v1 = save(client, pid)
    assert versions(client, pid)["has_unsaved_changes"] is False

    added = add_images(client, pid, [("b.png", image_bytes())])["slides"][0]
    annotate(client, pid, "a.png")
    assert versions(client, pid)["has_unsaved_changes"] is True
    res = switch(client, pid, v1["id"])
    assert res.status_code == 409 and "unsaved" in res.json()["detail"]
    assert names(client, pid) == ["a.png", "b.png"]  # nothing was touched

    b_dir = next(p for p in (settings.wsi_storage_dir / str(pid)).iterdir() if p.name.startswith("b-"))
    assert switch(client, pid, v1["id"], discard=True).status_code == 200
    assert names(client, pid) == ["a.png"] and annotations(client, pid, "a.png") == []
    assert versions(client, pid)["has_unsaved_changes"] is False
    assert not b_dir.exists()  # no version has b: its file went with it
    assert client.get(f"/api/slides/{added['id']}").status_code == 404


def test_classes_and_settings_are_the_versions_own(proj):
    client, pid, _, _ = proj
    add_images(client, pid, [("a.png", image_bytes())])
    annotate(client, pid, "a.png")
    before = classes_of(client, pid)
    v1 = save(client, pid)

    config = client.get(f"/api/projects/{pid}").json()["active_config"]
    renamed = [{**c, "name": "Carcinoma"} if c["name"] == "Tumor" else c for c in config["annotation_classes"]]
    res = client.put(f"/api/configs/{config['id']}", json={"annotation_classes": renamed + [{"name": "Stroma", "color_hex": "#16a34a"}], "allow_skip": False})
    assert res.status_code == 200, res.text
    assert versions(client, pid)["has_unsaved_changes"] is True
    v2 = save(client, pid)

    switch(client, pid, v1["id"])
    assert classes_of(client, pid) == before
    assert client.get(f"/api/projects/{pid}").json()["active_config"]["allow_skip"] is True
    assert [a["class_id"] for a in annotations(client, pid, "a.png")] == [before["Tumor"]]

    switch(client, pid, v2["id"])
    now = classes_of(client, pid)
    assert sorted(now) == ["Carcinoma", "Normal", "Stroma"] and now["Carcinoma"] == before["Tumor"]
    assert client.get(f"/api/projects/{pid}").json()["active_config"]["allow_skip"] is False


def test_ids_taken_by_another_project_meanwhile_do_not_mix_the_two(proj):
    client, pid, _, _ = proj
    add_images(client, pid, [("a.png", image_bytes(64, 48))])
    annotate(client, pid, "a.png")
    v1 = save(client, pid)
    client.delete(f"/api/slides/{slide_named(client, pid, 'a.png')['id']}")
    save(client, pid)

    # SQLite hands the freed slide, patch and annotation ids to the next rows: another project's.
    other = client.post("/api/projects", json={"name": "Other", "project_type": "image", "config": {"annotation_classes": [{"name": "Gland", "color_hex": "#000000"}]}}).json()["id"]
    add_images(client, other, [("x.png", image_bytes(20, 30))])
    annotate(client, other, "x.png", "Gland")

    assert switch(client, pid, v1["id"]).status_code == 200
    a, x = slide_named(client, pid, "a.png"), slide_named(client, other, "x.png")
    assert a["id"] != x["id"] and (a["width_l0"], x["width_l0"]) == (64, 20)
    assert only_patch(client, a["id"])["width"] == 64 and only_patch(client, x["id"])["width"] == 20
    (mine,), (theirs,) = annotations(client, pid, "a.png"), annotations(client, other, "x.png")
    assert mine["patch_id"] == only_patch(client, a["id"])["id"] and mine["class_id"] == classes_of(client, pid)["Tumor"]
    assert theirs["patch_id"] == only_patch(client, x["id"])["id"] and theirs["class_id"] == classes_of(client, other)["Gland"]
    assert client.get(f"/api/slides/{a['id']}/thumbnail").status_code == 200


def test_a_class_whose_id_was_taken_gets_one_new_id_for_good(proj):
    client, pid, _, _ = proj
    config = client.get(f"/api/projects/{pid}").json()["active_config"]
    kept = config["annotation_classes"]
    client.put(f"/api/configs/{config['id']}", json={"annotation_classes": kept + [{"name": "Stroma", "color_hex": "#16a34a"}]})
    old_id = classes_of(client, pid)["Stroma"]
    v1 = save(client, pid)
    assert client.put(f"/api/configs/{config['id']}", json={"annotation_classes": kept}).status_code == 200
    v2 = save(client, pid)
    other = client.post("/api/projects", json={"name": "Other", "project_type": "image", "config": {"annotation_classes": [{"name": "Gland", "color_hex": "#000000"}]}}).json()["id"]
    assert classes_of(client, other) == {"Gland": old_id}

    switch(client, pid, v1["id"])
    new_id = classes_of(client, pid)["Stroma"]
    assert new_id != old_id and classes_of(client, other) == {"Gland": old_id}
    add_images(client, pid, [("a.png", image_bytes())])
    annotate(client, pid, "a.png", "Stroma")
    v3 = save(client, pid)

    # Version 1 was saved with the old id and version 3 with the new one: both mean the same class.
    for version in (v2, v1, v3, v1):
        assert switch(client, pid, version["id"]).status_code == 200
        assert classes_of(client, pid).get("Stroma") == (None if version is v2 else new_id)
    assert names(client, pid) == [] and switch(client, pid, v3["id"]).status_code == 200
    assert [a["class_id"] for a in annotations(client, pid, "a.png")] == [new_id]


def test_a_models_suggestions_are_part_of_the_version(proj):
    from app.database.session import get_db
    from app.main import app
    from app.models.training import Suggestion, TrainedModel

    client, pid, _, _ = proj
    add_images(client, pid, [("a.png", image_bytes())])
    patch = only_patch(client, slide_named(client, pid, "a.png")["id"])
    v1 = save(client, pid)

    db = next(app.dependency_overrides[get_db]())
    model = TrainedModel(project_id=pid, name="m", task="detection", recipe_id="r", classes=[], code_path="c", weights_path="w")
    db.add(model)
    db.flush()
    db.add(Suggestion(patch_id=patch["id"], slide_id=patch["slide_id"], model_id=model.id, class_id=classes_of(client, pid)["Tumor"], type="rectangle", coordinates_patch_local=SQUARE, coordinates_level0=SQUARE, score=0.9))
    db.commit()
    db.close()

    def pending():
        return client.get(f"/api/patches/{only_patch(client, slide_named(client, pid, 'a.png')['id'])['id']}/suggestions").json()

    assert len(pending()) == 1 and versions(client, pid)["has_unsaved_changes"] is True
    v2 = save(client, pid)
    assert v2["summary"]["suggestions"] == 1
    switch(client, pid, v1["id"])
    assert pending() == []
    switch(client, pid, v2["id"])
    assert [(s["score"], s["class_id"]) for s in pending()] == [(0.9, classes_of(client, pid)["Tumor"])]


def test_a_removed_slides_file_stays_until_no_version_has_it(proj):
    client, pid, settings, _ = proj
    add_images(client, pid, [("a.png", image_bytes()), ("b.png", image_bytes())])
    project_dir = settings.wsi_storage_dir / str(pid)
    a_dir = next(p for p in project_dir.iterdir() if p.name.startswith("a-"))
    v1 = save(client, pid)
    client.delete(f"/api/slides/{slide_named(client, pid, 'a.png')['id']}")
    assert a_dir.exists()
    v2 = save(client, pid)

    body = client.delete(f"/api/projects/{pid}/versions/{v1['id']}").json()
    assert [v["id"] for v in body["versions"]] == [v2["id"]] and body["versions"][0]["parent_id"] is None
    assert not a_dir.exists() and not (project_dir / "_versions" / str(v1["id"])).exists()
    assert names(client, pid) == ["b.png"] and body["current_version_id"] == v2["id"]

    # Deleting the version the work is on leaves the work, as unsaved.
    body = client.delete(f"/api/projects/{pid}/versions/{v2['id']}").json()
    assert body == {"current_version_id": None, "has_unsaved_changes": True, "versions": []}
    assert names(client, pid) == ["b.png"]
    assert client.get(f"/api/slides/{slide_named(client, pid, 'b.png')['id']}/thumbnail").status_code == 200


def test_renaming_a_version_and_versions_of_another_project(proj):
    client, pid, _, wsi_pid = proj
    add_images(client, pid, [("a.png", image_bytes())])
    v1 = save(client, pid)
    body = client.put(f"/api/projects/{pid}/versions/{v1['id']}", json={"name": " Baseline ", "note": "before review"}).json()
    assert (body["versions"][0]["name"], body["versions"][0]["note"]) == ("Baseline", "before review")
    assert client.put(f"/api/projects/{pid}/versions/{v1['id']}", json={"name": "  "}).status_code == 422
    assert switch(client, wsi_pid, v1["id"]).status_code == 404  # not that project's version
    assert client.delete(f"/api/projects/{wsi_pid}/versions/{v1['id']}").status_code == 404


def test_tissue_and_patches_of_a_slide_come_back_with_the_version(proj):
    client, _, settings, pid = proj  # the WSI project
    slide = upload(client, pid, [("s.tif", SLIDE)]).json()["slides"][0]
    config_id = client.get(f"/api/projects/{pid}").json()["active_config_version_id"]
    put_regions(client, slide["id"], "manual", [rect(0, 0, W, H)])
    everywhere, (pw, ph) = generated(client, slide["id"], config_id)
    mask_everywhere = client.get(f"/api/slides/{slide['id']}/tissue-mask.png").content
    v1 = save(client, pid)

    put_regions(client, slide["id"], "manual", [rect(0, 0, pw, ph)])
    corner, _ = generated(client, slide["id"], config_id)
    assert corner == [(0, 0)] and len(everywhere) > 1
    mask_corner = client.get(f"/api/slides/{slide['id']}/tissue-mask.png").content
    v2 = save(client, pid)

    switch(client, pid, v1["id"])
    sid = slide_named(client, pid, "s.tif")["id"]
    assert sorted((p["x"], p["y"]) for p in client.get(f"/api/slides/{sid}/patches").json()["items"]) == everywhere
    assert client.get(f"/api/slides/{sid}/tissue-mask.png").content == mask_everywhere
    assert len(client.get(f"/api/slides/{sid}/tissue-regions").json()["regions"]) == 1

    switch(client, pid, v2["id"])
    assert [(p["x"], p["y"]) for p in client.get(f"/api/slides/{sid}/patches").json()["items"]] == [(0, 0)]
    assert client.get(f"/api/slides/{sid}/tissue-mask.png").content == mask_corner
    assert sorted(p.name for p in (settings.wsi_storage_dir / str(pid) / "_masks").iterdir()) == [f"{sid}_tissue_mask.png"]


def test_deleting_a_project_takes_its_versions_with_it(proj):
    client, pid, settings, _ = proj
    add_images(client, pid, [("a.png", image_bytes())])
    save(client, pid)
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert not (settings.wsi_storage_dir / str(pid)).exists()
    assert client.get(f"/api/projects/{pid}/versions").status_code == 404

# ------------------------------------------- exporting and training on a version, beside the work


def test_a_version_is_exported_without_opening_it(proj):
    client, pid, settings, _ = proj
    add_images(client, pid, [("a.png", image_bytes(64, 48)), ("b.png", image_bytes())])
    annotate(client, pid, "a.png")
    v1 = save(client, pid)
    client.delete(f"/api/slides/{slide_named(client, pid, 'a.png')['id']}")
    add_images(client, pid, [("c.png", image_bytes(20, 30))])  # may well get the id a.png had
    annotate(client, pid, "c.png", "Normal")
    annotate(client, pid, "b.png", "Normal")
    before = versions(client, pid)

    in_v1 = {s["filename"]: s for s in client.get(f"/api/projects/{pid}/slides", params={"version": v1["id"]}).json()}
    assert sorted(in_v1) == ["a.png", "b.png"] and (in_v1["a.png"]["annotated_patch_count"], in_v1["b.png"]["annotated_patch_count"]) == (1, 0)
    detail = client.get(f"/api/projects/{pid}", params={"version": v1["id"]}).json()
    assert (detail["stats"]["slide_count"], detail["stats"]["annotated_patches"]) == (2, 1)
    summary = client.get(f"/api/projects/{pid}/export-summary", params={"version": v1["id"]}).json()
    assert (summary["slides"], summary["annotations"]) == (2, 1)

    def coco(**params):
        res = client.get(f"/api/projects/{pid}/export", params={"formats": "coco", "combine": True, **params})
        assert res.status_code == 200, res.text
        data = res.json()
        names = {c["id"]: c["name"] for c in data["categories"]}
        return sorted((i["file_name"], i["width"]) for i in data["images"]), sorted(names[a["category_id"]] for a in data["annotations"])

    assert coco(version=v1["id"]) == ([("a.png", 64)], ["Tumor"])
    assert coco() == ([("b.png", 64), ("c.png", 20)], ["Normal", "Normal"])  # the work, as before
    assert coco(version=v1["id"], slides=in_v1["a.png"]["id"]) == ([("a.png", 64)], ["Tumor"])

    # The patch images are a.png's own pixels, whatever slide has its id in the work now.
    res = client.get(f"/api/projects/{pid}/export", params={"formats": "coco", "content": "images", "version": v1["id"]})
    assert res.status_code == 200, res.text
    import io, zipfile
    from PIL import Image
    archive = zipfile.ZipFile(io.BytesIO(res.content))
    image = next(n for n in archive.namelist() if n.lower().endswith(".png"))
    assert Image.open(io.BytesIO(archive.read(image))).size == (64, 48)

    assert versions(client, pid) == before and names(client, pid) == ["b.png", "c.png"]  # nothing was opened
    assert not list((settings.wsi_storage_dir / "_staging").glob("version-*"))  # nor left behind
    assert client.get(f"/api/projects/{pid}/slides", params={"version": 999}).status_code == 404


def test_a_run_trains_on_the_version_chosen_while_the_work_stays(env, monkeypatch):  # noqa: F811
    import json

    from app.api.training import run_dir
    from app.core.config import get_settings
    from tests.test_patch_grids import BIG, class_id, generate, patches, square
    from tests.test_training import RECIPE, split, trainer

    client, pid, settings = env
    monkeypatch.setattr(settings, "training_dir", get_settings().training_dir)
    for module in ("app.api.versions", "app.api.processing", "app.api.deps", "app.api.training"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    ids = [s["id"] for s in upload(client, pid, [("a.tif", SLIDE), ("b.tif", SLIDE), ("c.tif", SLIDE)]).json()["slides"]]
    config_id = client.get(f"/api/projects/{pid}").json()["active_config_version_id"]
    cls = class_id(client, pid)

    def box(patch):
        res = client.post(f"/api/patches/{patch['id']}/annotations", json={"type": "rectangle", "class_id": cls, "coordinates_patch_local": square(10, 10, 50)})
        assert res.status_code in (200, 201), res.text

    for sid in ids:
        generate(client, sid, config_id, BIG)
        for patch in patches(client, sid)[:2]:
            box(patch)
    split(client, pid, ids)
    v1 = save(client, pid, "Two boxes each")
    box(patches(client, ids[0])[2])  # the work goes on: a third box on the training slide

    def train_objects(**params):
        return client.get(f"/api/projects/{pid}/training/readiness", params=params).json()["sets"]["train"]["objects"]

    assert (train_objects(), train_objects(version=v1["id"])) == (3, 2)

    def cut(run):
        job = trainer(client, "/claim").json()["run"]
        assert job is not None and job["id"] == run["id"]
        folder = run_dir(run["id"]) / "dataset"
        layout = json.loads((folder / "dataset.json").read_text())
        return len(json.loads((folder / layout["sets"]["train"]["annotations"]).read_text())["annotations"])

    on_version = client.post(f"/api/projects/{pid}/training/runs", json={"recipe_id": RECIPE, "version_id": v1["id"]}).json()
    assert (on_version["version_id"], on_version["version_label"]) == (v1["id"], "v1 · Two boxes each")
    assert cut(on_version) == 2
    on_work = client.post(f"/api/projects/{pid}/training/runs", json={"recipe_id": RECIPE}).json()
    assert (on_work["version_id"], on_work["version_label"]) == (None, None)
    assert cut(on_work) == 3
    assert client.get(f"/api/training/runs/{on_work['id']}").json()["version_label"] == "v1 · Two boxes each + unsaved changes"
    body = versions(client, pid)
    assert (body["current_version_id"], body["has_unsaved_changes"]) == (v1["id"], True)

    assert client.post(f"/api/projects/{pid}/training/runs", json={"recipe_id": RECIPE, "version_id": 999}).status_code == 404
    gone = client.post(f"/api/projects/{pid}/training/runs", json={"recipe_id": RECIPE, "version_id": v1["id"]}).json()
    client.delete(f"/api/projects/{pid}/versions/{v1['id']}")
    assert trainer(client, "/claim").json()["run"] is None
    failed = client.get(f"/api/training/runs/{gone['id']}").json()
    assert failed["status"] == "failed" and "was deleted" in failed["error"]
