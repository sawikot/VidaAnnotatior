"""Hand-drawn tissue regions: the mask patches come from can be detected, drawn, or both.
Unit tests for how the mask is put together, then real HTTP against a real (tiny) slide."""
import numpy as np
import pytest

from app.models.slide import Slide
from app.services.tissue_mask import auto_mask_path, compose_mask, load_mask, rebuild_slide_mask, save_mask
from tests.test_slide_import_api import SLIDE, env, upload  # noqa: F401  (env is a fixture)

W, H = 1536, 1024  # the test slide's Level-0 size


def rect(x0, y0, x1, y1, mode="add"):
    return {"mode": mode, "type": "rectangle", "coordinates": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}


def at(mask, x_l0, y_l0):
    """The mask value under a Level-0 point."""
    return bool(mask[int(y_l0 * mask.shape[0] / H), int(x_l0 * mask.shape[1] / W)])


# ------------------------------------------------------------------ composing


def test_manual_without_regions_has_no_mask():
    assert compose_mask(W, H, "manual", None, []) is None


def test_manual_add_region_is_the_whole_mask():
    mask = compose_mask(W, H, "manual", None, [rect(100, 100, 700, 500)])
    assert at(mask, 400, 300) and not at(mask, 900, 300) and not at(mask, 400, 800)


def test_manual_mode_ignores_the_detected_mask():
    detected = np.ones((64, 96), dtype=bool)
    mask = compose_mask(W, H, "manual", detected, [rect(0, 0, 200, 200)])
    assert not at(mask, 1000, 800)


def test_remove_wins_over_add_whatever_the_drawing_order():
    regions = [rect(500, 300, 900, 700, "remove"), rect(0, 0, W, H)]
    mask = compose_mask(W, H, "manual", None, regions)
    assert at(mask, 100, 100) and not at(mask, 700, 500)


def test_auto_mask_plus_additions_and_removals():
    detected = np.zeros((64, 96), dtype=bool)
    detected[:, :48] = True  # detector found the left half
    regions = [rect(1200, 100, 1500, 400), rect(100, 100, 300, 300, "remove")]
    mask = compose_mask(W, H, "auto", detected, regions)
    assert at(mask, 500, 800)  # detected, untouched
    assert at(mask, 1350, 250)  # added
    assert not at(mask, 200, 200)  # removed
    assert not at(mask, 1350, 800)  # neither


def test_auto_without_regions_is_the_detector_result_unchanged():
    detected = np.random.default_rng(0).random((64, 96)) > 0.5
    assert np.array_equal(compose_mask(W, H, "auto", detected, []), detected)


def test_circle_polygon_and_freehand_regions():
    circle = {"mode": "add", "type": "circle", "coordinates": [[400, 400], [600, 400]]}
    triangle = {"mode": "add", "type": "polygon", "coordinates": [[1000, 100], [1400, 100], [1200, 500]]}
    freehand = {"mode": "add", "type": "freehand", "coordinates": [[900, 700], [1100, 700], [1100, 900], [900, 900]]}
    mask = compose_mask(W, H, "manual", None, [circle, triangle, freehand])
    assert at(mask, 400, 400) and at(mask, 540, 400) and not at(mask, 400, 620)
    assert at(mask, 1200, 200) and not at(mask, 1020, 480)
    assert at(mask, 1000, 800)


def test_a_mask_detected_before_regions_existed_is_kept_as_the_detector_result(tmp_path):
    slide = Slide(id=7, project_id=1, width_l0=W, height_l0=H, tissue_source="auto", tissue_params_used={"method": "hsv_otsu"})
    legacy = tmp_path / "1" / "_masks" / "7_tissue_mask.png"
    detected = np.zeros((64, 96), dtype=bool)
    detected[:32] = True
    save_mask(legacy, detected)
    slide.tissue_mask_path = "1/_masks/7_tissue_mask.png"

    slide.tissue_regions = [rect(0, 900, W, H)]
    rebuild_slide_mask(tmp_path, slide)
    slide.tissue_regions = []  # taking the region away again must give back exactly the detected mask
    rebuild_slide_mask(tmp_path, slide)

    assert np.array_equal(load_mask(auto_mask_path(tmp_path, slide)), detected)
    assert np.array_equal(load_mask(legacy), detected)


# ------------------------------------------------------------------ API


@pytest.fixture()
def slide_env(env, monkeypatch):  # noqa: F811
    client, pid, settings = env
    monkeypatch.setattr("app.api.processing.get_settings", lambda: settings)
    slide = upload(client, pid, [("s.tif", SLIDE)]).json()["slides"][0]
    config_id = client.get(f"/api/projects/{pid}").json()["active_config_version_id"]
    return client, slide["id"], config_id, settings


def put_regions(client, slide_id, source, regions):
    return client.put(f"/api/slides/{slide_id}/tissue-regions", json={"source": source, "regions": regions})


