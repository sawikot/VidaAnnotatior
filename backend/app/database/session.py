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
