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
from pathlib import Path

# Ensure 'src' package can be imported from root or src/
current_dir = Path(__file__).resolve().parent
parent_dir = current_dir.parent
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QPalette, QColor
from PyQt6.QtWidgets import QApplication

from src.core.session_store import SessionStore
from src.ui.main_window import MainWindow


def apply_theme(app: QApplication):
    """Applies a clean, modern Linux desktop theme with consistent high-contrast colors."""
    app.setStyle("Fusion")

    # Dark / Modern Commander Palette
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
        QLineEdit, QSpinBox, QComboBox {
            background-color: #2d2d30;
            color: #ffffff;
            border: 1px solid #3c3c3c;
            border-radius: 3px;
            padding: 4px 8px;
        }
        QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
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

    app = QApplication(sys.argv)
    app.setApplicationName("WinSCP-Linux")
    app.setOrganizationName("WinSCP-Linux")
    apply_theme(app)

    session_store = SessionStore()
    window = MainWindow(session_store)
    window.show()

    # If sessions are configured, or user wants to connect on startup, open session dialog
    # But don't block the main window
    if len(session_store.list_sessions()) == 0:
        # Prompt user with session dialog to configure their first connection
        window.open_session_dialog()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
