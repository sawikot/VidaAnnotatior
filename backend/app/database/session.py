from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.database.base import Base
    from app.models import (  # noqa: F401
        annotation,
        config_version,
        patch,
        project,
        slide,
    )

    if settings.database_url.startswith("sqlite"):
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001, ARG001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            # WAL + a busy timeout let concurrent request threads (FastAPI runs
            # sync endpoints in a threadpool) queue briefly instead of
            # immediately raising "database is locked" under light contention.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.close()

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()
    _upgrade_enabled_tools()
    _allow_slide_level_annotations()


def _add_missing_columns(bind=None) -> None:
    """Tiny forward-only migration for databases created by an older version.

    ``create_all`` only creates missing *tables*, so a column added to an existing
    table has to be added by hand. Only additive, defaulted columns belong here.
    """
    from sqlalchemy import inspect, text

    additions = {
        "projects": [("project_type", "VARCHAR(20) NOT NULL DEFAULT 'wsi'")],
        "slides": [("tissue_source", "VARCHAR(20) NOT NULL DEFAULT 'auto'"), ("tissue_regions", "JSON")],
    }
    bind = bind or engine
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    with bind.begin() as conn:
        for table, columns in additions.items():
            if table not in tables:
                continue  # create_all makes it, with every column
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


LEGACY_TOOLS = ["polygon", "rectangle", "point", "freehand"]
NEW_TOOLS = ["line", "freehand_line", "circle"]


def _upgrade_enabled_tools(bind=None) -> None:
    """Give existing projects the line/freehand-line/circle tools.

    Only configs still on the untouched legacy default (all four original tools) are upgraded;
    a config someone customised has made a choice and keeps it. Safe to run on every startup.
    """
    import json

    from sqlalchemy import text

    bind = bind or engine
    with bind.begin() as conn:
        for config_id, raw in conn.execute(text("SELECT id, enabled_tools FROM project_config_versions")).fetchall():
            tools = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(tools, list) and sorted(tools) == sorted(LEGACY_TOOLS):
                conn.execute(
                    text("UPDATE project_config_versions SET enabled_tools = :tools WHERE id = :id"),
                    {"tools": json.dumps(tools + NEW_TOOLS), "id": config_id},
                )


def _allow_slide_level_annotations(bind=None) -> None:
    """Let ``geometry_annotations.patch_id`` be NULL (annotations drawn on the whole slide).

    Older databases declared the column NOT NULL, and SQLite cannot drop a constraint in place, so the
    table is rebuilt: renamed aside, recreated from the current model, rows copied across, old table
    dropped -- all in one transaction with foreign keys switched off and re-checked before committing.
    A no-op once the column is nullable, so it is safe to run on every startup.
    """
    from sqlalchemy.schema import CreateIndex, CreateTable

    from app.models import annotation, config_version, patch, project, slide  # noqa: F401  (foreign keys resolve through all models)
    from app.models.annotation import GeometryAnnotation

    bind = bind or engine
    if bind.dialect.name != "sqlite":
        return  # a fresh database of any other kind is created nullable by create_all

    table = GeometryAnnotation.__table__
    raw = bind.raw_connection()
    original_isolation = raw.isolation_level
    try:
        raw.isolation_level = None  # explicit BEGIN/COMMIT below
        cur = raw.cursor()
        info = cur.execute(f"PRAGMA table_info({table.name})").fetchall()
        not_null = {row[1]: bool(row[3]) for row in info}
        if not info or not not_null.get("patch_id"):
            return

        old_columns = [row[1] for row in info]
        columns = ", ".join(c.name for c in table.columns if c.name in old_columns)
        old_indexes = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table.name,))]

        cur.execute("PRAGMA foreign_keys=OFF")
        cur.execute("BEGIN")
        try:
            cur.execute(f"ALTER TABLE {table.name} RENAME TO {table.name}_old")
            for name in old_indexes:
                cur.execute(f'DROP INDEX "{name}"')
            cur.execute(str(CreateTable(table).compile(dialect=bind.dialect)))
            for index in table.indexes:
                cur.execute(str(CreateIndex(index).compile(dialect=bind.dialect)))
            cur.execute(f"INSERT INTO {table.name} ({columns}) SELECT {columns} FROM {table.name}_old")
            cur.execute(f"DROP TABLE {table.name}_old")
            if cur.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("foreign key violations after rebuilding geometry_annotations")
            cur.execute("COMMIT")
        except BaseException:
            cur.execute("ROLLBACK")
            raise
    finally:
        # This connection goes back to the pool: leave it exactly as it was found.
        try:
            raw.cursor().execute("PRAGMA foreign_keys=ON")
            raw.isolation_level = original_isolation
        finally:
            raw.close()
