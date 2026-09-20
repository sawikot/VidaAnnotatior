"""The five exporters against one hand-built slide that contains every awkward
case: each shape type, a downsampled patch, a self-crossing polygon, a degenerate
polygon, unclassified shapes, an excluded patch and hostile spreadsheet text."""
import csv
import io
import json

import pytest

from app.models.annotation import GeometryAnnotation
from app.models.config_version import AnnotationClass, ProjectConfigVersion
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide
from app.services.exporter import REGISTRY, get_exporter
from app.services.exporter.options import ExportOptions
from app.services.exporter.tables import to_csv

INJECTION = '=HYPERLINK("http://evil.example","click")'


def _offset(points, dx, dy):
    return [[x + dx, y + dy] for x, y in points]


def seed(db):
    project = Project(slug="FMT_TEST", name="Format Test")
    db.add(project)
    db.flush()
    config = ProjectConfigVersion(
        project_id=project.id, version_label="v1.0", status="locked", patch_width=512, patch_height=512,
        stride_x=512, stride_y=512, min_tissue_fraction=0.5,
    )  # fmt: skip
    db.add(config)
    db.flush()
    tumor = AnnotationClass(config_version_id=config.id, name="Tumor", color_hex="#dc2626", order_index=0)
    stroma = AnnotationClass(config_version_id=config.id, name="Stroma", color_hex="#16a34a", order_index=1)
    necrosis = AnnotationClass(config_version_id=config.id, name="Necrosis", color_hex="#eab308", order_index=2)
    db.add_all([tumor, stroma, necrosis])
    db.flush()
    slide = Slide(
        project_id=project.id, filename="Case 7 (final).svs", source_type="upload", width_l0=100000, height_l0=80000,
        mpp_x=0.5, mpp_y=0.5, tissue_area_mm2=10.0, active_config_version_id=config.id,
    )  # fmt: skip
    db.add(slide)
    db.flush()

    def patch(index, x, y, **kw):
        defaults = dict(level=0, width=512, height=512, width_l0=512, height_l0=512, tissue_fraction=0.8123456, status="annotated")
        p = Patch(slide_id=slide.id, config_version_id=config.id, patch_index=index, x=x, y=y, **{**defaults, **kw})
        db.add(p)
        db.flush()
        return p

    def ann(p, kind, local, cls, *, scale=1, **kw):
        level0 = _offset([[u * scale, v * scale] for u, v in local], p.x, p.y)
        a = GeometryAnnotation(
            patch_id=p.id, slide_id=slide.id, config_version_id=config.id, class_id=cls.id if cls else None,
            type=kind, coordinates_patch_local=local, coordinates_level0=level0, **kw,
        )  # fmt: skip
        db.add(a)
        db.flush()
        return a

    a = patch(0, 1000, 2000)
    rect = ann(a, "rectangle", [[0, 0], [100, 0], [100, 50], [0, 50]], tumor)  # area 5000
    tri = ann(a, "polygon", [[10, 10], [110, 10], [10, 60]], tumor, unsure=True)  # area 2500
    point = ann(a, "point", [[200, 200]], tumor)
    square = ann(a, "polygon", [[0, 100], [100, 100], [100, 200], [0, 200]], stroma)  # area 10000
    bowtie = ann(a, "freehand", [[300, 0], [310, 10], [310, 0], [300, 10]], stroma)  # crosses itself; 2 x 25
    loose = ann(a, "polygon", [[400, 0], [420, 0], [420, 20], [400, 20]], None)  # unclassified, area 400
    flat = ann(a, "polygon", [[0, 300], [50, 300], [100, 300]], tumor)  # collinear -> no area

    b = patch(1, 3000, 4000, level=1, width=256, height=256, tissue_fraction=0.9, status="reviewed", patch_label="Tumor")
    down = ann(b, "polygon", [[10, 10], [60, 10], [60, 60], [10, 60]], tumor, scale=2, flagged=True)  # 50x50 px at level 1

    c = patch(2, 5000, 6000, excluded=True)
    hidden = ann(c, "polygon", [[0, 0], [10, 0], [10, 10], [0, 10]], tumor)

    d = patch(3, 7000, 8000, status="unannotated", notes=INJECTION, flagged=True)
    db.commit()
    return dict(slide=slide, config=config, a=a, b=b, c=c, d=d, rect=rect, tri=tri, point=point, square=square,
                bowtie=bowtie, loose=loose, flat=flat, down=down, hidden=hidden, necrosis=necrosis)  # fmt: skip


