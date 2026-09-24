"""Image projects: ordinary images (or pre-cut patches) annotated as they are.
Real HTTP against the real app, with small real image files."""
import csv
import io
import json
import zipfile

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import create_engine, inspect, text

from app.database.session import _add_missing_columns
from tests.test_slide_import_api import env, upload, zip_bytes  # noqa: F401  (env is a fixture)


def image_bytes(width=64, height=48, fmt="PNG", color=(200, 30, 30), **save_kwargs) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format=fmt, **save_kwargs)
    return buf.getvalue()


def noisy_png(width=40, height=30, seed=0) -> tuple[bytes, np.ndarray]:
    """Random pixels: any lossy re-encoding would change them."""
    pixels = np.random.default_rng(seed).integers(0, 256, (height, width, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(pixels).save(buf, format="PNG")
    return buf.getvalue(), pixels


SQUARE = [[10, 10], [30, 10], [30, 25], [10, 25]]


@pytest.fixture()
def imgproj(env):  # noqa: F811
    client, _wsi_pid, settings = env
    res = client.post(
        "/api/projects",
        json={
            "name": "Cell crops",
            "project_type": "image",
            "config": {"annotation_classes": [{"name": "Tumor", "color_hex": "#dc2626"}, {"name": "Normal", "color_hex": "#2563eb"}]},
        },
    )
    assert res.status_code == 201, res.text
    return client, res.json()["id"], settings


def classes_of(client, pid):
    return {c["name"]: c["id"] for c in client.get(f"/api/projects/{pid}").json()["active_config"]["annotation_classes"]}


def add_images(client, pid, files):
    res = upload(client, pid, files)
    assert res.status_code == 200, res.text
    return res.json()


def only_patch(client, slide_id):
    return client.get(f"/api/slides/{slide_id}/patches").json()["items"][0]


# ------------------------------------------------------------ project type


def test_project_type_defaults_to_wsi_and_image_projects_are_listed_as_such(env):  # noqa: F811
    client, wsi_pid, _ = env
    assert client.get(f"/api/projects/{wsi_pid}").json()["project_type"] == "wsi"
    image_pid = client.post("/api/projects", json={"name": "Imgs", "project_type": "image"}).json()["id"]
    assert client.get(f"/api/projects/{image_pid}").json()["project_type"] == "image"
    assert {p["id"]: p["project_type"] for p in client.get("/api/projects").json()} == {wsi_pid: "wsi", image_pid: "image"}
    assert client.post("/api/projects", json={"name": "Bad", "project_type": "video"}).status_code == 422


def test_migration_adds_project_type_to_a_database_created_before_it_existed(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE projects (id INTEGER PRIMARY KEY, name VARCHAR(200))"))
        conn.execute(text("INSERT INTO projects (id, name) VALUES (1, 'Legacy')"))

    _add_missing_columns(engine)
    _add_missing_columns(engine)  # idempotent: running on every startup must be harmless

    assert "project_type" in {c["name"] for c in inspect(engine).get_columns("projects")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT project_type FROM projects")).scalar() == "wsi"  # existing projects stay WSI


# ------------------------------------------------------------------ import


def test_each_image_becomes_one_slide_with_one_whole_image_patch(imgproj):
    client, pid, settings = imgproj
    body = add_images(client, pid, [("a.png", image_bytes(64, 48)), ("b.jpg", image_bytes(50, 80, "JPEG")), ("notes.txt", b"hi")])
    assert body["skipped"] == [] and body["ignored_file_count"] == 1
    a, b = body["slides"]
    assert (a["filename"], a["width_l0"], a["height_l0"], a["status"]) == ("a.png", 64, 48, "patches_generated")
    assert (b["filename"], b["width_l0"], b["height_l0"]) == ("b.jpg", 50, 80)

    patches = client.get(f"/api/slides/{a['id']}/patches").json()
    assert patches["total"] == 1
    p = patches["items"][0]
    assert (p["x"], p["y"], p["level"], p["width"], p["height"], p["width_l0"], p["height_l0"]) == (0, 0, 0, 64, 48, 64, 48)
    assert p["status"] == "unannotated"
    assert client.get(f"/api/projects/{pid}").json()["stats"]["total_patches"] == 2


def test_patch_endpoint_returns_the_original_pixels_losslessly(imgproj):
    client, pid, _ = imgproj
    data, pixels = noisy_png()
    slide = add_images(client, pid, [("noise.png", data)])["slides"][0]

    res = client.get(f"/api/slides/{slide['id']}/patch?x=0&y=0&width=40&height=30&level=0")
    assert res.status_code == 200 and res.headers["content-type"] == "image/png"
    assert np.array_equal(np.asarray(Image.open(io.BytesIO(res.content))), pixels)

    # a window partly outside the image is padded, never an error (the annotator can drag past the edge)
    edge = client.get(f"/api/slides/{slide['id']}/patch?x=30&y=20&width=20&height=20&level=0")
    assert edge.status_code == 200
    window = np.asarray(Image.open(io.BytesIO(edge.content)))
    assert np.array_equal(window[:10, :10], pixels[20:, 30:]) and not window[10:, :].any()


def test_thumbnail_and_viewer_tiles_work_for_an_image(imgproj):
    client, pid, _ = imgproj
    slide_id = add_images(client, pid, [("view.png", image_bytes(300, 200))])["slides"][0]["id"]
    assert client.get(f"/api/slides/{slide_id}/thumbnail?max_size=64").status_code == 200
    dzi = client.get(f"/api/slides/{slide_id}/dzi.dzi")
    assert dzi.status_code == 200 and 'Width="300" Height="200"' in dzi.text
    assert client.get(f"/api/slides/{slide_id}/dzi_files/9/0_0.jpeg").status_code == 200  # top level = native size


def test_zip_and_folder_keep_relative_names_and_strip_a_shared_wrapper_folder(imgproj):
    client, pid, _ = imgproj
    archive = zip_bytes(
        {
            "dataset/tumor/001.png": image_bytes(),
            "dataset/normal/001.png": image_bytes(color=(10, 200, 10)),
            "dataset/readme.md": b"x",
            "__MACOSX/dataset/._001.png": b"junk",
        }
    )
    body = add_images(client, pid, [("data.zip", archive)])
    assert sorted(s["filename"] for s in body["slides"]) == ["normal/001.png", "tumor/001.png"]  # same base name, told apart
    assert body["ignored_file_count"] == 1

    folder = upload(client, pid, [("x.png", image_bytes()), ("y.png", image_bytes())], manifest=["MyImages/x.png", "MyImages/sub/y.png"]).json()
    assert sorted(s["filename"] for s in folder["slides"]) == ["sub/y.png", "x.png"]


def test_unusable_images_are_reported_and_nothing_is_left_behind(imgproj):
    client, pid, settings = imgproj
    settings.max_image_pixels = 1000
    truncated = image_bytes(30, 30)[:40]
    body = add_images(client, pid, [("good.png", image_bytes(20, 20)), ("corrupt.png", truncated), ("big.png", image_bytes(100, 100)), ("fake.jpg", b"not an image")])
    assert [s["filename"] for s in body["slides"]] == ["good.png"]
    reasons = {s["name"]: s["reason"] for s in body["skipped"]}
    assert set(reasons) == {"corrupt.png", "big.png", "fake.jpg"}
    assert "could not be read as an image" in reasons["corrupt.png"] and "belong in a WSI project" in reasons["big.png"]
    stored = [p for p in (settings.wsi_storage_dir / str(pid)).rglob("*") if p.is_file()]
    assert len(stored) == 1 and stored[0].name == "good.png"
    assert not any((settings.wsi_storage_dir / "_staging").iterdir())


def test_exif_rotation_is_applied_so_size_and_pixels_match_what_the_annotator_sees(imgproj):
    client, pid, _ = imgproj
    img = Image.new("RGB", (60, 40), (0, 0, 255))
    img.paste((255, 0, 0), (0, 0, 10, 10))  # red corner at top-left of the *stored* pixels
    exif = Image.Exif()
    exif[0x0112] = 6  # "rotate 90 degrees clockwise to display"
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=100, exif=exif)

    slide = add_images(client, pid, [("phone.jpg", buf.getvalue())])["slides"][0]
    assert (slide["width_l0"], slide["height_l0"]) == (40, 60)  # upright size, not the stored 60x40
    served = Image.open(io.BytesIO(client.get(f"/api/slides/{slide['id']}/patch?x=0&y=0&width=40&height=60&level=0").content))
    assert served.size == (40, 60)
    assert served.getpixel((35, 5))[0] > 200  # after a clockwise turn the red corner is at the top-right


def test_transparent_and_16bit_images_become_sensible_rgb(imgproj):
    client, pid, _ = imgproj
    rgba = Image.new("RGBA", (20, 20), (255, 0, 0, 0))  # fully transparent
    buf = io.BytesIO()
    rgba.save(buf, format="PNG")
    gray16 = Image.fromarray((np.tile(np.linspace(0, 65535, 20), (20, 1))).astype(np.uint16))
    buf16 = io.BytesIO()
    gray16.save(buf16, format="PNG")

    a, b = add_images(client, pid, [("alpha.png", buf.getvalue()), ("deep.png", buf16.getvalue())])["slides"]
    alpha = Image.open(io.BytesIO(client.get(f"/api/slides/{a['id']}/patch?x=0&y=0&width=20&height=20&level=0").content))
    assert alpha.mode == "RGB" and alpha.getpixel((5, 5)) == (255, 255, 255)  # transparency shows as white, not black/red
    deep = np.asarray(Image.open(io.BytesIO(client.get(f"/api/slides/{b['id']}/patch?x=0&y=0&width=20&height=20&level=0").content)))
    assert deep[0, 0, 0] == 0 and deep[0, -1, 0] >= 250  # full range preserved, not clipped to white


def test_path_import_of_a_watch_folder_copies_and_never_moves_the_originals(imgproj):
    client, pid, settings = imgproj
    folder = settings.wsi_watch_dir / "crops"
    folder.mkdir()
    (folder / "one.png").write_bytes(image_bytes())
    (folder / "two.jpg").write_bytes(image_bytes(fmt="JPEG"))
    res = client.post(f"/api/projects/{pid}/slides/import-path", json={"path": str(folder)})
    assert res.status_code == 200 and sorted(s["filename"] for s in res.json()["slides"]) == ["one.png", "two.jpg"]
    assert (folder / "one.png").exists() and (folder / "two.jpg").exists()

    single = client.post(f"/api/projects/{pid}/slides/import-path", json={"path": str(folder / "one.png")})
    assert [s["filename"] for s in single.json()["slides"]] == ["one.png"]
    assert client.post(f"/api/projects/{pid}/slides/import-path", json={"path": str(folder / "x.svs")}).status_code == 404


# ------------------------------------------------------------- annotating


def test_annotation_coordinates_are_image_pixels_and_export_uses_them(imgproj):
    client, pid, _ = imgproj
    slide_id = add_images(client, pid, [("cell.png", image_bytes(64, 48))])["slides"][0]["id"]
    patch = only_patch(client, slide_id)
    tumor = classes_of(client, pid)["Tumor"]

    res = client.post(f"/api/patches/{patch['id']}/annotations", json={"type": "polygon", "class_id": tumor, "coordinates_patch_local": SQUARE})
    assert res.status_code == 201
    ann = res.json()
    assert ann["coordinates_level0"] == [[10, 10], [30, 10], [30, 25], [10, 25]]  # origin (0,0), downsample 1: local == global

    geo = client.get(f"/api/slides/{slide_id}/export/geojson").json()["features"][0]
    assert geo["geometry"]["coordinates"][0][:4] == [[10, 10], [30, 10], [30, 25], [10, 25]]

    coco = client.get(f"/api/slides/{slide_id}/export/coco").json()
    assert [i["file_name"] for i in coco["images"]] == ["cell.png"]  # the dataset's own file name
    assert (coco["images"][0]["width"], coco["images"][0]["height"]) == (64, 48)
    assert coco["annotations"][0]["segmentation"] == [[10, 10, 30, 10, 30, 25, 10, 25]] and coco["annotations"][0]["area"] == 300.0


def test_classes_can_still_be_edited_in_place(imgproj):
    client, pid, _ = imgproj
    add_images(client, pid, [("a.png", image_bytes())])
    config_id = client.get(f"/api/projects/{pid}").json()["active_config_version_id"]
    res = client.put(
        f"/api/configs/{config_id}",
        json={"annotation_classes": [{"name": "Tumor", "color_hex": "#dc2626", "id": classes_of(client, pid)["Tumor"]}, {"name": "Stroma", "color_hex": "#16a34a"}]},
    )
    assert res.status_code == 200, res.text


# ----------------------------------------------------------------- guards


def test_tiling_is_refused_for_image_projects(imgproj):
    client, pid, _ = imgproj
    slide_id = add_images(client, pid, [("a.png", image_bytes())])["slides"][0]["id"]
    config_id = client.get(f"/api/projects/{pid}").json()["active_config_version_id"]
    patches_before = client.get(f"/api/slides/{slide_id}/patches").json()["total"]

    assert client.post(f"/api/slides/{slide_id}/detect-tissue", json={}).status_code == 409
    assert client.post(f"/api/slides/{slide_id}/generate-patches", json={"config_version_id": config_id}).status_code == 409
    assert client.get(f"/api/slides/{slide_id}/patches").json()["total"] == patches_before  # the whole-image patch survived


def test_wsi_project_behaviour_is_unchanged(env):  # noqa: F811
    client, pid, _ = env
    assert client.get(f"/api/projects/{pid}/images").status_code == 409


# ------------------------------------------------------- navigation list


def test_image_list_reports_state_in_import_order_and_filters_by_status(imgproj):
    client, pid, _ = imgproj
    slides = add_images(client, pid, [(f"{n}.png", image_bytes()) for n in ("a", "b", "c")])["slides"]
    tumor = classes_of(client, pid)["Tumor"]
    patch_b = only_patch(client, slides[1]["id"])
    client.post(f"/api/patches/{patch_b['id']}/annotations", json={"type": "polygon", "class_id": tumor, "coordinates_patch_local": SQUARE})
    client.put(f"/api/patches/{only_patch(client, slides[2]['id'])['id']}", json={"flagged": True})

    body = client.get(f"/api/projects/{pid}/images").json()
    assert body["total"] == 3 and [i["filename"] for i in body["items"]] == ["a.png", "b.png", "c.png"]
    by_name = {i["filename"]: i for i in body["items"]}
    assert by_name["b.png"]["annotation_count"] == 1 and by_name["b.png"]["status"] == "annotated"
    assert by_name["a.png"]["annotation_count"] == 0 and by_name["a.png"]["status"] == "unannotated"
    assert by_name["c.png"]["flagged"] is True and by_name["a.png"]["slide_id"] == slides[0]["id"]

    todo = client.get(f"/api/projects/{pid}/images?status=unannotated").json()
    assert [i["filename"] for i in todo["items"]] == ["a.png", "c.png"] and todo["total"] == 2
    assert [i["filename"] for i in client.get(f"/api/projects/{pid}/images?limit=1&offset=1").json()["items"]] == ["b.png"]


# ----------------------------------------------------------- bulk export


def _annotated_dataset(client, pid):
    """a: annotated, b: reviewed with no objects (a confirmed negative), c: untouched."""
    slides = add_images(client, pid, [(f"{n}.png", image_bytes(64, 48)) for n in ("a", "b", "c")])["slides"]
    tumor = classes_of(client, pid)["Tumor"]
    client.post(f"/api/patches/{only_patch(client, slides[0]['id'])['id']}/annotations", json={"type": "polygon", "class_id": tumor, "coordinates_patch_local": SQUARE})
    client.put(f"/api/patches/{only_patch(client, slides[1]['id'])['id']}", json={"status": "reviewed"})
    return slides


def test_image_project_coco_is_one_merged_dataset_file(imgproj):
    client, pid, _ = imgproj
    slides = _annotated_dataset(client, pid)
    res = client.get(f"/api/projects/{pid}/export/coco")
    assert res.status_code == 200 and res.headers["content-type"] == "application/json"
    assert res.headers["content-disposition"].endswith('_coco.json"')  # a file, not a zip
    coco = json.loads(res.text)
    assert [i["file_name"] for i in coco["images"]] == ["a.png"]  # by default only images that have annotations
    assert len(coco["annotations"]) == 1 and coco["annotations"][0]["image_id"] == coco["images"][0]["id"]
    assert [c["name"] for c in coco["categories"]] == ["Tumor", "Normal"]
    assert coco["info"]["description"].startswith("1 annotated images from 3")

    # ...and the reviewed negative (or every image) comes along when asked for
    reviewed = json.loads(client.get(f"/api/projects/{pid}/export/coco?patches=reviewed").text)
    assert [i["file_name"] for i in reviewed["images"]] == ["b.png"] and reviewed["annotations"] == []
    everything = json.loads(client.get(f"/api/projects/{pid}/export/coco?patches=all").text)
    assert [i["file_name"] for i in everything["images"]] == ["a.png", "b.png", "c.png"]
    assert slides  # (kept for readability of the scenario above)


def test_image_project_csvs_merge_into_one_table_with_a_single_header(imgproj):
    client, pid, _ = imgproj
    _annotated_dataset(client, pid)
    patches = client.get(f"/api/projects/{pid}/export/patch_csv?patches=all")
    assert patches.headers["content-disposition"].endswith('_patch_csv_all.csv"')
    rows = list(csv.DictReader(io.StringIO(patches.text)))
    assert [r["slide"] for r in rows] == ["a.png", "b.png", "c.png"] and patches.text.count("level0_x") == 1
    assert [r["dominant_class"] for r in rows] == ["Tumor", "", ""]

    stats = list(csv.DictReader(io.StringIO(client.get(f"/api/projects/{pid}/export/stats_csv").text)))
    assert {r["slide"] for r in stats} == {"a.png", "b.png", "c.png"} and len(stats) == 6  # 2 classes x 3 images


def test_image_project_geojson_stays_one_file_per_image_in_a_zip(imgproj):
    client, pid, _ = imgproj
    _annotated_dataset(client, pid)
    res = client.get(f"/api/projects/{pid}/export/geojson")
    assert res.headers["content-type"] == "application/zip"
    assert sorted(zipfile.ZipFile(io.BytesIO(res.content)).namelist()) == sorted(["a_geojson.geojson", "b_geojson.geojson", "c_geojson.geojson", "manifest.json"])


# -------------------------------------------------------------- deletion


def test_deleting_an_image_project_removes_its_files_and_rows(imgproj):
    client, pid, settings = imgproj
    slides = _annotated_dataset(client, pid)
    assert any((settings.wsi_storage_dir / str(pid)).rglob("*.png"))
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert not (settings.wsi_storage_dir / str(pid)).exists()
    assert client.get(f"/api/slides/{slides[0]['id']}").status_code == 404
