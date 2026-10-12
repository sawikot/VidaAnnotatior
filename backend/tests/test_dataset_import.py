"""Importing a dataset: images and their annotations together, in the layouts and formats labelling
tools write. Real HTTP against the real app, with small real files."""
import io
import json

import numpy as np
import pytest
from PIL import Image

from tests.test_image_projects import image_bytes
from tests.test_slide_import_api import env, zip_bytes  # noqa: F401  (env is a fixture)

W, H = 200, 100  # every test image


@pytest.fixture()
def proj(env, monkeypatch):  # noqa: F811
    client, _wsi_pid, settings = env
    monkeypatch.setattr("app.api.dataset_import.get_settings", lambda: settings)
    res = client.post("/api/projects", json={"name": "Dataset", "project_type": "image", "config": {"annotation_classes": [{"name": "Tumor", "color_hex": "#dc2626"}]}})
    assert res.status_code == 201, res.text
    return client, res.json()["id"], settings


def img(color=(200, 30, 30)) -> bytes:
    return image_bytes(W, H, color=color)


def analysed(client, pid, files: dict[str, bytes | str], mask_folders: list[str] | None = None):
    """Start an import, send ``files`` ({path: content}) and return (import id, preview)."""
    import_id = client.post(f"/api/projects/{pid}/imports").json()["id"]
    parts = [("files", (path.rsplit("/", 1)[-1], data.encode() if isinstance(data, str) else data, "application/octet-stream")) for path, data in files.items()]
    res = client.post(f"/api/projects/{pid}/imports/{import_id}/files", files=parts, data={"manifest": json.dumps(list(files))})
    assert res.status_code == 200, res.text
    res = client.post(f"/api/projects/{pid}/imports/{import_id}/analyse", json={"mask_folders": mask_folders} if mask_folders is not None else None)
    assert res.status_code == 200, res.text
    return import_id, res.json()


def commit(client, pid, import_id, label_map=None, **more):
    res = client.post(f"/api/projects/{pid}/imports/{import_id}/commit", json={"label_map": label_map or {}, **more})
    assert res.status_code == 200, res.text
    return res.json()


def shapes(client, pid) -> dict[str, list]:
    """{image name: [(type, class name, bounding box rounded), ...]} of everything in the project."""
    classes = {c["id"]: c["name"] for c in client.get(f"/api/projects/{pid}").json()["active_config"]["annotation_classes"]}
    out = {}
    for slide in client.get(f"/api/projects/{pid}/slides").json():
        found = []
        for a in client.get(f"/api/slides/{slide['id']}/annotations").json():
            xs, ys = [p[0] for p in a["coordinates_level0"]], [p[1] for p in a["coordinates_level0"]]
            found.append((a["type"], classes.get(a["class_id"]), [round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))]))
        out[slide["filename"]] = sorted(found, key=str)
    return out


def label_names(preview) -> dict[str, tuple[int, int]]:
    return {item["label"]: (item["shapes"], item["images"]) for item in preview["labels"]}


# ------------------------------------------------------------------ YOLO, and the matching step


