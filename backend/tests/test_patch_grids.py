"""Patch grids (patch size / stride / magnification / threshold): a slide has one, and cutting a new size
replaces it without losing any annotation; exports can still be cut into a grid chosen at export time."""
import csv
import io
import json

import pytest

from app.services.patch_grid import GridSpec
from tests.test_slide_import_api import SLIDE, env, upload  # noqa: F401  (env is a fixture)

W, H = 1536, 1024  # the test slide; no magnification in it, so it counts as 40x: at 40x, 1 px = 1 Level-0 px

BIG = {"patch_width": 512, "patch_height": 512, "stride_x": 512, "stride_y": 512, "target_magnification": 40, "min_tissue_fraction": 0}
SMALL = {"patch_width": 256, "patch_height": 256, "stride_x": 256, "stride_y": 256, "target_magnification": 40, "min_tissue_fraction": 0}


def key(spec):
    return GridSpec(**spec).key


@pytest.fixture()
def slide(env, monkeypatch):  # noqa: F811
    client, pid, settings = env
    monkeypatch.setattr("app.api.processing.get_settings", lambda: settings)
    sid = upload(client, pid, [("s.tif", SLIDE)]).json()["slides"][0]["id"]
    config_id = client.get(f"/api/projects/{pid}").json()["active_config_version_id"]
    return client, pid, sid, config_id


