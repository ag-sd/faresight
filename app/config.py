import os
from pathlib import Path

import yaml


def _load() -> dict:
    cfg_path = Path(__file__).parent.parent / "config.yaml"
    with open(cfg_path) as f:
        return yaml.safe_load(f)


_cfg = _load()

NAS_SHARE_PATH: str = _cfg["nas_share_path"]
LOCAL_DB_PATH: Path = Path(
    os.environ.get("FARESIGHT_DB") or os.path.expanduser(_cfg["local_db_path"])
)
SYNC_ON_STARTUP: bool = _cfg.get("sync_on_startup", False)
SYNC_ON_SHUTDOWN: bool = _cfg.get("sync_on_shutdown", False)
SYNC_INTERVAL_MINUTES: int = _cfg.get("sync_interval_minutes", 5)
BANK_LOGOS: dict = _cfg.get("bank_logos", {})
OLLAMA_HOST: str = _cfg.get("ollama_host", "http://localhost:11434")
OLLAMA_MODEL: str = _cfg.get("ollama_model", "llama3.2:3b")
CATEGORIZATION_POLL_INTERVAL_S: int = _cfg.get("categorization_poll_interval_s", 10)
PAGE_SIZE: int = int(_cfg.get("page_size", 25))
TOP_CARD_PAGE_LIMIT: int = int(_cfg.get("top_card_page_limit", 5))
