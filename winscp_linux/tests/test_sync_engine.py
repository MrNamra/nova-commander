"""Unit tests for SyncEngine auto-sync and cache management."""

import os
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from src.core.sync_engine import SyncEngine


def test_cache_path_computation():
    engine = SyncEngine()
    session_id = "root_192.168.1.1_22"
    remote_path = "/var/www/html/index.php"

    cache_path = engine.get_cache_path(session_id, remote_path)
    expected_suffix = Path(session_id) / "var/www/html/index.php"
    assert str(cache_path).endswith(str(expected_suffix))
    assert str(cache_path).startswith("/tmp/winscp-linux-cache")


def test_sync_engine_cleanup():
    engine = SyncEngine()
    session_id = "test_cleanup_session"
    remote_path = "/etc/nginx/nginx.conf"

    cache_path = engine.get_cache_path(session_id, remote_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("server { listen 80; }")

    assert cache_path.exists()

    engine.cleanup_session(session_id)
    assert not cache_path.exists()
