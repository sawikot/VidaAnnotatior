"""End-to-end import tests: real HTTP multipart requests against the real app,
with real (tiny) tiled pyramidal TIFFs that OpenSlide genuinely opens. Only the
storage/database locations are redirected to a temp directory."""
import io
import json
import zipfile

import numpy as np
import pytest
import tifffile
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.database.base import Base
from app.database.session import get_db
from app.main import app
from app.models import annotation, config_version, patch, project, slide  # noqa: F401
from app.services import reader_cache

WIDTH, HEIGHT = 1536, 1024


def _tiled_pyramid_tiff() -> bytes:
    """A smooth 3-level tiled RGB TIFF -- the 'generic tiled TIFF' OpenSlide reads."""
    ramp = np.linspace(0, 255, WIDTH, dtype=np.uint8)
    level = np.stack([np.tile(ramp, (HEIGHT, 1))] * 3, axis=-1)
    buf = io.BytesIO()
    with tifffile.TiffWriter(buf) as tw:
        for i in range(3):
            tw.write(level, tile=(256, 256), photometric="rgb", compression="deflate", subfiletype=0 if i == 0 else 1)
            level = level[::2, ::2]
    return buf.getvalue()


def _plain_tiff() -> bytes:
    buf = io.BytesIO()
    tifffile.imwrite(buf, np.zeros((64, 64, 3), dtype=np.uint8))  # not tiled/pyramidal -> not a slide
    return buf.getvalue()


SLIDE = _tiled_pyramid_tiff()
PLAIN = _plain_tiff()


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    settings = Settings(
        wsi_storage_dir=tmp_path / "uploads",
        wsi_watch_dir=tmp_path / "watch",
        database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}",
    )
    settings.wsi_storage_dir.mkdir()
    settings.wsi_watch_dir.mkdir()
    for module in ("app.api.slides", "app.services.reader_cache", "app.api.projects"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)

    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):  # noqa: ANN001
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)  # not used as a context manager: startup hooks (real DB) don't run
    project_id = client.post("/api/projects", json={"name": "Import Test"}).json()["id"]
    yield client, project_id, settings
    for sid in list(reader_cache._cache):
        reader_cache.invalidate(sid)
    app.dependency_overrides.clear()
    engine.dispose()


def upload(client, project_id, files, manifest=None):
    parts = [("files", (name, data, "application/octet-stream")) for name, data in files]
    data = {"manifest": json.dumps(manifest)} if manifest is not None else None
    return client.post(f"/api/projects/{project_id}/slides/upload", files=parts, data=data)


def stored_files(settings: Settings, project_id: int) -> list[str]:
    root = settings.wsi_storage_dir / str(project_id)
    return sorted(p.relative_to(settings.wsi_storage_dir).as_posix() for p in root.rglob("*") if p.is_file()) if root.exists() else []


def no_staging_left(settings: Settings) -> bool:
    staging = settings.wsi_storage_dir / "_staging"
    return not staging.exists() or not any(staging.iterdir())


# ------------------------------------------------------------------ uploads


def test_formats_endpoint_lists_mrxs_and_zip(env):
    client, _, _ = env
    body = client.get("/api/wsi-formats").json()
    exts = {f["extension"] for f in body["formats"]}
    assert {".svs", ".tif", ".tiff", ".ndpi", ".mrxs", ".vms", ".vmu", ".scn", ".bif", ".svslide"} <= exts
    assert body["archives"] == [".zip"]


