"""Tests for app/sync.py and the /api/sync/status endpoint."""
from pathlib import Path

import pytest

import app.sync as sync_mod
from app.sync import sync_from_nas


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_status():
    """Ensure _status is pristine before and after every test in this module."""
    sync_mod._status.update({"reachable": None, "last_action": None, "detail": None})
    yield
    sync_mod._status.update({"reachable": None, "last_action": None, "detail": None})


def _patch(monkeypatch, nas_path: Path, local_db_path: Path):
    monkeypatch.setattr(sync_mod, "NAS_SHARE_PATH", str(nas_path))
    monkeypatch.setattr(sync_mod, "LOCAL_DB_PATH", local_db_path)


# ── Reachability ──────────────────────────────────────────────────────────────

def test_nas_unreachable_when_dir_missing(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path / "nonexistent" / "expenses.db", tmp_path / "local.db")

    sync_from_nas()

    assert sync_mod._status["reachable"] is False
    assert sync_mod._status["last_action"] is None


def test_nas_unreachable_sets_detail_to_missing_dir(monkeypatch, tmp_path):
    nas_path = tmp_path / "nonexistent" / "expenses.db"
    _patch(monkeypatch, nas_path, tmp_path / "local.db")

    sync_from_nas()

    assert str(nas_path.parent) in sync_mod._status["detail"]


