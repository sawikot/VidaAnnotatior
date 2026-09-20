"""The full tool set: point, line, freehand line, rectangle, circle, polygon, freehand polygon.
Validation and coordinate conversion over HTTP, then every export and the masks."""
import io
import json
import math

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import create_engine, text

from app.database.session import LEGACY_TOOLS, NEW_TOOLS, _upgrade_enabled_tools
from app.models.annotation import GeometryAnnotation
from app.services.exporter import get_exporter
from app.services.exporter.options import ExportOptions
from app.services.exporter.patch_images import render_mask
from app.services.geometry import (
    SHAPE_TYPES,
    area_ring,
    circle_center_radius,
    has_extent,
    line_length,
    shape_area,
    validate_shape,
)
from tests.test_export_api import annotated_slide  # noqa: F401  (fixture)
from tests.test_export_formats import rows, seed
from tests.test_slide_import_api import env  # noqa: F401  (fixture)

VALID = {
    "point": [[5, 5]],
    "line": [[0, 0], [30, 40]],
    "freehand_line": [[0, 0], [10, 5], [20, 0], [30, 10]],
    "rectangle": [[0, 0], [20, 0], [20, 10], [0, 10]],
    "circle": [[50, 50], [80, 50]],
    "polygon": [[0, 0], [20, 0], [10, 15]],
    "freehand": [[0, 0], [20, 0], [25, 10], [10, 15]],
}


# ---------------------------------------------------------------- geometry helpers


def test_every_tool_has_a_shape_type_and_valid_example():
    assert set(VALID) == set(SHAPE_TYPES)
    for kind, coords in VALID.items():
        validate_shape(kind, coords)


def test_circle_helpers_are_exact():
    (cx, cy), r = circle_center_radius([[10, 20], [13, 24]])
    assert (cx, cy, r) == (10.0, 20.0, 5.0)
    assert shape_area("circle", [[0, 0], [3, 4]]) == pytest.approx(math.pi * 25)
    ring = area_ring("circle", [[0, 0], [3, 4]])
    assert len(ring) == 64 and all(math.hypot(x, y) == pytest.approx(5) for x, y in ring)


def test_line_length_follows_the_path_and_respects_per_axis_scale():
    assert line_length([[0, 0], [3, 4], [3, 10]]) == pytest.approx(11)
    assert line_length([[0, 0], [10, 10]], 2.0, 1.0) == pytest.approx(math.hypot(20, 10))  # non-square pixels


def test_collapsed_shapes_have_no_extent():
    assert not has_extent("line", [[1, 1], [1, 1]])
    assert not has_extent("circle", [[1, 1], [1, 1]])
    assert not has_extent("polygon", [[0, 0], [1, 1], [2, 2]])
    assert has_extent("point", [[1, 1]]) and has_extent("freehand_line", [[0, 0], [1, 0]])


@pytest.mark.parametrize(
    "kind,coords,message",
    [
        ("blob", [[0, 0]], "unknown shape type"),
        ("point", [[0, 0], [1, 1]], "exactly 1"),
        ("line", [[0, 0]], "exactly 2"),
        ("line", [[3, 3], [3, 3]], "greater than zero"),
        ("circle", [[0, 0]], "exactly 2"),
        ("circle", [[4, 4], [4, 4]], "greater than zero"),
        ("rectangle", [[0, 0], [1, 1], [2, 2]], "exactly 4"),
        ("polygon", [[0, 0], [1, 1]], "at least 3"),
        ("freehand", [[0, 0], [1, 1]], "at least 3"),
        ("freehand_line", [[0, 0]], "at least 2"),
        ("polygon", [[0, 0], [1, 1], [float("inf"), 2]], "finite"),
        ("polygon", [[0, 0], [1, 1, 5], [2, 0]], "finite"),
    ],
)
def test_malformed_shapes_are_rejected_with_a_reason(kind, coords, message):
    with pytest.raises(ValueError, match=message):
        validate_shape(kind, coords)


# ------------------------------------------------------------------- over HTTP


@pytest.mark.parametrize("kind", list(VALID))
def test_every_shape_type_can_be_created_and_round_trips(annotated_slide, kind):  # noqa: F811
    client, _, patch = annotated_slide
    res = client.post(f"/api/patches/{patch['id']}/annotations", json={"type": kind, "coordinates_patch_local": VALID[kind]})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["type"] == kind and body["coordinates_patch_local"] == [list(map(float, p)) for p in VALID[kind]]
    assert len(body["coordinates_level0"]) == len(VALID[kind])


