"""Tests for the NAS sync stub."""
import pytest

from app.nas import sync_from_nas, sync_to_nas


def test_sync_to_nas_not_implemented():
    with pytest.raises(NotImplementedError):
        sync_to_nas()


def test_sync_from_nas_not_implemented():
    with pytest.raises(NotImplementedError):
        sync_from_nas()
