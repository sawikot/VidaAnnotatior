"""Outlines are stored thinned: points within a quarter Level-0 pixel of their neighbours' line are dropped,
however the shape arrives, and a database from before this is cleaned once at startup."""
import json
import math

from sqlalchemy import create_engine, text

from app.database.base import Base
from app.database.session import _thin_stored_outlines
from app.services.geometry import thin_annotation_coords
from tests.test_slide_annotations import grid, make  # noqa: F401  (fixture)
from tests.test_slide_import_api import env  # noqa: F401  (fixture)


def dense_ring(cx=500, cy=500, r=100, n=4000):
    """A circle-like outline with points a fraction of a pixel apart, as some importers write them."""
    return [[cx + r * math.cos(2 * math.pi * i / n) + 1e-9, cy + r * math.sin(2 * math.pi * i / n)] for i in range(n)]


def test_both_copies_keep_the_same_points():
    l0 = dense_ring()
    local = [[x / 4, y / 4] for x, y in l0]
    new_l0, new_local = thin_annotation_coords("polygon", l0, local)
    assert 20 < len(new_l0) < 100 and len(new_local) == len(new_l0)
    assert all(abs(a[0] / 4 - b[0]) < 0.05 and abs(a[1] / 4 - b[1]) < 0.05 for a, b in zip(new_l0, new_local))


def test_an_outline_traced_along_pixel_edges_keeps_every_corner():
    # a staircase with extra points along each straight edge, like a traced mask
    stair = []
    for i in range(10):
        stair += [[i, i], [i + 0.5, i], [i + 1, i], [i + 1, i + 0.5]]
    stair += [[10, 10], [0, 10]]
    l0, _ = thin_annotation_coords("polygon", stair, [])
    corners = {(i, i) for i in range(11)} | {(i + 1, i) for i in range(10)} | {(0, 10)}
    assert {tuple(p) for p in l0} == corners


def test_simple_shapes_are_left_exactly_as_they_are():
    rect = [[0.123456, 0], [10, 10]]
    assert thin_annotation_coords("rectangle", rect, []) == (rect, [])


def test_a_drawn_outline_is_stored_thinned(grid):  # noqa: F811
    client, slide_id, _, classes, _ = grid
    made = make(client, slide_id, "freehand", dense_ring(), classes["Tumor"]).json()
    assert 20 < len(made["coordinates_level0"]) < 100
    stored = client.get(f"/api/slides/{slide_id}/annotations").json()
    assert stored[0]["coordinates_level0"] == made["coordinates_level0"]


def test_an_edited_outline_is_stored_thinned_and_other_edits_leave_it_alone(grid):  # noqa: F811
    client, slide_id, _, classes, _ = grid
    ann = make(client, slide_id, "polygon", [[10, 10], [60, 10], [60, 60], [10, 60]], classes["Tumor"]).json()
    edited = client.put(f"/api/annotations/{ann['id']}", json={"coordinates_level0": dense_ring()}).json()
    assert 20 < len(edited["coordinates_level0"]) < 100
    noted = client.put(f"/api/annotations/{ann['id']}", json={"notes": "checked"}).json()
    assert noted["coordinates_level0"] == edited["coordinates_level0"]


def _engine_with(rows, tmp_path):
    from app.models import annotation, config_version, patch, project, slide, training, user, version  # noqa: F401

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        for i, (kind, l0, local) in enumerate(rows, start=1):
            conn.execute(
                text(
                    "INSERT INTO geometry_annotations (id, patch_id, slide_id, config_version_id, type, coordinates_patch_local,"
                    " coordinates_level0, unsure, flagged, excluded, whole_patch, created_at, updated_at)"
                    " VALUES (:id, NULL, 1, 1, :t, :local, :l0, 0, 0, 0, 0, '2026-01-01', '2026-01-01')"
                ),
                {"id": i, "t": kind, "l0": json.dumps(l0), "local": json.dumps(local)},
            )
    return eng


def _stored(eng):
    with eng.begin() as conn:
        return [(t, json.loads(a), json.loads(b)) for t, a, b in conn.execute(
            text("SELECT type, coordinates_level0, coordinates_patch_local FROM geometry_annotations ORDER BY id"))]


def test_an_older_database_is_cleaned_once_in_batches(tmp_path):
    ring = dense_ring()
    rect = [[1.234567, 2], [30, 40]]
    eng = _engine_with([("polygon", ring, [[x / 2, y / 2] for x, y in ring]), ("rectangle", rect, []), ("polygon", ring, [])], tmp_path)

    assert _thin_stored_outlines(eng, batch=2) == 2  # two batches; the rectangle is left alone
    (_, l0, local), (_, kept_rect, _), (_, l0_b, _) = _stored(eng)
    assert 20 < len(l0) < 100 and len(local) == len(l0) and l0_b == l0
    assert kept_rect == rect

    with eng.begin() as conn:  # an outline saved later the old way is not touched again: it runs once
        conn.execute(text("UPDATE geometry_annotations SET coordinates_level0 = :c WHERE id = 3"), {"c": json.dumps(ring)})
    assert _thin_stored_outlines(eng) == 0
    assert len(_stored(eng)[2][1]) == len(ring)


def test_a_row_edited_while_the_cleanup_runs_is_left_as_the_edit_saved_it(tmp_path, monkeypatch):
    import app.services.geometry as geometry

    ring = dense_ring()
    eng = _engine_with([("polygon", ring, []), ("polygon", ring, [])], tmp_path)
    real = geometry.thin_annotation_coords
    edited = [[1, 1], [9, 1], [9, 9], [1, 9], [1, 5]]

    def edit_row_2_meanwhile(*args):  # the app saves an edit between the cleanup's read and its write
        with eng.begin() as conn:
            conn.execute(text("UPDATE geometry_annotations SET coordinates_level0 = :c, updated_at = '2026-02-02 00:00:00.000000'"
                              " WHERE id = 2"), {"c": json.dumps(edited)})
        return real(*args)

    monkeypatch.setattr(geometry, "thin_annotation_coords", edit_row_2_meanwhile)
    assert _thin_stored_outlines(eng) == 1  # both looked thinnable when read; only the untouched one is written
    assert 20 < len(_stored(eng)[0][1]) < 100
    assert _stored(eng)[1][1] == edited  # the edit wins


def test_until_the_cleanup_is_done_lists_thin_as_they_send(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session

    import app.database.session as session_module
    from app.services.geometry import thin_for_display

    monkeypatch.setattr(session_module, "_outlines_thinned", False)
    eng = _engine_with([("polygon", dense_ring(), [])], tmp_path)
    with Session(eng) as db:
        assert session_module.outlines_thinned(db) is False  # no marker table yet
    assert _thin_stored_outlines(eng) == 1
    with Session(eng) as db:
        assert session_module.outlines_thinned(db) is True

    sent = thin_for_display("polygon", dense_ring())
    assert 500 < len(sent) < 700 and all(round(v, 2) == v for p in sent for v in p)
    rect = [[0.123456, 0], [10, 10]]
    assert thin_for_display("rectangle", rect) is rect
