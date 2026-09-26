"""Importing annotation files made elsewhere: every format is read into the same Level-0 entries,
labels are mapped to classes, and shapes are placed in the patch that contains them."""
import json

import pytest

from app.api.annotations import import_annotations
from app.models.annotation import GeometryAnnotation
from app.models.patch import Patch
from app.schemas.annotation import ImportAnnotationEntry, ImportAnnotationsRequest
from app.services.annotation_import import ImportFormatError, parse_annotation_file
from app.services.exporter.coco import COCOExporter
from app.services.exporter.geojson import GeoJSONExporter
from app.services.exporter.wsi_json import WSIJSONExporter
from tests.test_import_annotations import IMPORTER, _seed


def parse(content, filename="file", **kwargs):
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return parse_annotation_file(filename, raw, **kwargs)


def shapes(parsed):
    return [(e["type"], e["label"], e["coordinates"]) for e in parsed.entries]


# ------------------------------------------------------------------- GeoJSON


def test_qupath_geojson_polygons_points_and_lines():
    doc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[[10, 10], [60, 12], [40, 50], [10, 10]]]},
                "properties": {"objectType": "annotation", "classification": {"name": "Tumor", "color": [200, 0, 0]}},
            },
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [5, 6]}, "properties": {"classification": "Stroma"}},
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 0], [3, 4]]}, "properties": {}},
        ],
    }
    parsed = parse(json.dumps(doc))
    assert parsed.format == "geojson"
    assert shapes(parsed) == [
        ("polygon", "Tumor", [[10.0, 10.0], [60.0, 12.0], [40.0, 50.0]]),  # closing vertex dropped
        ("point", "Stroma", [[5.0, 6.0]]),
        ("line", None, [[0.0, 0.0], [3.0, 4.0]]),
    ]


def test_multipolygons_are_split_and_holes_are_dropped_with_a_warning():
    geom = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]], [[2, 2], [4, 2], [4, 4], [2, 2]]],
            [[[20, 20], [30, 20], [25, 30], [20, 20]]],
        ],
    }
    parsed = parse(json.dumps([{"type": "Feature", "geometry": geom, "properties": {"name": "Necrosis"}}]))
    assert [(t, label) for t, label, _ in shapes(parsed)] == [("rectangle", "Necrosis"), ("polygon", "Necrosis")]
    assert any("holes" in w for w in parsed.warnings)


def test_bad_geometry_is_counted_not_fatal():
    doc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": None, "properties": {}},
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [0, 0]]]}, "properties": {}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]}, "properties": {}},
    ]}
    parsed = parse(json.dumps(doc))
    assert len(parsed.entries) == 1
    assert sum(parsed.unreadable.values()) == 2


def test_scale_multiplies_coordinates_and_forgets_patch_origins():
    doc = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [10, 20]},
           "properties": {"drawn_in": "patch", "patch_x": 0, "patch_y": 0}}
    assert parse(json.dumps(doc)).entries[0]["source_patch"] == {"x": 0, "y": 0}
    scaled = parse(json.dumps(doc), scale=4).entries[0]
    assert scaled["coordinates"] == [[40.0, 80.0]] and scaled["source_patch"] is None


# ------------------------------------------------------------------- round trips of this app's exports


def _seed_with_shapes(db):
    project, config, slide, patch, tumor = _seed(db)
    import_annotations(
        ImportAnnotationsRequest(
            annotations=[
                ImportAnnotationEntry(type="polygon", label="Tumor", source_patch={"x": 20000, "y": 15000},
                                      coordinates=[[20100, 15080], [20300, 15090], [20350, 15250], [20150, 15300]]),
                ImportAnnotationEntry(type="circle", label="Tumor", source_patch={"x": 20000, "y": 15000},
                                      coordinates=[[20200, 15200], [20240, 15200]]),
                ImportAnnotationEntry(type="rectangle", label="Tumor", source_patch=None,
                                      coordinates=[[1000, 1000], [2000, 1000], [2000, 1500], [1000, 1500]]),
            ]
        ),
        slide=slide, db=db, user=IMPORTER,
    )
    return slide