def test_yolo_images_and_labels_in_their_usual_folders(proj):
    client, pid, settings = proj
    import_id, preview = analysed(client, pid, {
        "data/images/train/a.png": img(), "data/images/val/a.png": img(), "data/images/train/b.png": img(),
        "data/labels/train/a.txt": "0 0.5 0.5 0.2 0.4\n1 0.1 0.1 0.9 0.1 0.9 0.9 0.1 0.9\n",
        "data/labels/val/a.txt": "1 0.25 0.5 0.1 0.2\n",
        "data/labels/train/b.txt": "",  # an image with nothing in it
        "data/labels/train/zzz.txt": "0 0.5 0.5 0.1 0.1\n",  # no such image
        "data/classes.txt": "Tumor\nStroma\n",
        "data/README.md": "hello",
    })
    assert (preview["new_images"], preview["shapes"], preview["images_with_annotations"], preview["new_images_without_annotations"]) == (3, 3, 2, 1)
    assert preview["annotation_files"] == [{"format": "yolo", "name": "YOLO .txt", "count": 4}]
    assert label_names(preview) == {"Stroma": (2, 0), "Tumor": (1, 0)}
    tumor = preview["classes"][0]["id"]
    assert {item["label"]: item["class_id"] for item in preview["labels"]} == {"Stroma": None, "Tumor": tumor}
    assert preview["unmatched"] == [{"file": "data/labels/train/zzz.txt", "reason": "no image named zzz"}]
    assert preview["files_not_understood"] == 1 and preview["class_list"] == "data/classes.txt"
    assert client.get(f"/api/projects/{pid}/slides").json() == []  # nothing is imported before it is confirmed

    result = commit(client, pid, import_id, {"Tumor": tumor, "Stroma": "new"})
    assert (result["images_imported"], result["annotations_imported"], result["classes_created"]) == (3, 3, ["Stroma"])
    assert shapes(client, pid) == {
        "train/a.png": [("polygon", "Stroma", [20, 10, 180, 90]), ("rectangle", "Tumor", [80, 30, 120, 70])],
        "train/b.png": [],
        "val/a.png": [("rectangle", "Stroma", [40, 40, 60, 60])],
    }
    assert not (settings.wsi_storage_dir / "_staging" / f"import-{import_id}").exists()
    assert client.post(f"/api/projects/{pid}/imports/{import_id}/analyse").status_code == 404


def test_labels_can_be_given_another_class_left_out_or_imported_without_one(proj):
    client, pid, _ = proj
    files = {"a.png": img(), "a.txt": "0 0.5 0.5 0.2 0.2\n1 0.2 0.2 0.1 0.1\n2 0.8 0.8 0.1 0.1\n", "classes.txt": "cancer\njunk\nthing\n"}
    import_id, preview = analysed(client, pid, files)
    tumor = preview["classes"][0]["id"]
    result = commit(client, pid, import_id, {"cancer": tumor, "junk": "skip", "thing": "unlabeled"})
    assert result["annotations_imported"] == 2 and result["left_out"] == {"their label was left out": 1}
    assert shapes(client, pid)["a.png"] == [("rectangle", "Tumor", [80, 40, 120, 60]), ("rectangle", None, [150, 75, 170, 85])]


def test_annotations_alone_go_to_the_images_the_project_has_and_not_twice(proj):
    client, pid, _ = proj
    import_id, _ = analysed(client, pid, {"a.png": img(), "b.png": img()})
    assert commit(client, pid, import_id)["images_imported"] == 2

    voc = "<annotation><filename>a.png</filename><object><name>Tumor</name><bndbox><xmin>10</xmin><ymin>20</ymin><xmax>60</xmax><ymax>70</ymax></bndbox></object></annotation>"
    import_id, preview = analysed(client, pid, {"anns/a.xml": voc, "a.png": img()})  # the image again, too
    assert (preview["new_images"], preview["already_in_project"], preview["existing_images_annotated"]) == (0, 1, 1)
    result = commit(client, pid, import_id)
    assert (result["images_imported"], result["annotations_imported"]) == (0, 1)
    assert shapes(client, pid) == {"a.png": [("rectangle", "Tumor", [10, 20, 60, 70])], "b.png": []}

    import_id, _ = analysed(client, pid, {"anns/a.xml": voc})
    assert commit(client, pid, import_id) == {
        "images_imported": 0, "images_failed": [], "annotations_imported": 0, "image_labels_set": 0, "classes_created": [], "left_out": {"already there": 1},
    }


def test_a_zip_holds_a_dataset_as_well_as_a_folder_does(proj):
    client, pid, _ = proj
    archive = zip_bytes({"set/images/a.png": img(), "set/labels/a.txt": b"0 0.5 0.5 0.5 0.5\n", "set/classes.txt": b"Tumor\n"})
    import_id, preview = analysed(client, pid, {"set.zip": archive})
    assert (preview["new_images"], preview["shapes"], preview["unmatched_count"]) == (1, 1, 0)
    commit(client, pid, import_id)
    assert list(shapes(client, pid).values()) == [[("rectangle", "Tumor", [50, 25, 150, 75])]]


# ------------------------------------------------------------------ the other formats


