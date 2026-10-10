"""Export options: which patches are covered, and annotation-only versus a ZIP that also
holds the patch images (and masks). Uses the hand-built slide from test_export_formats
plus real HTTP with a real (tiny) OpenSlide file."""
import io
import json
import zipfile

import numpy as np
import pytest
from PIL import Image

from app.services.exporter import get_exporter
from app.services.exporter.options import ExportOptions, parse_options
from app.services.exporter.patch_images import unique_names
from tests.test_export_api import annotated_slide  # noqa: F401  (fixture)
from tests.test_export_formats import rows, seed
from tests.test_slide_import_api import SLIDE, env, upload  # noqa: F401  (fixture)


def opts(scope):
    return ExportOptions(patch_scope=scope)


# ----------------------------------------------------------------- parsing


def test_defaults_and_validation():
    d = parse_options()
    assert (d.patch_scope, d.content, d.image_format, d.masks, d.with_images) == ("annotated", "annotations", "png", False, False)
    assert parse_options("all", "images", "jpg", True).image_ext == "jpg"
    for bad in (dict(patches="everything"), dict(content="pdf"), dict(image_format="gif"), dict(masks=True)):  # masks need images
        with pytest.raises(ValueError):
            parse_options(**bad)


# ---------------------------------------------------- scope across the formats
# In the hand-built slide: patch a (7 annotations), b (1, reviewed), c (excluded, 1), d (none).


def patch_ids(s, *names):
    return sorted(s[n].id for n in names)


def test_scope_selects_the_patches_of_every_format(db):
    s = seed(db)
    coco = get_exporter("coco")

    def image_ids(scope):
        return sorted(i["id"] for i in coco.export(db, s["slide"], opts(scope))["images"])

    assert image_ids("annotated") == patch_ids(s, "a", "b")
    assert image_ids("all") == patch_ids(s, "a", "b", "d")  # excluded patch c never becomes an image
    assert image_ids("empty") == patch_ids(s, "d")
    assert image_ids("reviewed") == patch_ids(s, "b")


def test_empty_patches_are_images_without_annotations_in_coco(db):
    s = seed(db)
    doc = get_exporter("coco").export(db, s["slide"], opts("empty"))
    assert [i["id"] for i in doc["images"]] == [s["d"].id] and doc["annotations"] == []
    assert [c["name"] for c in doc["categories"]] == ["Tumor", "Stroma", "Necrosis"]  # the label set is still complete


def test_wsi_json_lists_the_patches_in_scope_and_keeps_its_annotations(db):
    s = seed(db)
    exporter = get_exporter("wsi_json")
    everything = exporter.export(db, s["slide"], opts("all"))
    by_id = {p["patch_id"]: p for p in everything["patches"]}
    assert set(by_id) == set(patch_ids(s, "a", "b", "c", "d"))  # excluded patch listed, flagged
    assert by_id[s["c"].id]["excluded"] is True and by_id[s["d"].id]["annotation_count"] == 0
    assert by_id[s["a"].id]["annotation_count"] == 7 and by_id[s["a"].id]["x"] == 1000
    assert by_id[s["b"].id]["width_px"] == 256 and by_id[s["b"].id]["width"] == 512  # read size vs Level-0 footprint

    default = exporter.export(db, s["slide"])
    assert [p["patch_id"] for p in default["patches"]] == patch_ids(s, "a", "b")
    assert len(default["annotations"]) == len(everything["annotations"])  # annotations never depend on listing empties
    assert exporter.export(db, s["slide"], opts("empty"))["annotations"] == []


def test_reviewed_scope_narrows_annotations_geojson_and_stats(db):
    s = seed(db)
    geo = get_exporter("geojson").export(db, s["slide"], opts("reviewed"))
    assert [f["id"] for f in geo["features"]] == [f"ann_{s['down'].id:06d}"]

    stats = {r["class"]: r for r in rows(get_exporter("stats_csv").export(db, s["slide"], opts("reviewed")))}
    assert stats["Tumor"]["n_annotations"] == "1" and stats["Stroma"]["n_annotations"] == "0"
    assert stats["Tumor"]["slide_patches"] == "3"  # slide-level counts still describe the whole grid


def test_patch_csv_default_is_annotated_and_all_adds_empty_and_excluded(db):
    s = seed(db)
    exporter = get_exporter("patch_csv")
    assert [r["patch_index"] for r in rows(exporter.export(db, s["slide"]))] == ["0", "1"]
    assert [r["patch_index"] for r in rows(exporter.export(db, s["slide"], opts("all")))] == ["0", "1", "2", "3"]
    assert [r["patch_index"] for r in rows(exporter.export(db, s["slide"], opts("empty")))] == ["3"]


# ----------------------------------------------------- unique image names


