from app.api.projects import delete_project
from app.models.annotation import GeometryAnnotation
from app.models.config_version import AnnotationClass, ProjectConfigVersion
from app.models.patch import Patch
from app.models.project import Project
from app.models.slide import Slide
from app.services.config_versioning import fork_config


def _seed_full_project(db):
    """A project with everything that has a foreign key into
    project_config_versions: slides, patches, annotations, annotation
    classes, and a forked (parent/child) config version -- the shape that
    triggered the FK-ordering bug."""
    project = Project(slug="BCA_2026", name="Breast Cancer Annotation")
    db.add(project)
    db.flush()

    config = ProjectConfigVersion(
        project_id=project.id, version_label="v1.0", status="locked",
        patch_width=512, patch_height=512, stride_x=512, stride_y=512, min_tissue_fraction=0.6,
    )
    db.add(config)
    db.flush()
    tumor = AnnotationClass(config_version_id=config.id, name="Tumor", color_hex="#dc2626")
    db.add(tumor)
    db.flush()

    # A forked child config version (parent_version_id self-FK).
    fork_config(db, config, {}, "v2.0")
    db.flush()

    slide = Slide(
        project_id=project.id, filename="Patient_001.svs", source_type="upload",
        width_l0=10000, height_l0=10000, active_config_version_id=config.id,
    )
    db.add(slide)
    db.flush()

    patch = Patch(
        slide_id=slide.id, config_version_id=config.id, patch_index=0,
        x=0, y=0, level=0, width=512, height=512, width_l0=512, height_l0=512,
    )
    db.add(patch)
    db.flush()

    db.add(
        GeometryAnnotation(
            patch_id=patch.id, slide_id=slide.id, config_version_id=config.id, class_id=tumor.id,
            type="polygon",
            coordinates_patch_local=[[0, 0], [10, 0], [10, 10]],
            coordinates_level0=[[0, 0], [10, 0], [10, 10]],
        )
    )
    project.active_config_version_id = config.id
    db.commit()
    return project


def test_deleting_a_project_with_full_graph_does_not_violate_foreign_keys(db):
    project = _seed_full_project(db)
    project_id = project.id

    # The regression: this used to raise sqlite3.IntegrityError (FOREIGN KEY
    # constraint failed) because Patch.config_version_id and similar columns
    # aren't ORM relationships, so cascade delete-ordering missed them.
    delete_project(project=project, db=db)

    assert db.get(Project, project_id) is None
    assert db.query(ProjectConfigVersion).filter(ProjectConfigVersion.project_id == project_id).count() == 0
    assert db.query(Slide).filter(Slide.project_id == project_id).count() == 0
    assert db.query(Patch).count() == 0
    assert db.query(GeometryAnnotation).count() == 0
    assert db.query(AnnotationClass).count() == 0