def test_labelme_cvat_label_studio_and_coco(proj):
    client, pid, _ = proj
    labelme = {"imagePath": "..\\imgs\\a.png", "imageHeight": H, "imageWidth": W, "shapes": [
        {"label": "Tumor", "shape_type": "rectangle", "points": [[60, 70], [10, 20]]},
        {"label": "Tumor", "shape_type": "polygon", "points": [[100, 10], [150, 10], [150, 60], [120, 80]]},
        {"label": "Tumor", "shape_type": "point", "points": [[5, 5]]},
    ]}
    cvat = (
        '<annotations><image id="0" name="b.png" width="200" height="100">'
        '<box label="Tumor" xtl="10" ytl="20" xbr="60" ybr="70"/><polygon label="Tumor" points="100,10;150,10;150,60;120,80"/>'
        '<points label="Tumor" points="5,5;9,9"/><ellipse label="Tumor" cx="50" cy="50" rx="10" ry="10"/></image></annotations>'
    )
    studio = [{"data": {"image": "/data/upload/1/9f8e7d6c-c.png"}, "annotations": [{"result": [
        {"type": "rectanglelabels", "original_width": W, "original_height": H, "value": {"x": 5, "y": 20, "width": 25, "height": 50, "rectanglelabels": ["Tumor"]}},
        {"type": "polygonlabels", "original_width": W, "original_height": H, "value": {"points": [[50, 10], [75, 10], [75, 60], [60, 80]], "polygonlabels": ["Tumor"]}},
    ]}]}]
    coco = {"images": [{"id": 7, "file_name": "tile_x512_y512.png", "width": W, "height": H}], "categories": [{"id": 3, "name": "Tumor"}], "annotations": [
        {"id": 1, "image_id": 7, "category_id": 3, "bbox": [10, 20, 50, 50]},
        {"id": 2, "image_id": 7, "category_id": 3, "segmentation": [[100, 10, 150, 10, 150, 60, 120, 80]]},
    ]}
    import_id, preview = analysed(client, pid, {
        "imgs/a.png": img(), "imgs/b.png": img(), "imgs/c.png": img(), "imgs/tile_x512_y512.png": img(),
        "ann/a.json": json.dumps(labelme), "ann/cvat.xml": cvat, "ann/studio.json": json.dumps(studio), "ann/coco.json": json.dumps(coco),
    })
    assert sorted(f["format"] for f in preview["annotation_files"]) == ["coco", "cvat", "label_studio", "labelme"]
    assert preview["unmatched"] == [] and preview["unreadable"] == {}
    commit(client, pid, import_id)
    box, outline = ("rectangle", "Tumor", [10, 20, 60, 70]), ("polygon", "Tumor", [100, 10, 150, 80])
    assert shapes(client, pid) == {
        "a.png": sorted([box, outline, ("point", "Tumor", [5, 5, 5, 5])], key=str),
        "b.png": sorted([box, outline, ("point", "Tumor", [5, 5, 5, 5]), ("point", "Tumor", [9, 9, 9, 9]), ("circle", "Tumor", [50, 50, 60, 50])], key=str),
        "c.png": sorted([box, outline], key=str),
        "tile_x512_y512.png": sorted([box, outline], key=str),  # in the image's own pixels, whatever its name looks like
    }


def test_a_table_with_a_file_column_gives_boxes_per_image(proj):
    client, pid, _ = proj
    table = "filename,xmin,ymin,xmax,ymax,class\na.png,10,20,60,70,Tumor\nb.png,1,2,30,40,Tumor\na.png,100,10,150,60,Tumor\n"
    import_id, preview = analysed(client, pid, {"a.png": img(), "b.png": img(), "boxes.csv": table})
    assert (preview["shapes"], preview["images_with_annotations"]) == (3, 2)
    commit(client, pid, import_id)
    assert shapes(client, pid) == {
        "a.png": [("rectangle", "Tumor", [10, 20, 60, 70]), ("rectangle", "Tumor", [100, 10, 150, 60])],
        "b.png": [("rectangle", "Tumor", [1, 2, 30, 40])],
    }


