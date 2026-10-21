"""
Full Properties, Permissions, and Checksum Dialog for WinSCP-Linux.
Matches WinSCP Properties (Properties.dfm) and Rights (Rights.dfm) with:
- General metadata inspection (Name, Path, Size, Mode, Timestamps, UID/GID)
- Interactive 3x3 permissions grid (Owner, Group, Others x Read, Write, Execute)
- Octal and symbolic permissions calculator
- Recursive permission application for directories
- Integrated cryptographic Checksum calculator (SHA-256, MD5, SHA-1)
"""

from __future__ import annotations

import os
import stat
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.sftp_client import RemoteFileInfo, SFTPClient


class ChecksumWorker(QThread):
    """Calculates file checksum in background."""

    checksum_calculated = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, sftp_client: Optional[SFTPClient], path: str, is_remote: bool, algorithm: str):
        super().__init__()
        self.sftp_client = sftp_client
        self.path = path
        self.is_remote = is_remote
        self.algorithm = algorithm

    def run(self):
        try:
            if self.is_remote and self.sftp_client:
                h = self.sftp_client.calculate_checksum(self.path, self.algorithm)
            else:
                h = SFTPClient.calculate_local_checksum(self.path, self.algorithm)
            self.checksum_calculated.emit(h)
        except Exception as e:
            self.failed.emit(str(e))