def generate(client, sid, config_id, spec=None):
    body = {"config_version_id": config_id, **({"grid": spec} if spec else {})}
    res = client.post(f"/api/slides/{sid}/generate-patches", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def class_id(client, pid):
    """A class of the project (the test project starts with none)."""
    config_id = client.get(f"/api/projects/{pid}").json()["active_config_version_id"]
    res = client.put(f"/api/configs/{config_id}", json={"annotation_classes": [{"name": "Tumor", "color_hex": "#dc2626"}]})
    return res.json()["annotation_classes"][0]["id"]


def patches(client, sid):
    return client.get(f"/api/slides/{sid}/patches", params={"limit": 5000}).json()["items"]


def square(x0, y0, size):
    return [[x0, y0], [x0 + size, y0], [x0 + size, y0 + size], [x0, y0 + size]]


def test_grid_keys_round_trip_and_read_well():
    spec = GridSpec(2048, 2048, 1024, 1024, 40.0, 0.04)
    assert spec.key == "2048x2048_s1024x1024_m40_t0.04"
    assert GridSpec.from_key(spec.key) == spec
    assert spec.label == "2048 px, stride 1024, 40x, tissue >= 4%"
    odd = GridSpec(512, 256, 100, 200, None, 0.5, include_edge_patches=True)
    assert GridSpec.from_key(odd.key) == odd
    with pytest.raises(ValueError):
        GridSpec.from_key("not-a-grid")


def test_generating_a_new_size_replaces_the_slides_grid(slide):
    client, _, sid, config_id = slide
    big = generate(client, sid, config_id, BIG)
    assert big["grid_key"] == key(BIG) and big["kept"] == 6 and big["replaced_grids"] == 0
    small = generate(client, sid, config_id, SMALL)
    assert small["kept"] == 24 and (small["replaced_grids"], small["replaced_patches"]) == (1, 6)

    assert client.get(f"/api/slides/{sid}").json()["active_grid_key"] == key(SMALL)
    assert len(patches(client, sid)) == 24
    made = {g["key"]: g["patch_count"] for g in client.get(f"/api/slides/{sid}/grids").json() if g["patch_count"]}
    assert made == {key(SMALL): 24}  # the big grid is gone
    assert client.put(f"/api/slides/{sid}/active-grid", json={"grid_key": key(BIG)}).status_code == 404

    before = {p["id"] for p in patches(client, sid)}
    again = generate(client, sid, config_id, SMALL)  # the same size again: updated in place, nothing replaced
    assert again["replaced_grids"] == 0 and {p["id"] for p in patches(client, sid)} == before


def test_replacing_the_grid_keeps_every_annotation_on_the_whole_slide(slide):
    client, _, sid, config_id = slide
    generate(client, sid, config_id, BIG)
    first_big = next(p for p in patches(client, sid) if (p["x"], p["y"]) == (0, 0))
    ann = client.post(f"/api/patches/{first_big['id']}/annotations", json={"type": "rectangle", "coordinates_patch_local": square(100, 100, 50)}).json()

    res = generate(client, sid, config_id, SMALL)  # switch to 256 px patches
    assert res["annotations_moved_to_slide"] == 1
    (kept,) = client.get(f"/api/slides/{sid}/annotations").json()
    assert kept["id"] == ann["id"] and kept["patch_id"] is None and kept["coordinates_level0"] == ann["coordinates_level0"]


def test_regenerating_a_grid_never_loses_annotations(slide):
    client, _, sid, config_id = slide
    # Only the left third is tissue, so the big grid has 2 patches (x = 0).
    client.put(f"/api/slides/{sid}/tissue-regions", json={"source": "manual", "regions": [{"mode": "add", "type": "rectangle", "coordinates": [[0, 0], [512, 0], [512, H], [0, H]]}]})
    spec = {**BIG, "min_tissue_fraction": 0.5}
    assert generate(client, sid, config_id, spec)["kept"] == 2
    top, bottom = sorted(patches(client, sid), key=lambda p: p["y"])
    ann = client.post(f"/api/patches/{bottom['id']}/annotations", json={"type": "point", "coordinates_patch_local": [[10, 10]]}).json()

    # The tissue now covers only the top patch: regenerating keeps the annotated bottom one anyway.
    client.put(f"/api/slides/{sid}/tissue-regions", json={"source": "manual", "regions": [{"mode": "add", "type": "rectangle", "coordinates": square(0, 0, 512)}]})
    res = generate(client, sid, config_id, spec)
    assert res["kept"] == 1 and res["preserved"] == 1
    ids = {p["id"] for p in patches(client, sid)}
    assert ids == {top["id"], bottom["id"]}  # same patches, same ids
    assert [a["id"] for a in client.get(f"/api/patches/{bottom['id']}/annotations").json()] == [ann["id"]]


def test_changing_the_grid_in_settings_keeps_existing_patches_until_that_size_is_picked(slide):
    client, _, sid, config_id = slide
    client.put(f"/api/configs/{config_id}", json=BIG)
    generate(client, sid, config_id)
    first = patches(client, sid)[0]
    client.post(f"/api/patches/{first['id']}/annotations", json={"type": "point", "coordinates_patch_local": [[5, 5]]})

    res = client.put(f"/api/configs/{config_id}", json=SMALL)  # allowed although patches exist
    assert res.status_code == 200, res.text
    assert len(patches(client, sid)) == 6  # the slide still shows the grid it has
    small_key = next(g for g in client.get(f"/api/slides/{sid}/grids").json() if g["is_default"])["key"]
    generate(client, sid, config_id)  # Generate Coords re-cuts the slide at the size it is on ...
    assert len(patches(client, sid)) == 6
    listed = {g["key"]: g for g in client.get(f"/api/slides/{sid}/grids").json()}
    (on,) = [g for g in listed.values() if g["patch_count"]]
    assert on["is_default"] and on["spec"]["patch_width"] == 512  # ... which becomes the default again
    assert listed[small_key]["patch_count"] == 0  # the small size stays listed, to pick
    generate(client, sid, config_id, listed[small_key]["spec"])  # picked for this slide: replaces the big one
    assert len(patches(client, sid)) == 24
    grids = [g for g in client.get(f"/api/slides/{sid}/grids").json() if g["patch_count"]]
    assert [(g["patch_count"], g["is_default"]) for g in grids] == [(24, True)]
    assert len(client.get(f"/api/slides/{sid}/annotations").json()) == 1


def test_export_in_a_grid_chosen_at_export_time(slide):
    client, pid, sid, config_id = slide
    generate(client, sid, config_id, BIG)
    first = next(p for p in patches(client, sid) if (p["x"], p["y"]) == (0, 0))
    # 100..400 on the slide: in a 256 px grid that crosses four patches.
    client.post(
        f"/api/patches/{first['id']}/annotations",
        json={"type": "rectangle", "class_id": class_id(client, pid), "coordinates_patch_local": square(100, 100, 300)},
    )

    table = list(csv.DictReader(io.StringIO(client.get(f"/api/slides/{sid}/export/patch_csv", params={"grid": key(SMALL), "patches": "all"}).text)))
    assert len(table) == 24
    touched = sorted((int(r["level0_x"]), int(r["level0_y"])) for r in table if int(r["n_annotations"]) > 0)
    assert touched == [(0, 0), (0, 256), (256, 0), (256, 256)]
    assert {r["width_level0"] for r in table} == {"256"}

    coco = client.get(f"/api/slides/{sid}/export/coco", params={"grid": key(SMALL)}).json()
    assert len(coco["images"]) == 4 and len(coco["annotations"]) == 4
    assert all(img["width"] == 256 for img in coco["images"])

    # Nothing was stored: the slide still has only its own grid.
    assert [g["key"] for g in client.get(f"/api/slides/{sid}/grids").json() if g["patch_count"]] == [key(BIG)]
    summary = client.get(f"/api/slides/{sid}/export-summary", params={"grid": key(SMALL), "patches": "all"}).json()
    assert summary["patches"] == 24

    assert client.get(f"/api/slides/{sid}/export/coco", params={"grid": "nonsense"}).status_code == 422


def test_export_after_replacing_the_grid_still_includes_earlier_annotations(slide):
    client, pid, sid, config_id = slide
    generate(client, sid, config_id, BIG)
    first = next(p for p in patches(client, sid) if (p["x"], p["y"]) == (0, 0))
    ann = client.post(
        f"/api/patches/{first['id']}/annotations",
        json={"type": "rectangle", "class_id": class_id(client, pid), "coordinates_patch_local": square(100, 100, 50)},
    ).json()
    generate(client, sid, config_id, SMALL)  # now annotating in the small grid

    coco = client.get(f"/api/slides/{sid}/export/coco").json()
    assert len(coco["annotations"]) == 1 and coco["images"][0]["width"] == 256
    assert coco["annotations"][0]["vp_scope"] == "slide" and coco["annotations"][0]["vp_source_annotation_id"] == ann["id"]
    doc = json.loads(client.get(f"/api/slides/{sid}/export/wsi_json").text)
    (entry,) = doc["annotations"]
    assert entry["annotation_id"] == f"ann_{ann['id']:06d}" and entry["source_patch"] is None


def test_the_slide_list_reports_each_slides_progress(slide):
    client, pid, sid, config_id = slide
    (row,) = client.get(f"/api/projects/{pid}/slides").json()
    assert (row["patch_count"], row["annotated_patch_count"], row["reviewed_patch_count"]) == (0, 0, 0)

    generate(client, sid, config_id, BIG)
    first, second = patches(client, sid)[:2]
    client.post(f"/api/patches/{first['id']}/annotations", json={"type": "point", "coordinates_patch_local": [[5, 5]]})
    client.put(f"/api/patches/{second['id']}", json={"status": "reviewed"})
    (row,) = client.get(f"/api/projects/{pid}/slides").json()
    assert (row["patch_count"], row["annotated_patch_count"], row["reviewed_patch_count"]) == (6, 2, 1)


def test_the_project_lists_every_patch_size_it_uses(slide):
    client, pid, sid, config_id = slide
    other = upload(client, pid, [("t.tif", SLIDE)]).json()["slides"][0]["id"]
    generate(client, sid, config_id, BIG)
    first = patches(client, sid)[0]
    client.post(f"/api/patches/{first['id']}/annotations", json={"type": "point", "coordinates_patch_local": [[5, 5]]})
    client.post(f"/api/patches/{first['id']}/annotations", json={"type": "point", "coordinates_patch_local": [[9, 9]]})
    generate(client, other, config_id, SMALL)  # each slide has its own size

    grids = {g["key"]: g for g in client.get(f"/api/projects/{pid}/grids").json()}
    big, small = grids[key(BIG)], grids[key(SMALL)]
    assert (big["slide_count"], big["patch_count"], big["annotated_patch_count"], big["annotation_count"]) == (1, 6, 1, 2)
    assert (small["slide_count"], small["patch_count"], small["annotation_count"]) == (1, 24, 0)
    assert sum(g["is_default"] for g in grids.values()) == 1  # the project's own grid, generated or not


def test_removing_a_patch_size_keeps_its_annotations_as_whole_slide_ones(slide):
    client, pid, sid, config_id = slide
    generate(client, sid, config_id, BIG)
    first = patches(client, sid)[0]
    ann = client.post(f"/api/patches/{first['id']}/annotations", json={"type": "rectangle", "coordinates_patch_local": square(10, 10, 40)}).json()

    res = client.delete(f"/api/projects/{pid}/grids/{key(BIG)}")
    assert res.status_code == 200, res.text
    assert res.json() == {"slides": 1, "patches": 6, "annotations_kept": 1}

    (kept,) = client.get(f"/api/slides/{sid}/annotations").json()
    assert kept["id"] == ann["id"] and kept["patch_id"] is None and kept["coordinates_level0"] == ann["coordinates_level0"]
    assert client.get(f"/api/slides/{sid}").json()["active_grid_key"] is None  # back to generating patches
    assert key(BIG) not in {g["key"] for g in client.get(f"/api/projects/{pid}/grids").json() if g["patch_count"]}
    assert client.delete(f"/api/projects/{pid}/grids/{key(BIG)}").status_code == 404  # nothing left to remove


def test_removing_a_slides_last_patch_size_sends_it_back_to_generating(slide):
    client, pid, sid, config_id = slide
    generate(client, sid, config_id, BIG)
    res = client.delete(f"/api/slides/{sid}/grids/{key(BIG)}")
    assert res.status_code == 200 and res.json()["patches"] == 6
    slide_now = client.get(f"/api/slides/{sid}").json()
    assert slide_now["active_grid_key"] is None and slide_now["status"] in ("imported", "tissue_detected")
    assert patches(client, sid) == []


def test_any_patch_size_can_become_the_project_default(slide):
    client, pid, sid, config_id = slide
    generate(client, sid, config_id, SMALL)
    res = client.put(f"/api/configs/{config_id}", json={k: SMALL[k] for k in ("patch_width", "patch_height", "stride_x", "stride_y", "target_magnification", "min_tissue_fraction")})
    assert res.status_code == 200
    defaults = [g for g in client.get(f"/api/projects/{pid}/grids").json() if g["is_default"]]
    assert len(defaults) == 1 and defaults[0]["spec"]["patch_width"] == 256


def test_a_patch_size_can_be_applied_to_every_slide_of_the_project(slide):
    client, pid, sid, config_id = slide
    SMALL_T = {**SMALL, "min_tissue_fraction": 0.5}  # a tissue threshold: needs tissue found first
    other = upload(client, pid, [("t.tif", SLIDE)]).json()["slides"][0]["id"]  # no tissue found yet
    client.put(f"/api/slides/{sid}/tissue-regions", json={"source": "manual", "regions": [{"mode": "add", "type": "rectangle", "coordinates": [[0, 0], [W, 0], [W, H], [0, H]]}]})
    generate(client, sid, config_id, BIG)

    res = client.post(f"/api/projects/{pid}/grids", json={"grid": SMALL_T})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["grid_key"] == key(SMALL_T) and body["slides"] == 1 and body["patches"] == 24
    assert [s["slide_id"] for s in body["skipped"]] == [other] and "tissue" in body["skipped"][0]["reason"]
    assert client.get(f"/api/slides/{sid}").json()["active_grid_key"] == key(SMALL_T)  # replaced the big grid
    assert {g["key"] for g in client.get(f"/api/slides/{sid}/grids").json() if g["patch_count"]} == {key(SMALL_T)}
    assert not any(g["is_default"] and g["key"] == key(SMALL_T) for g in client.get(f"/api/projects/{pid}/grids").json())

    client.post(f"/api/projects/{pid}/grids", json={"grid": SMALL_T, "make_default": True})  # again, now as default
    assert next(g for g in client.get(f"/api/projects/{pid}/grids").json() if g["is_default"])["key"] == key(SMALL_T)


def test_whole_slide_patches_need_no_tissue_and_cover_everything(slide):
    client, pid, sid, config_id = slide  # no tissue detected on this slide
    res = client.post(f"/api/slides/{sid}/generate-patches", json={"config_version_id": config_id, "grid": {**BIG, "min_tissue_fraction": 0.5}, "whole_slide": True})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["kept"] == body["total_candidates"] == (W // 512) * (H // 512) and body["excluded"] == 0
    assert body["grid_label"].endswith("whole slide")
    whole = GridSpec(**BIG).over_whole_slide()
    assert body["grid_key"] == whole.key and whole.include_edge_patches

    other = upload(client, pid, [("t.tif", SLIDE)]).json()["slides"][0]["id"]  # no tissue either
    res = client.post(f"/api/projects/{pid}/grids", json={"grid": SMALL}).json()  # 0% = whole slide
    assert res["skipped"] == [] and res["slides"] == 2
    assert {g["key"] for g in client.get(f"/api/slides/{other}/grids").json() if g["patch_count"]} == {key(SMALL)}


def test_grids_bigger_than_sqlites_variable_limit_can_be_regenerated_and_replaced(slide):
    # SQLite takes at most 32,766 variables per statement; a whole-slide grid easily has more patches.
    client, _, sid, config_id = slide
    tiny = {"patch_width": 16, "patch_height": 16, "stride_x": 6, "stride_y": 6, "target_magnification": 40, "min_tissue_fraction": 0}
    first = generate(client, sid, config_id, tiny)
    assert first["kept"] > 32766
    assert generate(client, sid, config_id, tiny)["replaced_grids"] == 0  # regenerated in place
    res = generate(client, sid, config_id, BIG)  # replaces it
    assert (res["replaced_grids"], res["replaced_patches"]) == (1, first["kept"])


def test_generate_coords_recuts_the_slide_at_its_current_patch_size(slide):
    client, _, sid, config_id = slide  # the configuration's own grid is not BIG
    client.put(f"/api/slides/{sid}/tissue-regions", json={"source": "manual", "regions": [{"mode": "add", "type": "rectangle", "coordinates": [[0, 0], [W, 0], [W, H], [0, H]]}]})
    tissue_big = {**BIG, "min_tissue_fraction": 0.5}
    generate(client, sid, config_id, tissue_big)

    again = client.post(f"/api/slides/{sid}/generate-patches", json={"config_version_id": config_id}).json()
    assert again["grid_key"] == key(tissue_big) and again["replaced_grids"] == 0  # not back to the project's size

    whole = client.post(f"/api/slides/{sid}/generate-patches", json={"config_version_id": config_id, "whole_slide": True}).json()
    assert whole["grid_key"] == GridSpec(**BIG).over_whole_slide().key  # same size, whole slide
    back = client.post(f"/api/slides/{sid}/generate-patches", json={"config_version_id": config_id, "whole_slide": False}).json()
    spec = GridSpec.from_key(back["grid_key"])
    assert (spec.patch_width, spec.whole_slide) == (512, False)  # same size, tissue only again


def test_choosing_one_of_a_slides_older_grids_removes_the_others(slide):
    """Slides cut into several sizes before a slide had one: picking a size keeps only that one."""
    from app.database.session import get_db
    from app.main import app
    from app.models.patch import Patch

    client, _, sid, config_id = slide
    generate(client, sid, config_id, BIG)
    first = next(p for p in patches(client, sid) if (p["x"], p["y"]) == (0, 0))
    ann = client.post(f"/api/patches/{first['id']}/annotations", json={"type": "point", "coordinates_patch_local": [[5, 5]]}).json()
    db = next(app.dependency_overrides[get_db]())  # an older, second grid, as the app used to allow
    db.add(Patch(slide_id=sid, config_version_id=config_id, grid_key=key(SMALL), patch_index=0, x=0, y=0, level=0,
                 width=256, height=256, width_l0=256, height_l0=256, tissue_fraction=1.0))
    db.commit()

    res = client.put(f"/api/slides/{sid}/active-grid", json={"grid_key": key(SMALL)})
    assert res.status_code == 200 and res.json()["active_grid_key"] == key(SMALL)
    assert {g["key"] for g in client.get(f"/api/slides/{sid}/grids").json() if g["patch_count"]} == {key(SMALL)}
    (kept,) = client.get(f"/api/slides/{sid}/annotations").json()
    assert kept["id"] == ann["id"] and kept["patch_id"] is None  # kept, on the whole slide


def test_sizes_made_stay_listed_until_removed_and_the_one_picked_becomes_the_default(slide):
    client, pid, sid, config_id = slide
    generate(client, sid, config_id, BIG)
    generate(client, sid, config_id, SMALL)  # replaces the big patches ...

    listed = {g["key"]: g for g in client.get(f"/api/slides/{sid}/grids").json()}
    assert listed[key(BIG)]["patch_count"] == 0  # ... but the big size is still there to pick
    assert listed[key(SMALL)]["patch_count"] == 24 and listed[key(SMALL)]["is_default"]  # and the small one is the project's
    project = {g["key"]: g for g in client.get(f"/api/projects/{pid}/grids").json()}
    assert key(BIG) in project and project[key(SMALL)]["is_default"]

    assert client.delete(f"/api/projects/{pid}/grids/{key(BIG)}").status_code == 200  # removed on purpose: gone
    assert key(BIG) not in {g["key"] for g in client.get(f"/api/projects/{pid}/grids").json()}
    assert client.delete(f"/api/projects/{pid}/grids/{key(BIG)}").status_code == 404
