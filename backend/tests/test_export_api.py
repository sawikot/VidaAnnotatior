"""The export endpoint over real HTTP: headers, download names and a full
upload -> patches -> annotate -> export run on a real (tiny) OpenSlide file."""
import csv
import io
import json
import zipfile

import pytest

from tests.test_slide_import_api import SLIDE, env, upload  # noqa: F401  (env is a fixture)

FORMATS = {
    "wsi_json": ("application/json", "json"),
    "geojson": ("application/geo+json", "geojson"),
    "coco": ("application/json", "json"),
    "patch_csv": ("text/csv; charset=utf-8", "csv"),
    "stats_csv": ("text/csv; charset=utf-8", "csv"),
}


@pytest.fixture()
def annotated_slide(env):  # noqa: F811
    client, pid, _ = env
    slide_id = upload(client, pid, [("Case 1 (v2).tif", SLIDE)]).json()["slides"][0]["id"]

    config = client.get(f"/api/projects/{pid}/configs").json()[0]
    updated = client.put(
        f"/api/configs/{config['id']}",
        json={
            "patch_width": 512, "patch_height": 512, "stride_x": 512, "stride_y": 512, "min_tissue_fraction": 0.0,
            "annotation_classes": [{"name": "Tumor", "color_hex": "#dc2626"}, {"name": "Stroma", "color_hex": "#16a34a"}],
        },
    )  # fmt: skip
    assert updated.status_code == 200, updated.text
    classes = {c["name"]: c["id"] for c in updated.json()["annotation_classes"]}

    gen = client.post(f"/api/slides/{slide_id}/generate-patches", json={"config_version_id": config["id"]})
    assert gen.status_code == 200, gen.text
    patch = client.get(f"/api/slides/{slide_id}/patches?limit=1").json()["items"][0]

    square = [[50, 50], [250, 50], [250, 250], [50, 250]]  # 200 x 200 px inside the patch
    res = client.post(
        f"/api/patches/{patch['id']}/annotations",
        json={"type": "polygon", "class_id": classes["Tumor"], "coordinates_patch_local": square},
    )
    assert res.status_code == 201, res.text
    return client, slide_id, patch


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_every_format_downloads_with_the_right_headers(annotated_slide, fmt):
    client, slide_id, _ = annotated_slide
    res = client.get(f"/api/slides/{slide_id}/export/{fmt}")
    content_type, ext = FORMATS[fmt]
    assert res.status_code == 200
    assert res.headers["content-type"] == content_type
    disposition = res.headers["content-disposition"]
    assert disposition == f'attachment; filename="Case_1_v2_{fmt}.{ext}"'  # user filename sanitised for the header
    assert res.text.strip()


def test_exported_geometry_is_the_patch_origin_plus_scaled_local_coordinates(annotated_slide):
    """The tiny slide is read at level 1, so local pixels are 2x Level-0 pixels:
    global = origin + local * downsample, in every format."""
    client, slide_id, patch = annotated_slide
    ox, oy = patch["x"], patch["y"]
    ds = patch["width_l0"] / patch["width"]
    assert ds == 2.0

    feature = client.get(f"/api/slides/{slide_id}/export/geojson").json()["features"][0]
    assert feature["geometry"]["coordinates"][0][0] == [ox + 50 * ds, oy + 50 * ds]
    assert feature["properties"]["classification"]["name"] == "Tumor"

    coco = client.get(f"/api/slides/{slide_id}/export/coco").json()
    ann = coco["annotations"][0]
    assert ann["segmentation"] == [[50, 50, 250, 50, 250, 250, 50, 250]] and ann["area"] == 40000.0
    assert ann["vp_level0_segmentation"][0][:2] == [ox + 50 * ds, oy + 50 * ds]
    assert coco["images"][0]["vp_downsample"] == ds and coco["images"][0]["width"] == patch["width"]
    assert coco["images"][0]["id"] == patch["id"] and [c["name"] for c in coco["categories"]] == ["Tumor", "Stroma"]