@pytest.mark.parametrize("exporter", [WSIJSONExporter, GeoJSONExporter, COCOExporter])
def test_every_export_imports_back_as_duplicates(db, exporter):
    slide = _seed_with_shapes(db)
    doc = exporter().export(db, slide)
    parsed = parse(json.dumps(doc))
    assert {e["type"] for e in parsed.entries} >= {"polygon", "circle"}  # the circle is restored, not a 64-gon

    result = import_annotations(
        ImportAnnotationsRequest(annotations=parsed.entries), slide=slide, db=db, user=IMPORTER
    )
    assert result.imported == 0
    assert result.skipped_duplicate == len(parsed.entries)


# ------------------------------------------------------------------- COCO from elsewhere


def test_generic_coco_uses_tile_positions_from_file_names():
    doc = {
        "images": [{"id": 1, "file_name": "slide_x1000_y2000.png", "width": 256, "height": 256}],
        "categories": [{"id": 3, "name": "Tumor"}],
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 3, "segmentation": [[0, 0, 10, 0, 5, 8]], "bbox": [0, 0, 10, 8]},
            {"id": 2, "image_id": 1, "category_id": 3, "segmentation": {"counts": "xyz", "size": [256, 256]}, "bbox": [10, 20, 5, 5]},
        ],
    }
    parsed = parse(json.dumps(doc))
    assert parsed.format == "coco"
    assert shapes(parsed) == [
        ("polygon", "Tumor", [[1000.0, 2000.0], [1010.0, 2000.0], [1005.0, 2008.0]]),
        ("rectangle", "Tumor", [[1010.0, 2020.0], [1015.0, 2020.0], [1015.0, 2025.0], [1010.0, 2025.0]]),
    ]
    assert any("RLE" in w for w in parsed.warnings)


# ------------------------------------------------------------------- XML


def test_asap_xml():
    xml = """<?xml version="1.0"?>
<ASAP_Annotations><Annotations>
  <Annotation Name="A1" Type="Polygon" PartOfGroup="Tumor"><Coordinates>
    <Coordinate Order="1" X="20" Y="0"/><Coordinate Order="0" X="0" Y="0"/><Coordinate Order="2" X="10,5" Y="15"/>
  </Coordinates></Annotation>
  <Annotation Name="A2" Type="Dot" PartOfGroup="None"><Coordinates><Coordinate Order="0" X="7" Y="8"/></Coordinates></Annotation>
</Annotations></ASAP_Annotations>"""
    parsed = parse(xml)
    assert parsed.format == "asap_xml"
    assert shapes(parsed) == [
        ("polygon", "Tumor", [[0.0, 0.0], [20.0, 0.0], [10.5, 15.0]]),  # ordered by Order; decimal comma read
        ("point", None, [[7.0, 8.0]]),
    ]


def test_aperio_xml():
    xml = """<Annotations MicronsPerPixel="0.25">
  <Annotation Id="1" Name="Tumor"><Regions>
    <Region Id="1" Type="0"><Vertices><Vertex X="0" Y="0"/><Vertex X="10" Y="0"/><Vertex X="5" Y="9"/></Vertices></Region>
    <Region Id="2" Type="2"><Vertices><Vertex X="100" Y="100"/><Vertex X="140" Y="140"/></Vertices></Region>
    <Region Id="3" Type="0" NegativeROA="1"><Vertices><Vertex X="1" Y="1"/><Vertex X="2" Y="1"/><Vertex X="2" Y="2"/></Vertices></Region>
  </Regions></Annotation>
</Annotations>"""
    parsed = parse(xml)
    assert parsed.format == "aperio_xml"
    assert shapes(parsed) == [
        ("polygon", "Tumor", [[0.0, 0.0], [10.0, 0.0], [5.0, 9.0]]),
        ("circle", "Tumor", [[120.0, 120.0], [140.0, 120.0]]),
    ]
    assert parsed.unreadable["negative region"] == 1


# ------------------------------------------------------------------- CSV