def rows(text):
    return list(csv.DictReader(io.StringIO(text)))


# ---------------------------------------------------------------- registry


def test_all_five_formats_are_registered_and_none_is_a_stub(db):
    assert set(REGISTRY) == {"wsi_json", "geojson", "coco", "patch_csv", "stats_csv"}
    s = seed(db)
    for fmt in REGISTRY:
        get_exporter(fmt).export(db, s["slide"])  # must not raise NotImplementedError


def test_unknown_format_is_rejected(db):
    with pytest.raises(ValueError):
        get_exporter("shapefile")


@pytest.mark.parametrize("fmt", ["wsi_json", "geojson", "coco"])
def test_rendered_json_is_valid_and_equals_the_structured_result(db, fmt):
    s = seed(db)
    exporter = get_exporter(fmt)
    result = exporter.export(db, s["slide"])
    assert json.loads(exporter.render(result)) == result


# ----------------------------------------------------------------- GeoJSON


def test_geojson_geometry_is_level0_and_rings_are_closed(db):
    s = seed(db)
    doc = get_exporter("geojson").export(db, s["slide"])
    assert doc["type"] == "FeatureCollection"
    assert doc["virtualpatch"]["coordinate_space"] == "level0_pixels"

    by_id = {f["id"]: f for f in doc["features"]}
    rect = by_id[f"ann_{s['rect'].id:06d}"]
    ring = rect["geometry"]["coordinates"][0]
    assert rect["geometry"]["type"] == "Polygon"
    assert ring[0] == ring[-1] and len(ring) == 5
    assert ring[:4] == [[1000, 2000], [1100, 2000], [1100, 2050], [1000, 2050]]  # patch origin + local

    point = by_id[f"ann_{s['point'].id:06d}"]["geometry"]
    assert point == {"type": "Point", "coordinates": [1200, 2200]}

    # a downsampled patch: local 50 px at level 1 is 100 Level-0 px
    down_ring = by_id[f"ann_{s['down'].id:06d}"]["geometry"]["coordinates"][0]
    assert down_ring[0] == [3020, 4020] and down_ring[2] == [3120, 4120]


def test_geojson_properties_follow_qupath_convention(db):
    s = seed(db)
    doc = get_exporter("geojson").export(db, s["slide"])
    by_id = {f["id"]: f["properties"] for f in doc["features"]}
    tumor = by_id[f"ann_{s['tri'].id:06d}"]
    assert tumor["objectType"] == "annotation"
    assert tumor["classification"] == {"name": "Tumor", "color": [220, 38, 38]}
    assert tumor["unsure"] is True and tumor["area_px2"] == 2500.0 and tumor["valid_geometry"] is True
    assert tumor["patch_x"] == 1000 and tumor["patch_level"] == 0

    loose = by_id[f"ann_{s['loose'].id:06d}"]
    assert loose["label"] is None and "classification" not in loose


def test_geojson_flags_self_crossing_polygon_and_skips_degenerate_and_excluded(db):
    s = seed(db)
    doc = get_exporter("geojson").export(db, s["slide"])
    ids = {f["id"] for f in doc["features"]}
    props = {f["id"]: f["properties"] for f in doc["features"]}

    bow = props[f"ann_{s['bowtie'].id:06d}"]
    assert bow["valid_geometry"] is False and bow["area_px2"] == 50.0  # repaired area, geometry kept as drawn

    assert f"ann_{s['flat'].id:06d}" not in ids
    assert doc["virtualpatch"]["skipped_degenerate_geometry"] == 1
    assert f"ann_{s['hidden'].id:06d}" not in ids  # patch excluded from training
    assert doc["virtualpatch"]["feature_count"] == len(doc["features"]) == 7


# -------------------------------------------------------------------- COCO


