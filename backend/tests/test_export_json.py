from app.models.annotation import GeometryAnnotation
from app.models.config_version import AnnotationClass, ProjectConfigVersion
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide
from app.services.exporter import get_exporter


def _seed_project_with_annotation(db):
    project = Project(slug="BCA_2026", name="Breast Cancer Annotation")
    db.add(project)
    db.flush()

    config = ProjectConfigVersion(
        project_id=project.id,
        version_label="v1.0",
        status="locked",
        patch_width=512,
        patch_height=512,
        stride_x=512,
        stride_y=512,
        min_tissue_fraction=0.6,
    )
    db.add(config)
    db.flush()

    tumor = AnnotationClass(config_version_id=config.id, name="Tumor", color_hex="#dc2626", order_index=0)
    db.add(tumor)
    db.flush()

    slide = Slide(
        project_id=project.id,
        filename="Patient_001.svs",
        source_type="upload",
        width_l0=100000,
        height_l0=80000,
        mpp_x=0.25,
        mpp_y=0.25,
        active_config_version_id=config.id,
    )
    db.add(slide)
    db.flush()

    patch = Patch(
        slide_id=slide.id,
        config_version_id=config.id,
        patch_index=245,
        x=20000,
        y=15000,
        level=0,
        width=512,
        height=512,
        width_l0=512,
        height_l0=512,
        tissue_fraction=0.87,
        status="annotated",
    )
    db.add(patch)
    db.flush()

    coords_l0 = [[20100, 15080], [20300, 15090], [20350, 15250], [20150, 15300]]
    ann = GeometryAnnotation(
        patch_id=patch.id,
        slide_id=slide.id,
        config_version_id=config.id,
        class_id=tumor.id,
        type="polygon",
        coordinates_patch_local=[[100, 80], [300, 90], [350, 250], [150, 300]],
        coordinates_level0=coords_l0,
    )
    db.add(ann)
    db.commit()
    return slide, patch, ann


def test_wsi_json_export_matches_spec_schema(db):
    slide, patch, ann = _seed_project_with_annotation(db)
    exporter = get_exporter("wsi_json")
    result = exporter.export(db, slide)

    assert result["schema_version"] == "1.0"
    assert result["project"]["project_id"] == "BCA_2026"
    assert result["slide"]["filename"] == "Patient_001.svs"
    assert result["slide"]["coordinate_level"] == 0
    assert result["patch_configuration"]["width"] == 512

    assert len(result["annotations"]) == 1
    out_ann = result["annotations"][0]
    assert out_ann["type"] == "polygon"
    assert out_ann["label"] == "Tumor"
    assert out_ann["source_patch"]["patch_id"] == patch.id
    assert out_ann["source_patch"]["x"] == 20000
    assert out_ann["source_patch"]["y"] == 15000
    # This is the crux of the whole spec: exported coordinates must be Level-0 absolute.
    assert out_ann["coordinates"] == [[20100, 15080], [20300, 15090], [20350, 15250], [20150, 15300]]


def test_excluded_patches_are_left_out_of_export(db):
    slide, patch, ann = _seed_project_with_annotation(db)
    patch.excluded = True
    db.commit()

    exporter = get_exporter("wsi_json")
    result = exporter.export(db, slide)
    assert result["annotations"] == []


def test_unimplemented_exporters_raise_not_implemented(db):
    slide, _, _ = _seed_project_with_annotation(db)
    for fmt in ("geojson", "coco", "patch_csv", "stats_csv"):
        exporter = get_exporter(fmt)
        try:
            exporter.export(db, slide)
            assert False, f"{fmt} should not be implemented yet"
        except NotImplementedError:
            pass
