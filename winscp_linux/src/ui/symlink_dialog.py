"""
Create Symbolic Link Dialog for WinSCP-Linux.
Matches WinSCP Symbolic Link dialog (Symlink.dfm).
Supports creating symbolic or hard links on remote SFTP server or local filesystem.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)


class SymlinkDialog(QDialog):
    """Dialog to create a Symbolic Link or Hard Link."""

    link_created = pyqtSignal(str, str, bool)  # (target, link_name, is_hardlink)

    def __init__(
        self,
        default_target: str = "",
        current_dir: str = "/",
        is_remote: bool = True,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.default_target = default_target
        self.current_dir = current_dir
        self.is_remote = is_remote

        self.setWindowTitle("New Symbolic Link")
        self.resize(500, 200)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        group = QGroupBox("Link Parameters")
        form = QFormLayout(group)

        self.txt_target = QLineEdit(self.default_target)
        self.txt_target.setPlaceholderText("Path to target file or directory")
        form.addRow("Target Path:", self.txt_target)

        self.txt_link_name = QLineEdit()
        self.txt_link_name.setPlaceholderText("Name of the new link")
        form.addRow("Link Name:", self.txt_link_name)

        self.cmb_type = QComboBox()
        self.cmb_type.addItem("Symbolic link (ln -s)", False)
        self.cmb_type.addItem("Hard link (ln)", True)
        form.addRow("Link Type:", self.cmb_type)

        layout.addWidget(group)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _on_accept(self):
        target = self.txt_target.text().strip()
        link_name = self.txt_link_name.text().strip()
        is_hard = self.cmb_type.currentData()

        if not target or not link_name:
            QMessageBox.warning(self, "Missing Fields", "Please enter both target path and link name.")
            return

        self.link_created.emit(target, link_name, is_hard)
        self.accept()
