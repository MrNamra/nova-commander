"""
Directory Bookmarks Manager and Dialog for WinSCP-Linux.
Matches WinSCP Location Profiles / Bookmarks (LocationProfiles.dfm / Ctrl+D).
Supports saving directory pairs (remote + local path), quick jumping, and bookmark editing.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


@dataclass
class BookmarkItem:
    """Represents a saved directory bookmark pair."""

    name: str
    remote_path: str
    local_path: str


class BookmarksStore:
    """Persists bookmarks to ~/.config/winscp-linux/bookmarks.json."""

    @staticmethod
    def get_path() -> Path:
        p = Path.home() / ".config" / "winscp-linux" / "bookmarks.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @classmethod
    def load(cls) -> List[BookmarkItem]:
        p = cls.get_path()
        if not p.exists():
            return []
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                return [BookmarkItem(**item) for item in data]
        except Exception:
            return []

    @classmethod
    def save(cls, bookmarks: List[BookmarkItem]) -> None:
        p = cls.get_path()
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump([asdict(b) for b in bookmarks], f, indent=2)
        except Exception:
            pass


class BookmarksDialog(QDialog):
    """
    WinSCP-style Manage Bookmarks dialog.
    Allows creating, editing, removing, and jumping to saved directory locations.
    """

    jump_requested = pyqtSignal(str, str)  # (remote_path, local_path)

    def __init__(
        self,
        current_remote: str = "/",
        current_local: str = "",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.current_remote = current_remote
        self.current_local = current_local or str(Path.home())
        self.bookmarks = BookmarksStore.load()

        self.setWindowTitle("Directory Bookmarks (Ctrl+D)")
        self.resize(680, 360)

        self._setup_ui()
        self._populate_table()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Bookmark Name", "Remote Path", "Local Path"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemDoubleClicked.connect(self._on_jump)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.btn_add_current = QPushButton("⭐ Add Current Location")
        self.btn_edit = QPushButton("✏ Edit")
        self.btn_delete = QPushButton("🗑 Delete")
        self.btn_jump = QPushButton("🚀 Jump to Location")
        self.btn_jump.setDefault(True)

        btn_layout.addWidget(self.btn_add_current)
        btn_layout.addWidget(self.btn_edit)
        btn_layout.addWidget(self.btn_delete)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_jump)

        layout.addLayout(btn_layout)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self.btn_add_current.clicked.connect(self._add_current)
        self.btn_edit.clicked.connect(self._edit_selected)
        self.btn_delete.clicked.connect(self._delete_selected)
        self.btn_jump.clicked.connect(self._on_jump)

    def _populate_table(self):
        self.table.setRowCount(0)
        for row, bm in enumerate(self.bookmarks):
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(bm.name))
            self.table.setItem(row, 1, QTableWidgetItem(bm.remote_path))
            self.table.setItem(row, 2, QTableWidgetItem(bm.local_path))

    def _add_current(self):
        default_name = f"Bookmark {len(self.bookmarks) + 1}"
        name, ok = QInputDialog.getText(self, "Add Bookmark", "Bookmark Name:", text=default_name)
        if ok and name.strip():
            bm = BookmarkItem(name.strip(), self.current_remote, self.current_local)
            self.bookmarks.append(bm)
            BookmarksStore.save(self.bookmarks)
            self._populate_table()

    def _edit_selected(self):
        selected = self.table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        bm = self.bookmarks[row]

        name, ok = QInputDialog.getText(self, "Edit Bookmark", "Bookmark Name:", text=bm.name)
        if ok and name.strip():
            bm.name = name.strip()
            BookmarksStore.save(self.bookmarks)
            self._populate_table()

    def _delete_selected(self):
        selected = self.table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        del self.bookmarks[row]
        BookmarksStore.save(self.bookmarks)
        self._populate_table()

    def _on_jump(self):
        selected = self.table.selectedItems()
        if not selected:
            QMessageBox.information(self, "No Bookmark Selected", "Please select a bookmark to jump to.")
            return
        row = selected[0].row()
        bm = self.bookmarks[row]
        self.jump_requested.emit(bm.remote_path, bm.local_path)
        self.accept()
