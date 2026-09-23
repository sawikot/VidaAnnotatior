import os
import stat

from app.services.project_storage import project_dir, remove_orphaned_project_dirs, remove_tree


def _fill(directory):
    (directory / "slide-1").mkdir(parents=True)
    (directory / "slide-1" / "s.svs").write_bytes(b"x")
    (directory / "_masks").mkdir()
    (directory / "_masks" / "1_tissue_mask.png").write_bytes(b"x")


def test_remove_tree_deletes_read_only_files(tmp_path):
    target = project_dir(tmp_path, 7)
    _fill(target)
    locked = target / "slide-1" / "s.svs"
    os.chmod(locked, stat.S_IREAD)  # e.g. copied off a read-only scanner share

    assert remove_tree(target) is True
    assert not target.exists()


def test_remove_tree_of_a_missing_directory_is_fine(tmp_path):
    assert remove_tree(tmp_path / "nope") is True


def test_orphan_sweep_removes_only_directories_of_deleted_projects(tmp_path):
    tmp_path = tmp_path / "uploads"
    for pid in (1, 2, 3):
        _fill(project_dir(tmp_path, pid))
    (tmp_path / "_staging").mkdir()
    (tmp_path / ".gitkeep").write_text("")

    removed = remove_orphaned_project_dirs(tmp_path, existing_project_ids=[1, 3])

    assert [p.name for p in removed] == ["2"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [".gitkeep", "1", "3", "_staging"]