def test_single_file_upload_imports_a_real_readable_slide(env):
    client, pid, settings = env
    res = upload(client, pid, [("Case1.tif", SLIDE)])
    assert res.status_code == 200
    body = res.json()
    assert body["skipped"] == [] and len(body["slides"]) == 1
    s = body["slides"][0]
    assert (s["filename"], s["width_l0"], s["height_l0"], s["level_count"]) == ("Case1.tif", WIDTH, HEIGHT, 3)
    assert s["source_type"] == "upload" and s["status"] == "imported"

    files = stored_files(settings, pid)
    assert len(files) == 1 and files[0].startswith(f"{pid}/Case1-") and files[0].endswith("/Case1.tif")
    assert no_staging_left(settings)
    # The stored copy really is served through OpenSlide.
    assert client.get(f"/api/slides/{s['id']}/thumbnail").status_code == 200
    assert client.get(f"/api/slides/{s['id']}/patch?x=0&y=0&width=64&height=64&level=0").status_code == 200


def test_uploaded_slide_serves_viewer_tiles(env):
    """The OpenSeadragon viewer needs the DZI descriptor and tiles; a slide that
    imports but can't be viewed is useless (this once broke on a missing import)."""
    client, pid, _ = env
    slide_id = upload(client, pid, [("v.tif", SLIDE)]).json()["slides"][0]["id"]

    dzi = client.get(f"/api/slides/{slide_id}/dzi.dzi")
    assert dzi.status_code == 200 and f'Width="{WIDTH}" Height="{HEIGHT}"' in dzi.text
    for level in (0, 6, 11):
        tile = client.get(f"/api/slides/{slide_id}/dzi_files/{level}/0_0.jpeg")
        assert tile.status_code == 200 and tile.headers["content-type"] == "image/jpeg" and tile.content[:2] == b"\xff\xd8"  # JPEG start-of-image marker
    assert client.get(f"/api/slides/{slide_id}/dzi_files/11/not-a-tile.jpeg").status_code == 400


def test_multiple_files_selected_at_once_all_import(env):
    client, pid, settings = env
    body = upload(client, pid, [("a.tif", SLIDE), ("b.tiff", SLIDE), ("notes.txt", b"hello")]).json()
    assert sorted(s["filename"] for s in body["slides"]) == ["a.tif", "b.tiff"]
    assert body["ignored_file_count"] == 1 and body["skipped"] == []
    assert len({stored.rsplit("/", 1)[0] for stored in stored_files(settings, pid)}) == 2  # own directory each


def test_zip_with_several_slides_imports_every_slide_and_reports_the_rest(env):
    client, pid, settings = env
    archive = zip_bytes(
        {
            "batch/patient1.tif": SLIDE,
            "batch/deep/patient2.tiff": SLIDE,
            "batch/mask.tif": PLAIN,
            "batch/readme.txt": b"hi",
            "__MACOSX/batch/._patient1.tif": b"junk",
        }
    )
    body = upload(client, pid, [("cohort.zip", archive)]).json()
    assert sorted(s["filename"] for s in body["slides"]) == ["patient1.tif", "patient2.tiff"]
    assert [s["name"] for s in body["skipped"]] == ["mask.tif"]
    assert "plain TIFFs" in body["skipped"][0]["reason"]
    assert body["ignored_file_count"] == 1
    assert no_staging_left(settings)


def test_zip_and_loose_files_can_be_mixed_in_one_request(env):
    client, pid, _ = env
    body = upload(client, pid, [("one.zip", zip_bytes({"a.tif": SLIDE})), ("loose.tif", SLIDE)]).json()
    assert sorted(s["filename"] for s in body["slides"]) == ["a.tif", "loose.tif"]


def test_two_zips_with_identical_inner_paths_do_not_collide(env):
    client, pid, _ = env
    same = zip_bytes({"slide.tif": SLIDE})
    body = upload(client, pid, [("z1.zip", same), ("z2.zip", same)]).json()
    assert [s["filename"] for s in body["slides"]] == ["slide.tif", "slide.tif"]


def test_folder_upload_keeps_structure_via_manifest(env):
    client, pid, _ = env
    body = upload(
        client, pid, [("a.tif", SLIDE), ("b.tiff", SLIDE)], manifest=["MyFolder/a.tif", "MyFolder/sub/b.tiff"]
    ).json()
    assert sorted(s["filename"] for s in body["slides"]) == ["a.tif", "b.tiff"]