def test_mask_images_become_outlines_of_their_classes(proj):
    client, pid, _ = proj
    mask = np.zeros((H, W), dtype=np.uint8)
    mask[20:60, 10:50] = 1
    mask[10:90, 120:180] = 2
    mask[40:60, 140:160] = 0  # a hole
    buf = io.BytesIO()
    Image.fromarray(mask).save(buf, format="PNG")
    import_id, preview = analysed(client, pid, {"images/a.png": img(), "masks/a.png": buf.getvalue(), "classes.txt": "background\nTumor\nStroma\n"})
    assert preview["new_images"] == 1  # the mask is not an image of the dataset
    assert preview["annotation_files"] == [{"format": "mask", "name": "Mask images", "count": 1}]
    # The area with a hole comes as pieces without one (an outline here is a single ring).
    assert label_names(preview) == {"Stroma": (2, 0), "Tumor": (1, 0)}
    commit(client, pid, import_id, {"Tumor": preview["classes"][0]["id"], "Stroma": "new"})

    # Drawn back, the outlines are the mask, pixel for pixel -- the hole included.
    from PIL import ImageDraw

    classes = {c["id"]: c["name"] for c in client.get(f"/api/projects/{pid}").json()["active_config"]["annotation_classes"]}
    slide = client.get(f"/api/projects/{pid}/slides").json()[0]
    assert slide["filename"] == "images/a.png"
    drawn = Image.new("L", (W * 4, H * 4), 0)
    for a in client.get(f"/api/slides/{slide['id']}/annotations").json():
        value = {"Tumor": 1, "Stroma": 2}[classes[a["class_id"]]]
        ImageDraw.Draw(drawn).polygon([(x * 4, y * 4) for x, y in a["coordinates_level0"]], fill=value)
    centres = np.array(drawn)[2::4, 2::4]  # the middle of every pixel of the mask
    assert np.array_equal(centres, mask)


# ------------------------------------------------------------------ labels for whole images


def test_folders_of_images_are_classes_when_nothing_else_says_what_they_are(proj):
    client, pid, _ = proj
    files = {"set/train/Tumor/a.png": img(), "set/train/Normal/b.png": img(), "set/val/Tumor/c.png": img()}
    import_id, preview = analysed(client, pid, files)
    assert preview["folder_labels"] is True and label_names(preview) == {"Tumor": (0, 2), "Normal": (0, 1)}
    result = commit(client, pid, import_id, {"Normal": "new"})
    assert (result["images_imported"], result["image_labels_set"], result["classes_created"]) == (3, 3, ["Normal"])
    labels = {}
    for slide in client.get(f"/api/projects/{pid}/slides").json():
        labels[slide["filename"]] = client.get(f"/api/slides/{slide['id']}/patches").json()["items"][0]["patch_label"]
    assert labels == {"train/Tumor/a.png": "Tumor", "train/Normal/b.png": "Normal", "val/Tumor/c.png": "Tumor"}

    # Asked not to: the images come in without labels.
    client.post("/api/projects", json={"name": "x"})
    import_id, _ = analysed(client, pid, {"more/Tumor/d.png": img(), "more/Normal/e.png": img()})
    assert commit(client, pid, import_id, folder_labels=False)["image_labels_set"] == 0


def test_a_table_of_file_and_label_labels_the_images(proj):
    client, pid, _ = proj
    import_id, preview = analysed(client, pid, {"a.png": img(), "b.png": img(), "labels.csv": "image,diagnosis\na.png,Tumor\nb.png,Benign\n"})
    assert preview["folder_labels"] is False and label_names(preview) == {"Benign": (0, 1), "Tumor": (0, 1)}
    result = commit(client, pid, import_id, {"Benign": "skip"})
    assert result["image_labels_set"] == 1 and result["left_out"] == {"their label was left out": 1}


# ------------------------------------------------------------------ slides, and housekeeping