def test_csv_points_boxes_and_wkt():
    points = parse("x,y,class\n10,20,Lymphocyte\n30,40,Tumor\n")
    assert shapes(points) == [("point", "Lymphocyte", [[10.0, 20.0]]), ("point", "Tumor", [[30.0, 40.0]])]

    boxes = parse("xmin\tymin\txmax\tymax\tlabel\n0\t0\t10\t5\tTumor\n", "boxes.tsv")
    assert shapes(boxes) == [("rectangle", "Tumor", [[0.0, 0.0], [10.0, 0.0], [10.0, 5.0], [0.0, 5.0]])]

    wkt = parse('label,wkt\nTumor,"POLYGON ((0 0, 8 0, 4 6, 0 0))"\n')
    assert shapes(wkt) == [("polygon", "Tumor", [[0.0, 0.0], [8.0, 0.0], [4.0, 6.0]])]


def test_csv_in_microns_needs_the_slide_pixel_size():
    table = "Class\tCentroid X µm\tCentroid Y µm\nTumor\t5\t10\n"
    parsed = parse(table, "cells.tsv", mpp=(0.5, 0.5))
    assert shapes(parsed) == [("point", "Tumor", [[10.0, 20.0]])]
    with pytest.raises(ImportFormatError):
        parse(table, "cells.tsv")


def test_unknown_content_is_refused_with_a_useful_message():
    with pytest.raises(ImportFormatError, match="Supported"):
        parse(json.dumps({"hello": "world"}))
    with pytest.raises(ImportFormatError, match="x and y"):
        parse("foo,bar\n1,2\n")


# ------------------------------------------------------------------- label mapping and placement


def _point(x, y, label=None):
    return ImportAnnotationEntry(type="point", label=label, coordinates=[[x, y]])


def test_label_map_assigns_skips_or_clears_classes(db):
    project, config, slide, patch, tumor = _seed(db)
    result = import_annotations(
        ImportAnnotationsRequest(
            annotations=[_point(10, 10, "tumour"), _point(20, 20, "Other"), _point(30, 30, "Cell"), _point(40, 40, "Nope"), _point(50, 50)],
            label_map={"tumour": tumor.id, "Other": "skip", "Cell": "unlabeled"},
        ),
        slide=slide, db=db, user=IMPORTER,
    )
    assert (result.imported, result.skipped_by_choice, result.skipped_unknown_class) == (3, 1, 1)
    classes = {tuple(a.coordinates_level0[0]): a.class_id for a in db.query(GeometryAnnotation)}
    assert classes == {(10.0, 10.0): tumor.id, (30.0, 30.0): None, (50.0, 50.0): None}


def test_label_map_rejects_a_class_of_another_configuration(db):
    from fastapi import HTTPException

    project, config, slide, patch, tumor = _seed(db)
    with pytest.raises(HTTPException):
        import_annotations(
            ImportAnnotationsRequest(annotations=[_point(1, 1, "x")], label_map={"x": 9999}), slide=slide, db=db, user=IMPORTER
        )


def test_assign_to_patches_places_shapes_in_the_containing_patch(db):
    project, config, slide, patch, tumor = _seed(db)  # patch at (20000, 15000), 512 px
    result = import_annotations(
        ImportAnnotationsRequest(
            annotations=[
                _point(20100, 15100, "Tumor"),  # inside the patch
                ImportAnnotationEntry(type="polygon", coordinates=[[20400, 15100], [20600, 15100], [20500, 15200]]),  # crosses its edge
                _point(500, 500),  # in no patch
                _point(999999, 5),  # off the slide
            ],
            assign_to_patches=True,
        ),
        slide=slide, db=db, user=IMPORTER,
    )
    assert (result.imported_to_patches, result.imported_on_slide, result.skipped_outside_slide) == (1, 2, 1)
    placed = db.query(GeometryAnnotation).filter(GeometryAnnotation.patch_id == patch.id).one()
    assert placed.coordinates_patch_local == [[100.0, 100.0]]
    db.refresh(patch)
    assert patch.status == "annotated"


