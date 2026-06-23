"""
NAS sync — called once at FastAPI startup (before requests are served).

Decision tree:
  NAS unreachable          → warn, continue offline
  NAS reachable, no file   → push local DB up (first run)
  NAS newer than marker    → backup local, pull NAS down, update marker
  local is current         → skip
"""
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import LOCAL_DB_PATH, NAS_SHARE_PATH

logger = logging.getLogger(__name__)

# Updated during sync; read by GET /api/sync/status.
_status: dict = {"reachable": None, "last_action": None, "detail": None}


def get_status() -> dict:
    return dict(_status)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _marker_path() -> Path:
    """Marker file records the mtime of the NAS file at last pull/push."""
    return Path(str(LOCAL_DB_PATH) + ".synced_at")


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


# ── Public entry point ────────────────────────────────────────────────────────

def sync_from_nas() -> None:
    nas_path = Path(NAS_SHARE_PATH)
    nas_dir = nas_path.parent

    # ── 1. Reachability check ─────────────────────────────────────────────
    if not nas_dir.exists() or not os.access(str(nas_dir), os.R_OK):
        logger.warning(
            "NAS sync: share unreachable at '%s' — starting offline with local copy",
            nas_dir,
        )
        _status.update({"reachable": False, "last_action": None, "detail": str(nas_dir)})
        return

    _status["reachable"] = True

    # ── 2. NAS file absent → first run, push local copy up ────────────────
    if not nas_path.exists():
        if LOCAL_DB_PATH.exists():
            nas_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(LOCAL_DB_PATH), str(nas_path))
            _write_marker(nas_path.stat().st_mtime)
            logger.info("NAS sync: first run — pushed local DB → %s", nas_path)
            _status.update({"last_action": "pushed_initial", "detail": str(nas_path)})
        else:
            logger.info("NAS sync: NAS file absent and no local DB yet — nothing to sync")
            _status.update({"last_action": "skipped_empty", "detail": None})
        return

    # ── 3. Compare NAS mtime against last-pull marker ─────────────────────
    nas_mtime = nas_path.stat().st_mtime
    last_pull = _read_marker()

    if last_pull is None or nas_mtime > last_pull:
        if LOCAL_DB_PATH.exists():
            bak = LOCAL_DB_PATH.with_suffix(".db.bak")
            shutil.copy2(str(LOCAL_DB_PATH), str(bak))
            logger.info("NAS sync: backed up local DB → %s", bak)

        LOCAL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(nas_path), str(LOCAL_DB_PATH))
        _write_marker(nas_mtime)
        ts = datetime.fromtimestamp(nas_mtime).isoformat(timespec="seconds")
        logger.info("NAS sync: pulled update from NAS (NAS last modified %s)", ts)
        _status.update({"last_action": "pulled_update", "detail": ts})
    else:
        logger.info("NAS sync: local copy is current — skipped pull")
        _status.update({"last_action": "skipped_current", "detail": None})
