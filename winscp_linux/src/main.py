"""
Application Entry Point for WinSCP-Linux.
A native Linux alternative to WinSCP with dual-pane SFTP/SSH commander,
remote file direct-editing with inotify auto-sync, embedded terminal,
and AES-256-GCM encrypted cloud session sharing.
"""

from __future__ import annotations

import os
import signal
import sys
import traceback
from pathlib import Path

def _ensure_xcb_cursor():
    # Ensure Qt 6.5+ xcb-cursor0 dependency is loaded seamlessly on Linux
    if sys.platform.startswith("linux") and os.environ.get("_WINSCP_XCB_LOADED") != "1":
        candidates = [
            Path(__file__).resolve().parent.parent / "lib",
            Path.home() / ".local" / "lib",
        ]
        for c in candidates:
            if (c / "libxcb-cursor.so.0").exists():
                lib_path = str(c)
                current_ld = os.environ.get("LD_LIBRARY_PATH", "")
                if lib_path not in current_ld.split(":"):
                    os.environ["_WINSCP_XCB_LOADED"] = "1"
                    os.environ["LD_LIBRARY_PATH"] = f"{lib_path}:{current_ld}" if current_ld else lib_path
                    try:
                        argv = getattr(sys, "orig_argv", [sys.executable] + sys.argv)
                        os.execv(sys.executable, argv)
                    except Exception:
                        pass
                break

# Ensure root and src directory are in sys.path
_src_dir = Path(__file__).resolve().parent
_root_dir = _src_dir.parent
for _p in [str(_root_dir), str(_src_dir)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QPalette
from PyQt6.QtWidgets import QApplication

try:
    from src.core.session_store import SessionStore
    from src.ui.main_window import MainWindow
except ImportError:
    from core.session_store import SessionStore
    from ui.main_window import MainWindow


def apply_theme(app: QApplication, mode: str = "classic"):
    """Applies either Classic WinSCP Light theme or Modern Dark theme."""
    app.setStyle("Fusion")

    if mode == "classic":
        # Authentic Classic WinSCP Light / Windows VCL Palette
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(240, 240, 240))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(248, 248, 249))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 220))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.Text, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.Button, QColor(228, 228, 228))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
        palette.setColor(QPalette.ColorRole.Link, QColor(0, 102, 204))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(10, 36, 106))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
        app.setPalette(palette)

        app.setStyleSheet("""
            QMainWindow, QDialog {
                background-color: #f0f0f0;
                color: #000000;
            }
            QTableWidget {
                background-color: #ffffff;
                alternate-background-color: #f8f8f9;
                color: #000000;
                gridline-color: #dcdcdc;
                border: 1px solid #ababab;
                selection-background-color: #0a246a;
                selection-color: #ffffff;
            }
            QHeaderView::section {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #fcfcfc, stop:1 #e8e8e8);
                color: #000000;
                padding: 4px 6px;
                border: 1px solid #c8c8c8;
                font-weight: bold;
            }
            QLineEdit, QSpinBox, QComboBox, QTextEdit {
                background-color: #ffffff;
                color: #000000;
                border: 1px solid #7a7a7a;
                border-radius: 2px;
                padding: 3px 6px;
                selection-background-color: #0a246a;
                selection-color: #ffffff;
            }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QTextEdit:focus {
                border: 1px solid #0078d7;
            }
            QPushButton {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f5f5f5, stop:1 #e1e1e1);
                color: #000000;
                border: 1px solid #adadad;
                border-radius: 3px;
                padding: 4px 10px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #e5f1fb;
                border-color: #0078d7;
            }
            QPushButton:pressed {
                background-color: #cce8ff;
                border-color: #005499;
            }
            QPushButton#functionKeyButton {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ffffff, stop:1 #e0e0e0);
                color: #000000;
                border: 1px solid #999999;
                border-radius: 2px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton#functionKeyButton:hover {
                background-color: #e5f1fb;
                border-color: #0078d7;
            }
            QToolBar {
                background-color: #f5f5f5;
                border-bottom: 1px solid #d4d4d4;
                spacing: 4px;
                padding: 3px;
            }
            QToolButton {
                background-color: transparent;
                color: #000000;
                border: 1px solid transparent;
                border-radius: 3px;
                padding: 4px 8px;
                font-weight: 500;
            }
            QToolButton:hover {
                background-color: #e5f1fb;
                border-color: #70c0e7;
            }
            QStatusBar {
                background-color: #f0f0f0;
                color: #000000;
                border-top: 1px solid #c0c0c0;
                font-size: 11px;
            }
            QStatusBar QLabel {
                color: #000000;
            }
            QProgressBar {
                border: 1px solid #ababab;
                border-radius: 2px;
                text-align: center;
                background-color: #ffffff;
                color: #000000;
            }
            QProgressBar::chunk {
                background-color: #06b025;
            }
            QTreeWidget, QListWidget {
                background-color: #ffffff;
                color: #000000;
                border: 1px solid #ababab;
            }
            QTreeWidget::item:selected, QListWidget::item:selected {
                background-color: #0a246a;
                color: #ffffff;
            }
            QSplitter::handle {
                background-color: #d4d4d4;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #c0c0c0;
                border-radius: 4px;
                margin-top: 10px;
                padding-top: 10px;
                background-color: #f7f7f7;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 4px;
                background-color: #f7f7f7;
                color: #000000;
            }
        """)
    else:
        # Modern Dark Commander Palette
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(37, 37, 38))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(220, 220, 220))
        palette.setColor(QPalette.ColorRole.Base, QColor(30, 30, 30))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(45, 45, 48))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))
        palette.setColor(QPalette.ColorRole.Button, QColor(45, 45, 48))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(220, 220, 220))
        palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
        palette.setColor(QPalette.ColorRole.Link, QColor(86, 156, 214))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(38, 79, 120))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
        app.setPalette(palette)

        app.setStyleSheet("""
            QMainWindow, QDialog {
                background-color: #1e1e1e;
            }
            QTableWidget {
                background-color: #1e1e1e;
                alternate-background-color: #252526;
                color: #cccccc;
                gridline-color: #333333;
                border: 1px solid #3c3c3c;
                selection-background-color: #264f78;
                selection-color: #ffffff;
            }
            QHeaderView::section {
                background-color: #2d2d30;
                color: #d4d4d4;
                padding: 4px 8px;
                border: 1px solid #3c3c3c;
                font-weight: bold;
            }
            QLineEdit, QSpinBox, QComboBox, QTextEdit {
                background-color: #2d2d30;
                color: #ffffff;
                border: 1px solid #3c3c3c;
                border-radius: 3px;
                padding: 4px 8px;
            }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QTextEdit:focus {
                border: 1px solid #007acc;
            }
            QPushButton {
                background-color: #333333;
                color: #cccccc;
                border: 1px solid #3c3c3c;
                border-radius: 3px;
                padding: 5px 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #3e3e42;
                color: #ffffff;
            }
            QPushButton:pressed {
                background-color: #007acc;
                color: #ffffff;
            }
            QPushButton#functionKeyButton {
                background-color: #2b2b2b;
                color: #e0e0e0;
                border: 1px solid #3c3c3c;
                border-radius: 3px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton#functionKeyButton:hover {
                background-color: #383838;
                border-color: #555555;
                color: #ffffff;
            }
            QToolBar {
                background-color: #2d2d30;
                border-bottom: 1px solid #3c3c3c;
                spacing: 4px;
                padding: 3px;
            }
            QToolButton {
                background-color: transparent;
                color: #cccccc;
                border: 1px solid transparent;
                border-radius: 3px;
                padding: 4px 8px;
                font-weight: 500;
            }
            QToolButton:hover {
                background-color: #3e3e42;
                border-color: #555555;
                color: #ffffff;
            }
            QStatusBar {
                background-color: #007acc;
                color: #ffffff;
                font-size: 11px;
            }
            QStatusBar QLabel {
                color: #ffffff;
            }
            QProgressBar {
                border: 1px solid #3c3c3c;
                border-radius: 3px;
                text-align: center;
                background-color: #2d2d30;
                color: #ffffff;
            }
            QProgressBar::chunk {
                background-color: #4ec9b0;
            }
            QTreeWidget, QListWidget {
                background-color: #252526;
                color: #cccccc;
                border: 1px solid #3c3c3c;
            }
            QTreeWidget::item:selected, QListWidget::item:selected {
                background-color: #264f78;
                color: #ffffff;
            }
            QSplitter::handle {
                background-color: #333333;
            }
        """)


