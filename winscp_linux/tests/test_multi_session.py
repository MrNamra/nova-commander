"""Unit tests for Multi-Session tabbed SSH/SFTP connection management."""

import os
import pytest
from PyQt6.QtWidgets import QApplication

from src.core.session_store import SessionConfig, SessionStore
from src.ui.main_window import MainWindow, SessionCommanderView

os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_multi_session_tabs_creation_and_switching(qapp, tmp_path):
    store = SessionStore(storage_path=tmp_path / "sessions.ini")
    win = MainWindow(store)

    # Initial default tab
    assert win.tab_widget.count() == 1
    assert isinstance(win.active_session_view(), SessionCommanderView)

    # Connect to Server 1
    s1 = SessionConfig(name="Server_Alpha", host="192.168.1.10", username="root")
    win.connect_to_session(s1)

    assert win.tab_widget.count() == 1
    assert "Server_Alpha" in win.tab_widget.tabText(0)

    # Connect to Server 2 -> Opens second tab!
    s2 = SessionConfig(name="Server_Beta", host="10.0.0.5", username="ubuntu")
    win.connect_to_session(s2)

    assert win.tab_widget.count() == 2
    assert "Server_Beta" in win.tab_widget.tabText(1)
    assert win.tab_widget.currentIndex() == 1

    # Connect to Server 3 -> Opens third tab!
    s3 = SessionConfig(name="Server_Gamma", host="172.16.0.2", username="deploy")
    win.connect_to_session(s3)

    assert win.tab_widget.count() == 3
    assert "Server_Gamma" in win.tab_widget.tabText(2)
    assert win.tab_widget.currentIndex() == 2

    # Switch back to Tab 0
    win.tab_widget.setCurrentIndex(0)
    assert win.tab_widget.currentIndex() == 0
    view0 = win.active_session_view()
    assert view0.current_session.name == "Server_Alpha"

    # Close Tab 1 (Server_Beta)
    win.tab_widget.setCurrentIndex(1)
    win._close_session_tab(1)
    assert win.tab_widget.count() == 2

    # Remaining tabs are Server_Alpha and Server_Gamma
    assert "Server_Alpha" in win.tab_widget.tabText(0)
    assert "Server_Gamma" in win.tab_widget.tabText(1)

    win.close()
