"""The export screen's download: chosen slides of a project, one or several formats, with or without images."""
import io
import json
import zipfile

from tests.test_patch_grids import BIG, generate, patches, square  # noqa: F401
from tests.test_slide_import_api import SLIDE, env, upload  # noqa: F401  (env is a fixture)

import pytest


@pytest.fixture()
def project(env, monkeypatch):  # noqa: F811
    """Three slides: two with a patch grid (one annotated), one never processed."""
    client, pid, settings = env
    monkeypatch.setattr("app.api.processing.get_settings", lambda: settings)
    ids = [s["id"] for s in upload(client, pid, [("a.tif", SLIDE), ("b.tif", SLIDE), ("c.tif", SLIDE)]).json()["slides"]]
    config_id = client.get(f"/api/projects/{pid}").json()["active_config_version_id"]
    for sid in ids[:2]:
        generate(client, sid, config_id, BIG)
    first = patches(client, ids[0])[0]
    client.post(f"/api/patches/{first['id']}/annotations", json={"type": "rectangle", "coordinates_patch_local": square(10, 10, 50)})
    return client, pid, ids


def unzip(res):
    assert res.status_code == 200, res.text
    return zipfile.ZipFile(io.BytesIO(res.content))


def test_one_format_for_one_chosen_slide_is_that_file(project):
    client, pid, ids = project
    res = client.get(f"/api/projects/{pid}/export", params={"formats": "wsi_json", "slides": str(ids[0])})
    assert res.status_code == 200 and res.headers["content-disposition"].endswith('a_wsi_json.json"')
    assert json.loads(res.text)["slide"]


def test_several_formats_and_images_for_the_chosen_slides_in_one_zip(project):
    client, pid, ids = project
    res = client.get(
        f"/api/projects/{pid}/export",
        params={"formats": "coco,patch_csv", "slides": f"{ids[0]},{ids[1]}", "content": "images", "patches": "all", "combine": "true"},
    )
    with unzip(res) as z:
        names = z.namelist()
        manifest = json.loads(z.read("manifest.json"))
    assert any(n.endswith("_coco.json") for n in names) and any(n.endswith("_patch_csv.csv") for n in names)
    assert len([n for n in names if n.startswith("images/")]) == 12  # 6 patches on each chosen slide
    assert [s["slide_id"] for s in manifest["slides"]] == ids[:2]
    assert manifest["format"] == "coco,patch_csv"


def test_several_formats_without_images_and_unready_slides_listed_as_skipped(project):
    client, pid, ids = project
    with unzip(client.get(f"/api/projects/{pid}/export", params={"formats": "geojson,stats_csv"})) as z:
        names = z.namelist()
        manifest = json.loads(z.read("manifest.json"))
    assert not any(n.startswith("images/") for n in names)
    assert [s["slide_id"] for s in manifest["skipped_slides"]] == [ids[2]]
    assert len([n for n in names if n.endswith(".geojson")]) == 2


def test_bad_choices_are_refused(project):
    client, pid, ids = project
    assert client.get(f"/api/projects/{pid}/export", params={"formats": ""}).status_code == 422
    assert client.get(f"/api/projects/{pid}/export", params={"formats": "shapefile"}).status_code == 422
    assert client.get(f"/api/projects/{pid}/export", params={"formats": "coco", "slides": "999999"}).status_code == 422
    assert client.get(f"/api/projects/{pid}/export", params={"formats": "coco", "slides": str(ids[2])}).status_code == 422  # no grid


def test_the_summary_counts_only_the_chosen_slides(project):
    client, pid, ids = project
    one = client.get(f"/api/projects/{pid}/export-summary", params={"slides": str(ids[1]), "patches": "all"}).json()
    both = client.get(f"/api/projects/{pid}/export-summary", params={"patches": "all"}).json()
    assert (one["slides"], one["patches"]) == (1, 6)
    assert (both["slides"], both["patches"]) == (2, 12)
