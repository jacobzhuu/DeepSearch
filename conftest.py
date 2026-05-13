from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from packages.db.session import build_engine, build_session_factory


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


@pytest.fixture()
def database_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'deepresearch.db'}"


@pytest.fixture()
def alembic_config(database_url: str) -> Config:
    repo_root = _repo_root()
    config = Config(str(repo_root / "alembic.ini"))
    config.set_main_option("script_location", str(repo_root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture()
def upgraded_engine(alembic_config: Config, database_url: str) -> Generator[Engine, None, None]:
    # ``migrations/env.py`` overwrites ``sqlalchemy.url`` from ``DATABASE_URL`` when set,
    # which would migrate a different database than ``database_url`` used by tests.
    prior_database_url = os.environ.pop("DATABASE_URL", None)
    try:
        command.upgrade(alembic_config, "head")
        engine = build_engine(database_url)
        try:
            yield engine
        finally:
            engine.dispose()
            command.downgrade(alembic_config, "base")
    finally:
        if prior_database_url is not None:
            os.environ["DATABASE_URL"] = prior_database_url


@pytest.fixture()
def session_factory(upgraded_engine: Engine) -> sessionmaker[Session]:
    return build_session_factory(upgraded_engine)


@pytest.fixture()
def db_session(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    with session_factory() as session:
        yield session
        session.rollback()
