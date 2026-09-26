"""Annotating on the whole slide (WSI mode): shapes stored in Level-0 pixels with no patch, and how
they reach patch views, every export and the masks."""
import io
import json
import math
from types import SimpleNamespace as NS

import numpy as np
import pytest
from PIL import Image
from shapely.geometry import Polygon

from app.services.geometry import circle_center_radius, polygon_area
from app.services.projection import level0_geometry, project_slide_annotations
from tests.test_export_formats import rows
from tests.test_slide_import_api import SLIDE, env, upload  # noqa: F401  (fixture)


def patch(id, x, y, size=100, downsample=1.0):
    px = int(size / downsample)
    return NS(id=id, x=x, y=y, width=px, height=px, width_l0=size, height_l0=size)


def shape(id, kind, coords, class_id=1):
    return NS(id=id, type=kind, coordinates_level0=coords, class_id=class_id)


# ------------------------------------------------------------------- projection


def test_a_shape_wholly_inside_a_patch_is_kept_as_drawn_in_patch_pixels():
    (piece,) = project_slide_annotations([shape(1, "rectangle", [[110, 120], [150, 120], [150, 160], [110, 160]])], [patch(7, 100, 100)])[7]
    assert piece.type == "rectangle" and not piece.clipped
    assert piece.parts == [[[10.0, 20.0], [50.0, 20.0], [50.0, 60.0], [10.0, 60.0]]]  # (level0 - origin) / 1


def test_a_shape_crossing_two_patches_is_cut_and_the_pieces_add_up_to_the_whole():
    big = shape(1, "polygon", [[60, 20], [160, 20], [160, 80], [60, 80]])  # 100 x 60
    pieces = project_slide_annotations([big], [patch(1, 0, 0), patch(2, 100, 0)])
    left, right = pieces[1][0], pieces[2][0]
    assert left.clipped and right.clipped and left.type == right.type == "polygon"
    assert polygon_area(left.parts[0]) == pytest.approx(40 * 60) and polygon_area(right.parts[0]) == pytest.approx(60 * 60)
    assert polygon_area(left.parts[0]) + polygon_area(right.parts[0]) == pytest.approx(polygon_area(big.coordinates_level0))
    assert min(x for x, _ in right.parts[0]) == 0.0  # the right piece starts at its own patch's left edge


def test_downsampled_patches_divide_the_coordinates_by_their_scale():
    (piece,) = project_slide_annotations([shape(1, "circle", [[240, 240], [280, 240]])], [patch(1, 200, 200, size=200, downsample=2.0)])[1]
    assert piece.type == "circle" and not piece.clipped
    (cx, cy), r = circle_center_radius(piece.parts[0])
    assert (cx, cy, r) == (20.0, 20.0, 20.0)  # centre (240-200)/2, radius 40/2


def test_a_circle_crossing_a_border_becomes_polygon_pieces_that_add_up():
    circle = shape(1, "circle", [[100, 50], [130, 50]])  # r = 30, centred on the border between the patches
    pieces = project_slide_annotations([circle], [patch(1, 0, 0), patch(2, 100, 0)])
    assert pieces[1][0].type == pieces[2][0].type == "polygon"
    total = sum(polygon_area(p[0].parts[0]) for p in pieces.values())
    assert total == pytest.approx(level0_geometry("circle", circle.coordinates_level0).area, rel=1e-6)


def test_lines_are_cut_into_paths_and_points_go_to_the_patch_they_are_in():
    line = shape(1, "line", [[50, 50], [150, 50]])
    pieces = project_slide_annotations([line, shape(2, "point", [[130, 30]])], [patch(1, 0, 0), patch(2, 100, 0)])
    assert [p.parts for p in pieces[1] if p.annotation.id == 1] == [[[[50.0, 50.0], [100.0, 50.0]]]]
    assert [p.parts for p in pieces[2] if p.annotation.id == 1] == [[[[0.0, 50.0], [50.0, 50.0]]]]
    assert [p.type for p in pieces[2] if p.annotation.id == 2] == ["point"] and 2 not in [p.annotation.id for p in pieces[1]]


