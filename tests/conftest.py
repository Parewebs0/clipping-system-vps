"""Shared pytest fixtures.

SAFETY: the suite deletes rows (e.g. the jobs table). It must never run against
the live database. Before any app module is imported we point the app settings
at a dedicated test DB and refuse to run if the DB name is not a test DB.

  TEST_DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/clipping_test  (optional)
  TEST_DB_NAME=clipping_test   (default; same host/user as .env)

Create it once:  CREATE DATABASE clipping_test;  then
  CLIPPING_DB_NAME=clipping_test alembic upgrade head
"""
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _is_test_db(name: str) -> bool:
    return bool(name) and (name.endswith("_test") or name.startswith("test_"))


def _point_settings_at_test_db() -> None:
    if "app.config" in sys.modules:
        raise RuntimeError("app.config imported before tests/conftest.py; refusing to run")
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if url:
        u = urlparse(url)
        if u.hostname:
            os.environ["CLIPPING_DB_HOST"] = u.hostname
        if u.port:
            os.environ["CLIPPING_DB_PORT"] = str(u.port)
        if u.username:
            os.environ["CLIPPING_DB_USER"] = unquote(u.username)
        if u.password:
            os.environ["CLIPPING_DB_PASSWORD"] = unquote(u.password)
        os.environ["CLIPPING_DB_NAME"] = u.path.lstrip("/")
    else:
        os.environ["CLIPPING_DB_NAME"] = os.environ.get("TEST_DB_NAME", "clipping_test")
    if not _is_test_db(os.environ["CLIPPING_DB_NAME"]):
        raise RuntimeError(
            f"Refusing to run tests against non-test DB {os.environ['CLIPPING_DB_NAME']!r} "
            "(name must end with _test or start with test_)"
        )


_point_settings_at_test_db()

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db.database import SessionLocal, engine
from app.main import app

# Belt and braces: verify what the engine will actually connect to.
if not _is_test_db(settings.clipping_db_name) or not _is_test_db(engine.url.database or ""):
    raise RuntimeError(f"Refusing to run tests: engine DB is {engine.url.database!r}")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth_headers() -> dict:
    return {"Authorization": f"Bearer {settings.api_token}"}


@pytest.fixture(autouse=True)
def _clean_jobs_table():
    """Clean jobs table before every test to ensure isolation."""
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM jobs"))
        session.commit()
    finally:
        session.close()


@pytest.fixture
def db():
    """Database session with cleanup. Cleans jobs table before each test."""
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM jobs"))
        session.commit()
        yield session
    finally:
        session.close()