def test_image_names_are_unique_even_when_two_slides_share_a_filename(db):
    s = seed(db)
    other = type(s["slide"])(project_id=s["slide"].project_id, filename=s["slide"].filename, source_type="upload")
    db.add(other)
    db.flush()
    from app.models.patch import Patch

    twin = Patch(slide_id=other.id, config_version_id=s["config"].id, patch_index=0, x=1000, y=2000, level=0, width=512, height=512, width_l0=512, height_l0=512)
    db.add(twin)
    db.commit()
    names = unique_names([(s["slide"], s["a"]), (other, twin)], "jpg")
    assert len(set(n.lower() for n in names.values())) == 2
    assert names[s["a"].id] == "Case_7_final_p0_x1000_y2000_L0.jpg" and "__s" in names[twin.id]


# ------------------------------------------------------- over HTTP: images


def zip_of(res):
    assert res.status_code == 200 and res.headers["content-type"] == "application/zip"
    assert int(res.headers["content-length"]) == len(res.content)  # streamed with a known size
    return zipfile.ZipFile(io.BytesIO(res.content))


def test_slide_export_with_images_is_a_trainable_coco_dataset(annotated_slide):  # noqa: F811
    client, slide_id, patch = annotated_slide
    res = client.get(f"/api/slides/{slide_id}/export/coco?content=images&patches=all")
    assert res.headers["content-disposition"] == 'attachment; filename="Case_1_v2_coco_all_with_images.zip"'
    archive = zip_of(res)
    names = archive.namelist()

    coco = json.loads(archive.read("annotations/Case_1_v2_coco.json"))
    image_files = sorted(n for n in names if n.startswith("images/"))
    # every COCO image points at a file that is really in the ZIP, and vice versa
    assert sorted("images/" + i["file_name"] for i in coco["images"]) == image_files
    assert len(image_files) == client.get(f"/api/slides/{slide_id}/patches?limit=1").json()["total"]  # 'all' = the whole grid

    entry = next(i for i in coco["images"] if i["id"] == patch["id"])
    pixels = Image.open(io.BytesIO(archive.read("images/" + entry["file_name"])))
    assert pixels.size == (entry["width"], entry["height"]) == (patch["width"], patch["height"]) and pixels.format == "PNG"

    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["image_count"] == len(image_files) and manifest["image_errors"] == []
    assert manifest["options"] == {"patches": "all", "images": True, "image_format": "png", "masks": False, "grid": "as annotated", "combined": False}


def test_images_scope_annotated_only_writes_just_those_patches(annotated_slide):  # noqa: F811
    client, slide_id, patch = annotated_slide
    archive = zip_of(client.get(f"/api/slides/{slide_id}/export/coco?content=images"))
    images = [n for n in archive.namelist() if n.startswith("images/")]
    assert len(images) == 1 and images[0].endswith(".png")
    coco = json.loads(archive.read("annotations/Case_1_v2_coco.json"))
    assert len(coco["images"]) == 1 and len(coco["annotations"]) == 1


def test_png_images_are_lossless_and_match_the_patch_endpoint(annotated_slide):  # noqa: F811
    client, slide_id, patch = annotated_slide
    archive = zip_of(client.get(f"/api/slides/{slide_id}/export/wsi_json?content=images&image_format=png"))
    name = next(n for n in archive.namelist() if n.startswith("images/"))
    assert name.endswith(".png")
    exported = np.asarray(Image.open(io.BytesIO(archive.read(name))).convert("RGB"))
    live = client.get(f"/api/slides/{slide_id}/patch?x={patch['x']}&y={patch['y']}&width={patch['width']}&height={patch['height']}&level={patch['level']}")
    # same pixels the annotator saw (the live endpoint is JPEG, so allow its compression noise)
    assert np.abs(exported.astype(int) - np.asarray(Image.open(io.BytesIO(live.content)).convert("RGB")).astype(int)).mean() < 3


def test_masks_carry_the_class_of_every_pixel(annotated_slide):  # noqa: F811
    client, slide_id, patch = annotated_slide
    archive = zip_of(client.get(f"/api/slides/{slide_id}/export/coco?content=images&masks=true"))
    assert json.loads(archive.read("mask_classes.json")) == {"0": "background", "1": "Tumor", "2": "Stroma"}
    mask_name = next(n for n in archive.namelist() if n.startswith("masks/"))
    mask = np.asarray(Image.open(io.BytesIO(archive.read(mask_name))))
    assert mask.shape == (patch["height"], patch["width"]) and mask.dtype == np.uint8
    assert mask[150, 150] == 1 and mask[10, 10] == 0  # inside / outside the 50..250 square
    assert 39000 < (mask == 1).sum() < 41000  # ~ 200 x 200 px


def test_excluded_patches_get_no_image_and_no_annotations(annotated_slide):  # noqa: F811
    client, slide_id, patch = annotated_slide
    client.put(f"/api/patches/{patch['id']}", json={"excluded": True})
    archive = zip_of(client.get(f"/api/slides/{slide_id}/export/coco?content=images&patches=annotated"))
    assert not [n for n in archive.namelist() if n.startswith("images/")]
    assert json.loads(archive.read("annotations/Case_1_v2_coco.json"))["annotations"] == []


def test_bad_options_are_rejected_with_a_clear_message(annotated_slide):  # noqa: F811
    client, slide_id, _ = annotated_slide
    for query in ("patches=lots", "content=video", "image_format=bmp", "masks=true"):
        res = client.get(f"/api/slides/{slide_id}/export/coco?{query}")
        assert res.status_code == 422 and res.json()["detail"], query


