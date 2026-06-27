"""Tests for config loading."""
from pathlib import Path

import app.config as cfg


def test_local_db_path_is_absolute():
    assert cfg.LOCAL_DB_PATH.is_absolute()


def test_local_db_path_expands_home():
    assert "~" not in str(cfg.LOCAL_DB_PATH)


def test_local_db_path_is_path_instance():
    assert isinstance(cfg.LOCAL_DB_PATH, Path)


def test_nas_share_path_set():
    assert cfg.NAS_SHARE_PATH
    assert isinstance(cfg.NAS_SHARE_PATH, str)


def test_sync_flags_are_bool():
    assert isinstance(cfg.SYNC_ON_STARTUP, bool)
    assert isinstance(cfg.SYNC_ON_SHUTDOWN, bool)


def test_sync_interval_is_int():
    assert isinstance(cfg.SYNC_INTERVAL_MINUTES, int)
    assert cfg.SYNC_INTERVAL_MINUTES > 0


def test_faresight_db_env_overrides_config(monkeypatch, tmp_path):
    override = str(tmp_path / "test.db")
    monkeypatch.setenv("FARESIGHT_DB", override)
    import importlib
    importlib.reload(cfg)
    assert str(cfg.LOCAL_DB_PATH) == override
    # Restore so other tests see the original value
    monkeypatch.delenv("FARESIGHT_DB")
    importlib.reload(cfg)