def test_mrxs_without_its_folder_is_refused_with_actionable_advice(env):
    client, pid, settings = env
    body = upload(client, pid, [("Slide1.mrxs", b"stub")]).json()
    assert body["slides"] == []
    assert "data folder" in body["skipped"][0]["reason"] and "zip" in body["skipped"][0]["reason"]
    assert stored_files(settings, pid) == []


def test_mrxs_with_folder_reaches_openslide_and_leaves_nothing_behind_when_unreadable(env):
    """The layout is accepted (folder found), OpenSlide gets to judge the stub
    files and rejects them, and the failed import leaves no slide and no files."""
    client, pid, settings = env
    body = upload(
        client,
        pid,
        [("Slide1.mrxs", b"stub"), ("Slidedat.ini", b"[GENERAL]"), ("Index.dat", b"x")],
        manifest=["Case/Slide1.mrxs", "Case/Slide1/Slidedat.ini", "Case/Slide1/Index.dat"],
    ).json()
    assert body["slides"] == []
    assert "not recognized" in body["skipped"][0]["reason"] or "Failed to read" in body["skipped"][0]["reason"]
    assert client.get(f"/api/projects/{pid}/slides").json() == []
    assert stored_files(settings, pid) == [] and no_staging_left(settings)


def test_unsafe_names_and_duplicates_are_skipped_while_good_files_still_import(env):
    client, pid, _ = env
    body = upload(
        client, pid, [("evil.tif", SLIDE), ("ok.tif", SLIDE), ("dup.tif", SLIDE), ("dup.tif", SLIDE)],
        manifest=["../../evil.tif", "ok.tif", "d/dup.tif", "D/DUP.TIF"],
    ).json()
    assert [s["filename"] for s in body["slides"]] == ["dup.tif", "ok.tif"]
    reasons = {s["name"]: s["reason"] for s in body["skipped"]}
    assert "unsafe" in reasons["../../evil.tif"] and "twice" in reasons["D/DUP.TIF"]


def test_corrupt_zip_is_reported_but_other_files_still_import(env):
    client, pid, _ = env
    body = upload(client, pid, [("broken.zip", b"not really a zip"), ("fine.tif", SLIDE)]).json()
    assert [s["filename"] for s in body["slides"]] == ["fine.tif"]
    assert body["skipped"][0]["name"] == "broken.zip" and "not a valid zip" in body["skipped"][0]["reason"]


def test_upload_with_nothing_slide_like_reports_why(env):
    client, pid, _ = env
    body = upload(client, pid, [("a.txt", b"hi"), ("mask.tif", PLAIN)]).json()
    assert body["slides"] == [] and body["skipped"][0]["name"] == "mask.tif" and body["ignored_file_count"] == 1


def test_request_errors(env):
    client, pid, settings = env
    assert client.post(f"/api/projects/{pid}/slides/upload").status_code == 422
    assert upload(client, pid, [("a.tif", SLIDE)], manifest=["x.tif", "y.tif"]).status_code == 422
    assert client.post(f"/api/projects/{pid}/slides/upload", files=[("files", ("a.tif", SLIDE))], data={"manifest": "{not json"}).status_code == 422
    assert client.post("/api/projects/9999/slides/upload", files=[("files", ("a.tif", SLIDE))]).status_code == 404


def test_size_limit_returns_413_and_cleans_up(env):
    client, pid, settings = env
    settings.max_upload_bytes = 1000
    assert upload(client, pid, [("a.tif", SLIDE)]).status_code == 413
    assert no_staging_left(settings) and stored_files(settings, pid) == []


def test_zip_that_unpacks_over_the_limit_is_rejected(env):
    client, pid, settings = env
    settings.max_upload_bytes = len(SLIDE) + 50_000
    huge = zip_bytes({"a.bin": b"\0" * 400_000})  # compresses to almost nothing, unpacks large
    assert len(huge) < 50_000
    body = upload(client, pid, [("bomb.zip", huge)]).json()
    assert body["slides"] == [] and "limit" in body["skipped"][0]["reason"]


