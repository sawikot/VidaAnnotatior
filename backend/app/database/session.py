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


def _add_missing_columns(bind=None) -> None:
    """Tiny forward-only migration for databases created by an older version.

    ``create_all`` only creates missing *tables*, so a column added to an existing
    table has to be added by hand. Only additive, defaulted columns belong here.
    """
    from sqlalchemy import inspect, text

    additions = {"projects": [("project_type", "VARCHAR(20) NOT NULL DEFAULT 'wsi'")]}
    bind = bind or engine
    inspector = inspect(bind)
    with bind.begin() as conn:
        for table, columns in additions.items():
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
