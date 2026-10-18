"""
Embedded Linux SSH Terminal Widget for WinSCP-Linux.
Powered by pyte virtual terminal screen emulation and PyQt6 QPainter rendering,
linked directly to an interactive Paramiko SSH channel.
"""

from __future__ import annotations

import sys
from typing import Optional

import pyte
from PyQt6.QtCore import QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QKeySequence,
    QPainter,
    QPen,
)
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.ssh_client import SSHShellClient

# Standard ANSI 16-color palette
PALETTE = {
    "default_bg": QColor("#1e1e1e"),
    "default_fg": QColor("#e0e0e0"),
    "cursor": QColor("#00ff66"),
    "selection": QColor("#264f78"),
    "black": QColor("#000000"),
    "red": QColor("#f44747"),
    "green": QColor("#6a9955"),
    "brown": QColor("#d7ba7d"),
    "yellow": QColor("#d7ba7d"),
    "blue": QColor("#569cd6"),
    "magenta": QColor("#c586c0"),
    "cyan": QColor("#4ec9b0"),
    "white": QColor("#d4d4d4"),
    "bright_black": QColor("#666666"),
    "bright_red": QColor("#f44747"),
    "bright_green": QColor("#b5cea8"),
    "bright_yellow": QColor("#ce9178"),
    "bright_blue": QColor("#9cdcfe"),
    "bright_magenta": QColor("#c586c0"),
    "bright_cyan": QColor("#4ec9b0"),
    "bright_white": QColor("#ffffff"),
}