def test_a_shape_that_only_touches_a_patch_edge_or_misses_it_leaves_nothing_there():
    touching = shape(1, "rectangle", [[100, 0], [150, 0], [150, 50], [100, 50]])  # shares only the border at x = 100
    assert project_slide_annotations([touching], [patch(1, 0, 0)]) == {}
    assert project_slide_annotations([shape(2, "polygon", [[500, 500], [600, 500], [550, 600]])], [patch(1, 0, 0)]) == {}


def test_a_shape_in_the_overlap_of_two_patches_appears_in_both():
    pieces = project_slide_annotations([shape(1, "rectangle", [[60, 10], [80, 10], [80, 30], [60, 30]])], [patch(1, 0, 0), patch(2, 50, 0)])
    assert set(pieces) == {1, 2}
    assert pieces[1][0].parts[0][0] == [60.0, 10.0] and pieces[2][0].parts[0][0] == [10.0, 10.0]  # each in its own pixels


def test_a_self_crossing_outline_is_repaired_before_clipping_and_collapsed_shapes_vanish():
    bowtie = shape(1, "freehand", [[10, 10], [60, 60], [60, 10], [10, 60]])
    (piece,) = project_slide_annotations([bowtie], [patch(1, 0, 0)])[1]
    assert sum(polygon_area(r) for r in piece.parts) == pytest.approx(Polygon([(10, 10), (35, 35), (10, 60)]).area * 2)
    assert project_slide_annotations([shape(2, "line", [[5, 5], [5, 5]]), shape(3, "circle", [[9, 9], [9, 9]])], [patch(1, 0, 0)]) == {}


# ------------------------------------------------------------------ over HTTP


@pytest.fixture()
def grid(env):  # noqa: F811
    """A real slide with a 3 x 2 grid of patches (each 512 Level-0 px wide, read at level 1) and two classes."""
    client, pid, _ = env
    slide_id = upload(client, pid, [("Whole.tif", SLIDE)]).json()["slides"][0]["id"]
    config = client.get(f"/api/projects/{pid}/config").json()
    updated = client.put(
        f"/api/configs/{config['id']}",
        json={
            "patch_width": 256, "patch_height": 256, "stride_x": 256, "stride_y": 256, "min_tissue_fraction": 0.0,
            "annotation_classes": [{"name": "Tumor", "color_hex": "#dc2626"}, {"name": "Stroma", "color_hex": "#16a34a"}],
        },
    )  # fmt: skip
    assert updated.status_code == 200, updated.text
    classes = {c["name"]: c["id"] for c in updated.json()["annotation_classes"]}
    assert client.post(f"/api/slides/{slide_id}/generate-patches", json={"config_version_id": config["id"]}).status_code == 200
    patches = client.get(f"/api/slides/{slide_id}/patches?limit=100").json()["items"]
    return client, slide_id, patches, classes, config["id"]


def make(client, slide_id, kind, coords, class_id=None, **extra):
    return client.post(f"/api/slides/{slide_id}/annotations", json={"type": kind, "coordinates_level0": coords, "class_id": class_id, **extra})


def by_origin(patches):
    return {(p["x"], p["y"]): p for p in patches}


def test_the_grid_is_what_the_tests_assume(grid):
    _, _, patches, _, _ = grid
    assert len(patches) == 6 and {(p["x"], p["y"]) for p in patches} == {(x, y) for x in (0, 512, 1024) for y in (0, 512)}
    assert all(p["width_l0"] == 512 and p["width"] == 256 for p in patches)  # read at level 1: downsample 2


def test_creating_a_slide_annotation_stores_only_level0_and_touches_no_patch(grid):
    client, slide_id, patches, classes, _ = grid
    square = [[100, 100], [300, 100], [300, 300], [100, 300]]
    res = make(client, slide_id, "polygon", square, classes["Tumor"], notes="whole-slide outline")
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["patch_id"] is None and body["coordinates_patch_local"] == [] and body["coordinates_level0"] == [list(map(float, p)) for p in square]
    assert body["notes"] == "whole-slide outline"
    assert all(p["status"] == "unannotated" for p in client.get(f"/api/slides/{slide_id}/patches?limit=100").json()["items"])