def test_coco_images_are_patches_with_annotations_and_categories_come_from_config(db):
    s = seed(db)
    doc = get_exporter("coco").export(db, s["slide"])

    assert [im["id"] for im in doc["images"]] == [s["a"].id, s["b"].id]  # excluded and empty patches absent
    assert [c["name"] for c in doc["categories"]] == ["Tumor", "Stroma", "Necrosis"]  # unused classes stay listed
    assert s["necrosis"].id in {c["id"] for c in doc["categories"]}

    img_b = next(im for im in doc["images"] if im["id"] == s["b"].id)
    assert (img_b["width"], img_b["height"]) == (256, 256)
    assert img_b["vp_origin_level0"] == [3000, 4000] and img_b["vp_read_level"] == 1 and img_b["vp_downsample"] == 2.0
    assert img_b["coco_url"].startswith(f"/api/slides/{s['slide'].id}/patch?") and "level=1" in img_b["coco_url"]
    assert img_b["file_name"].endswith("_L1.png")


def test_coco_segmentation_is_patch_local_and_level0_is_carried_alongside(db):
    s = seed(db)
    doc = get_exporter("coco").export(db, s["slide"])
    by_id = {a["id"]: a for a in doc["annotations"]}

    tri = by_id[s["tri"].id]
    assert tri["segmentation"] == [[10, 10, 110, 10, 10, 60]]
    assert tri["area"] == 2500.0 and tri["bbox"] == [10, 10, 100, 50] and tri["iscrowd"] == 0
    assert tri["vp_level0_segmentation"] == [[1010, 2010, 1110, 2010, 1010, 2060]]
    assert tri["vp_unsure"] is True

    down = by_id[s["down"].id]
    assert down["segmentation"] == [[10, 10, 60, 10, 60, 60, 10, 60]]  # the patch image's own pixels
    assert down["area"] == 2500.0 and down["bbox"] == [10, 10, 50, 50]
    assert down["vp_level0_segmentation"] == [[3020, 4020, 3120, 4020, 3120, 4120, 3020, 4120]]  # 2x, plus origin
    assert down["vp_flagged"] is True


def test_coco_reports_what_it_could_not_represent(db):
    s = seed(db)
    doc = get_exporter("coco").export(db, s["slide"])
    ids = {a["id"] for a in doc["annotations"]}
    assert ids == {s[k].id for k in ("rect", "tri", "square", "bowtie", "down")}
    assert doc["info"]["vp_skipped"] == {"point_annotations": 1, "line_annotations": 0, "unclassified": 1, "degenerate_geometry": 1}
    assert doc["info"]["vp_config_version"] == "v1.0" and doc["info"]["vp_slide"]["width_level0"] == 100000


# ----------------------------------------------------------------- Patch CSV


ALL = ExportOptions(patch_scope="all")


def test_patch_csv_lists_every_patch_with_level0_coordinates(db):
    s = seed(db)
    text = get_exporter("patch_csv").export(db, s["slide"], ALL)
    assert text.splitlines()[0].split(",")[:7] == [
        "slide", "patch_id", "patch_index", "level0_x", "level0_y", "width_level0", "height_level0",
    ]  # fmt: skip
    out = rows(text)
    assert [r["patch_index"] for r in out] == ["0", "1", "2", "3"]  # excluded patch is listed too

    a, b, _c, _d = out
    assert (a["level0_x"], a["level0_y"], a["width_level0"], a["read_level"], a["width_px"]) == ("1000", "2000", "512", "0", "512")
    assert (b["level0_x"], b["read_level"], b["width_px"], b["width_level0"]) == ("3000", "1", "256", "512")
    assert a["tissue_fraction"] == "0.8123"
    assert a["slide"] == "Case 7 (final).svs"  # spaces and parentheses survive the round trip


def test_patch_csv_flags_counts_and_dominant_class(db):
    s = seed(db)
    a, b, c, d = rows(get_exporter("patch_csv").export(db, s["slide"], ALL))
    assert a["n_annotations"] == "7" and a["dominant_class"] == "Stroma"  # 10050 px2 beats Tumor's 7500
    assert b["dominant_class"] == "Tumor" and b["flagged"] == "false" and b["status"] == "reviewed" and b["patch_label"] == "Tumor"
    assert (c["excluded"], c["n_annotations"]) == ("true", "1")
    assert d["dominant_class"] == "" and d["n_annotations"] == "0" and d["flagged"] == "true" and d["unsure"] == "false"