def test_deleting_a_slide_removes_its_whole_directory(env):
    client, pid, settings = env
    slide_id = upload(client, pid, [("x.tif", SLIDE)]).json()["slides"][0]["id"]
    assert stored_files(settings, pid)
    assert client.delete(f"/api/slides/{slide_id}").status_code == 204
    assert stored_files(settings, pid) == []


def test_deleting_a_project_removes_all_of_its_files_and_only_its_files(env):
    client, pid, settings = env
    upload(client, pid, [("a.tif", SLIDE), ("b.tif", SLIDE)])
    masks = settings.wsi_storage_dir / str(pid) / "_masks"
    masks.mkdir()
    (masks / "1_tissue_mask.png").write_bytes(b"mask")
    other = client.post("/api/projects", json={"name": "Keep Me"}).json()["id"]
    upload(client, other, [("c.tif", SLIDE)])
    for sid in list(reader_cache._cache):  # the viewer had the slides open, as it would in real use
        reader_cache._cache[sid].get_metadata()

    assert client.delete(f"/api/projects/{pid}").status_code == 204

    assert not (settings.wsi_storage_dir / str(pid)).exists()
    assert len(stored_files(settings, other)) == 1


def test_a_new_project_never_inherits_files_left_under_a_reused_id(env):
    client, pid, settings = env
    client.delete(f"/api/projects/{pid}")
    leftover = settings.wsi_storage_dir / str(pid + 1) / "old-slide"
    leftover.mkdir(parents=True)
    (leftover / "old.svs").write_bytes(b"x")

    new_id = client.post("/api/projects", json={"name": "Fresh"}).json()["id"]

    assert stored_files(settings, new_id) == []


# ------------------------------------------------------------------ path import


def test_import_path_file_folder_and_zip(env):
    client, pid, settings = env
    watch = settings.wsi_watch_dir
    (watch / "single.tif").write_bytes(SLIDE)
    (watch / "batch").mkdir()
    (watch / "batch" / "b1.tif").write_bytes(SLIDE)
    (watch / "batch" / "b2.tiff").write_bytes(SLIDE)
    (watch / "pack.zip").write_bytes(zip_bytes({"z1.tif": SLIDE, "z2.tif": SLIDE}))

    def do(path):
        return client.post(f"/api/projects/{pid}/slides/import-path", json={"path": str(path)})

    one = do(watch / "single.tif").json()
    assert [s["filename"] for s in one["slides"]] == ["single.tif"] and one["slides"][0]["source_type"] == "path"
    assert (watch / "single.tif").exists()  # the watch directory is only ever read

    folder = do(watch / "batch").json()
    assert sorted(s["filename"] for s in folder["slides"]) == ["b1.tif", "b2.tiff"]
    assert (watch / "batch" / "b1.tif").exists()

    zipped = do(watch / "pack.zip").json()
    assert sorted(s["filename"] for s in zipped["slides"]) == ["z1.tif", "z2.tif"]
    assert (watch / "pack.zip").exists() and no_staging_left(settings)


def test_import_path_rejects_outside_missing_and_unsupported(env):
    client, pid, settings = env
    outside = settings.wsi_storage_dir.parent / "elsewhere.tif"
    outside.write_bytes(SLIDE)
    (settings.wsi_watch_dir / "notes.txt").write_text("x")

    def status(p):
        return client.post(f"/api/projects/{pid}/slides/import-path", json={"path": str(p)}).status_code

    assert status(outside) == 400
    assert status(settings.wsi_watch_dir / ".." / "elsewhere.tif") == 400
    assert status(settings.wsi_watch_dir / "missing.tif") == 404
    assert status(settings.wsi_watch_dir / "notes.txt") == 415
