import pytest
from sqlalchemy.orm import Session

from autosmart24.db.session import init_db, make_engine, make_session_factory


@pytest.fixture()
def db_session() -> Session:
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    factory = make_session_factory(engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