def test_csv_downloads_parse_and_agree_with_the_data(annotated_slide):
    client, slide_id, patch = annotated_slide
    patches = list(csv.DictReader(io.StringIO(client.get(f"/api/slides/{slide_id}/export/patch_csv").text)))
    assert len(patches) == client.get(f"/api/slides/{slide_id}/patches?limit=1").json()["total"]
    row = next(r for r in patches if int(r["patch_id"]) == patch["id"])
    assert (int(row["level0_x"]), int(row["level0_y"]), row["dominant_class"], row["n_annotations"]) == (patch["x"], patch["y"], "Tumor", "1")

    stats = {r["class"]: r for r in csv.DictReader(io.StringIO(client.get(f"/api/slides/{slide_id}/export/stats_csv").text))}
    # areas are reported in Level-0 px2: 200x200 local px at downsample 2
    assert float(stats["Tumor"]["summed_area_px2"]) == 40000.0 * 4 and stats["Stroma"]["n_annotations"] == "0"


def test_unknown_format_and_unknown_slide_are_404(annotated_slide):
    client, slide_id, _ = annotated_slide
    assert client.get(f"/api/slides/{slide_id}/export/shapefile").status_code == 404
    assert client.get("/api/slides/999999/export/geojson").status_code == 404
    assert json.loads(client.get(f"/api/slides/{slide_id}/export/wsi_json").text)["slide"]["filename"] == "Case 1 (v2).tif"


# ------------------------------------------------------------ whole project


def _project_id(client, slide_id):
    return client.get(f"/api/slides/{slide_id}").json()["project_id"]


def _open_zip(res):
    assert res.status_code == 200 and res.headers["content-type"] == "application/zip"
    return zipfile.ZipFile(io.BytesIO(res.content))


def test_project_export_bundles_every_processed_slide_with_a_manifest(annotated_slide):
    client, slide_id, patch = annotated_slide
    pid = _project_id(client, slide_id)
    upload(client, pid, [("Unprocessed.tif", SLIDE)])  # imported, but no patch grid yet

    res = client.get(f"/api/projects/{pid}/export/wsi_json")
    assert res.headers["content-disposition"].endswith('_wsi_json_all_slides.zip"')
    archive = _open_zip(res)
    assert sorted(archive.namelist()) == ["Case_1_v2_wsi_json.json", "manifest.json"]

    # the bundled file is byte-for-byte what the single-slide download gives
    assert archive.read("Case_1_v2_wsi_json.json").decode() == client.get(f"/api/slides/{slide_id}/export/wsi_json").text

    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["format"] == "wsi_json" and manifest["slide_count"] == 1
    assert manifest["files"][0]["slide_id"] == slide_id and manifest["files"][0]["bytes"] > 0
    assert [(k["slide"], k["reason"]) for k in manifest["skipped"]] == [("Unprocessed.tif", "No patch grid generated yet")]


@pytest.mark.parametrize("fmt", ["geojson", "coco", "patch_csv", "stats_csv"])
def test_project_export_works_for_every_format(annotated_slide, fmt):
    client, slide_id, _ = annotated_slide
    archive = _open_zip(client.get(f"/api/projects/{_project_id(client, slide_id)}/export/{fmt}"))
    ext = FORMATS[fmt][1]
    assert f"Case_1_v2_{fmt}.{ext}" in archive.namelist()


def test_slides_sharing_a_filename_do_not_overwrite_each_other_in_the_zip(annotated_slide):
    client, slide_id, patch = annotated_slide
    pid = _project_id(client, slide_id)
    second = upload(client, pid, [("Case 1 (v2).tif", SLIDE)]).json()["slides"][0]["id"]
    assert client.post(f"/api/slides/{second}/generate-patches", json={"config_version_id": patch["config_version_id"]}).status_code == 200

    archive = _open_zip(client.get(f"/api/projects/{pid}/export/patch_csv"))
    assert sorted(archive.namelist()) == sorted(["Case_1_v2_patch_csv.csv", f"Case_1_v2_{second}_patch_csv.csv", "manifest.json"])


def test_project_export_refuses_when_nothing_is_processed_and_rejects_bad_input(env):
    client, pid, _ = env
    upload(client, pid, [("raw.tif", SLIDE)])
    assert client.get(f"/api/projects/{pid}/export/wsi_json").status_code == 422
    assert client.get(f"/api/projects/{pid}/export/shapefile").status_code == 404
    assert client.get("/api/projects/999999/export/wsi_json").status_code == 404
