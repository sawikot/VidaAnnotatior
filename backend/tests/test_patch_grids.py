"""Several patch grids on one slide (different patch size / stride / magnification / threshold), all
sharing the slide's annotations; and exports cut into a grid chosen only at export time."""
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


def test_a_slide_holds_several_grids_and_shows_the_active_one(slide):
    client, _, sid, config_id = slide
    big = generate(client, sid, config_id, BIG)
    assert big["grid_key"] == key(BIG) and big["kept"] == 6
    small = generate(client, sid, config_id, SMALL)
    assert small["kept"] == 24

    assert client.get(f"/api/slides/{sid}").json()["active_grid_key"] == key(SMALL)
    assert len(patches(client, sid)) == 24  # only the active grid is listed

    grids = {g["key"]: g for g in client.get(f"/api/slides/{sid}/grids").json()}
    assert grids[key(BIG)]["patch_count"] == 6 and grids[key(SMALL)]["patch_count"] == 24
    assert grids[key(SMALL)]["active"] and not grids[key(BIG)]["active"]
    assert sum(g["is_default"] for g in grids.values()) == 1  # the configuration's own grid is listed too

    res = client.put(f"/api/slides/{sid}/active-grid", json={"grid_key": key(BIG)})
    assert res.status_code == 200 and res.json()["active_grid_key"] == key(BIG)
    assert len(patches(client, sid)) == 6
    assert client.put(f"/api/slides/{sid}/active-grid", json={"grid_key": "64x64_s64x64_m40_t0"}).status_code == 404


def test_annotations_are_shared_between_grids(slide):
    client, _, sid, config_id = slide
    generate(client, sid, config_id, BIG)
    first_big = next(p for p in patches(client, sid) if (p["x"], p["y"]) == (0, 0))
    ann = client.post(f"/api/patches/{first_big['id']}/annotations", json={"type": "rectangle", "coordinates_patch_local": square(100, 100, 50)}).json()

    generate(client, sid, config_id, SMALL)  # switch to 256 px patches
    small = {(p["x"], p["y"]): p for p in patches(client, sid)}
    # The rectangle (100..150 on the slide) lies in the small patch at the origin: it shows there, owned by the big patch.
    seen = client.get(f"/api/patches/{small[(0, 0)]['id']}/overlapping-annotations").json()
    assert [(o["annotation"]["id"], o["owner"]["id"]) for o in seen] == [(ann["id"], first_big["id"])]
    assert client.get(f"/api/patches/{small[(256, 0)]['id']}/overlapping-annotations").json() == []
    assert [a["id"] for a in client.get(f"/api/slides/{sid}/annotations").json()] == [ann["id"]]


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


def test_changing_the_grid_in_settings_keeps_existing_patches(slide):
    client, _, sid, config_id = slide
    client.put(f"/api/configs/{config_id}", json=BIG)
    generate(client, sid, config_id)
    first = patches(client, sid)[0]
    client.post(f"/api/patches/{first['id']}/annotations", json={"type": "point", "coordinates_patch_local": [[5, 5]]})

    res = client.put(f"/api/configs/{config_id}", json=SMALL)  # allowed although patches exist
    assert res.status_code == 200, res.text
    assert len(patches(client, sid)) == 6  # the slide still shows the grid it has
    generate(client, sid, config_id)  # the configuration's grid is now the small one
    assert len(patches(client, sid)) == 24
    grids = client.get(f"/api/slides/{sid}/grids").json()
    assert sorted(g["patch_count"] for g in grids) == [6, 24]
    assert next(g for g in grids if g["is_default"])["patch_count"] == 24


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


def test_as_annotated_export_includes_annotations_from_other_grids(slide):
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
    assert coco["annotations"][0]["vp_scope"] == "patch" and coco["annotations"][0]["vp_source_patch_id"] == first["id"]
    doc = json.loads(client.get(f"/api/slides/{sid}/export/wsi_json").text)
    (entry,) = doc["annotations"]
    assert entry["annotation_id"] == f"ann_{ann['id']:06d}" and entry["source_patch"]["patch_id"] == first["id"]  # provenance kept