def test_a_slide_project_takes_a_file_per_slide_in_the_formats_it_already_reads(env, monkeypatch):  # noqa: F811
    from tests.test_slide_import_api import SLIDE

    client, pid, settings = env
    monkeypatch.setattr("app.api.dataset_import.get_settings", lambda: settings)
    geojson = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"classification": {"name": "Tumor"}}, "geometry": {"type": "Polygon", "coordinates": [[[100, 100], [400, 100], [400, 300], [100, 100]]]}},
    ]}
    import_id, preview = analysed(client, pid, {"case1.tif": SLIDE, "qupath/case1.geojson": json.dumps(geojson), "qupath/other.geojson": json.dumps(geojson)})
    assert (preview["new_images"], preview["shapes"], preview["image_project"]) == (1, 1, False)
    assert preview["unmatched"] == [{"file": "qupath/other.geojson", "reason": "no slide named other"}]
    result = commit(client, pid, import_id, {"Tumor": "new"})
    assert (result["images_imported"], result["annotations_imported"]) == (1, 1)
    slide = client.get(f"/api/projects/{pid}/slides").json()[0]
    assert [a["type"] for a in client.get(f"/api/slides/{slide['id']}/annotations").json()] == ["polygon"]


def test_an_import_can_be_dropped_and_belongs_to_its_project(proj):
    client, pid, settings = proj
    import_id, _ = analysed(client, pid, {"a.png": img()})
    other = client.post("/api/projects", json={"name": "Other", "project_type": "image"}).json()["id"]
    assert client.post(f"/api/projects/{other}/imports/{import_id}/analyse").status_code == 404
    assert client.post(f"/api/projects/{pid}/imports/../analyse").status_code in (404, 405)
    assert client.delete(f"/api/projects/{pid}/imports/{import_id}").status_code == 204
    assert not (settings.wsi_storage_dir / "_staging" / f"import-{import_id}").exists()
    assert client.post(f"/api/projects/{pid}/imports/{import_id}/commit", json={}).status_code == 404
    assert client.get(f"/api/projects/{pid}/slides").json() == []

# ------------------------------------------------------------------ masks as large as their slides


def slide_mask_tiff(regions: dict[int, tuple[int, int, int, int]], hole: tuple[int, int, int, int] | None = None) -> tuple[bytes, np.ndarray]:
    """A class mask stored like a slide: a tiled, pyramidal TIFF with the class number in the red
    channel (as the PANDA dataset's masks are). ``regions``: class -> (x0, y0, x1, y1)."""
    import tifffile

    from tests.test_slide_import_api import HEIGHT, WIDTH

    labels = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    for value, (x0, y0, x1, y1) in regions.items():
        labels[y0:y1, x0:x1] = value
    if hole:
        labels[hole[1]:hole[3], hole[0]:hole[2]] = 0
    level = np.stack([labels, np.zeros_like(labels), np.zeros_like(labels)], axis=-1)
    buf = io.BytesIO()
    with tifffile.TiffWriter(buf) as tw:
        for i in range(3):
            tw.write(level, tile=(256, 256), photometric="rgb", compression="deflate", subfiletype=0 if i == 0 else 1)
            level = level[::2, ::2]
    return buf.getvalue(), labels


