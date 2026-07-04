"""Tests for app/sync.py and the sync-related API endpoints."""
import json
import sqlite3 as _sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytest

import app.sync as sync_mod
from app.sync import disable_sync, sync_from_nas, sync_to_nas


# ── SQLite DB helpers ─────────────────────────────────────────────────────────

def _make_sqlite_db(path: Path, value: str = "test") -> None:
    """Create a minimal valid SQLite DB with one row at path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _sqlite3.connect(str(path))
    conn.execute("CREATE TABLE data (v TEXT)")
    conn.execute("INSERT INTO data VALUES (?)", (value,))
    conn.commit()
    conn.close()


def _read_sqlite_value(path: Path) -> Optional[str]:
    """Read the single value from a DB created by _make_sqlite_db."""
    conn = _sqlite3.connect(str(path))
    row = conn.execute("SELECT v FROM data").fetchone()
    conn.close()
    return row[0] if row else None

_FULL_STATUS_KEYS = {"reachable", "last_action", "detail", "lock_warning", "last_push", "sync_enabled"}

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_status():
    """Restore _status to a pristine state before and after every test."""
    _clean = {
        "reachable": None, "last_action": None, "detail": None,
        "lock_warning": None, "last_push": None, "sync_enabled": True,
    }
    sync_mod._status.update(_clean)
    yield
    sync_mod._status.update(_clean)


def _patch(monkeypatch, nas_path: Path, local_db_path: Path, hostname: str = "testhost"):
    monkeypatch.setattr(sync_mod, "NAS_SHARE_PATH", str(nas_path))
    monkeypatch.setattr(sync_mod, "LOCAL_DB_PATH", local_db_path)
    monkeypatch.setattr(sync_mod, "_OWN_HOSTNAME", hostname)
    monkeypatch.setattr(sync_mod, "SYNC_INTERVAL_MINUTES", 5)


# ── sync_from_nas: reachability ───────────────────────────────────────────────

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


# ── sync_from_nas: first-run push ─────────────────────────────────────────────

def test_pushes_initial_copy_when_nas_file_absent(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    local_db = tmp_path / "local.db"
    _make_sqlite_db(local_db, "local-content")
    _patch(monkeypatch, nas_dir / "expenses.db", local_db)
    sync_from_nas()
    assert _read_sqlite_value(nas_dir / "expenses.db") == "local-content"
    assert sync_mod._status["last_action"] == "pushed_initial"


def test_push_writes_marker(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    local_db = tmp_path / "local.db"
    _make_sqlite_db(local_db)
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


# ── sync_from_nas: pull ───────────────────────────────────────────────────────

def test_pulls_when_no_marker_exists(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    nas_db.write_bytes(b"nas-content")
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    local_db = local_dir / "local.db"
    _make_sqlite_db(local_db, "old-local")
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
    _make_sqlite_db(local_db, "stale-local")
    Path(str(local_db) + ".synced_at").write_text(str(nas_db.stat().st_mtime - 100))
    _patch(monkeypatch, nas_db, local_db)
    sync_from_nas()
    assert local_db.read_bytes() == b"updated-nas"
    assert sync_mod._status["last_action"] == "pulled_update"


def test_pull_removes_stale_local_wal_sidecars(monkeypatch, tmp_path):
    """A leftover local -wal from a previous run must not be replayed on top
    of the freshly pulled DB — that would corrupt it."""
    nas_db = tmp_path / "nas" / "expenses.db"
    _make_sqlite_db(nas_db, "from-nas")
    local_db = tmp_path / "local.db"
    _make_sqlite_db(local_db, "old-local")
    Path(str(local_db) + "-wal").write_bytes(b"stale wal garbage")
    Path(str(local_db) + "-shm").write_bytes(b"stale shm garbage")
    _patch(monkeypatch, nas_db, local_db)

    sync_from_nas()

    assert sync_mod._status["last_action"] == "pulled_update"
    assert not Path(str(local_db) + "-wal").exists()
    assert not Path(str(local_db) + "-shm").exists()
    assert _read_sqlite_value(local_db) == "from-nas"


def test_pull_creates_backup_of_existing_local(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    nas_db.write_bytes(b"nas-content")
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    local_db = local_dir / "local.db"
    _make_sqlite_db(local_db, "precious")
    _patch(monkeypatch, nas_db, local_db)
    sync_from_nas()
    bak = local_db.with_suffix(".db.bak")
    assert bak.exists()
    assert _read_sqlite_value(bak) == "precious"


def test_pull_no_backup_when_local_absent(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    nas_db.write_bytes(b"nas-content")
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    local_db = local_dir / "local.db"
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
    local_db = tmp_path / "deep" / "nested" / "local.db"
    _patch(monkeypatch, nas_db, local_db)
    sync_from_nas()
    assert local_db.exists()
    assert local_db.read_bytes() == b"content"


# ── sync_from_nas: skip ───────────────────────────────────────────────────────

def test_skips_when_marker_matches_nas_mtime(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    nas_db.write_bytes(b"nas-content")
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    local_db = local_dir / "local.db"
    local_db.write_bytes(b"current-local")
    Path(str(local_db) + ".synced_at").write_text(str(nas_db.stat().st_mtime))
    _patch(monkeypatch, nas_db, local_db)
    sync_from_nas()
    assert local_db.read_bytes() == b"current-local"
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
    Path(str(local_db) + ".synced_at").write_text(str(nas_db.stat().st_mtime + 100))
    _patch(monkeypatch, nas_db, local_db)
    sync_from_nas()
    assert sync_mod._status["last_action"] == "skipped_current"


# ── sync_from_nas: lock file written after sync ───────────────────────────────

def test_lock_written_after_pull(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    nas_db.write_bytes(b"nas-content")
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    local_db = local_dir / "local.db"
    _patch(monkeypatch, nas_db, local_db)
    sync_from_nas()
    lock = json.loads((nas_dir / "expenses.db.lock").read_text())
    assert lock["hostname"] == "testhost"
    assert lock["timestamp"] > 0


def test_lock_written_after_skip(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    nas_db.write_bytes(b"nas-content")
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    local_db = local_dir / "local.db"
    local_db.write_bytes(b"current-local")
    Path(str(local_db) + ".synced_at").write_text(str(nas_db.stat().st_mtime))
    _patch(monkeypatch, nas_db, local_db)
    sync_from_nas()
    assert (nas_dir / "expenses.db.lock").exists()


def test_lock_written_after_first_run_push(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    local_db = tmp_path / "local.db"
    _make_sqlite_db(local_db, "local-content")
    _patch(monkeypatch, nas_dir / "expenses.db", local_db)
    sync_from_nas()
    assert (nas_dir / "expenses.db.lock").exists()


# ── sync_from_nas: foreign lock conflict ─────────────────────────────────────

def _write_lock_file(nas_dir: Path, hostname: str, age_seconds: float = 10):
    lock_data = {
        "hostname": hostname,
        "timestamp": datetime.now().timestamp() - age_seconds,
    }
    (nas_dir / "expenses.db.lock").write_text(json.dumps(lock_data))


def test_foreign_fresh_lock_sets_warning(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    nas_db.write_bytes(b"nas-content")
    _write_lock_file(nas_dir, hostname="other-machine", age_seconds=30)
    local_db = tmp_path / "local.db"
    _patch(monkeypatch, nas_db, local_db)

    sync_from_nas()

    assert sync_mod._status["lock_warning"] == "other-machine"
    assert sync_mod._status["last_action"] == "lock_conflict"


def test_foreign_fresh_lock_prevents_pull(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    nas_db.write_bytes(b"nas-content")
    _write_lock_file(nas_dir, hostname="other-machine", age_seconds=30)
    local_db = tmp_path / "local.db"
    _patch(monkeypatch, nas_db, local_db)

    sync_from_nas()

    assert not local_db.exists()


def test_own_lock_does_not_block(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    nas_db.write_bytes(b"nas-content")
    _write_lock_file(nas_dir, hostname="testhost", age_seconds=30)
    local_db = tmp_path / "local.db"
    _patch(monkeypatch, nas_db, local_db)

    sync_from_nas()

    assert sync_mod._status["lock_warning"] is None
    assert local_db.read_bytes() == b"nas-content"


def test_stale_foreign_lock_is_ignored(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    nas_db.write_bytes(b"nas-content")
    # Age is 10 minutes = 600 s, interval is 5 min — lock is stale
    _write_lock_file(nas_dir, hostname="other-machine", age_seconds=600)
    local_db = tmp_path / "local.db"
    _patch(monkeypatch, nas_db, local_db)

    sync_from_nas()

    assert sync_mod._status["lock_warning"] is None
    assert local_db.read_bytes() == b"nas-content"


# ── sync_to_nas ───────────────────────────────────────────────────────────────

def test_sync_to_nas_pushes_local_to_nas(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    local_db = tmp_path / "local.db"
    _make_sqlite_db(local_db, "latest-local")
    _patch(monkeypatch, nas_db, local_db)

    sync_to_nas()

    assert _read_sqlite_value(nas_db) == "latest-local"
    assert sync_mod._status["last_action"] == "pushed_update"


def test_push_snapshot_is_self_contained_despite_wal_source(monkeypatch, tmp_path):
    """Un-checkpointed WAL writes must land in the NAS .db file itself —
    pulls copy only that file, so a -wal sidecar on the NAS is silent data loss."""
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    local_db = tmp_path / "local.db"

    # WAL-mode local DB with a commit that stays in the -wal file: the open
    # connection prevents the close-time checkpoint.
    conn = _sqlite3.connect(str(local_db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE data (v TEXT)")
    conn.execute("INSERT INTO data VALUES ('wal-only-row')")
    conn.commit()
    assert Path(str(local_db) + "-wal").stat().st_size > 0

    _patch(monkeypatch, nas_db, local_db)
    sync_to_nas()
    conn.close()

    assert _read_sqlite_value(nas_db) == "wal-only-row"
    assert not Path(str(nas_db) + "-wal").exists()
    check = _sqlite3.connect(str(nas_db))
    assert check.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    check.close()


def test_sync_to_nas_writes_marker(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    local_db = tmp_path / "local.db"
    _make_sqlite_db(local_db)
    _patch(monkeypatch, nas_dir / "expenses.db", local_db)

    sync_to_nas()

    marker = Path(str(local_db) + ".synced_at")
    assert marker.exists()
    assert float(marker.read_text()) > 0


def test_sync_to_nas_writes_lock(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    local_db = tmp_path / "local.db"
    _make_sqlite_db(local_db)
    _patch(monkeypatch, nas_dir / "expenses.db", local_db)

    sync_to_nas()

    lock = json.loads((nas_dir / "expenses.db.lock").read_text())
    assert lock["hostname"] == "testhost"


def test_sync_to_nas_records_last_push_timestamp(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    local_db = tmp_path / "local.db"
    _make_sqlite_db(local_db)
    _patch(monkeypatch, nas_dir / "expenses.db", local_db)

    sync_to_nas()

    assert sync_mod._status["last_push"] is not None
    datetime.fromisoformat(sync_mod._status["last_push"])  # must parse cleanly


def test_sync_to_nas_clears_lock_warning(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    local_db = tmp_path / "local.db"
    _make_sqlite_db(local_db)
    _patch(monkeypatch, nas_dir / "expenses.db", local_db)
    sync_mod._status["lock_warning"] = "other-machine"

    sync_to_nas()

    assert sync_mod._status["lock_warning"] is None


def test_sync_to_nas_skips_when_nas_unreachable(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path / "missing" / "db", tmp_path / "local.db")
    sync_mod._status["reachable"] = True  # was reachable before

    sync_to_nas()

    assert sync_mod._status["reachable"] is False
    assert sync_mod._status["last_action"] != "pushed_update"


def test_sync_to_nas_skips_when_local_db_absent(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    _patch(monkeypatch, nas_db, tmp_path / "nonexistent.db")

    sync_to_nas()

    assert not nas_db.exists()


def test_sync_to_nas_skips_when_disabled(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    local_db = tmp_path / "local.db"
    local_db.write_bytes(b"content")
    _patch(monkeypatch, nas_db, local_db)
    sync_mod._status["sync_enabled"] = False

    sync_to_nas()

    assert not nas_db.exists()
    assert sync_mod._status["last_action"] != "pushed_update"


# ── lock helpers ──────────────────────────────────────────────────────────────

def test_release_lock_removes_own_lock(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    local_db = tmp_path / "local.db"
    _make_sqlite_db(local_db)
    _patch(monkeypatch, nas_dir / "expenses.db", local_db)

    sync_to_nas()  # writes lock
    assert (nas_dir / "expenses.db.lock").exists()

    sync_mod._release_lock()
    assert not (nas_dir / "expenses.db.lock").exists()


def test_release_lock_does_not_remove_foreign_lock(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    lock_file = nas_dir / "expenses.db.lock"
    lock_file.write_text(json.dumps({"hostname": "other-host", "timestamp": 9999}))
    _patch(monkeypatch, nas_dir / "expenses.db", tmp_path / "local.db")

    sync_mod._release_lock()

    assert lock_file.exists()


def test_release_lock_is_safe_when_no_lock_file(monkeypatch, tmp_path):
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    _patch(monkeypatch, nas_dir / "expenses.db", tmp_path / "local.db")
    sync_mod._release_lock()  # must not raise


# ── disable_sync ──────────────────────────────────────────────────────────────

def test_disable_sync_sets_flag(monkeypatch, tmp_path):
    disable_sync()
    assert sync_mod._status["sync_enabled"] is False


def test_disable_sync_clears_lock_warning(monkeypatch, tmp_path):
    sync_mod._status["lock_warning"] = "some-host"
    disable_sync()
    assert sync_mod._status["lock_warning"] is None


# ── get_status ────────────────────────────────────────────────────────────────

def test_get_status_returns_copy(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path / "nonexistent" / "db", tmp_path / "local.db")
    sync_from_nas()
    s = sync_mod.get_status()
    assert set(s.keys()) == _FULL_STATUS_KEYS
    s["reachable"] = "tampered"
    assert sync_mod._status["reachable"] is False


# ── /api/sync/status endpoint ─────────────────────────────────────────────────

def test_sync_status_endpoint_has_all_fields(client):
    r = client.get("/api/sync/status")
    assert r.status_code == 200
    assert set(r.json().keys()) == _FULL_STATUS_KEYS


def test_sync_status_endpoint_reflects_lock_warning(client, monkeypatch):
    monkeypatch.setitem(sync_mod._status, "lock_warning", "remote-host")
    data = client.get("/api/sync/status").json()
    assert data["lock_warning"] == "remote-host"


# ── POST /api/sync ────────────────────────────────────────────────────────────

def test_post_sync_calls_sync_to_nas(client, monkeypatch):
    called = []
    monkeypatch.setattr(sync_mod, "sync_to_nas", lambda: called.append(True))
    r = client.post("/api/sync")
    assert r.status_code == 200
    assert called


def test_post_sync_returns_status_shape(client, monkeypatch):
    monkeypatch.setattr(sync_mod, "sync_to_nas", lambda: None)
    data = client.post("/api/sync").json()
    assert set(data.keys()) == _FULL_STATUS_KEYS


# ── POST /api/sync/go-offline ─────────────────────────────────────────────────

def test_go_offline_disables_sync(client):
    r = client.post("/api/sync/go-offline")
    assert r.status_code == 200
    assert sync_mod._status["sync_enabled"] is False


def test_go_offline_returns_status_shape(client):
    data = client.post("/api/sync/go-offline").json()
    assert set(data.keys()) == _FULL_STATUS_KEYS


def test_go_offline_clears_lock_warning(client, monkeypatch):
    monkeypatch.setitem(sync_mod._status, "lock_warning", "remote-host")
    client.post("/api/sync/go-offline")
    assert sync_mod._status["lock_warning"] is None


# ── WAL safety ────────────────────────────────────────────────────────────────

def test_sync_to_nas_includes_wal_data(monkeypatch, tmp_path):
    """sync_to_nas must capture un-checkpointed WAL writes in the NAS copy."""
    nas_dir = tmp_path / "nas"
    nas_dir.mkdir()
    nas_db = nas_dir / "expenses.db"
    local_db = tmp_path / "local.db"
    _patch(monkeypatch, nas_db, local_db)

    # Set up DB in WAL mode with autocheckpoint disabled so writes stay in WAL.
    writer = _sqlite3.connect(str(local_db))
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("CREATE TABLE data (v TEXT)")
    writer.execute("INSERT INTO data VALUES ('wal-row')")
    writer.commit()

    # Hold a reader connection open — prevents the auto-checkpoint that would
    # otherwise run when the writer closes, keeping the WAL non-empty.
    reader = _sqlite3.connect(str(local_db))
    reader.execute("PRAGMA journal_mode=WAL")

    writer.close()

    wal_file = Path(str(local_db) + "-wal")
    assert wal_file.exists() and wal_file.stat().st_size > 0, (
        "WAL file must be non-empty to prove the row is not yet in the main DB"
    )

    sync_to_nas()

    reader.close()

    # The NAS copy must contain the row even though it was only in the WAL.
    nas_conn = _sqlite3.connect(str(nas_db))
    row = nas_conn.execute("SELECT v FROM data").fetchone()
    nas_conn.close()
    assert row is not None and row[0] == "wal-row"