def test_nas_reachable_when_dir_exists(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    _patch(monkeypatch, nas_dir / "expenses.db", tmp_path / "local.db")

    sync_from_nas()

    assert sync_mod._status["reachable"] is True


# ── First run: push local → NAS ───────────────────────────────────────────────

def test_pushes_initial_copy_when_nas_file_absent(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    local_db = tmp_path / "local.db"
    local_db.write_bytes(b"local-content")
    _patch(monkeypatch, nas_dir / "expenses.db", local_db)

    sync_from_nas()

    assert (nas_dir / "expenses.db").read_bytes() == b"local-content"
    assert sync_mod._status["last_action"] == "pushed_initial"


def test_push_writes_marker(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    local_db = tmp_path / "local.db"
    local_db.write_bytes(b"content")
    _patch(monkeypatch, nas_dir / "expenses.db", local_db)

    sync_from_nas()

    marker = Path(str(local_db) + ".synced_at")
    assert marker.exists()
    assert float(marker.read_text()) > 0


def test_skipped_empty_when_no_local_and_no_nas_file(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    _patch(monkeypatch, nas_dir / "expenses.db", tmp_path / "local.db")

    sync_from_nas()

    assert sync_mod._status["last_action"] == "skipped_empty"
    assert not (nas_dir / "expenses.db").exists()


# ── Pull: NAS → local ─────────────────────────────────────────────────────────

def test_pulls_when_no_marker_exists(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    nas_db.write_bytes(b"nas-content")

    local_dir = tmp_path / "local"
    local_dir.mkdir()
    local_db = local_dir / "local.db"
    local_db.write_bytes(b"old-local")
    _patch(monkeypatch, nas_db, local_db)

    sync_from_nas()

    assert local_db.read_bytes() == b"nas-content"
    assert sync_mod._status["last_action"] == "pulled_update"


def test_pulls_when_nas_is_newer_than_marker(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    nas_db.write_bytes(b"updated-nas")

    local_dir = tmp_path / "local"
    local_dir.mkdir()
    local_db = local_dir / "local.db"
    local_db.write_bytes(b"stale-local")

    # Write a marker older than the NAS file's mtime
    marker = Path(str(local_db) + ".synced_at")
    marker.write_text(str(nas_db.stat().st_mtime - 100))
    _patch(monkeypatch, nas_db, local_db)

    sync_from_nas()

    assert local_db.read_bytes() == b"updated-nas"
    assert sync_mod._status["last_action"] == "pulled_update"


def test_pull_creates_backup_of_existing_local(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    nas_db.write_bytes(b"nas-content")

    local_dir = tmp_path / "local"
    local_dir.mkdir()
    local_db = local_dir / "local.db"
    local_db.write_bytes(b"precious-local-data")
    _patch(monkeypatch, nas_db, local_db)

    sync_from_nas()

    bak = local_db.with_suffix(".db.bak")
    assert bak.exists()
    assert bak.read_bytes() == b"precious-local-data"


def test_pull_no_backup_when_local_absent(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    nas_db.write_bytes(b"nas-content")

    local_dir = tmp_path / "local"
    local_dir.mkdir()
    local_db = local_dir / "local.db"  # intentionally not created
    _patch(monkeypatch, nas_db, local_db)

    sync_from_nas()

    assert not local_db.with_suffix(".db.bak").exists()
    assert local_db.read_bytes() == b"nas-content"


def test_pull_writes_marker_with_nas_mtime(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    nas_db.write_bytes(b"nas-content")
    expected_mtime = nas_db.stat().st_mtime

    local_dir = tmp_path / "local"
    local_dir.mkdir()
    local_db = local_dir / "local.db"
    _patch(monkeypatch, nas_db, local_db)

    sync_from_nas()

    marker = Path(str(local_db) + ".synced_at")
    assert float(marker.read_text()) == expected_mtime


def test_pull_creates_local_parent_dir_if_missing(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    nas_db.write_bytes(b"content")

    local_db = tmp_path / "deep" / "nested" / "local.db"  # parents don't exist
    _patch(monkeypatch, nas_db, local_db)

    sync_from_nas()

    assert local_db.exists()
    assert local_db.read_bytes() == b"content"


# ── Skip: local already current ───────────────────────────────────────────────

def test_skips_when_marker_matches_nas_mtime(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    nas_db.write_bytes(b"nas-content")

    local_dir = tmp_path / "local"
    local_dir.mkdir()
    local_db = local_dir / "local.db"
    local_db.write_bytes(b"current-local")

    # Marker exactly equals NAS mtime → already up to date
    marker = Path(str(local_db) + ".synced_at")
    marker.write_text(str(nas_db.stat().st_mtime))
    _patch(monkeypatch, nas_db, local_db)

    sync_from_nas()

    assert local_db.read_bytes() == b"current-local"  # untouched
    assert sync_mod._status["last_action"] == "skipped_current"


def test_skips_when_marker_is_newer_than_nas(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    nas_db.write_bytes(b"nas-content")

    local_dir = tmp_path / "local"
    local_dir.mkdir()
    local_db = local_dir / "local.db"
    local_db.write_bytes(b"current-local")

    marker = Path(str(local_db) + ".synced_at")
    marker.write_text(str(nas_db.stat().st_mtime + 100))
    _patch(monkeypatch, nas_db, local_db)

    sync_from_nas()

    assert sync_mod._status["last_action"] == "skipped_current"


# ── get_status() ──────────────────────────────────────────────────────────────

def test_get_status_returns_copy(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path / "nonexistent" / "db", tmp_path / "local.db")
    sync_from_nas()

    s = sync_mod.get_status()
    assert isinstance(s, dict)
    assert "reachable" in s
    assert "last_action" in s
    assert "detail" in s
    # Mutating the returned dict must not affect the module state
    s["reachable"] = "tampered"
    assert sync_mod._status["reachable"] is False


# ── /api/sync/status endpoint ─────────────────────────────────────────────────

def test_sync_status_endpoint_shape(client):
    r = client.get("/api/sync/status")
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"reachable", "last_action", "detail"}


def test_sync_status_endpoint_reflects_state(client, monkeypatch):
    monkeypatch.setitem(sync_mod._status, "reachable", False)
    monkeypatch.setitem(sync_mod._status, "last_action", None)
    monkeypatch.setitem(sync_mod._status, "detail", "/mnt/nas")

    r = client.get("/api/sync/status")
    assert r.status_code == 200
    data = r.json()
    assert data["reachable"] is False
    assert data["detail"] == "/mnt/nas"


def test_sync_status_endpoint_pulled_update(client, monkeypatch):
    monkeypatch.setitem(sync_mod._status, "reachable", True)
    monkeypatch.setitem(sync_mod._status, "last_action", "pulled_update")
    monkeypatch.setitem(sync_mod._status, "detail", "2026-06-22T10:00:00")

    data = client.get("/api/sync/status").json()
    assert data["last_action"] == "pulled_update"
    assert data["detail"] == "2026-06-22T10:00:00"