def test_too_many_images_is_refused_before_any_work_is_done(annotated_slide, monkeypatch):  # noqa: F811
    client, slide_id, _ = annotated_slide
    from app.core.config import Settings

    small = Settings(max_export_images=1)
    monkeypatch.setattr("app.api.export.get_settings", lambda: small)
    res = client.get(f"/api/slides/{slide_id}/export/coco?content=images&patches=all")
    assert res.status_code == 413 and "at most 1" in res.json()["detail"]


# ------------------------------------------------------ over HTTP: project


def _pid(client, slide_id):
    return client.get(f"/api/slides/{slide_id}").json()["project_id"]


def test_project_export_with_images_combines_coco_into_one_file(annotated_slide):  # noqa: F811
    client, slide_id, patch = annotated_slide
    pid = _pid(client, slide_id)
    second = upload(client, pid, [("Second.tif", SLIDE)]).json()["slides"][0]["id"]
    assert client.post(f"/api/slides/{second}/generate-patches", json={"config_version_id": patch["config_version_id"]}).status_code == 200
    p2 = client.get(f"/api/slides/{second}/patches?limit=1").json()["items"][0]
    cls = client.get(f"/api/projects/{pid}").json()["active_config"]["annotation_classes"][0]["id"]
    client.post(f"/api/patches/{p2['id']}/annotations", json={"type": "polygon", "class_id": cls, "coordinates_patch_local": [[5, 5], [60, 5], [60, 60]]})

    archive = zip_of(client.get(f"/api/projects/{pid}/export/coco?content=images"))
    annotation_files = [n for n in archive.namelist() if n.startswith("annotations/")]
    assert len(annotation_files) == 1  # one dataset file, not one per slide
    coco = json.loads(archive.read(annotation_files[0]))
    assert len(coco["images"]) == 2 and len(coco["annotations"]) == 2
    assert sorted("images/" + i["file_name"] for i in coco["images"]) == sorted(n for n in archive.namelist() if n.startswith("images/"))
    assert json.loads(archive.read("manifest.json"))["options"]["combined"] is True

    per_slide = zip_of(client.get(f"/api/projects/{pid}/export/coco?content=images&combine=false"))
    assert len([n for n in per_slide.namelist() if n.startswith("annotations/")]) == 2


def test_image_bundle_manifest_lists_slides_that_had_to_be_left_out(annotated_slide):  # noqa: F811
    client, slide_id, patch = annotated_slide
    pid = _pid(client, slide_id)
    upload(client, pid, [("Unprocessed.tif", SLIDE)])  # no patch grid yet
    manifest = json.loads(zip_of(client.get(f"/api/projects/{pid}/export/coco?content=images")).read("manifest.json"))
    assert [(k["slide"], k["reason"]) for k in manifest["skipped_slides"]] == [("Unprocessed.tif", "No patch grid generated yet")]


def test_project_export_scope_reaches_the_plain_downloads_too(annotated_slide):  # noqa: F811
    client, slide_id, patch = annotated_slide
    pid = _pid(client, slide_id)
    res = client.get(f"/api/projects/{pid}/export/patch_csv?patches=all")
    assert res.headers["content-disposition"].endswith('_patch_csv_all_all_slides.zip"')
    inner = zip_of(res)
    table = rows(inner.read("Case_1_v2_patch_csv.csv").decode())
    assert len(table) == client.get(f"/api/slides/{slide_id}/patches?limit=1").json()["total"]


# ------------------------------------------------------------- summaries


def test_summary_reports_what_an_export_would_contain(annotated_slide):  # noqa: F811
    client, slide_id, patch = annotated_slide
    total = client.get(f"/api/slides/{slide_id}/patches?limit=1").json()["total"]

    annotated = client.get(f"/api/slides/{slide_id}/export-summary").json()
    assert (annotated["patches"], annotated["annotations"], annotated["images"]) == (1, 1, 1)
    assert annotated["approx_image_bytes"] > 0 and annotated["max_images"] > 0

    everything = client.get(f"/api/slides/{slide_id}/export-summary?patches=all").json()
    assert everything["patches"] == everything["images"] == total and everything["annotations"] == 1
    assert everything["approx_image_bytes"] > annotated["approx_image_bytes"]  # the whole grid is bigger
    as_jpeg = client.get(f"/api/slides/{slide_id}/export-summary?patches=all&image_format=jpg").json()
    assert everything["approx_image_bytes"] > as_jpeg["approx_image_bytes"] * 5  # PNG, the default, is much bigger than JPEG

    empty = client.get(f"/api/slides/{slide_id}/export-summary?patches=empty").json()
    assert empty["patches"] == total - 1 and empty["annotations"] == 0

    project = client.get(f"/api/projects/{_pid(client, slide_id)}/export-summary?patches=all").json()
    assert project["slides"] == 1 and project["patches"] == total
    assert client.get(f"/api/slides/{slide_id}/export-summary?patches=bogus").status_code == 422