@pytest.mark.parametrize(
    "kind,coords,fragment",
    [
        ("polygon", [[100, 100], [5000, 100], [300, 300]], "outside the slide"),
        ("circle", [[-50, 40], [10, 40]], "outside the slide"),
        ("circle", [[100, 100]], "exactly 2"),
        ("polygon", [[0, 0], [10, 10]], "at least 3"),
        ("blob", [[0, 0]], "unknown shape type"),
    ],
)
def test_bad_slide_annotations_are_refused(grid, kind, coords, fragment):
    client, slide_id, _, _, _ = grid
    res = make(client, slide_id, kind, coords)
    assert res.status_code == 422 and fragment in json.dumps(res.json())
    assert client.get(f"/api/slides/{slide_id}/annotations?scope=slide").json() == []


def test_a_slide_annotation_can_only_use_a_class_of_the_slides_configuration_and_never_in_an_image_project(grid):
    client, slide_id, _, _, _ = grid
    foreign = client.post("/api/projects", json={"name": "Elsewhere", "config": {"annotation_classes": [{"name": "Alien", "color_hex": "#123456"}]}}).json()
    assert make(client, slide_id, "point", [[10, 10]], foreign["active_config"]["annotation_classes"][0]["id"]).status_code == 422

    image_project = client.post("/api/projects", json={"name": "Imgs", "project_type": "image"}).json()
    png = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 10, 10)).save(png, format="PNG")
    image_slide = upload(client, image_project["id"], [("a.png", png.getvalue())]).json()["slides"][0]["id"]
    refused = make(client, image_slide, "point", [[5, 5]])
    assert refused.status_code == 409 and "image" in refused.text.lower()


def test_scope_separates_patch_and_slide_annotations_in_listings(grid):
    client, slide_id, patches, classes, _ = grid
    first = patches[0]
    own = client.post(f"/api/patches/{first['id']}/annotations", json={"type": "point", "class_id": classes["Tumor"], "coordinates_patch_local": [[10, 10]]}).json()
    slide_level = make(client, slide_id, "point", [[700, 700]], classes["Stroma"]).json()

    def ids(scope):
        return [a["id"] for a in client.get(f"/api/slides/{slide_id}/annotations?scope={scope}").json()]

    assert ids("all") == [own["id"], slide_level["id"]] and ids("patch") == [own["id"]] and ids("slide") == [slide_level["id"]]
    assert [a["id"] for a in client.get(f"/api/patches/{first['id']}/annotations").json()] == [own["id"]]  # a patch lists only its own


def test_editing_uses_the_coordinate_space_the_annotation_lives_in(grid):
    client, slide_id, patches, classes, _ = grid
    ann = make(client, slide_id, "line", [[10, 10], [200, 10]]).json()

    moved = client.put(f"/api/annotations/{ann['id']}", json={"coordinates_level0": [[20, 30], [220, 30]], "class_id": classes["Tumor"], "unsure": True})
    assert moved.status_code == 200 and moved.json()["coordinates_level0"] == [[20, 30], [220, 30]] and moved.json()["unsure"] is True
    assert client.put(f"/api/annotations/{ann['id']}", json={"coordinates_patch_local": [[1, 1], [2, 2]]}).status_code == 422
    assert client.put(f"/api/annotations/{ann['id']}", json={"coordinates_level0": [[0, 0], [99999, 5]]}).status_code == 422
    assert client.put(f"/api/annotations/{ann['id']}", json={"coordinates_level0": [[0, 0]]}).status_code == 422  # a line needs 2 points

    # A patch's annotation may be edited in Level-0 pixels too (on the whole slide), but only within its patch.
    p0 = patches[0]
    own = client.post(f"/api/patches/{p0['id']}/annotations", json={"type": "point", "coordinates_patch_local": [[5, 5]]}).json()
    inside = [[p0["x"] + 1, p0["y"] + 1]]
    res = client.put(f"/api/annotations/{own['id']}", json={"coordinates_level0": inside})
    assert res.status_code == 200 and res.json()["patch_id"] == p0["id"] and res.json()["coordinates_level0"] == inside
    outside = [[p0["x"] + p0["width_l0"] + 50, p0["y"]]]
    assert client.put(f"/api/annotations/{own['id']}", json={"coordinates_level0": outside}).status_code == 422

    assert client.delete(f"/api/annotations/{ann['id']}").status_code == 204
    assert client.get(f"/api/slides/{slide_id}/annotations?scope=slide").json() == []


