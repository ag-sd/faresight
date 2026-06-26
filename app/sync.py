"""
NAS sync — startup pull, periodic push, shutdown push, lock file.

Startup (sync_from_nas):
  NAS unreachable       → warn, continue offline
  Foreign active lock   → warn + set lock_warning; user must confirm via POST /api/sync
  NAS file absent       → push local DB up (first-run bootstrap)
  NAS newer than marker → backup local, pull NAS down, update marker
  Local current         → skip pull
  After any successful sync → write our .lock file claiming the DB

Push (sync_to_nas):
  Called by the periodic background loop, on graceful shutdown,
  and by POST /api/sync ("Sync now" / "Proceed anyway").

Shutdown:
  Background loop cancelled → final sync_to_nas() → _release_lock()
"""
import asyncio
import json
import logging
import os
import shutil
import socket
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import LOCAL_DB_PATH, NAS_SHARE_PATH, SYNC_INTERVAL_MINUTES

logger = logging.getLogger(__name__)

# Resolved once at import; can be monkeypatched in tests.
_OWN_HOSTNAME: str = socket.gethostname()

_status: dict = {
    "reachable": None,
    "last_action": None,
    "detail": None,
    "lock_warning": None,   # hostname of active foreign lock, or None
    "last_push": None,      # ISO timestamp of last successful push to NAS
    "sync_enabled": True,   # False when user chooses "Work offline"
}


def get_status() -> dict:
    return dict(_status)


# ── Path helpers (reference module globals so monkeypatching works) ────────────

def _marker_path() -> Path:
    return Path(str(LOCAL_DB_PATH) + ".synced_at")


def _lock_path() -> Path:
    return Path(str(NAS_SHARE_PATH) + ".lock")


# ── Marker ────────────────────────────────────────────────────────────────────

def _read_marker() -> Optional[float]:
    p = _marker_path()
    if not p.exists():
        return None
    try:
        return float(p.read_text().strip())
    except (ValueError, OSError):
        return None


def _write_marker(mtime: float) -> None:
    _marker_path().write_text(str(mtime))


# ── Lock file ─────────────────────────────────────────────────────────────────

