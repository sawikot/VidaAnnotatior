"""Regression tests for a real data-loss bug: a test that deleted a project (id 1 in the in-memory
database) removed the developer's real ``data/uploads/1`` directory, holding real slides."""
from pathlib import Path

from app.api.projects import delete_project
from app.core.config import PROJECT_ROOT, get_settings
from tests.test_project_deletion import _seed_full_project


def test_tests_never_see_the_real_data_directory():
    settings = get_settings()
    real = (PROJECT_ROOT / "data").resolve()
    for directory in (settings.wsi_storage_dir, settings.wsi_watch_dir):
        assert real not in Path(directory).resolve().parents and Path(directory).resolve() != real


def test_deleting_project_one_removes_only_the_throwaway_directory(db):
    """The exact scenario that used to destroy real files: project id 1 is deleted."""
    real_slides = PROJECT_ROOT / "data" / "uploads" / "1"
    existed_before = real_slides.exists()

    project = _seed_full_project(db)
    assert project.id == 1
    fake_files = get_settings().wsi_storage_dir / "1" / "slide-abc"
    fake_files.mkdir(parents=True)
    (fake_files / "slide.svs").write_bytes(b"x")

    delete_project(project=project, db=db)

    assert not (get_settings().wsi_storage_dir / "1").exists()  # the throwaway copy is gone...
    assert real_slides.exists() == existed_before  # ...and the real directory was never involved