def test_all_annotations_survive_regenerating_the_patch_grid(grid):
    """Regenerating a grid updates it in place: a patch at the same place keeps its id and annotations,
    and slide-level annotations belong to no patch anyway."""
    client, slide_id, patches, classes, config_id = grid
    point = client.post(f"/api/patches/{patches[0]['id']}/annotations", json={"type": "point", "coordinates_patch_local": [[5, 5]]}).json()
    circle = make(client, slide_id, "circle", [[400, 400], [450, 400]], classes["Tumor"]).json()

    assert client.post(f"/api/slides/{slide_id}/generate-patches", json={"config_version_id": config_id}).status_code == 200
    remaining = client.get(f"/api/slides/{slide_id}/annotations").json()
    assert sorted(a["id"] for a in remaining) == sorted([point["id"], circle["id"]])
    assert any(p["id"] == patches[0]["id"] for p in client.get(f"/api/slides/{slide_id}/patches").json()["items"])


def test_importing_a_wsi_json_file_recreates_slide_level_annotations_and_is_repeatable(grid):
    client, slide_id, _, classes, _ = grid
    make(client, slide_id, "polygon", [[100, 100], [300, 100], [200, 300]], classes["Tumor"])
    exported = client.get(f"/api/slides/{slide_id}/export/wsi_json").json()
    assert [a["source_patch"] for a in exported["annotations"]] == [None]

    client.delete(f"/api/annotations/{client.get(f'/api/slides/{slide_id}/annotations').json()[0]['id']}")
    first = client.post(f"/api/slides/{slide_id}/import-annotations", json=exported).json()
    again = client.post(f"/api/slides/{slide_id}/import-annotations", json=exported).json()
    assert (first["imported"], again["imported"], again["skipped_duplicate"]) == (1, 0, 1)
    restored = client.get(f"/api/slides/{slide_id}/annotations?scope=slide").json()
    assert len(restored) == 1 and restored[0]["patch_id"] is None and restored[0]["class_id"] == classes["Tumor"]

    outside = {"annotations": [{"type": "polygon", "source_patch": None, "coordinates": [[0, 0], [99999, 0], [5, 5]]}]}
    assert client.post(f"/api/slides/{slide_id}/import-annotations", json=outside).json()["skipped_outside_slide"] == 1


def test_a_qupath_geojson_file_is_read_mapped_and_placed_in_patches(grid):
    client, slide_id, _, classes, _ = grid
    qupath = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[100, 100], [300, 100], [200, 300], [100, 100]]]},
         "properties": {"objectType": "annotation", "classification": {"name": "Tumour"}}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [600, 50]}, "properties": {"classification": {"name": "stroma"}}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [700, 60]}, "properties": {"classification": {"name": "Ignore"}}},
    ]}  # fmt: skip
    files = {"file": ("export.geojson", json.dumps(qupath).encode(), "application/geo+json")}
    parsed = client.post(f"/api/slides/{slide_id}/import-annotations/parse", files=files, data={"scale": "1"})
    assert parsed.status_code == 200, parsed.text
    body = parsed.json()
    assert body["format"] == "geojson" and body["shape_counts"] == {"polygon": 1, "point": 2}
    assert {l["label"]: l["class_id"] for l in body["labels"]} == {"Tumour": None, "stroma": classes["Stroma"], "Ignore": None}

    result = client.post(f"/api/slides/{slide_id}/import-annotations", json={
        "annotations": body["annotations"],
        "label_map": {"Tumour": classes["Tumor"], "stroma": classes["Stroma"], "Ignore": "skip"},
        "assign_to_patches": True,
    }).json()  # fmt: skip
    assert (result["imported_to_patches"], result["skipped_by_choice"]) == (2, 1)
    polygon = next(a for a in client.get(f"/api/slides/{slide_id}/annotations").json() if a["type"] == "polygon")
    assert polygon["class_id"] == classes["Tumor"] and polygon["patch_id"] is not None
    assert polygon["coordinates_patch_local"][0] == [50.0, 50.0]  # the patch at (0, 0) is read at level 1: downsample 2

    bad = client.post(f"/api/slides/{slide_id}/import-annotations/parse", files={"file": ("x.json", b'{"a": 1}', "application/json")})
    assert bad.status_code == 422 and "Supported" in bad.json()["detail"]


