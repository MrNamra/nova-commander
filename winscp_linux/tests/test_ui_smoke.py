"""Smoke tests verifying PyQt6 UI dialogs and MainWindow initialize cleanly."""

import os
import pytest
from PyQt6.QtWidgets import QApplication

# Set headless platform before any Qt widget instantiation
os.environ["QT_QPA_PLATFORM"] = "offscreen"

@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_main_window_instantiation(qapp, tmp_path):
    from src.core.session_store import SessionStore
    from src.ui.main_window import MainWindow, ChmodDialog

    store_file = tmp_path / "test_sessions.ini"
    store = SessionStore(storage_path=store_file)

    win = MainWindow(store)
    assert win.windowTitle() == "WinSCP-Linux"
    assert win.table_local.rowCount() >= 0

    # Chmod Dialog
    chmod_dlg = ChmodDialog("test.txt", "755", win)
    assert chmod_dlg.get_mode_int() == 0o755
    win.close()


def test_session_dialog_instantiation(qapp, tmp_path):
    from src.core.session_store import SessionConfig, SessionStore
    from src.ui.session_dialog import SessionDialog, KeyManagerDialog

    store_file = tmp_path / "test_sessions.ini"
    store = SessionStore(storage_path=store_file)
    store.save_session(SessionConfig(name="Web1", host="10.0.0.1", username="admin"))

    dlg = SessionDialog(store)
    assert dlg.windowTitle().startswith("WinSCP-Linux")
    assert dlg.tree.topLevelItemCount() > 0

    km_dlg = KeyManagerDialog(dlg)
    assert km_dlg.windowTitle() == "Manage SSH Keys"
    dlg.close()


def test_export_dialog_instantiation(qapp, tmp_path):
    from src.core.session_store import SessionConfig, SessionStore
    from src.ui.export_dialog import ExportDialog

    store_file = tmp_path / "test_sessions.ini"
    store = SessionStore(storage_path=store_file)
    store.save_session(SessionConfig(name="ServerX", host="1.2.3.4"))

    dlg = ExportDialog(store)
    assert dlg.session_list.count() == 1
    dlg.close()
