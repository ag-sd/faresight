import os
import tempfile

# Repoint the app engine/migrations/sync at a throwaway file BEFORE any app
# module is imported — app.config reads FARESIGHT_DB at import time. Without
# this, the per-test lifespan runs create_all/migrate_db against the user's
# real local DB and pushes it to the real NAS share on every teardown.
os.environ["FARESIGHT_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="faresight-tests-"), "local.db"
)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.faresight import app

# In-memory SQLite — isolated per test session, never touches the real DB.
TEST_DATABASE_URL = "sqlite://"

_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


class _FakeProc:
    def terminate(self): pass
    def wait(self, timeout=None): pass
    def kill(self): pass
    def poll(self): return None  # None = still running


@pytest.fixture(autouse=True)
def no_categorizer_subprocess(monkeypatch):
    """Prevent the lifespan from spawning a real categorizer process during tests."""
    monkeypatch.setattr("app.faresight._spawn_categorizer", lambda: _FakeProc())


@pytest.fixture(autouse=True)
def isolated_nas(monkeypatch, tmp_path):
    """Point NAS sync at a nonexistent tmp dir so the lifespan's startup pull
    and shutdown push are instant offline no-ops (they were hitting the real
    NAS mount, costing seconds per test). test_sync.py overrides this with its
    own paths when exercising real sync logic."""
    monkeypatch.setattr("app.sync.NAS_SHARE_PATH", str(tmp_path / "nas" / "faresight.db"))


def _seed_categories():
    """Populate the categories table with the 15 defaults after schema creation."""
    from app.category_defaults import DEFAULT_CATEGORIES
    from app.models import Category
    db = TestingSession()
    for i, (name, color, bucket, desc) in enumerate(DEFAULT_CATEGORIES):
        db.add(Category(name=name, color=color, bucket=bucket, description=desc, sort_order=i))
    db.commit()
    db.close()


@pytest.fixture(autouse=True)
def reset_db():
    """Drop and recreate all tables before every test for full isolation."""
    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)
    _seed_categories()
    yield
    Base.metadata.drop_all(bind=_engine)


@pytest.fixture()
def client():
    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_file_import() -> int:
    """Create a synthetic FileImport record in the test DB; return its id."""
    from app.models import FileImport
    db = TestingSession()
    fi = FileImport(filename="test.csv", rows_seen=1, rows_persisted=1)
    db.add(fi)
    db.commit()
    db.refresh(fi)
    fid = fi.id
    db.close()
    return fid


def make_tx(client, **kwargs):
    """POST a transaction with sensible defaults; return the JSON body."""
    if "file_id" not in kwargs:
        kwargs["file_id"] = _make_file_import()
    payload = {
        "date": "2026-01-15",
        "description": "Test expense",
        "amount": -10.00,
        "category": "Food",
        **kwargs,
    }
    r = client.post("/api/transactions", json=payload)
    assert r.status_code == 201, r.text
    return r.json()