def test_a_mask_stored_like_a_slide_annotates_the_slide_of_its_name(env, monkeypatch):  # noqa: F811
    from PIL import ImageDraw

    from tests.test_slide_import_api import HEIGHT, SLIDE, WIDTH

    client, pid, settings = env
    monkeypatch.setattr("app.api.dataset_import.get_settings", lambda: settings)
    mask, labels = slide_mask_tiff({1: (64, 64, 704, 832), 3: (896, 128, 1408, 640)}, hole=(256, 256, 512, 512))

    # Slides and masks together, told apart by what is in them (they have the same names).
    import_id, preview = analysed(client, pid, {"image/case1.tif": SLIDE, "ano/case1.tif": mask, "ano/lonely.tif": mask})
    assert (preview["new_images"], preview["image_project"]) == (1, False)
    assert preview["annotation_files"] == [{"format": "mask", "name": "Mask images", "count": 2}]
    assert label_names(preview) == {"class 1": (2, 0), "class 3": (1, 0)}  # the area with a hole: two pieces
    assert preview["unmatched"] == [{"file": "ano/lonely.tif", "reason": "a mask, but no slide named lonely is in the upload or the project"}]
    result = commit(client, pid, import_id, {"class 1": "new", "class 3": "new"}, new_names={"class 1": "Stroma", "class 3": "Gleason 3"})
    assert (result["images_imported"], result["annotations_imported"], result["classes_created"]) == (1, 3, ["Stroma", "Gleason 3"])

    slides = client.get(f"/api/projects/{pid}/slides").json()
    assert [s["filename"] for s in slides] == ["case1.tif"]  # neither mask became a slide
    classes = {c["id"]: c["name"] for c in client.get(f"/api/projects/{pid}").json()["active_config"]["annotation_classes"]}
    drawn = Image.new("L", (WIDTH, HEIGHT), 0)
    for a in client.get(f"/api/slides/{slides[0]['id']}/annotations").json():
        ImageDraw.Draw(drawn).polygon([(x, y) for x, y in a["coordinates_level0"]], fill={"Stroma": 1, "Gleason 3": 3}[classes[a["class_id"]]])
    assert (np.array(drawn) != labels).mean() < 0.002  # the mask again, to within its outlines' edges

    # Masks alone, for slides added earlier -- and not twice.
    other, _ = slide_mask_tiff({1: (0, 0, 512, 512)})
    import_id, preview = analysed(client, pid, {"masks/case1_mask.tif": mask})
    assert (preview["new_images"], preview["existing_images_annotated"], preview["shapes"]) == (0, 1, 3)
    assert commit(client, pid, import_id, {"class 1": "new", "class 3": "new"}, new_names={"class 1": "Stroma", "class 3": "Gleason 3"})["left_out"] == {"already there": 3}
    import_id, preview = analysed(client, pid, {"masks/case1_mask.tif": other})
    assert commit(client, pid, import_id, {"class 1": "new"}, new_names={"class 1": "Stroma"})["annotations_imported"] == 1


# ------------------------------------------------------------------ a folder per case; masks by say-so


def png_mask(regions: dict[int, tuple[int, int, int, int]]) -> bytes:
    labels = np.zeros((H, W), dtype=np.uint8)
    for value, (x0, y0, x1, y1) in regions.items():
        labels[y0:y1, x0:x1] = value
    buf = io.BytesIO()
    Image.fromarray(labels).save(buf, format="PNG")
    return buf.getvalue()


def photo(seed: int) -> bytes:
    """A picture with thousands of colours, as a scan has (a flat one would pass for a mask)."""
    buf = io.BytesIO()
    Image.fromarray(np.random.default_rng(seed).integers(0, 256, (H, W, 3), dtype=np.uint8)).save(buf, format="PNG")
    return buf.getvalue()


def test_files_alone_together_in_a_folder_belong_together(proj):
    client, pid, _ = proj
    labelme = {"imagePath": "scan_0042.png", "shapes": [{"label": "Tumor", "shape_type": "rectangle", "points": [[10, 20], [60, 70]]}]}
    import_id, preview = analysed(client, pid, {
        "set/case1/image.png": photo(1), "set/case1/mask.png": png_mask({1: (10, 20, 60, 70)}),
        "set/case2/image.png": photo(2), "set/case2/shapes.json": json.dumps(labelme),
        "set/case3/image.png": photo(3), "set/case3/first.txt": "0 0.5 0.5 0.2 0.2\n", "set/case3/second.txt": "0 0.5 0.5 0.4 0.4\n",
        "set/classes.txt": "Tumor\n",
    })
    assert preview["new_images"] == 3 and preview["shapes"] == 2  # the mask is no image; case 3 is not guessed at
    assert sorted(u["file"] for u in preview["unmatched"]) == ["set/case3/first.txt", "set/case3/second.txt"]
    assert any("only one in their folder" in w and "2 file(s)" in w for w in preview["warnings"])
    commit(client, pid, import_id, {"class 1": preview["classes"][0]["id"]})
    assert shapes(client, pid) == {
        "case1/image.png": [("polygon", "Tumor", [10, 20, 60, 70])],
        "case2/image.png": [("rectangle", "Tumor", [10, 20, 60, 70])],
        "case3/image.png": [],
    }