# ------------------------------------------------------------------- exports

CROSSING = [[400, 100], [700, 100], [700, 300], [400, 300]]  # 300 x 200 across the border at x = 512


def test_wsi_json_and_geojson_carry_slide_annotations_in_level0(grid):
    client, slide_id, patches, classes, _ = grid
    ann = make(client, slide_id, "polygon", CROSSING, classes["Tumor"]).json()

    wsi = client.get(f"/api/slides/{slide_id}/export/wsi_json").json()
    (entry,) = wsi["annotations"]
    assert entry["source_patch"] is None and entry["coordinates"] == [list(map(float, p)) for p in CROSSING] and entry["label"] == "Tumor"
    counts = {(p["x"], p["y"]): (p["annotation_count"], p["slide_annotation_count"]) for p in wsi["patches"]}
    assert counts == {(0, 0): (1, 1), (512, 0): (1, 1)}  # the two patches the polygon reaches; the default scope hides the rest

    (feature,) = client.get(f"/api/slides/{slide_id}/export/geojson").json()["features"]
    assert feature["id"] == f"ann_{ann['id']:06d}" and feature["properties"]["drawn_in"] == "slide" and feature["properties"]["patch_id"] is None
    assert feature["geometry"]["coordinates"][0][0] == [400.0, 100.0] and feature["properties"]["area_px2"] == 60000.0


def test_coco_lists_the_pieces_in_each_patch_in_that_patchs_pixels(grid):
    client, slide_id, patches, classes, _ = grid
    ann = make(client, slide_id, "polygon", CROSSING, classes["Tumor"]).json()
    coco = client.get(f"/api/slides/{slide_id}/export/coco").json()

    origins = by_origin(patches)
    image_ids = {origins[(0, 0)]["id"], origins[(512, 0)]["id"]}
    assert {i["id"] for i in coco["images"]} == image_ids  # only the patches the shape reaches
    pieces = {a["image_id"]: a for a in coco["annotations"]}
    assert set(pieces) == image_ids and len({a["id"] for a in coco["annotations"]}) == 2  # unique ids per piece
    left, right = pieces[origins[(0, 0)]["id"]], pieces[origins[(512, 0)]["id"]]
    assert left["id"] == ann["id"] * 10**9 + origins[(0, 0)]["id"] and left["vp_source_annotation_id"] == ann["id"] and left["vp_scope"] == "slide"
    # patches are read at level 1 (2x): 112 x 200 Level-0 px on the left is 56 x 100 patch px, and 188 x 200 on the right is 94 x 100
    assert left["area"] == pytest.approx(56 * 100) and right["area"] == pytest.approx(94 * 100) and left["vp_clipped"] and right["vp_clipped"]
    assert left["bbox"] == [200.0, 50.0, 56.0, 100.0] and right["bbox"] == [0.0, 50.0, 94.0, 100.0]
    # ...and the same pieces in Level-0 pixels, so nothing about their real position is lost
    assert left["vp_level0_segmentation"][0][:2] == [pytest.approx(400), pytest.approx(100)]


def test_a_patch_only_covered_by_a_slide_annotation_counts_as_annotated(grid):
    client, slide_id, patches, classes, _ = grid
    make(client, slide_id, "rectangle", [[520, 520], [600, 520], [600, 600], [520, 600]], classes["Tumor"])  # inside patch (512, 512) only
    origins = by_origin(patches)

    def csv_rows(scope):
        return rows(client.get(f"/api/slides/{slide_id}/export/patch_csv?patches={scope}").text)

    annotated = csv_rows("annotated")
    assert [(r["level0_x"], r["level0_y"], r["n_annotations"], r["n_slide_annotations"], r["dominant_class"]) for r in annotated] == [("512", "512", "1", "1", "Tumor")]
    assert len(csv_rows("empty")) == 5 and len(csv_rows("all")) == 6
    target = origins[(512, 512)]["id"]
    assert [i["id"] for i in client.get(f"/api/slides/{slide_id}/export/coco?patches=annotated").json()["images"]] == [target]