def test_local_to_level0_conversion_keeps_a_circle_a_circle_on_a_downsampled_patch(annotated_slide):  # noqa: F811
    """This slide's patch is read at level 1 (2x): centre and edge scale together, so the radius doubles."""
    client, _, patch = annotated_slide
    ds = patch["width_l0"] / patch["width"]
    assert ds == 2.0
    res = client.post(
        f"/api/patches/{patch['id']}/annotations", json={"type": "circle", "coordinates_patch_local": [[100, 100], [130, 100]]}
    ).json()
    (cx, cy), r = circle_center_radius(res["coordinates_level0"])
    assert (cx, cy) == (patch["x"] + 200, patch["y"] + 200) and r == pytest.approx(60)


@pytest.mark.parametrize(
    "payload,fragment",
    [
        ({"type": "hexagon", "coordinates_patch_local": [[0, 0], [1, 1], [2, 0]]}, "unknown shape type"),
        ({"type": "circle", "coordinates_patch_local": [[5, 5]]}, "exactly 2"),
        ({"type": "line", "coordinates_patch_local": [[7, 7], [7, 7]]}, "greater than zero"),
        ({"type": "polygon", "coordinates_patch_local": [[0, 0], [9, 9]]}, "at least 3"),
    ],
)
def test_invalid_shapes_are_refused_with_422(annotated_slide, payload, fragment):  # noqa: F811
    client, _, patch = annotated_slide
    before = len(client.get(f"/api/patches/{patch['id']}/annotations").json())
    res = client.post(f"/api/patches/{patch['id']}/annotations", json=payload)
    assert res.status_code == 422 and fragment in json.dumps(res.json())
    assert len(client.get(f"/api/patches/{patch['id']}/annotations").json()) == before  # nothing was stored


def test_moving_a_shape_updates_both_coordinate_spaces_and_bad_updates_are_refused(annotated_slide):  # noqa: F811
    client, _, patch = annotated_slide
    ann = client.post(f"/api/patches/{patch['id']}/annotations", json={"type": "line", "coordinates_patch_local": [[10, 10], [40, 10]]}).json()

    moved = client.put(f"/api/annotations/{ann['id']}", json={"coordinates_patch_local": [[20, 30], [50, 30]]})
    assert moved.status_code == 200 and moved.json()["coordinates_patch_local"] == [[20, 30], [50, 30]]
    assert moved.json()["coordinates_level0"] == [[patch["x"] + 40, patch["y"] + 60], [patch["x"] + 100, patch["y"] + 60]]

    bad = client.put(f"/api/annotations/{ann['id']}", json={"coordinates_patch_local": [[1, 1], [2, 2], [3, 3]]})  # a line has 2 points
    assert bad.status_code == 422
    assert client.get(f"/api/patches/{patch['id']}/annotations").json()[-1]["coordinates_patch_local"] == [[20, 30], [50, 30]]


def test_import_skips_malformed_entries_instead_of_failing_the_whole_file(annotated_slide):  # noqa: F811
    client, slide_id, patch = annotated_slide
    origin = {"x": patch["x"], "y": patch["y"]}
    res = client.post(
        f"/api/slides/{slide_id}/import-annotations",
        json={
            "annotations": [
                {"type": "circle", "source_patch": origin, "coordinates": [[patch["x"] + 100, patch["y"] + 100], [patch["x"] + 160, patch["y"] + 100]]},
                {"type": "circle", "source_patch": origin, "coordinates": [[1, 1]]},  # malformed
                {"type": "zigzag", "source_patch": origin, "coordinates": [[1, 1], [2, 2]]},  # unknown
            ]
        },
    )
    assert res.status_code == 200
    assert res.json()["imported"] == 1 and res.json()["skipped_invalid_shape"] == 2


def test_new_projects_get_all_the_tools_by_default(env):  # noqa: F811
    client, _, _ = env
    project = client.post("/api/projects", json={"name": "Tools"}).json()
    assert set(project["active_config"]["enabled_tools"]) == {"polygon", "rectangle", "point", "freehand", *NEW_TOOLS}


