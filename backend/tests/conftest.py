import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database.base import Base
from app.models import annotation, config_version, patch, project, slide  # noqa: F401


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