def test_overlapping_patches_take_the_one_the_shape_is_most_central_in(db):
    project, config, slide, patch, tumor = _seed(db)
    neighbour = Patch(slide_id=slide.id, config_version_id=config.id, patch_index=246, x=20256, y=15000, level=0,
                      width=512, height=512, width_l0=512, height_l0=512, tissue_fraction=0.9)
    db.add(neighbour)
    db.commit()
    result = import_annotations(
        ImportAnnotationsRequest(annotations=[_point(20400, 15256)], assign_to_patches=True), slide=slide, db=db, user=IMPORTER
    )
    assert result.imported_to_patches == 1
    assert db.query(GeometryAnnotation).one().patch_id == neighbour.id


# ------------------------------------------------------------------- Cytomine


def _cytomine(location, term):
    return {"id": 1, "image": 5, "location": location, "term": term, "centroid": {"x": 0, "y": 0}}


def test_cytomine_flips_y_and_matches_terms_to_class_ids():
    doc = [
        _cytomine("POLYGON ((100 900, 200 900, 200 800, 100 800, 100 900))", [50581, 904558]),
        _cytomine("POINT (10 990)", [903887]),
        _cytomine("POLYGON ((0 0, 10 0, 5 5, 0 0))", [777]),
        _cytomine("POLYGON ((0 0, 10 0, 5 5, 0 0))", []),
    ]
    # Class order decides between several matching terms: 904558's class is listed first.
    codes = [(904558, "Grade A"), (50581, "Tumor"), (903887, "Mitosis")]
    parsed = parse(json.dumps(doc), slide_size=(1000, 1000), class_codes=codes)
    assert parsed.format == "cytomine"
    assert shapes(parsed) == [
        ("rectangle", "Grade A", [[100.0, 100.0], [200.0, 100.0], [200.0, 200.0], [100.0, 200.0]]),  # y = 1000 - y
        ("point", "Mitosis", [[10.0, 10.0]]),
        ("polygon", "Term 777", [[0.0, 1000.0], [10.0, 1000.0], [5.0, 995.0]]),
        ("polygon", "No term", [[0.0, 1000.0], [10.0, 1000.0], [5.0, 995.0]]),
    ]
    assert any("several classes" in w for w in parsed.warnings)
    assert any("777 (1)" in w for w in parsed.warnings)


def test_cytomine_needs_the_slide_height():
    with pytest.raises(ImportFormatError, match="height"):
        parse(json.dumps([_cytomine("POINT (1 1)", [])]))


def test_class_ids_of_any_size_are_kept_and_must_be_unique(db):
    from app.schemas.config_version import AnnotationClassSync
    from app.services.config_versioning import ClassSyncError, sync_annotation_classes

    project, config, slide, patch, tumor = _seed(db)
    sync_annotation_classes(db, config, [AnnotationClassSync(id=tumor.id, name="Tumor", code=9045581234)])
    db.commit()
    db.refresh(tumor)
    assert tumor.code == 9045581234
    with pytest.raises(ClassSyncError, match="unique"):
        sync_annotation_classes(db, config, [AnnotationClassSync(id=tumor.id, name="Tumor", code=5), AnnotationClassSync(name="B", code=5)])


# ------------------------------------------------------------------- scale


def test_scale_is_worked_out_from_a_whole_slide_coco_image_and_otherwise_1():
    doc = {
        "images": [{"id": 1, "file_name": "thumb.png", "width": 250, "height": 200}],
        "categories": [{"id": 1, "name": "Tumor"}],
        "annotations": [{"id": 1, "image_id": 1, "category_id": 1, "segmentation": [[10, 10, 20, 10, 15, 20]]}],
    }
    auto = parse(json.dumps(doc), slide_size=(1000, 800))
    assert auto.scale == 4 and auto.scale_note and auto.entries[0]["coordinates"][0] == [40.0, 40.0]
    manual = parse(json.dumps(doc), scale=1, slide_size=(1000, 800))  # a given scale is used as it is
    assert manual.scale == 1 and manual.scale_note is None

    geojson = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [10, 20]}, "properties": {}}
    plain = parse(json.dumps(geojson), slide_size=(1000, 800))
    assert plain.scale == 1 and plain.auto_scale and plain.entries[0]["coordinates"] == [[10.0, 20.0]]
