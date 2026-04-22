from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from packages.db.session import build_engine, build_session_factory
from services.orchestrator.app.settings import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return build_engine(settings.database_url)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return build_session_factory(get_engine())


def get_db_session() -> Generator[Session, None, None]:
    session_factory = get_session_factory()
    with session_factory() as session:
        yield session
