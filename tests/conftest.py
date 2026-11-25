"""Shared fixtures. Database tests use the separate coffwe_test database, never coffwe_dev."""

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from coffwe.sim import engine


def _test_db_url() -> str:
    from dotenv import load_dotenv

    load_dotenv()
    url = os.environ.get("TEST_DATABASE_URL") or os.environ["DATABASE_URL"]
    return make_url(url).set(database="coffwe_test").render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def test_db():
    """A session factory for coffwe_test, or skip if it isn't there / isn't migrated."""
    eng = create_engine(_test_db_url())
    try:
        with eng.connect() as conn:
            conn.execute(text("select 1 from milkings limit 1"))
    except Exception as exc:
        pytest.skip(f"coffwe_test database not available: {exc}")
    yield sessionmaker(bind=eng)
    eng.dispose()


@pytest.fixture(scope="session")
def db_run(test_db, tmp_path_factory):
    """One full simulator run written into coffwe_test, shared by all database tests."""
    store = engine.run(
        seed=42, root=tmp_path_factory.mktemp("dbrun"), write_db=True, session_factory=test_db
    )
    return store
