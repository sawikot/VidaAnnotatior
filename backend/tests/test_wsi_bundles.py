import stat
import zipfile
from pathlib import Path

import pytest

from app.services.wsi_bundles import (
    ArchiveError,
    UnsafePathError,
    discover_bundles,
    extract_zip,
    install_bundle,
    is_junk,
    safe_parts,
    slide_dir_name,
)


def always_a_slide(_path: str) -> str:
    return "test-format"


def touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------- safe_parts


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Slide.svs", ("Slide.svs",)),
        ("Case 1/Slide.mrxs", ("Case 1", "Slide.mrxs")),
        ("Slide/Data0000.dat", ("Slide", "Data0000.dat")),
        ("a\\b\\c.tif", ("a", "b", "c.tif")),  # zips made on Windows
        ("./a//b.tif", ("a", "b.tif")),
        ("CMU-1(0,0).jpg", ("CMU-1(0,0).jpg",)),  # VMS tile names must survive untouched
    ],
)
def test_safe_parts_accepts_normal_paths_unchanged(raw, expected):
    assert safe_parts(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "/etc/passwd",
        "../evil.svs",
        "a/../../evil.svs",
        "a/../b.svs",
        "C:\\Windows\\x.svs",
        "bad|name.svs",
        "what?.svs",
        "trailing. ",
        "nul.svs",
        "COM1",
        "a\x00b.svs",
        "/".join(["d"] * 40) + "/x.svs",
    ],
)
def test_safe_parts_rejects_dangerous_paths(raw):
    with pytest.raises(UnsafePathError):
        safe_parts(raw)


def test_is_junk():
    assert is_junk("__MACOSX/Slide/._Data.dat")
    assert is_junk("folder/.DS_Store")
    assert is_junk("Thumbs.db")
    assert is_junk("._Slide.svs")
    assert not is_junk("Slide.svs")


# ---------------------------------------------------------------- extract_zip