# ------------------------------------------------------- upgrade of existing configs


def test_only_untouched_legacy_tool_lists_are_upgraded(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE project_config_versions (id INTEGER PRIMARY KEY, enabled_tools JSON)"))
        for row_id, tools in enumerate(
            [LEGACY_TOOLS, list(reversed(LEGACY_TOOLS)), ["polygon", "rectangle"], LEGACY_TOOLS + NEW_TOOLS], start=1
        ):
            conn.execute(text("INSERT INTO project_config_versions VALUES (:i, :t)"), {"i": row_id, "t": json.dumps(tools)})

    _upgrade_enabled_tools(engine)
    _upgrade_enabled_tools(engine)  # runs on every startup: must be idempotent
    with engine.connect() as conn:
        result = {i: json.loads(t) for i, t in conn.execute(text("SELECT id, enabled_tools FROM project_config_versions"))}
    assert sorted(result[1]) == sorted(LEGACY_TOOLS + NEW_TOOLS) and len(result[1]) == 7  # upgraded, once
    assert sorted(result[2]) == sorted(LEGACY_TOOLS + NEW_TOOLS)  # same set, different order: still untouched default
    assert result[3] == ["polygon", "rectangle"]  # someone chose this: left alone
    assert len(result[4]) == 7  # not doubled


# ----------------------------------------------------------------- exporters
# Patch a of the hand-built slide (origin 1000,2000; mpp 0.5) gains: a circle r=100 (Necrosis),
# a 60 px line (Tumor), a 60 px freehand line (Stroma) and a zero-radius circle (degenerate).


def add_shapes(db, s):
    a, config = s["a"], s["config"]
    classes = {c.name: c.id for c in config.annotation_classes}

    def add(kind, local, cls):
        ann = GeometryAnnotation(
            patch_id=a.id, slide_id=s["slide"].id, config_version_id=config.id, class_id=classes[cls], type=kind,
            coordinates_patch_local=local, coordinates_level0=[[a.x + x, a.y + y] for x, y in local],
        )  # fmt: skip
        db.add(ann)
        db.flush()
        return ann

    made = dict(
        circle=add("circle", [[300, 300], [400, 300]], "Necrosis"),
        line=add("line", [[10, 400], [70, 400]], "Tumor"),
        freehand_line=add("freehand_line", [[0, 450], [30, 450], [30, 480]], "Stroma"),
        empty_circle=add("circle", [[5, 5], [5, 5]], "Tumor"),
    )
    db.commit()
    return made


def test_geojson_writes_lines_as_linestrings_and_circles_as_closed_polygons(db):
    s = seed(db)
    made = add_shapes(db, s)
    doc = get_exporter("geojson").export(db, s["slide"])
    by_id = {f["id"]: f for f in doc["features"]}

    line = by_id[f"ann_{made['line'].id:06d}"]
    assert line["geometry"] == {"type": "LineString", "coordinates": [[1010, 2400], [1070, 2400]]}
    assert line["properties"]["length_px"] == 60.0 and "area_px2" not in line["properties"]
    fl = by_id[f"ann_{made['freehand_line'].id:06d}"]
    assert fl["geometry"]["type"] == "LineString" and fl["properties"]["length_px"] == 60.0 and fl["properties"]["shape_type"] == "freehand_line"

    circle = by_id[f"ann_{made['circle'].id:06d}"]
    ring = circle["geometry"]["coordinates"][0]
    assert circle["geometry"]["type"] == "Polygon" and ring[0] == ring[-1] and len(ring) == 65
    assert circle["properties"]["radius_px"] == 100.0 and circle["properties"]["valid_geometry"] is True
    assert circle["properties"]["area_px2"] == pytest.approx(math.pi * 100**2, abs=0.01)  # exact, not the polygon's

    assert f"ann_{made['empty_circle'].id:06d}" not in by_id  # zero radius
    assert doc["virtualpatch"]["skipped_degenerate_geometry"] == 2  # the collinear polygon and the empty circle
    assert doc["virtualpatch"]["feature_count"] == 7 + 3


def test_coco_takes_circles_as_polygons_and_reports_lines_as_unsupported(db):
    s = seed(db)
    made = add_shapes(db, s)
    doc = get_exporter("coco").export(db, s["slide"])
    ann = next(a for a in doc["annotations"] if a["id"] == made["circle"].id)

    assert ann["bbox"] == [200.0, 200.0, 200.0, 200.0] and ann["vp_shape_type"] == "circle"
    assert len(ann["segmentation"][0]) == 64 * 2
    assert ann["area"] == pytest.approx(math.pi * 100**2, rel=0.005)
    assert ann["vp_level0_segmentation"][0][:2] == [1400.0, 2300.0]  # angle 0 vertex, in Level-0 pixels
    ids = {a["id"] for a in doc["annotations"]}
    assert made["line"].id not in ids and made["freehand_line"].id not in ids
    assert doc["info"]["vp_skipped"] == {"point_annotations": 1, "line_annotations": 2, "unclassified": 1, "degenerate_geometry": 2}


def test_wsi_json_keeps_the_native_shape_and_adds_radius_and_length(db):
    s = seed(db)
    made = add_shapes(db, s)
    by_id = {a["annotation_id"]: a for a in get_exporter("wsi_json").export(db, s["slide"])["annotations"]}
    circle = by_id[f"ann_{made['circle'].id:06d}"]
    assert circle["type"] == "circle" and circle["coordinates"] == [[1300, 2300], [1400, 2300]] and circle["radius"] == 100.0
    line = by_id[f"ann_{made['line'].id:06d}"]
    assert line["type"] == "line" and line["length"] == 60.0 and "radius" not in line
    assert "length" not in by_id[f"ann_{s['rect'].id:06d}"]


def test_stats_count_lines_measure_them_and_treat_a_circle_as_area(db):
    s = seed(db)
    add_shapes(db, s)
    stats = {r["class"]: r for r in rows(get_exporter("stats_csv").export(db, s["slide"]))}

    necrosis = stats["Necrosis"]
    assert (necrosis["n_annotations"], necrosis["n_polygons"], necrosis["n_points"], necrosis["n_lines"]) == ("1", "1", "0", "0")
    assert float(necrosis["summed_area_px2"]) == pytest.approx(math.pi * 100**2, abs=0.01)

    tumor = stats["Tumor"]  # rect, tri, point, degenerate flat polygon, downsampled polygon, empty circle, line
    assert (tumor["n_points"], tumor["n_lines"]) == ("1", "1")
    assert float(tumor["summed_length_px"]) == 60.0 and float(tumor["summed_length_um"]) == pytest.approx(30.0)  # mpp 0.5
    assert stats["Stroma"]["n_lines"] == "1" and float(stats["Stroma"]["summed_length_px"]) == 60.0
    assert float(stats["Necrosis"]["summed_length_px"]) == 0.0


def test_stats_leave_physical_length_blank_without_resolution(db):
    s = seed(db)
    add_shapes(db, s)
    s["slide"].mpp_x = s["slide"].mpp_y = None
    db.commit()
    tumor = {r["class"]: r for r in rows(get_exporter("stats_csv").export(db, s["slide"]))}["Tumor"]
    assert tumor["summed_length_px"] == "60.0" and tumor["summed_length_um"] == ""


def test_patch_csv_dominant_class_counts_a_circles_area(db):
    s = seed(db)
    add_shapes(db, s)
    patch_a = rows(get_exporter("patch_csv").export(db, s["slide"], ExportOptions(patch_scope="all")))[0]
    assert patch_a["dominant_class"] == "Necrosis"  # pi * 100^2 = 31,416 beats Stroma's 10,050


def test_masks_paint_circles_as_discs_and_leave_lines_and_points_out(db):
    s = seed(db)
    add_shapes(db, s)
    anns = db.query(GeometryAnnotation).filter(GeometryAnnotation.patch_id == s["a"].id).order_by(GeometryAnnotation.id).all()
    classes = {c.name: i for i, c in enumerate(sorted(s["config"].annotation_classes, key=lambda c: c.order_index), start=1)}
    class_index = {c.id: classes[c.name] for c in s["config"].annotation_classes}

    mask = np.asarray(Image.open(io.BytesIO(render_mask(s["a"], anns, class_index))))
    necrosis = classes["Necrosis"]
    assert mask[300, 300] == necrosis and mask[300, 399] == necrosis and mask[300, 405] == 0  # a disc of radius 100
    assert (mask == necrosis).sum() == pytest.approx(math.pi * 100**2, rel=0.02)
    assert not mask[395:405, 10:70].any() and not mask[445:485, 0:35].any()  # the two lines leave no trace


# ------------------------------------------------------------ editing an existing annotation


def _classes(client, slide_id):
    project_id = client.get(f"/api/slides/{slide_id}").json()["project_id"]
    return {c["name"]: c["id"] for c in client.get(f"/api/projects/{project_id}").json()["active_config"]["annotation_classes"]}


def test_an_annotation_can_be_reclassified_unclassified_and_flagged(annotated_slide):  # noqa: F811
    client, slide_id, patch = annotated_slide
    classes = _classes(client, slide_id)
    ann = client.post(
        f"/api/patches/{patch['id']}/annotations",
        json={"type": "circle", "class_id": classes["Tumor"], "coordinates_patch_local": [[50, 50], [70, 50]]},
    ).json()

    changed = client.put(f"/api/annotations/{ann['id']}", json={"class_id": classes["Stroma"], "unsure": True, "notes": "check margin"})
    assert changed.status_code == 200
    body = changed.json()
    assert (body["class_id"], body["unsure"], body["notes"]) == (classes["Stroma"], True, "check margin")
    assert body["coordinates_patch_local"] == ann["coordinates_patch_local"]  # shape untouched

    assert client.put(f"/api/annotations/{ann['id']}", json={"class_id": None}).json()["class_id"] is None
    exported = client.get(f"/api/slides/{slide_id}/export/geojson").json()["features"]
    assert [f["properties"]["label"] for f in exported if f["properties"]["shape_type"] == "circle"] == [None]


def test_a_class_from_another_configuration_is_refused(annotated_slide):  # noqa: F811
    client, slide_id, patch = annotated_slide
    other_project = client.post(
        "/api/projects", json={"name": "Elsewhere", "config": {"annotation_classes": [{"name": "Alien", "color_hex": "#123456"}]}}
    ).json()
    foreign = other_project["active_config"]["annotation_classes"][0]["id"]
    circle = [[50, 50], [70, 50]]

    refused = client.post(f"/api/patches/{patch['id']}/annotations", json={"type": "circle", "class_id": foreign, "coordinates_patch_local": circle})
    assert refused.status_code == 422 and "does not belong" in refused.text

    ann = client.post(f"/api/patches/{patch['id']}/annotations", json={"type": "circle", "coordinates_patch_local": circle}).json()
    assert client.put(f"/api/annotations/{ann['id']}", json={"class_id": foreign}).status_code == 422
    assert client.put(f"/api/annotations/{ann['id']}", json={"class_id": 999999}).status_code == 422
    assert client.get(f"/api/patches/{patch['id']}/annotations").json()[-1]["class_id"] is None  # nothing changed


def test_reshaping_can_add_and_remove_vertices_but_not_below_the_minimum(annotated_slide):  # noqa: F811
    client, _, patch = annotated_slide
    ann = client.post(f"/api/patches/{patch['id']}/annotations", json={"type": "polygon", "coordinates_patch_local": [[0, 0], [40, 0], [40, 40]]}).json()

    grown = client.put(f"/api/annotations/{ann['id']}", json={"coordinates_patch_local": [[0, 0], [40, 0], [50, 20], [40, 40]]})
    assert grown.status_code == 200 and len(grown.json()["coordinates_level0"]) == 4

    shrunk = client.put(f"/api/annotations/{ann['id']}", json={"coordinates_patch_local": [[0, 0], [40, 0]]})
    assert shrunk.status_code == 422 and "at least 3" in shrunk.text
    assert len(client.get(f"/api/patches/{patch['id']}/annotations").json()[-1]["coordinates_patch_local"]) == 4


def test_resizing_a_circle_updates_its_level0_radius(annotated_slide):  # noqa: F811
    client, _, patch = annotated_slide
    ds = patch["width_l0"] / patch["width"]
    ann = client.post(f"/api/patches/{patch['id']}/annotations", json={"type": "circle", "coordinates_patch_local": [[100, 100], [110, 100]]}).json()
    resized = client.put(f"/api/annotations/{ann['id']}", json={"coordinates_patch_local": [[100, 100], [140, 100]]}).json()
    assert circle_center_radius(resized["coordinates_level0"])[1] == pytest.approx(40 * ds)