def generated(client, slide_id, config_id):
    """(origins, footprint) of the patches Generate Coords keeps."""
    res = client.post(f"/api/slides/{slide_id}/generate-patches", json={"config_version_id": config_id})
    if res.status_code != 200:
        return [], None
    items = client.get(f"/api/slides/{slide_id}/patches").json()["items"]
    return sorted((p["x"], p["y"]) for p in items), (items[0]["width_l0"], items[0]["height_l0"])


def test_manual_regions_decide_where_patches_go(slide_env):
    client, sid, config_id, _ = slide_env
    put_regions(client, sid, "manual", [rect(0, 0, W, H)])
    everywhere, (pw, ph) = generated(client, sid, config_id)
    assert len(everywhere) > 1

    # Only the first patch's area is tissue: only it is kept. A region covering half of the
    # next patch (under the 60% minimum) does not bring that one in. (An edge patch's fraction is
    # measured over the part of it inside the slide, so "half" is half of that part.)
    half_next = min(pw, W - pw) // 2
    res = put_regions(client, sid, "manual", [rect(0, 0, pw, ph), rect(pw, 0, pw + half_next, ph)])
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["has_mask"] and body["source"] == "manual" and [r["id"] for r in body["regions"]] == [1, 2]
    assert body["tissue_coverage_pct"] == pytest.approx(100 * (pw + half_next) * ph / (W * H), abs=0.5)
    assert client.get(f"/api/slides/{sid}").json()["status"] in ("tissue_detected", "patches_generated")

    origins, _ = generated(client, sid, config_id)
    assert origins == [(0, 0)]


def test_remove_regions_cut_patches_out(slide_env):
    client, sid, config_id, _ = slide_env
    put_regions(client, sid, "manual", [rect(0, 0, W, H)])
    everywhere, (pw, ph) = generated(client, sid, config_id)
    put_regions(client, sid, "manual", [rect(0, 0, W, H), rect(0, 0, pw, ph, "remove")])
    origins, _ = generated(client, sid, config_id)
    assert origins == [o for o in everywhere if o != (0, 0)]


def test_regions_survive_detection_and_are_applied_on_top(slide_env):
    client, sid, _, settings = slide_env
    put_regions(client, sid, "manual", [rect(0, 0, 400, 400, "remove")])
    assert client.post(f"/api/slides/{sid}/detect-tissue", json={}).status_code == 200

    body = client.get(f"/api/slides/{sid}/tissue-regions").json()
    assert body["source"] == "auto" and len(body["regions"]) == 1  # detection switched the source, kept the regions
    slide = client.get(f"/api/slides/{sid}").json()
    mask = load_mask(settings.wsi_storage_dir / slide["tissue_mask_path"])
    assert not at(mask, 200, 200)


def test_manual_with_nothing_drawn_clears_the_mask(slide_env):
    client, sid, _, settings = slide_env
    put_regions(client, sid, "manual", [rect(0, 0, W, H)])
    body = put_regions(client, sid, "manual", []).json()
    assert not body["has_mask"] and body["tissue_coverage_pct"] is None
    slide = client.get(f"/api/slides/{sid}").json()
    assert slide["status"] == "imported" and slide["tissue_mask_path"] is None


def test_bad_regions_are_refused(slide_env):
    client, sid, _, _ = slide_env
    assert put_regions(client, sid, "manual", [{"mode": "add", "type": "polygon", "coordinates": [[0, 0], [1, 1]]}]).status_code == 422
    assert put_regions(client, sid, "manual", [{"mode": "add", "type": "line", "coordinates": [[0, 0], [9, 9]]}]).status_code == 422
    assert put_regions(client, sid, "manual", [{"mode": "keep", "type": "rectangle", "coordinates": rect(0, 0, 9, 9)["coordinates"]}]).status_code == 422
    assert put_regions(client, sid, "sometimes", []).status_code == 422


def test_deleting_the_slide_removes_both_mask_files(slide_env):
    client, sid, _, settings = slide_env
    client.post(f"/api/slides/{sid}/detect-tissue", json={})
    put_regions(client, sid, "auto", [rect(0, 0, 100, 100)])
    masks = list((settings.wsi_storage_dir).rglob("*_tissue_*.png"))
    assert len(masks) == 2
    assert client.delete(f"/api/slides/{sid}").status_code == 204
    assert not any(m.exists() for m in masks)


def test_migration_adds_the_region_columns_to_an_older_database(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from app.database.session import _add_missing_columns

    engine = create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE slides (id INTEGER PRIMARY KEY, filename VARCHAR(255))"))
        conn.execute(text("INSERT INTO slides (id, filename) VALUES (1, 'old.svs')"))
    _add_missing_columns(engine)
    _add_missing_columns(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT tissue_source, tissue_regions FROM slides")).one() == ("auto", None)
    assert {"tissue_source", "tissue_regions"} <= {c["name"] for c in inspect(engine).get_columns("slides")}
