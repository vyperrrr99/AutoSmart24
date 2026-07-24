from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from autosmart24.db.models import Base


def _is_sqlite_memory_url(url: str) -> bool:
    parsed = make_url(url)
    if not parsed.drivername.startswith("sqlite"):
        return False
    # Mirrors SQLAlchemy's own pysqlite dialect logic (see
    # SQLiteDialect_pysqlite.create_connect_args): a falsy database
    # (e.g. "sqlite://" or "sqlite:///") or an explicit ":memory:" both
    # resolve to the same anonymous in-memory SQLite database.
    return (parsed.database or ":memory:") == ":memory:"


def make_engine(database_url: str | None = None) -> Engine:
    url = database_url or os.environ["DATABASE_URL"]
    if _is_sqlite_memory_url(url):
        # A bare in-memory SQLite DB is keyed to the connection, and
        # SQLAlchemy's default SingletonThreadPool keys connections by
        # thread. Without StaticPool, code running on a different thread
        # (e.g. FastAPI's TestClient, which dispatches requests to worker
        # threads) would see a separate, schema-less in-memory database.
        # StaticPool shares a single connection across all threads for
        # this engine, and check_same_thread=False allows that shared
        # connection to be used from threads other than the one that
        # created it.
        return create_engine(
            url,
            future=True,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
    return create_engine(url, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