def test_patch_csv_dominant_class_uses_the_area_inside_the_patch(grid):
    client, slide_id, patches, classes, _ = grid
    # Tumor is by far the bigger shape overall (400 x 200), but only a 12 px sliver of it lies in the left patch.
    make(client, slide_id, "rectangle", [[500, 100], [900, 100], [900, 300], [500, 300]], classes["Tumor"])
    make(client, slide_id, "rectangle", [[100, 100], [300, 100], [300, 200], [100, 200]], classes["Stroma"])  # 200 x 100, wholly in (0, 0)
    table = {(r["level0_x"], r["level0_y"]): r for r in rows(client.get(f"/api/slides/{slide_id}/export/patch_csv").text)}
    left, right = table[("0", "0")], table[("512", "0")]
    assert (left["n_annotations"], left["n_slide_annotations"]) == ("2", "2")
    assert left["dominant_class"] == "Stroma"  # 20,000 Level-0 px2 of Stroma beats Tumor's 12 x 200 = 2,400 sliver
    assert right["dominant_class"] == "Tumor" and right["n_annotations"] == "1"


def test_statistics_count_a_slide_annotation_once_in_full(grid):
    client, slide_id, patches, classes, _ = grid
    make(client, slide_id, "polygon", CROSSING, classes["Tumor"])
    make(client, slide_id, "line", [[10, 10], [110, 10]], classes["Tumor"])
    stats = {r["class"]: r for r in rows(client.get(f"/api/slides/{slide_id}/export/stats_csv").text)}
    tumor = stats["Tumor"]
    assert (tumor["n_annotations"], tumor["n_polygons"], tumor["n_lines"], tumor["n_patches"]) == ("2", "1", "1", "2")  # crossing 2 patches, but 1 polygon
    assert float(tumor["summed_area_px2"]) == 60000.0 and float(tumor["summed_length_px"]) == 100.0
    assert stats["Stroma"]["n_annotations"] == "0"


def test_masks_and_images_include_the_part_of_a_slide_annotation_in_each_patch(grid):
    import zipfile

    client, slide_id, patches, classes, _ = grid
    make(client, slide_id, "polygon", CROSSING, classes["Tumor"])
    res = client.get(f"/api/slides/{slide_id}/export/coco?content=images&masks=true")
    archive = zipfile.ZipFile(io.BytesIO(res.content))
    assert json.loads(archive.read("mask_classes.json")) == {"0": "background", "1": "Tumor", "2": "Stroma"}
    coco = json.loads(archive.read("annotations/Whole_coco.json"))
    assert len(coco["images"]) == 2 and len([n for n in archive.namelist() if n.startswith("images/")]) == 2

    total = 0
    for image in coco["images"]:
        mask = np.asarray(Image.open(io.BytesIO(archive.read("masks/" + image["file_name"].rsplit(".", 1)[0] + ".png"))))
        piece = next(a for a in coco["annotations"] if a["image_id"] == image["id"])
        assert set(np.unique(mask)) == {0, 1}
        assert (mask == 1).sum() == pytest.approx(piece["area"], rel=0.03)  # the painted region is the clipped piece
        total += (mask == 1).sum()
    assert total * 4 == pytest.approx(300 * 200, rel=0.03)  # 2x2 Level-0 px per patch px: the pieces add up to the whole shape


def test_the_summary_counts_slide_annotations(grid):
    client, slide_id, _, classes, _ = grid
    make(client, slide_id, "polygon", CROSSING, classes["Tumor"])
    summary = client.get(f"/api/slides/{slide_id}/export-summary").json()
    assert summary["annotations"] == 1 and summary["patches"] == 2  # the shape, and the patches it reaches


def test_circles_wholly_inside_a_patch_stay_circles_in_coco_masks_and_projection(grid):
    client, slide_id, patches, classes, _ = grid
    make(client, slide_id, "circle", [[256, 256], [336, 256]], classes["Stroma"])  # r = 80 Level-0 px, inside patch (0, 0)
    coco = client.get(f"/api/slides/{slide_id}/export/coco").json()
    (piece,) = coco["annotations"]
    assert piece["vp_shape_type"] == "circle" and not piece["vp_clipped"]
    assert piece["area"] == pytest.approx(math.pi * 40**2, rel=0.005)  # radius 40 in patch pixels (level 1)
