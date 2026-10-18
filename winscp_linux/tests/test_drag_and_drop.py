"""Unit tests for bidirectional Drag & Drop functionality and batch transfers."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PyQt6.QtGui import QDropEvent
from PyQt6.QtWidgets import QApplication

os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_commander_table_widget_properties(qapp):
    from src.ui.main_window import CommanderTableWidget

    table = CommanderTableWidget("local")
    assert table.pane_type == "local"
    assert table.dragEnabled() is True
    assert table.acceptDrops() is True

    table.set_current_directory("/home/user/docs")
    assert table.get_current_directory() == "/home/user/docs"


def test_commander_table_drop_local_to_remote(qapp):
    from src.ui.main_window import CommanderTableWidget

    remote_table = CommanderTableWidget("remote")
    remote_table.set_current_directory("/var/www/html")

    captured_events = []
    remote_table.items_dropped.connect(lambda d: captured_events.append(d))

    # Simulate dropping local items
    items_to_drop = [
        {"name": "index.html", "path": "/home/user/index.html", "is_dir": False},
        {"name": "assets", "path": "/home/user/assets", "is_dir": True},
    ]

    mime = QMimeData()
    mime.setData("application/x-winscp-local-items", json.dumps(items_to_drop).encode("utf-8"))

    drop_event = QDropEvent(
        QPointF(10, 10),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    remote_table.dropEvent(drop_event)

    assert len(captured_events) == 1
    event_data = captured_events[0]
    assert event_data["source_pane"] == "local"
    assert event_data["target_pane"] == "remote"
    assert event_data["target_folder"] == "/var/www/html"
    assert len(event_data["items"]) == 2
    assert event_data["items"][0]["name"] == "index.html"
    assert event_data["items"][1]["is_dir"] is True


def test_commander_table_drop_remote_to_local(qapp):
    from src.ui.main_window import CommanderTableWidget

    local_table = CommanderTableWidget("local")
    local_table.set_current_directory("/home/user/Downloads")

    captured_events = []
    local_table.items_dropped.connect(lambda d: captured_events.append(d))

    # Simulate dropping remote items
    remote_items = [
        {"name": "server_backup.tar.gz", "path": "/backups/server_backup.tar.gz", "is_dir": False},
        {"name": "logs", "path": "/var/log/nginx", "is_dir": True},
    ]

    mime = QMimeData()
    mime.setData("application/x-winscp-remote-items", json.dumps(remote_items).encode("utf-8"))

    drop_event = QDropEvent(
        QPointF(10, 10),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    local_table.dropEvent(drop_event)

    assert len(captured_events) == 1
    event_data = captured_events[0]
    assert event_data["source_pane"] == "remote"
    assert event_data["target_pane"] == "local"
    assert event_data["target_folder"] == "/home/user/Downloads"
    assert len(event_data["items"]) == 2
    assert event_data["items"][0]["name"] == "server_backup.tar.gz"
    assert event_data["items"][1]["name"] == "logs"
    assert event_data["items"][1]["is_dir"] is True


def test_batch_transfer_worker(qapp, tmp_path):
    from src.core.sftp_client import SFTPBatchTransferWorker, SFTPClient

    mock_client = MagicMock(spec=SFTPClient)
    transfers = [
        {"source": str(tmp_path / "file1.txt"), "dest": "/remote/file1.txt", "is_dir": False, "direction": "upload"},
        {"source": str(tmp_path / "folder1"), "dest": "/remote/folder1", "is_dir": True, "direction": "upload"},
    ]

    worker = SFTPBatchTransferWorker(mock_client, transfers)
    finished_counts = []
    worker.finished.connect(lambda c: finished_counts.append(c))

    worker.run()

    assert mock_client.upload_file.called
    assert mock_client.upload_directory.called
    assert finished_counts == [2]
