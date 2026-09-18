from app.api.annotations import _coords_close, import_annotations
from app.models.annotation import GeometryAnnotation
from app.models.config_version import AnnotationClass, ProjectConfigVersion
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide
from app.schemas.annotation import ImportAnnotationEntry, ImportAnnotationsRequest


def _seed(db):
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

    tumor = AnnotationClass(config_version_id=config.id, name="Tumor", color_hex="#dc2626")
    db.add(tumor)
    db.flush()

    slide = Slide(
        project_id=project.id,
        filename="Patient_001.svs",
        source_type="upload",
        width_l0=100000,
        height_l0=80000,
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
        status="unannotated",
    )
    db.add(patch)
    db.commit()
    return project, config, slide, patch, tumor


def _entry(x=20000, y=15000, label="Tumor", ann_type="polygon", coords=None):
    return ImportAnnotationEntry(
        type=ann_type,
        label=label,
        source_patch={"patch_id": 1, "x": x, "y": y, "width": 512, "height": 512, "level": 0},
        coordinates=coords or [[20100.0, 15080.0], [20300.0, 15090.0], [20350.0, 15250.0], [20150.0, 15300.0]],
    )


def test_import_creates_annotation_at_correct_local_coords(db):
    project, config, slide, patch, tumor = _seed(db)
    result = import_annotations(
        ImportAnnotationsRequest(annotations=[_entry()]), slide=slide, db=db
    )

    assert result.imported == 1
    assert result.skipped_no_matching_patch == 0
    assert result.skipped_unknown_class == 0

    ann = db.query(GeometryAnnotation).filter(GeometryAnnotation.patch_id == patch.id).one()
    assert ann.class_id == tumor.id
    # Level-0 (20100, 15080) at patch origin (20000, 15000), downsample 1 -> local (100, 80)
    assert ann.coordinates_patch_local[0] == [100.0, 80.0]
    db.refresh(patch)
    assert patch.status == "annotated"


def test_import_skips_annotation_with_no_matching_patch(db):
    project, config, slide, patch, tumor = _seed(db)
    result = import_annotations(
        ImportAnnotationsRequest(annotations=[_entry(x=99999, y=99999)]), slide=slide, db=db
    )
    assert result.imported == 0
    assert result.skipped_no_matching_patch == 1


def test_import_skips_unknown_class_label(db):
    project, config, slide, patch, tumor = _seed(db)
    result = import_annotations(
        ImportAnnotationsRequest(annotations=[_entry(label="Necrosis")]), slide=slide, db=db
    )
    assert result.imported == 0
    assert result.skipped_unknown_class == 1


def test_reimporting_the_same_file_is_a_noop(db):
    project, config, slide, patch, tumor = _seed(db)
    entry = _entry()
    first = import_annotations(ImportAnnotationsRequest(annotations=[entry]), slide=slide, db=db)
    assert first.imported == 1

    second = import_annotations(ImportAnnotationsRequest(annotations=[entry]), slide=slide, db=db)
    assert second.imported == 0
    assert second.skipped_duplicate == 1
    assert db.query(GeometryAnnotation).filter(GeometryAnnotation.patch_id == patch.id).count() == 1


def test_coords_close_helper():
    a = [[10.0, 20.0], [30.0, 40.0]]
    assert _coords_close(a, [[10.2, 19.9], [30.1, 40.0]])
    assert not _coords_close(a, [[10.0, 20.0]])
    assert not _coords_close(a, [[15.0, 20.0], [30.0, 40.0]])