def make_zip(path: Path, entries: dict[str, bytes], extra=None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        if extra:
            extra(zf)
    return path


def test_extract_zip_unpacks_nested_files(tmp_path):
    z = make_zip(tmp_path / "a.zip", {"one/two/a.svs": b"A", "b.ndpi": b"B"})
    warnings = extract_zip(z, tmp_path / "out", max_bytes=10**6, max_files=100)
    assert warnings == []
    assert (tmp_path / "out/one/two/a.svs").read_bytes() == b"A"
    assert (tmp_path / "out/b.ndpi").read_bytes() == b"B"


def test_extract_zip_blocks_zip_slip_and_absolute_paths(tmp_path):
    z = make_zip(tmp_path / "evil.zip", {"../escaped.txt": b"!", "/abs.txt": b"!", "ok/../../up.txt": b"!", "fine.svs": b"ok"})
    out = tmp_path / "deep" / "out"
    warnings = extract_zip(z, out, max_bytes=10**6, max_files=100)
    assert len(warnings) == 3
    assert (out / "fine.svs").exists()
    # Nothing may appear anywhere outside the destination.
    assert not (tmp_path / "escaped.txt").exists() and not (tmp_path / "deep" / "escaped.txt").exists()
    assert not (tmp_path / "up.txt").exists() and not (tmp_path / "deep" / "up.txt").exists()
    assert sorted(p.name for p in out.rglob("*") if p.is_file()) == ["fine.svs"]


def test_extract_zip_skips_symlinks_and_junk(tmp_path):
    def add_symlink(zf):
        info = zipfile.ZipInfo("link.svs")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")

    z = make_zip(tmp_path / "s.zip", {"__MACOSX/._a": b"j", ".DS_Store": b"j", "real.svs": b"r"}, extra=add_symlink)
    warnings = extract_zip(z, tmp_path / "out", max_bytes=10**6, max_files=100)
    assert any("symbolic link" in w for w in warnings)
    assert sorted(p.name for p in (tmp_path / "out").rglob("*") if p.is_file()) == ["real.svs"]


def test_extract_zip_size_and_count_limits(tmp_path):
    big = make_zip(tmp_path / "big.zip", {"a.svs": b"x" * 5000})
    with pytest.raises(ArchiveError, match="limit"):
        extract_zip(big, tmp_path / "o1", max_bytes=1000, max_files=10)

    many = make_zip(tmp_path / "many.zip", {f"f{i}.dat": b"x" for i in range(11)})
    with pytest.raises(ArchiveError, match="contains 11 files"):
        extract_zip(many, tmp_path / "o2", max_bytes=10**6, max_files=10)


def test_extract_zip_rejects_non_zip(tmp_path):
    bad = touch(tmp_path / "bad.zip", b"definitely not a zip")
    with pytest.raises(ArchiveError, match="not a valid zip"):
        extract_zip(bad, tmp_path / "o", max_bytes=10**6, max_files=10)


# ---------------------------------------------------------------- discovery


def names(discovery):
    return sorted(b.name for b in discovery.bundles)


def test_single_file_formats_are_each_a_slide(tmp_path):
    for n in ["a.svs", "b.ndpi", "c.scn", "d.bif", "e.svslide", "f.tif", "g.TIFF"]:
        touch(tmp_path / n)
    result = discover_bundles(tmp_path, detect=always_a_slide)
    assert names(result) == ["a.svs", "b.ndpi", "c.scn", "d.bif", "e.svslide", "f.tif", "g.TIFF"]
    assert result.skipped == []


def test_mrxs_bundle_includes_its_data_folder_and_keeps_layout(tmp_path):
    src = tmp_path / "src"
    touch(src / "Case/Slide1.mrxs")
    touch(src / "Case/Slide1/Slidedat.ini", b"[GENERAL]")
    touch(src / "Case/Slide1/Index.dat")
    touch(src / "Case/Slide1/Data0000.dat")
    touch(src / "Case/Other.mrxs")  # a second slide with no folder
    result = discover_bundles(src, detect=always_a_slide)

    assert names(result) == ["Slide1.mrxs"]
    bundle = result.bundles[0]
    assert {m.name for m in bundle.members} == {"Slide1.mrxs", "Slidedat.ini", "Index.dat", "Data0000.dat"}
    assert any("Other.mrxs" == s.name and "data folder" in s.reason for s in result.skipped)

    dest = tmp_path / "installed"
    primary = install_bundle(bundle, dest, move=False)
    assert primary == dest / "Slide1.mrxs"
    assert (dest / "Slide1" / "Slidedat.ini").exists() and (dest / "Slide1" / "Data0000.dat").exists()
    assert (src / "Case/Slide1/Index.dat").exists()  # copy leaves the source alone


def test_mrxs_folder_match_is_case_insensitive(tmp_path):
    touch(tmp_path / "SLIDE.mrxs")
    touch(tmp_path / "slide/Slidedat.ini")
    assert names(discover_bundles(tmp_path, detect=always_a_slide)) == ["SLIDE.mrxs"]


def test_mrxs_without_slidedat_ini_is_skipped_with_reason(tmp_path):
    touch(tmp_path / "S.mrxs")
    touch(tmp_path / "S/Data0000.dat")
    result = discover_bundles(tmp_path, detect=always_a_slide)
    assert result.bundles == [] and "Slidedat.ini" in result.skipped[0].reason


def test_files_inside_a_slides_data_folder_are_not_separate_slides(tmp_path):
    touch(tmp_path / "S.mrxs")
    touch(tmp_path / "S/Slidedat.ini")
    touch(tmp_path / "S/preview.tif")
    assert names(discover_bundles(tmp_path, detect=always_a_slide)) == ["S.mrxs"]


def test_vms_needs_and_collects_its_tile_files(tmp_path):
    touch(tmp_path / "CMU-1.vms")
    touch(tmp_path / "CMU-1(0,0).jpg")
    touch(tmp_path / "CMU-1.opt")
    touch(tmp_path / "unrelated.jpg")
    touch(tmp_path / "LONE.vms")
    result = discover_bundles(tmp_path, detect=always_a_slide)
    assert names(result) == ["CMU-1.vms"]
    assert {m.name for m in result.bundles[0].members} == {"CMU-1.vms", "CMU-1(0,0).jpg", "CMU-1.opt"}
    assert any(s.name == "LONE.vms" for s in result.skipped)


def test_unrecognized_files_are_skipped_not_imported(tmp_path):
    touch(tmp_path / "mask.tif")
    touch(tmp_path / "good.svs")
    result = discover_bundles(tmp_path, detect=lambda p: None if p.endswith("mask.tif") else "aperio")
    assert names(result) == ["good.svs"]
    assert "plain TIFFs" in result.skipped[0].reason


def test_nested_archives_are_reported_and_other_files_counted_as_ignored(tmp_path):
    touch(tmp_path / "inner.zip")
    touch(tmp_path / "s.svs")
    touch(tmp_path / "readme.txt")
    touch(tmp_path / "notes.docx")
    result = discover_bundles(tmp_path, detect=always_a_slide)
    assert names(result) == ["s.svs"]
    assert any(s.name == "inner.zip" and "nested archive" in s.reason for s in result.skipped)
    assert result.ignored_files == 2


def test_only_mode_picks_up_companions_but_not_neighbouring_slides(tmp_path):
    touch(tmp_path / "A.mrxs")
    touch(tmp_path / "A/Slidedat.ini")
    touch(tmp_path / "B.svs")
    result = discover_bundles(tmp_path, only=tmp_path / "A.mrxs", detect=always_a_slide)
    assert names(result) == ["A.mrxs"] and {m.name for m in result.bundles[0].members} == {"A.mrxs", "Slidedat.ini"}


def test_install_move_removes_source_and_dir_names_are_unique_and_safe(tmp_path):
    src = touch(tmp_path / "in" / "x.svs")
    bundle = discover_bundles(tmp_path / "in", detect=always_a_slide).bundles[0]
    install_bundle(bundle, tmp_path / "out", move=True)
    assert not src.exists() and (tmp_path / "out" / "x.svs").exists()

    a, b = slide_dir_name("My Slide (v2)"), slide_dir_name("My Slide (v2)")
    assert a != b and a.startswith("My_Slide_v2") and "/" not in a and " " not in a