def test_a_file_named_like_no_image_is_not_given_to_an_image_that_has_its_own(proj):
    client, pid, _ = proj
    _, preview = analysed(client, pid, {"a.png": img(), "a.txt": "0 0.5 0.5 0.2 0.2\n", "zzz.txt": "0 0.5 0.5 0.4 0.4\n", "classes.txt": "Tumor\n"})
    assert preview["shapes"] == 1 and preview["unmatched"] == [{"file": "zzz.txt", "reason": "no image named zzz"}]


def test_pairs_of_picture_and_mask_in_every_folder_need_no_telling_name(proj):
    client, pid, _ = proj
    _, preview = analysed(client, pid, {
        "p1/scan.png": photo(1), "p1/truth.png": png_mask({1: (10, 20, 60, 70)}),
        "p2/scan.png": photo(2), "p2/truth.png": png_mask({2: (0, 0, 50, 50)}),
    })
    assert (preview["new_images"], preview["shapes"], preview["unmatched_count"]) == (2, 2, 0)
    # One such pair alone, with nothing calling it a mask, is left as two images.
    _, preview = analysed(client, pid, {"p1/scan.png": photo(1), "p1/truth.png": png_mask({1: (10, 20, 60, 70)})})
    assert (preview["new_images"], preview["shapes"]) == (2, 0)


def test_the_person_can_say_which_folder_holds_the_masks(proj):
    client, pid, _ = proj
    files = {"photos/a.png": photo(1), "photos/b.png": photo(2), "output2/a.png": png_mask({1: (10, 20, 60, 70)}), "output2/b.png": photo(3), "output2/c.png": png_mask({1: (0, 0, 9, 9)})}
    _, preview = analysed(client, pid, files)
    assert (preview["new_images"], preview["shapes"]) == (5, 0)  # nothing gives the masks away
    assert preview["folders"] == [{"folder": "output2", "images": 3, "masks": False}, {"folder": "photos", "images": 2, "masks": False}]

    import_id, preview = analysed(client, pid, files, mask_folders=["output2"])
    assert (preview["new_images"], preview["shapes"]) == (2, 1)
    assert [f["masks"] for f in preview["folders"]] == [True, False]
    reasons = {u["file"]: u["reason"] for u in preview["unmatched"]}
    assert "does not look like a class mask" in reasons["output2/b.png"] and reasons["output2/c.png"] == "no image with the mask's name"
    commit(client, pid, import_id, {"class 1": preview["classes"][0]["id"]})
    assert shapes(client, pid) == {"photos/a.png": [("polygon", "Tumor", [10, 20, 60, 70])], "photos/b.png": []}


def test_slides_in_a_folder_per_case_are_named_by_their_folders(env, monkeypatch):  # noqa: F811
    from tests.test_slide_import_api import SLIDE

    client, pid, settings = env
    monkeypatch.setattr("app.api.dataset_import.get_settings", lambda: settings)
    mask, _ = slide_mask_tiff({1: (64, 64, 704, 832)})
    import_id, preview = analysed(client, pid, {"c1/slide.tif": SLIDE, "c1/truth.tif": mask, "c2/slide.tif": SLIDE})
    assert (preview["new_images"], preview["shapes"], preview["unmatched"]) == (2, 1, [])
    result = commit(client, pid, import_id, {"class 1": "new"}, new_names={"class 1": "Stroma"})
    assert (result["images_imported"], result["annotations_imported"]) == (2, 1)
    slides = {s["filename"]: s["id"] for s in client.get(f"/api/projects/{pid}/slides").json()}
    assert sorted(slides) == ["c1_slide.tif", "c2_slide.tif"]
    assert len(client.get(f"/api/slides/{slides['c1_slide.tif']}/annotations").json()) == 1
    assert client.get(f"/api/slides/{slides['c2_slide.tif']}/annotations").json() == []


# ------------------------------------------------------------------ a mask for one slide, from the workspace


def parse_one(client, slide_id, name, data, **form):
    return client.post(f"/api/slides/{slide_id}/import-annotations/parse", files={"file": (name, data, "application/octet-stream")}, data=form or None)


