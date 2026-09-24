"""Older databases declare geometry_annotations.patch_id NOT NULL; slide-level annotations need it
nullable. SQLite can't relax a constraint in place, so the table is rebuilt -- this must not lose
or damage a single row."""
import json

import pytest
from sqlalchemy import create_engine, event, text

from app.database.session import _allow_slide_level_annotations

# The table exactly as the previous version of the app created it.
OLD_DDL = """
CREATE TABLE geometry_annotations (
	id INTEGER NOT NULL,
	patch_id INTEGER NOT NULL,
	slide_id INTEGER NOT NULL,
	config_version_id INTEGER NOT NULL,
	class_id INTEGER,
	type VARCHAR(20) NOT NULL,
	coordinates_patch_local JSON NOT NULL,
	coordinates_level0 JSON NOT NULL,
	created_by VARCHAR(120),
	notes VARCHAR(2000),
	unsure BOOLEAN NOT NULL,
	flagged BOOLEAN NOT NULL,
	excluded BOOLEAN NOT NULL,
	created_at DATETIME NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(patch_id) REFERENCES patches (id) ON DELETE CASCADE,
	FOREIGN KEY(slide_id) REFERENCES slides (id) ON DELETE CASCADE,
	FOREIGN KEY(config_version_id) REFERENCES project_config_versions (id),
	FOREIGN KEY(class_id) REFERENCES annotation_classes (id)
)
"""
OLD_INDEXES = [
    "CREATE INDEX ix_geometry_annotations_slide_id ON geometry_annotations (slide_id)",
    "CREATE INDEX ix_geometry_annotations_config_version_id ON geometry_annotations (config_version_id)",
    "CREATE INDEX ix_geometry_annotations_patch_id ON geometry_annotations (patch_id)",
]
PARENTS = [
    "CREATE TABLE patches (id INTEGER PRIMARY KEY)",
    "CREATE TABLE slides (id INTEGER PRIMARY KEY)",
    "CREATE TABLE project_config_versions (id INTEGER PRIMARY KEY)",
    "CREATE TABLE annotation_classes (id INTEGER PRIMARY KEY)",
    "CREATE TABLE users (id INTEGER PRIMARY KEY)",  # exists before this migration runs (create_all makes it)
]


@pytest.fixture()
def old_db(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):  # noqa: ANN001
        dbapi_connection.execute("PRAGMA foreign_keys=ON")  # like the production engine

    with engine.begin() as conn:
        for ddl in [*PARENTS, OLD_DDL, *OLD_INDEXES]:
            conn.execute(text(ddl))
        for i in (1, 2, 3):
            conn.execute(text("INSERT INTO patches VALUES (:i)"), {"i": i})
        conn.execute(text("INSERT INTO slides VALUES (1)"))
        conn.execute(text("INSERT INTO project_config_versions VALUES (1)"))
        conn.execute(text("INSERT INTO annotation_classes VALUES (5)"))
        for i in (1, 2, 3):
            conn.execute(
                text(
                    "INSERT INTO geometry_annotations VALUES (:i, :i, 1, 1, 5, 'polygon', :local, :l0, 'me', :note, 1, 0, 0,"
                    " '2026-01-01 10:00:00.000000', '2026-01-02 11:00:00.000000')"
                ),
                {"i": i, "local": json.dumps([[0, 0], [1.5, 2], [3, 0]]), "l0": json.dumps([[10, 10], [13, 14], [16, 10]]), "note": f"note {i}"},
            )
    yield engine
    engine.dispose()


def columns(engine):
    with engine.connect() as conn:
        return {row[1]: row for row in conn.exec_driver_sql("PRAGMA table_info(geometry_annotations)")}


def test_the_column_becomes_nullable_and_every_row_survives_unchanged(old_db):
    old = ", ".join(columns(old_db))  # the rebuilt table may add newer columns; the old ones must match
    with old_db.connect() as conn:
        before = conn.exec_driver_sql(f"SELECT {old} FROM geometry_annotations ORDER BY id").fetchall()
    assert columns(old_db)["patch_id"][3] == 1  # NOT NULL

    _allow_slide_level_annotations(old_db)

    assert columns(old_db)["patch_id"][3] == 0  # nullable now
    with old_db.connect() as conn:
        after = conn.exec_driver_sql(f"SELECT {old} FROM geometry_annotations ORDER BY id").fetchall()
    assert after == before and len(after) == 3  # ids, JSON, flags, timestamps, notes: identical


def test_indexes_and_foreign_keys_are_rebuilt(old_db):
    _allow_slide_level_annotations(old_db)
    with old_db.connect() as conn:
        indexes = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='geometry_annotations'")}
        fks = {(r[2], r[3], r[6]) for r in conn.exec_driver_sql("PRAGMA foreign_key_list(geometry_annotations)")}
        leftovers = [r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE name LIKE '%_old'")]
    assert {"ix_geometry_annotations_slide_id", "ix_geometry_annotations_config_version_id", "ix_geometry_annotations_patch_id"} <= indexes
    assert ("patches", "patch_id", "CASCADE") in fks and ("slides", "slide_id", "CASCADE") in fks  # cascades kept
    assert leftovers == []  # the temporary copy is gone


def test_slide_level_rows_can_now_be_stored_and_patch_cascades_still_work(old_db):
    _allow_slide_level_annotations(old_db)
    with old_db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO geometry_annotations (id, patch_id, slide_id, config_version_id, type, coordinates_patch_local, coordinates_level0,"
                " unsure, flagged, excluded, created_at, updated_at) VALUES (10, NULL, 1, 1, 'circle', '[]', '[[5,5],[9,5]]', 0, 0, 0, '2026-01-01', '2026-01-01')"
            )
        )
        conn.execute(text("DELETE FROM patches WHERE id = 1"))  # foreign keys are on: this cascades
    with old_db.connect() as conn:
        ids = [r[0] for r in conn.exec_driver_sql("SELECT id FROM geometry_annotations ORDER BY id")]
    assert ids == [2, 3, 10]  # the patch's annotation went with it; the slide-level one stayed


def test_running_it_again_does_nothing_and_leaves_the_connection_pool_healthy(old_db):
    _allow_slide_level_annotations(old_db)
    _allow_slide_level_annotations(old_db)
    with old_db.connect() as conn:
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM geometry_annotations").scalar() == 3
    # The pooled connection must be back in normal transaction mode with foreign keys on.
    raw = old_db.raw_connection()
    try:
        assert raw.isolation_level == ""  # pysqlite's default (deferred transactions)
        assert raw.cursor().execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        raw.close()
    with pytest.raises(Exception):  # a rolled-back write is really rolled back
        with old_db.begin() as conn:
            conn.execute(text("DELETE FROM geometry_annotations"))
            raise RuntimeError("abort")
    with old_db.connect() as conn:
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM geometry_annotations").scalar() == 3


def test_a_failed_rebuild_changes_nothing(old_db):
    with old_db.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
    with old_db.begin() as conn:  # a row whose patch does not exist: the post-rebuild foreign key check must refuse it
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.execute(
            text(
                "INSERT INTO geometry_annotations VALUES (99, 777, 1, 1, NULL, 'point', '[[1,1]]', '[[1,1]]', NULL, NULL, 0, 0, 0, '2026-01-01', '2026-01-01')"
            )
        )
    with pytest.raises(RuntimeError, match="foreign key"):
        _allow_slide_level_annotations(old_db)
    assert columns(old_db)["patch_id"][3] == 1  # still the old, untouched table
    with old_db.connect() as conn:
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM geometry_annotations").scalar() == 4
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%_old'").scalar() == 0