class PropertiesDialog(QDialog):
    """
    WinSCP-style comprehensive Properties and Rights Dialog.
    """

    permissions_changed = pyqtSignal(int, bool)  # (new_octal_mode, recursive)

    def __init__(
        self,
        name: str,
        path: str,
        size: int,
        mode: int,
        mtime: float,
        is_dir: bool,
        is_remote: bool,
        sftp_client: Optional[SFTPClient] = None,
        uid: int = 0,
        gid: int = 0,
        symlink_target: str = "",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.item_name = name
        self.path = path
        self.size = size
        self.mode = mode
        self.mtime = mtime
        self.is_dir = is_dir
        self.is_remote = is_remote
        self.sftp_client = sftp_client
        self.uid = uid
        self.gid = gid
        self.symlink_target = symlink_target

        self.setWindowTitle(f"Properties - {name}")
        self.resize(520, 500)

        self._updating_widgets = False
        self._setup_ui()
        self._load_data()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # ---------------- Tab 1: General & Permissions ----------------
        tab_general = QWidget()
        layout_gen = QVBoxLayout(tab_general)

        # Metadata Form
        group_info = QGroupBox("File Information")
        form_info = QFormLayout(group_info)

        self.lbl_name = QLabel(self.item_name)
        self.lbl_name.setFont(QFont("sans-serif", 10, QFont.Weight.Bold))
        form_info.addRow("Name:", self.lbl_name)

        self.lbl_path = QLabel(self.path)
        self.lbl_path.setWordWrap(True)
        form_info.addRow("Location:", self.lbl_path)

        file_type = "Directory" if self.is_dir else ("Symbolic Link" if self.symlink_target else "Regular File")
        form_info.addRow("Type:", QLabel(file_type))

        if self.symlink_target:
            form_info.addRow("Link Target:", QLabel(self.symlink_target))

        size_text = f"{self.size:,} bytes" if not self.is_dir else "<Directory>"
        form_info.addRow("Size:", QLabel(size_text))

        mtime_str = datetime.fromtimestamp(self.mtime).strftime("%Y-%m-%d %H:%M:%S") if self.mtime else "Unknown"
        form_info.addRow("Last Modified:", QLabel(mtime_str))

        if self.uid or self.gid:
            form_info.addRow("Owner / Group:", QLabel(f"UID: {self.uid} / GID: {self.gid}"))

        layout_gen.addWidget(group_info)

        # Permissions 3x3 Grid
        group_perm = QGroupBox("Permissions")
        layout_perm = QVBoxLayout(group_perm)

        grid = QGridLayout()
        grid.addWidget(QLabel(""), 0, 0)
        grid.addWidget(QLabel("<b>Read (r)</b>"), 0, 1)
        grid.addWidget(QLabel("<b>Write (w)</b>"), 0, 2)
        grid.addWidget(QLabel("<b>Execute (x)</b>"), 0, 3)

        # Owner
        grid.addWidget(QLabel("Owner:"), 1, 0)
        self.chk_ur = QCheckBox()
        self.chk_uw = QCheckBox()
        self.chk_ux = QCheckBox()
        grid.addWidget(self.chk_ur, 1, 1)
        grid.addWidget(self.chk_uw, 1, 2)
        grid.addWidget(self.chk_ux, 1, 3)

        # Group
        grid.addWidget(QLabel("Group:"), 2, 0)
        self.chk_gr = QCheckBox()
        self.chk_gw = QCheckBox()
        self.chk_gx = QCheckBox()
        grid.addWidget(self.chk_gr, 2, 1)
        grid.addWidget(self.chk_gw, 2, 2)
        grid.addWidget(self.chk_gx, 2, 3)

        # Others
        grid.addWidget(QLabel("Others:"), 3, 0)
        self.chk_or = QCheckBox()
        self.chk_ow = QCheckBox()
        self.chk_ox = QCheckBox()
        grid.addWidget(self.chk_or, 3, 1)
        grid.addWidget(self.chk_ow, 3, 2)
        grid.addWidget(self.chk_ox, 3, 3)

        layout_perm.addLayout(grid)

        # Connect checkboxes
        for chk in [
            self.chk_ur, self.chk_uw, self.chk_ux,
            self.chk_gr, self.chk_gw, self.chk_gx,
            self.chk_or, self.chk_ow, self.chk_ox,
        ]:
            chk.stateChanged.connect(self._on_checkbox_changed)

        # Octal representation
        octal_layout = QHBoxLayout()
        octal_layout.addWidget(QLabel("Octal Representation:"))
        self.txt_octal = QLineEdit("0755")
        self.txt_octal.setFixedWidth(80)
        self.txt_octal.textChanged.connect(self._on_octal_edited)
        octal_layout.addWidget(self.txt_octal)

        self.lbl_symbolic = QLabel("rwxr-xr-x")
        self.lbl_symbolic.setStyleSheet("font-family: monospace; font-weight: bold; padding-left: 10px;")
        octal_layout.addWidget(self.lbl_symbolic)
        octal_layout.addStretch()
        layout_perm.addLayout(octal_layout)

        # Recursive option (if directory)
        self.chk_recursive = QCheckBox("Set permissions recursively to all subdirectories and files")
        if not self.is_dir:
            self.chk_recursive.hide()
        layout_perm.addWidget(self.chk_recursive)

        layout_gen.addWidget(group_perm)
        layout_gen.addStretch()
        self.tabs.addTab(tab_general, "📁 General & Rights")

        # ---------------- Tab 2: Checksums ----------------
        tab_checksum = QWidget()
        layout_chk = QVBoxLayout(tab_checksum)

        group_algo = QGroupBox("Cryptographic Checksum Calculation")
        form_algo = QFormLayout(group_algo)

        self.cmb_algo = QComboBox()
        self.cmb_algo.addItems(["SHA-256", "MD5", "SHA-1"])
        form_algo.addRow("Hash Algorithm:", self.cmb_algo)

        self.txt_hash = QLineEdit()
        self.txt_hash.setReadOnly(True)
        self.txt_hash.setFont(QFont("Monospace", 9))
        self.txt_hash.setPlaceholderText("Click 'Calculate Checksum' below to generate hash...")
        form_algo.addRow("Hash Value:", self.txt_hash)

        layout_chk.addWidget(group_algo)

        self.chk_progress = QProgressBar()
        self.chk_progress.setRange(0, 0)
        self.chk_progress.hide()
        layout_chk.addWidget(self.chk_progress)

        chk_btn_layout = QHBoxLayout()
        self.btn_calc_hash = QPushButton("⚡ Calculate Checksum")
        self.btn_calc_hash.clicked.connect(self._calculate_hash)
        self.btn_copy_hash = QPushButton("📋 Copy Hash")
        self.btn_copy_hash.clicked.connect(self._copy_hash)
        chk_btn_layout.addWidget(self.btn_calc_hash)
        chk_btn_layout.addWidget(self.btn_copy_hash)
        chk_btn_layout.addStretch()
        layout_chk.addLayout(chk_btn_layout)

        layout_chk.addStretch()
        self.tabs.addTab(tab_checksum, "🔒 Checksum")

        # Dialog Buttons
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self._on_save_permissions)
        btn_box.rejected.connect(self.reject)
        main_layout.addWidget(btn_box)

    def _load_data(self):
        cur_octal = stat.S_IMODE(self.mode) if self.mode else 0o755
        self._set_ui_from_mode(cur_octal)

    def _set_ui_from_mode(self, mode: int):
        self._updating_widgets = True
        self.chk_ur.setChecked(bool(mode & 0o400))
        self.chk_uw.setChecked(bool(mode & 0o200))
        self.chk_ux.setChecked(bool(mode & 0o100))

        self.chk_gr.setChecked(bool(mode & 0o040))
        self.chk_gw.setChecked(bool(mode & 0o020))
        self.chk_gx.setChecked(bool(mode & 0o010))

        self.chk_or.setChecked(bool(mode & 0o004))
        self.chk_ow.setChecked(bool(mode & 0o002))
        self.chk_ox.setChecked(bool(mode & 0o001))

        octal_str = f"0{oct(mode)[2:].zfill(3)}"
        self.txt_octal.setText(octal_str)
        self.lbl_symbolic.setText(stat.filemode(mode)[1:])
        self._updating_widgets = False

    def _on_checkbox_changed(self):
        if self._updating_widgets:
            return
        mode = 0
        if self.chk_ur.isChecked(): mode |= 0o400
        if self.chk_uw.isChecked(): mode |= 0o200
        if self.chk_ux.isChecked(): mode |= 0o100
        if self.chk_gr.isChecked(): mode |= 0o040
        if self.chk_gw.isChecked(): mode |= 0o020
        if self.chk_gx.isChecked(): mode |= 0o010
        if self.chk_or.isChecked(): mode |= 0o004
        if self.chk_ow.isChecked(): mode |= 0o002
        if self.chk_ox.isChecked(): mode |= 0o001

        self._updating_widgets = True
        self.txt_octal.setText(f"0{oct(mode)[2:].zfill(3)}")
        self.lbl_symbolic.setText(stat.filemode(mode)[1:])
        self._updating_widgets = False

    def _on_octal_edited(self, text: str):
        if self._updating_widgets:
            return
        try:
            val = int(text, 8)
            self._set_ui_from_mode(val)
        except ValueError:
            pass

    def _on_save_permissions(self):
        try:
            oct_val = int(self.txt_octal.text(), 8)
        except ValueError:
            oct_val = 0o755
        recursive = self.chk_recursive.isChecked() if self.is_dir else False
        self.permissions_changed.emit(oct_val, recursive)
        self.accept()

    def _calculate_hash(self):
        algo = self.cmb_algo.currentText().replace("-", "").lower()
        self.btn_calc_hash.setEnabled(False)
        self.chk_progress.show()

        self.worker = ChecksumWorker(self.sftp_client, self.path, self.is_remote, algo)
        self.worker.checksum_calculated.connect(self._on_checksum_calculated)
        self.worker.failed.connect(self._on_checksum_failed)
        self.worker.start()

    def _on_checksum_calculated(self, h: str):
        self.chk_progress.hide()
        self.btn_calc_hash.setEnabled(True)
        self.txt_hash.setText(h)

    def _on_checksum_failed(self, err: str):
        self.chk_progress.hide()
        self.btn_calc_hash.setEnabled(True)
        QMessageBox.warning(self, "Checksum Error", f"Could not calculate hash: {err}")

    def _copy_hash(self):
        h = self.txt_hash.text().strip()
        if h:
            QGuiApplication.clipboard().setText(h)
            QMessageBox.information(self, "Copied", "Checksum hash copied to clipboard!")
