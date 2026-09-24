import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.base import Base
from app.models import annotation, config_version, patch, project, slide, user  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """No test may ever touch the real ``data/`` directory.

    Code under test reads its storage locations from the shared settings object. Some tests call
    route functions directly (with an in-memory database, where a project gets id 1), and
    ``delete_project`` removes ``<storage>/<project id>`` -- which against the real settings is
    the real project 1's uploaded slides. So every test gets throw-away storage directories.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "wsi_storage_dir", tmp_path / "isolated_uploads")
    monkeypatch.setattr(settings, "wsi_watch_dir", tmp_path / "isolated_watch")
    settings.wsi_storage_dir.mkdir()
    settings.wsi_watch_dir.mkdir()


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    # Mirror the production engine's PRAGMA (see database/session.py) so tests
    # actually exercise foreign-key constraint behavior instead of silently
    # allowing violations that SQLite only rejects when this is enabled.
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001, ARG001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()


TEST_ADMIN_EMAIL = "test-admin@example.org"


def _signed_in_admin(db=None):
    """Every API test runs as an administrator stored in that test's own database (tests of signing
    in and of the access rules remove this override; see test_auth.py)."""
    from app.models.user import User

    user = db.query(User).filter(User.email == TEST_ADMIN_EMAIL).first()
    if user is None:
        user = User(email=TEST_ADMIN_EMAIL, name="Test Admin", role="admin")
        db.add(user)
        db.commit()
    return user


@pytest.fixture(autouse=True)
def signed_in_as_admin():
    from fastapi import Depends

    from app.api.access import current_user
    from app.database.session import get_db
    from app.main import app

    def override(db=Depends(get_db)):
        return _signed_in_admin(db)

    app.dependency_overrides[current_user] = override
    yield
    app.dependency_overrides.pop(current_user, None)
