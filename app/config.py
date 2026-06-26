import os
from pathlib import Path

import yaml


def _load() -> dict:
    cfg_path = Path(__file__).parent.parent / "config.yaml"
    with open(cfg_path) as f:
        return yaml.safe_load(f)


_cfg = _load()

NAS_SHARE_PATH: str = _cfg["nas_share_path"]
LOCAL_DB_PATH: Path = Path(os.path.expanduser(_cfg["local_db_path"]))
SYNC_ON_STARTUP: bool = _cfg.get("sync_on_startup", False)
SYNC_ON_SHUTDOWN: bool = _cfg.get("sync_on_shutdown", False)
SYNC_INTERVAL_MINUTES: int = _cfg.get("sync_interval_minutes", 5)
BANK_LOGOS: dict = _cfg.get("bank_logos", {})