def main():
    # Allow SIGINT (Ctrl+C) to terminate app
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # Ignore SIGPIPE to prevent unexpected process exit on broken sockets or subprocesses
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)

    # Global excepthook preventing unhandled Qt slot exceptions from aborting the process
    def _safe_excepthook(exc_type, exc_val, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_val, exc_tb)
            return
        sys.stderr.write("Unhandled exception:\n")
        traceback.print_exception(exc_type, exc_val, exc_tb, file=sys.stderr)

    sys.excepthook = _safe_excepthook

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    app.setApplicationName("Nova Commander")
    app.setOrganizationName("NovaCommander")

    # Set default application and window icon
    icon_candidates = [
        _src_dir / "resources" / "winscp-linux.png",
        _src_dir / "resources" / "winscp-linux.svg",
        Path("/usr/share/icons/hicolor/scalable/apps/winscp-linux.svg"),
        Path("/usr/share/icons/hicolor/256x256/apps/winscp-linux.png"),
        Path("/usr/share/pixmaps/winscp-linux.png"),
    ]
    for icp in icon_candidates:
        if icp.exists():
            app_icon = QIcon(str(icp))
            app.setWindowIcon(app_icon)
            break

    apply_theme(app)

    session_store = SessionStore()
    window = MainWindow(session_store)
    window.show()

    # Open Login & Saved Sessions dialog by default on startup
    QTimer.singleShot(0, window.open_session_dialog)

    sys.exit(app.exec())


if __name__ == "__main__":
    _ensure_xcb_cursor()
    main()