def _read_lock() -> Optional[dict]:
    try:
        return json.loads(_lock_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _write_lock() -> None:
    data = {"hostname": _OWN_HOSTNAME, "timestamp": datetime.now().timestamp()}
    try:
        _lock_path().write_text(json.dumps(data))
    except OSError as exc:
        logger.warning("NAS sync: could not write lock file: %s", exc)


def _release_lock() -> None:
    """Remove the lock file only if it currently belongs to us."""
    lock = _read_lock()
    if lock and lock.get("hostname") == _OWN_HOSTNAME:
        try:
            _lock_path().unlink(missing_ok=True)
            logger.info("NAS sync: released lock file")
        except OSError as exc:
            logger.warning("NAS sync: could not release lock file: %s", exc)


def _check_foreign_lock() -> Optional[str]:
    """
    Return the hostname from a fresh, foreign lock file; None otherwise.
    A lock is "fresh" (still active) when its age < sync_interval_minutes.
    """
    lock = _read_lock()
    if lock is None:
        return None
    hostname = lock.get("hostname", "")
    if hostname == _OWN_HOSTNAME:
        return None
    age = datetime.now().timestamp() - lock.get("timestamp", 0)
    if age < SYNC_INTERVAL_MINUTES * 60:
        return hostname
    logger.info("NAS sync: ignoring stale lock (age=%.0fs) from '%s'", age, hostname)
    return None


# ── Reachability ──────────────────────────────────────────────────────────────

def _nas_reachable() -> bool:
    nas_dir = Path(NAS_SHARE_PATH).parent
    return nas_dir.exists() and os.access(str(nas_dir), os.R_OK)


# ── Public sync operations ────────────────────────────────────────────────────

def sync_from_nas() -> None:
    """
    Called once at startup before requests are served. Pulls from NAS if
    the NAS copy is newer. Sets _status for the /api/sync/status endpoint.
    """
    nas_path = Path(NAS_SHARE_PATH)

    # ── 1. Reachability ───────────────────────────────────────────────────
    if not _nas_reachable():
        logger.warning(
            "NAS sync: share unreachable at '%s' — starting offline with local copy",
            nas_path.parent,
        )
        _status.update({
            "reachable": False, "last_action": None, "detail": str(nas_path.parent),
        })
        return

    _status["reachable"] = True

    # ── 2. Foreign lock check ─────────────────────────────────────────────
    foreign_host = _check_foreign_lock()
    if foreign_host:
        logger.warning(
            "NAS sync: DB is locked by '%s' — skipping pull; "
            "user must confirm via 'Proceed anyway'",
            foreign_host,
        )
        _status.update({"lock_warning": foreign_host, "last_action": "lock_conflict"})
        return

    # ── 3. NAS file absent → first-run push ───────────────────────────────
    if not nas_path.exists():
        if LOCAL_DB_PATH.exists():
            nas_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(LOCAL_DB_PATH), str(nas_path))
            _write_marker(nas_path.stat().st_mtime)
            _write_lock()
            logger.info("NAS sync: first run — pushed local DB → %s", nas_path)
            _status.update({"last_action": "pushed_initial", "detail": str(nas_path)})
        else:
            logger.info("NAS sync: NAS file absent and no local DB yet — nothing to sync")
            _status.update({"last_action": "skipped_empty", "detail": None})
        return

    # ── 4. Compare NAS mtime against last-pull marker ─────────────────────
    nas_mtime = nas_path.stat().st_mtime
    last_pull = _read_marker()

    if last_pull is None or nas_mtime > last_pull:
        if LOCAL_DB_PATH.exists():
            bak = LOCAL_DB_PATH.with_suffix(".db.bak")
            shutil.copyfile(str(LOCAL_DB_PATH), str(bak))
            logger.info("NAS sync: backed up local DB → %s", bak)

        LOCAL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(nas_path), str(LOCAL_DB_PATH))
        _write_marker(nas_mtime)
        ts = datetime.fromtimestamp(nas_mtime).isoformat(timespec="seconds")
        logger.info("NAS sync: pulled update from NAS (NAS last modified %s)", ts)
        _status.update({"last_action": "pulled_update", "detail": ts})
    else:
        logger.info("NAS sync: local copy is current — skipped pull")
        _status.update({"last_action": "skipped_current", "detail": None})

    _write_lock()


def sync_to_nas() -> None:
    """
    Push local DB → NAS. No-op when sync is disabled or NAS is unreachable.
    Overwrites any existing lock (claiming ownership). Clears lock_warning.
    """
    if not _status.get("sync_enabled", True):
        logger.info("NAS sync: sync disabled for this session — skipping push")
        return

    if not _nas_reachable():
        logger.warning("NAS sync: share unreachable — skipping push")
        _status["reachable"] = False
        return

    if not LOCAL_DB_PATH.exists():
        logger.warning("NAS sync: local DB does not exist — nothing to push")
        return

    nas_path = Path(NAS_SHARE_PATH)
    nas_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(LOCAL_DB_PATH), str(nas_path))
    _write_marker(nas_path.stat().st_mtime)
    _write_lock()

    ts = datetime.now().isoformat(timespec="seconds")
    logger.info("NAS sync: pushed local DB → %s", nas_path)
    _status.update({
        "reachable": True,
        "last_action": "pushed_update",
        "last_push": ts,
        "lock_warning": None,   # claiming the lock clears any conflict
    })


def disable_sync() -> None:
    """User chose 'Work offline'. Stops all NAS pushes for this session."""
    _status.update({"sync_enabled": False, "lock_warning": None})
    logger.info("NAS sync: disabled for this session (user chose offline mode)")


# ── Background periodic loop ──────────────────────────────────────────────────

async def _periodic_sync_loop() -> None:
    interval = SYNC_INTERVAL_MINUTES * 60
    logger.info("NAS sync: background loop started (every %d min)", SYNC_INTERVAL_MINUTES)
    while True:
        await asyncio.sleep(interval)
        sync_to_nas()