def test_csv_text_that_looks_like_a_formula_is_neutralised(db):
    s = seed(db)
    d = rows(get_exporter("patch_csv").export(db, s["slide"], ALL))[3]
    assert d["notes"] == "'" + INJECTION  # would otherwise execute when opened in Excel
    # numbers (including negative ones) are never altered
    assert to_csv(["n", "t"], [[-5, "-cmd"]]) == "n,t\n-5,'-cmd\n"


# ----------------------------------------------------------------- Stats CSV


def by_class(text):
    return {r["class"]: r for r in rows(text)}


def test_stats_has_one_row_per_class_including_empty_and_unclassified(db):
    s = seed(db)
    stats = by_class(get_exporter("stats_csv").export(db, s["slide"]))
    assert list(stats) == ["Tumor", "Stroma", "Necrosis", "(unclassified)"]

    t = stats["Tumor"]
    assert (t["n_annotations"], t["n_polygons"], t["n_points"], t["n_patches"]) == ("5", "4", "1", "2")
    assert float(t["summed_area_px2"]) == 17500.0  # 5000 + 2500 + 10000 (Level-0 area of the downsampled patch)
    assert (t["n_unsure"], t["n_flagged"]) == ("1", "1")

    st = stats["Stroma"]
    assert st["n_annotations"] == "2" and float(st["summed_area_px2"]) == 10050.0

    n = stats["Necrosis"]
    assert (n["n_annotations"], n["n_patches"]) == ("0", "0") and float(n["summed_area_px2"]) == 0.0
    assert float(stats["(unclassified)"]["summed_area_px2"]) == 400.0


def test_stats_converts_to_mm2_with_slide_resolution_and_reports_percentages(db):
    s = seed(db)
    t = by_class(get_exporter("stats_csv").export(db, s["slide"]))["Tumor"]
    assert float(t["summed_area_mm2"]) == pytest.approx(17500 * 0.5 * 0.5 / 1e6)  # 0.004375 mm2
    assert float(t["mean_area_mm2"]) == pytest.approx(0.004375 / 4, abs=1e-6)
    assert float(t["pct_of_annotated_area"]) == pytest.approx(17500 / 27950 * 100, abs=0.001)
    assert float(t["pct_of_tissue_area"]) == pytest.approx(0.004375 / 10.0 * 100, abs=0.001)
    assert float(t["mpp_x"]) == 0.5 and float(t["slide_tissue_area_mm2"]) == 10.0


def test_stats_slide_level_counts_ignore_excluded_patches(db):
    s = seed(db)
    t = by_class(get_exporter("stats_csv").export(db, s["slide"]))["Tumor"]
    assert t["slide_patches"] == "3"  # a, b, d (c is excluded)
    assert (t["slide_annotated_patches"], t["slide_reviewed_patches"], t["slide_excluded_patches"]) == ("2", "1", "1")
    assert t["slide"] == "Case 7 (final).svs" and t["project_id"] == "FMT_TEST" and t["config_version"] == "v1.0"


def test_stats_leaves_physical_units_blank_when_resolution_is_unknown(db):
    s = seed(db)
    s["slide"].mpp_x = s["slide"].mpp_y = None
    db.commit()
    t = by_class(get_exporter("stats_csv").export(db, s["slide"]))["Tumor"]
    assert float(t["summed_area_px2"]) == 17500.0
    assert (t["summed_area_mm2"], t["mean_area_mm2"], t["pct_of_tissue_area"]) == ("", "", "")


# -------------------------------------------------------------- edge cases


def test_slide_with_no_active_config_still_exports_without_crashing(db):
    s = seed(db)
    s["slide"].active_config_version_id = None
    db.commit()
    for fmt in REGISTRY:
        exporter = get_exporter(fmt)
        assert exporter.render(exporter.export(db, s["slide"]))


def test_only_the_active_config_version_is_exported(db):
    s = seed(db)
    other = ProjectConfigVersion(
        project_id=s["config"].project_id, version_label="v2.0", status="draft", patch_width=256, patch_height=256,
        stride_x=256, stride_y=256, min_tissue_fraction=0.5,
    )  # fmt: skip
    db.add(other)
    db.flush()
    s["slide"].active_config_version_id = other.id
    db.commit()
    assert get_exporter("geojson").export(db, s["slide"])["features"] == []
    assert get_exporter("coco").export(db, s["slide"])["images"] == []
    assert rows(get_exporter("patch_csv").export(db, s["slide"], ALL)) == []
