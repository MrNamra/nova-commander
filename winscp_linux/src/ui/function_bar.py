"""
Classic Commander Function Key Bar (F-Keys Bar) for WinSCP-Linux.
Matches the iconic WinSCP Commander interface bottom bar with quick action buttons:
[F2 Rename] [F3 View] [F4 Edit] [F5 Copy] [F6 Move] [F7 New Folder] [F8 Delete] [F9 Properties] [F10 Quit]
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QWidget,
)


class FunctionBarWidget(QWidget):
    """
    Iconic WinSCP Commander bottom button bar.
    Provides immediate 1-click access to the classic F-key operations.
    """

    f2_rename_clicked = pyqtSignal()
    f3_view_clicked = pyqtSignal()
    f4_edit_clicked = pyqtSignal()
    f5_copy_clicked = pyqtSignal()
    f6_move_clicked = pyqtSignal()
    f7_mkdir_clicked = pyqtSignal()
    f8_delete_clicked = pyqtSignal()
    f9_properties_clicked = pyqtSignal()
    f10_quit_clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedHeight(34)
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        buttons_def = [
            ("F2", "Rename", self.f2_rename_clicked),
            ("F3", "View", self.f3_view_clicked),
            ("F4", "Edit", self.f4_edit_clicked),
            ("F5", "Copy", self.f5_copy_clicked),
            ("F6", "Move", self.f6_move_clicked),
            ("F7", "New Folder", self.f7_mkdir_clicked),
            ("F8", "Delete", self.f8_delete_clicked),
            ("F9", "Properties", self.f9_properties_clicked),
            ("F10", "Quit", self.f10_quit_clicked),
        ]

        self.buttons: list[QPushButton] = []
        for key, label, signal in buttons_def:
            btn = QPushButton(f"{key} {label}")
            btn.setObjectName("functionKeyButton")
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Prevent stealing keyboard focus from file lists
            btn.clicked.connect(signal.emit)
            layout.addWidget(btn)
            self.buttons.append(btn)