class TerminalCanvas(QWidget):
    """
    Renders the pyte virtual terminal screen using QPainter and captures keystrokes.
    """

    def __init__(self, shell_client: SSHShellClient, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.shell_client = shell_client
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

        # Monospace Font
        self._font = QFont("DejaVu Sans Mono", 11)
        self._font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(self._font)

        # Metrics
        fm = QFontMetrics(self._font)
        self.char_width = max(fm.horizontalAdvance("M"), 8)
        self.char_height = max(fm.lineSpacing(), 16)
        self.char_ascent = fm.ascent()

        # Pyte virtual screen
        self.cols = 80
        self.rows = 24
        self.screen = pyte.Screen(self.cols, self.rows)
        self.stream = pyte.Stream(self.screen)

        # Cursor blink
        self.cursor_visible = True
        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._toggle_cursor)
        self._blink_timer.start(500)

        # Wire shell data
        self.shell_client.data_received.connect(self.on_data_received)

        # Context menu
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def on_data_received(self, data: bytes):
        """Feeds incoming bytes into the VT100/ANSI stream."""
        try:
            text = data.decode("utf-8", errors="replace")
            self.stream.feed(text)
            self.update()
        except Exception:
            pass

    def _toggle_cursor(self):
        self.cursor_visible = not self.cursor_visible
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        new_cols = max(20, self.width() // self.char_width)
        new_rows = max(5, self.height() // self.char_height)

        if new_cols != self.cols or new_rows != self.rows:
            self.cols = new_cols
            self.rows = new_rows
            try:
                self.screen.resize(self.rows, self.cols)
            except Exception:
                pass
            if self.shell_client.is_active:
                self.shell_client.resize_pty(self.cols, self.rows)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self._font)

        # Fill background
        painter.fillRect(self.rect(), PALETTE["default_bg"])

        # Draw lines
        display_lines = self.screen.display
        cursor_x = self.screen.cursor.x
        cursor_y = self.screen.cursor.y

        for row_idx, line in enumerate(display_lines):
            if row_idx >= self.rows:
                break
            y_baseline = row_idx * self.char_height + self.char_ascent
            y_top = row_idx * self.char_height

            # pyte buffer row
            buf_row = self.screen.buffer[row_idx] if row_idx < len(self.screen.buffer) else {}

            for col_idx in range(min(len(line), self.cols)):
                char = line[col_idx] if col_idx < len(line) else " "
                char_info = buf_row.get(col_idx)

                fg_color = PALETTE["default_fg"]
                bg_color = None

                if char_info:
                    if char_info.fg and char_info.fg in PALETTE:
                        fg_color = PALETTE[char_info.fg]
                    if char_info.bg and char_info.bg in PALETTE:
                        bg_color = PALETTE[char_info.bg]
                    if char_info.reverse:
                        fg_color, bg_color = (
                            bg_color or PALETTE["default_bg"],
                            fg_color or PALETTE["default_fg"],
                        )

                x_pos = col_idx * self.char_width

                if bg_color and bg_color != PALETTE["default_bg"]:
                    painter.fillRect(
                        QRect(x_pos, y_top, self.char_width, self.char_height),
                        bg_color,
                    )

                if char != " ":
                    painter.setPen(fg_color)
                    painter.drawText(x_pos, y_baseline, char)

        # Draw cursor
        if self.cursor_visible and 0 <= cursor_x < self.cols and 0 <= cursor_y < self.rows:
            cursor_rect = QRect(
                cursor_x * self.char_width,
                cursor_y * self.char_height,
                self.char_width,
                self.char_height,
            )
            painter.fillRect(cursor_rect, PALETTE["cursor"])

            # Invert char under cursor
            char_under = " "
            if cursor_y < len(display_lines) and cursor_x < len(display_lines[cursor_y]):
                char_under = display_lines[cursor_y][cursor_x]
            if char_under != " ":
                painter.setPen(PALETTE["black"])
                y_base = cursor_y * self.char_height + self.char_ascent
                painter.drawText(cursor_x * self.char_width, y_base, char_under)

    def keyPressEvent(self, event):
        """Translates keyboard events to terminal sequences and sends to SSH shell."""
        key = event.key()
        modifiers = event.modifiers()
        text = event.text()

        # Handle Ctrl + Key shortcuts
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            # Clipboard Copy/Paste
            if key == Qt.Key.Key_C and (modifiers & Qt.KeyboardModifier.ShiftModifier):
                self.copy_selection()
                return
            elif key == Qt.Key.Key_V and (modifiers & Qt.KeyboardModifier.ShiftModifier):
                self.paste_clipboard()
                return
            elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
                # ASCII control code (Ctrl+C = 3, Ctrl+D = 4, etc.)
                code = key - Qt.Key.Key_A + 1
                self.shell_client.send(bytes([code]))
                return

        # Special navigation & control keys
        key_map = {
            Qt.Key.Key_Return: b"\r",
            Qt.Key.Key_Enter: b"\r",
            Qt.Key.Key_Backspace: b"\x7f",
            Qt.Key.Key_Tab: b"\t",
            Qt.Key.Key_Escape: b"\x1b",
            Qt.Key.Key_Up: b"\x1b[A",
            Qt.Key.Key_Down: b"\x1b[B",
            Qt.Key.Key_Right: b"\x1b[C",
            Qt.Key.Key_Left: b"\x1b[D",
            Qt.Key.Key_Home: b"\x1b[H",
            Qt.Key.Key_End: b"\x1b[F",
            Qt.Key.Key_PageUp: b"\x1b[5~",
            Qt.Key.Key_PageDown: b"\x1b[6~",
            Qt.Key.Key_Insert: b"\x1b[2~",
            Qt.Key.Key_Delete: b"\x1b[3~",
        }

        if key in key_map:
            self.shell_client.send(key_map[key])
            event.accept()
        elif text:
            self.shell_client.send(text.encode("utf-8"))
            event.accept()
        else:
            super().keyPressEvent(event)

    def copy_selection(self):
        """Copies all active screen text to system clipboard."""
        text = "\n".join(line.rstrip() for line in self.screen.display)
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(text)

    def paste_clipboard(self):
        """Pastes clipboard text directly to SSH terminal."""
        clipboard = QApplication.clipboard()
        if clipboard:
            text = clipboard.text()
            if text:
                self.shell_client.send(text)

    def clear_screen(self):
        """Resets virtual screen buffer."""
        self.screen.reset()
        self.update()

    def _show_context_menu(self, pos: QPoint):
        menu = QMenu(self)
        copy_act = menu.addAction("Copy Screen (Ctrl+Shift+C)")
        copy_act.triggered.connect(self.copy_selection)

        paste_act = menu.addAction("Paste into Terminal (Ctrl+Shift+V)")
        paste_act.triggered.connect(self.paste_clipboard)

        menu.addSeparator()
        clear_act = menu.addAction("Clear Buffer")
        clear_act.triggered.connect(self.clear_screen)

        menu.exec(self.mapToGlobal(pos))


class TerminalWidget(QWidget):
    """
    Complete Terminal Panel with header toolbar and embedded canvas.
    """

    close_requested = pyqtSignal()

    def __init__(self, shell_client: SSHShellClient, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.shell_client = shell_client

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header toolbar
        toolbar = QWidget()
        toolbar.setStyleSheet("background-color: #252526; color: #cccccc; border-bottom: 1px solid #3c3c3c;")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(8, 4, 8, 4)

        self.title_label = QLabel("💻 Embedded SSH Shell")
        self.title_label.setStyleSheet("font-weight: bold; color: #ffffff;")
        tb_layout.addWidget(self.title_label)

        self.status_label = QLabel("● Disconnected")
        self.status_label.setStyleSheet("color: #888888; font-size: 11px;")
        tb_layout.addWidget(self.status_label)

        tb_layout.addStretch()

        self.btn_copy = QPushButton("Copy")
        self.btn_copy.setToolTip("Copy terminal text")
        self.btn_copy.setStyleSheet("padding: 2px 8px;")
        tb_layout.addWidget(self.btn_copy)

        self.btn_paste = QPushButton("Paste")
        self.btn_paste.setToolTip("Paste from clipboard")
        self.btn_paste.setStyleSheet("padding: 2px 8px;")
        tb_layout.addWidget(self.btn_paste)

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setStyleSheet("padding: 2px 8px;")
        tb_layout.addWidget(self.btn_clear)

        self.btn_close = QPushButton("✕")
        self.btn_close.setToolTip("Close Terminal Panel")
        self.btn_close.setStyleSheet("background: transparent; color: #aaaaaa; font-weight: bold; padding: 2px 6px;")
        tb_layout.addWidget(self.btn_close)

        layout.addWidget(toolbar)

        # Canvas
        self.canvas = TerminalCanvas(self.shell_client, self)
        layout.addWidget(self.canvas)

        # Wire buttons
        self.btn_copy.clicked.connect(self.canvas.copy_selection)
        self.btn_paste.clicked.connect(self.canvas.paste_clipboard)
        self.btn_clear.clicked.connect(self.canvas.clear_screen)
        self.btn_close.clicked.connect(self.close_requested.emit)

        self.shell_client.session_closed.connect(self._on_session_closed)

    def set_session_info(self, username: str, host: str):
        self.title_label.setText(f"💻 SSH: {username}@{host}")
        self.status_label.setText("● Connected")
        self.status_label.setStyleSheet("color: #4ec9b0; font-weight: bold;")
        self.canvas.setFocus()

    def _on_session_closed(self):
        self.status_label.setText("● Session Terminated")
        self.status_label.setStyleSheet("color: #f44747; font-weight: bold;")
