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
def reset_db():
    """Drop and recreate all tables before every test for full isolation."""
    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)
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

def make_tx(client, **kwargs):
    """POST a transaction with sensible defaults; return the JSON body."""
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