def test_the_per_slide_import_reads_a_mask_too(proj, monkeypatch):
    from tests.test_slide_import_api import SLIDE, upload

    client, pid, settings = proj
    import_id, _ = analysed(client, pid, {"a.png": photo(1)})
    commit(client, pid, import_id)
    image = client.get(f"/api/projects/{pid}/slides").json()[0]

    # The image's own size.
    res = parse_one(client, image["id"], "a_mask.png", png_mask({1: (10, 20, 60, 70), 2: (100, 10, 150, 60)}))
    assert res.status_code == 200, res.text
    parsed = res.json()
    assert (parsed["format"], parsed["scale"], parsed["outside_slide"]) == ("mask", 1, 0)
    assert {l["label"]: l["count"] for l in parsed["labels"]} == {"class 1": 1, "class 2": 1}
    assert parsed["bounds"] == [10, 10, 150, 70]

    # A smaller copy of the same shape: scaled up, and said so.
    small = np.zeros((H // 2, W // 2), dtype=np.uint8)
    small[10:35, 5:30] = 1
    buf = io.BytesIO()
    Image.fromarray(small).save(buf, format="PNG")
    parsed = parse_one(client, image["id"], "half.png", buf.getvalue()).json()
    assert (parsed["scale"], parsed["scale_auto"], parsed["bounds"]) == (2, True, [10, 20, 60, 70]) and "scaled up" in parsed["scale_note"]
    tumor = parsed["classes"][0]["id"]
    done = client.post(f"/api/slides/{image['id']}/import-annotations", json={"annotations": parsed["annotations"], "label_map": {"class 1": tumor}}).json()
    assert done["imported"] == 1
    assert shapes(client, pid) == {"a.png": [("polygon", "Tumor", [10, 20, 60, 70])]}

    # A label can become a new class there too, named as wished (or after the label).
    parsed = parse_one(client, image["id"], "a_mask.png", png_mask({2: (100, 10, 150, 60), 3: (160, 70, 190, 95)})).json()
    done = client.post(f"/api/slides/{image['id']}/import-annotations", json={
        "annotations": parsed["annotations"], "label_map": {"class 2": "new", "class 3": "new"}, "new_names": {"class 2": "Stroma"},
    }).json()
    assert (done["imported"], done["classes_created"]) == (2, ["Stroma", "class 3"])
    assert shapes(client, pid)["a.png"] == sorted([("polygon", "Tumor", [10, 20, 60, 70]), ("polygon", "Stroma", [100, 10, 150, 60]), ("polygon", "class 3", [160, 70, 190, 95])], key=str)
    bad = client.post(f"/api/slides/{image['id']}/import-annotations", json={"annotations": parsed["annotations"], "label_map": {"class 2": "new"}, "new_names": {"class 2": "  "}})
    assert bad.status_code == 200 and bad.json()["classes_created"] == ["class 2"]  # a blank name: the label itself

    # Not a mask, or not this image's shape: refused with the reason.
    assert "does not look like a class mask" in parse_one(client, image["id"], "photo.png", photo(2)).json()["detail"]
    odd = io.BytesIO()
    Image.fromarray(np.ones((30, 30), dtype=np.uint8)).save(odd, format="PNG")
    assert "30 x 30 px" in parse_one(client, image["id"], "odd.png", odd.getvalue()).json()["detail"]

    # A slide and a mask stored like a slide.
    wsi = client.post("/api/projects", json={"name": "Slides"}).json()["id"]
    slide = upload(client, wsi, [("case1.tif", SLIDE)]).json()["slides"][0]
    mask, _ = slide_mask_tiff({1: (64, 64, 704, 832), 3: (896, 128, 1408, 640)})
    parsed = parse_one(client, slide["id"], "case1_mask.tif", mask).json()
    assert (parsed["format"], parsed["scale"]) == ("mask", 1)
    assert {l["label"]: l["count"] for l in parsed["labels"]} == {"class 1": 1, "class 3": 1}
    assert parsed["bounds"] == [64, 64, 1408, 832]
    assert "picture of tissue" in parse_one(client, slide["id"], "again.tif", SLIDE).json()["detail"]
