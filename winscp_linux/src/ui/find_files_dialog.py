"""
Find Files Dialog for WinSCP-Linux.
Matches WinSCP Find Files (FileFind.dfm / Ctrl+F).
Supports searching on Remote SFTP server and Local filesystem with:
- File name wildcard masks (*.py, *.log, etc.)
- Containing text search (grep)
- Recursive subdirectories search
- Interactive results table with direct navigation and file opening
"""

from __future__ import annotations

import fnmatch
import os
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.sftp_client import RemoteFileInfo, SFTPClient


@dataclass
class FoundFileItem:
    """Represents a matched file search result."""

    name: str
    folder: str
    full_path: str
    size: int
    mtime: float
    is_dir: bool
    is_remote: bool


class FileSearchWorker(QThread):
    """Background worker to perform recursive file searches without freezing the GUI."""

    file_found = pyqtSignal(object)  # FoundFileItem
    progress = pyqtSignal(str)
    finished_search = pyqtSignal(int)  # total found
    failed = pyqtSignal(str)

    def __init__(
        self,
        is_remote: bool,
        sftp_client: SFTPClient,
        start_dir: str,
        name_mask: str,
        containing_text: str,
        case_sensitive: bool,
        recursive: bool,
    ):
        super().__init__()
        self.is_remote = is_remote
        self.sftp_client = sftp_client
        self.start_dir = start_dir
        self.name_mask = name_mask.strip() or "*"
        self.containing_text = containing_text
        self.case_sensitive = case_sensitive
        self.recursive = recursive
        self._stopped = False

    def stop(self):
        self._stopped = True

    def run(self):
        try:
            total_found = 0
            if self.is_remote:
                total_found = self._search_remote(self.start_dir)
            else:
                total_found = self._search_local(self.start_dir)
            self.finished_search.emit(total_found)
        except Exception as e:
            self.failed.emit(str(e))

    def _match_name(self, filename: str) -> bool:
        if self.name_mask == "*":
            return True
        for pattern in self.name_mask.split(";"):
            p = pattern.strip()
            if not p:
                continue
            if self.case_sensitive:
                if fnmatch.fnmatchcase(filename, p):
                    return True
            else:
                if fnmatch.fnmatch(filename.lower(), p.lower()):
                    return True
        return False

    def _match_content_local(self, file_path: str) -> bool:
        if not self.containing_text:
            return True
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                if self.case_sensitive:
                    return self.containing_text in content
                return self.containing_text.lower() in content.lower()
        except Exception:
            return False

    def _search_local(self, directory: str) -> int:
        count = 0
        if self.recursive:
            for root, dirs, files in os.walk(directory):
                if self._stopped:
                    break
                self.progress.emit(f"Scanning: {root}")
                for f in files:
                    if self._match_name(f):
                        full = os.path.join(root, f)
                        if self._match_content_local(full):
                            try:
                                st = os.stat(full)
                                item = FoundFileItem(
                                    name=f,
                                    folder=root,
                                    full_path=full,
                                    size=st.st_size,
                                    mtime=st.st_mtime,
                                    is_dir=False,
                                    is_remote=False,
                                )
                                self.file_found.emit(item)
                                count += 1
                            except Exception:
                                pass
        else:
            p = Path(directory)
            for item_p in p.iterdir():
                if self._stopped:
                    break
                if item_p.is_file() and self._match_name(item_p.name):
                    full = str(item_p)
                    if self._match_content_local(full):
                        st = item_p.stat()
                        item = FoundFileItem(
                            name=item_p.name,
                            folder=directory,
                            full_path=full,
                            size=st.st_size,
                            mtime=st.st_mtime,
                            is_dir=False,
                            is_remote=False,
                        )
                        self.file_found.emit(item)
                        count += 1
        return count

    def _search_remote(self, directory: str) -> int:
        count = 0
        # If containing_text is set and remote shell is available, we can use grep directly
        if self.containing_text and self.sftp_client._ssh:
            try:
                import shlex
                grep_flags = "-rn" if self.case_sensitive else "-rni"
                cmd = f"grep {grep_flags} {shlex.quote(self.containing_text)} {shlex.quote(directory)} 2>/dev/null | head -n 300"
                code, out, _ = self.sftp_client.exec_command(cmd, timeout=20)
                if code == 0 and out:
                    seen_files = set()
                    for line in out.strip().splitlines():
                        if ":" in line:
                            path_part = line.split(":", 1)[0].strip()
                            if path_part and path_part not in seen_files:
                                seen_files.add(path_part)
                                fn = os.path.basename(path_part)
                                if self._match_name(fn):
                                    item = FoundFileItem(
                                        name=fn,
                                        folder=os.path.dirname(path_part),
                                        full_path=path_part,
                                        size=0,
                                        mtime=0.0,
                                        is_dir=False,
                                        is_remote=True,
                                    )
                                    self.file_found.emit(item)
                                    count += 1
                    return count
            except Exception:
                pass

        # Standard remote file tree traversal
        stack = [directory]
        while stack and not self._stopped:
            curr_dir = stack.pop()
            self.progress.emit(f"Scanning remote: {curr_dir}")
            try:
                entries = self.sftp_client.listdir(curr_dir)
                for it in entries:
                    if it.name in (".", ".."):
                        continue
                    if it.is_dir and self.recursive:
                        stack.append(it.path)
                    elif not it.is_dir and self._match_name(it.name):
                        item = FoundFileItem(
                            name=it.name,
                            folder=curr_dir,
                            full_path=it.path,
                            size=it.size,
                            mtime=it.modified_time,
                            is_dir=False,
                            is_remote=True,
                        )
                        self.file_found.emit(item)
                        count += 1
            except Exception:
                continue
        return count


class FindFilesDialog(QDialog):
    """
    WinSCP-style Find Files Dialog.
    Allows searching across remote server or local folders and navigating directly to results.
    """

    navigate_requested = pyqtSignal(str, bool)  # (path, is_remote)
    open_file_requested = pyqtSignal(str, bool)  # (path, is_remote)

    def __init__(
        self,
        sftp_client: SFTPClient,
        current_remote: str = "/",
        current_local: str = "",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.sftp_client = sftp_client
        self.current_remote = current_remote
        self.current_local = current_local or str(Path.home())
        self.found_items: List[FoundFileItem] = []
        self.search_worker: Optional[FileSearchWorker] = None

        self.setWindowTitle("Find Files (Ctrl+F)")
        self.resize(750, 520)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Search Parameters Form
        group_params = QGroupBox("Search Parameters")
        form = QFormLayout(group_params)

        self.cmb_target = QComboBox()
        self.cmb_target.addItem("Remote Server (SFTP)", "remote")
        self.cmb_target.addItem("Local Linux Filesystem", "local")
        self.cmb_target.currentIndexChanged.connect(self._on_target_changed)
        form.addRow("Search In:", self.cmb_target)

        self.txt_path = QLineEdit(self.current_remote)
        form.addRow("Starting Directory:", self.txt_path)

        self.txt_mask = QLineEdit("*")
        self.txt_mask.setPlaceholderText("e.g. *.py; *.log; nginx*")
        form.addRow("File Name Mask:", self.txt_mask)

        self.txt_content = QLineEdit()
        self.txt_content.setPlaceholderText("Text contained inside the file (optional)")
        form.addRow("Containing Text:", self.txt_content)

        opts_layout = QHBoxLayout()
        self.chk_recursive = QCheckBox("Search subdirectories")
        self.chk_recursive.setChecked(True)
        self.chk_case = QCheckBox("Case sensitive")
        opts_layout.addWidget(self.chk_recursive)
        opts_layout.addWidget(self.chk_case)
        opts_layout.addStretch()
        form.addRow("Options:", opts_layout)

        layout.addWidget(group_params)

        # Status and Controls
        ctrl_layout = QHBoxLayout()
        self.btn_find = QPushButton("🔍 Find Now")
        self.btn_find.setDefault(True)
        self.btn_stop = QPushButton("⏹ Stop")
        self.btn_stop.setEnabled(False)
        self.lbl_status = QLabel("Ready")
        ctrl_layout.addWidget(self.btn_find)
        ctrl_layout.addWidget(self.btn_stop)
        ctrl_layout.addWidget(self.lbl_status, 1)
        layout.addLayout(ctrl_layout)

        self.btn_find.clicked.connect(self._start_search)
        self.btn_stop.clicked.connect(self._stop_search)

        # Results Table
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["File Name", "In Folder", "Size", "Modified"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.table)

        # Bottom Actions
        act_layout = QHBoxLayout()
        self.btn_goto = QPushButton("📂 Go to Directory")
        self.btn_open = QPushButton("✏ Open / Edit")
        self.btn_copy_path = QPushButton("📋 Copy Path")
        act_layout.addWidget(self.btn_goto)
        act_layout.addWidget(self.btn_open)
        act_layout.addWidget(self.btn_copy_path)
        act_layout.addStretch()

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.reject)
        act_layout.addWidget(btn_close)
        layout.addLayout(act_layout)

        self.btn_goto.clicked.connect(self._on_goto)
        self.btn_open.clicked.connect(self._on_open)
        self.btn_copy_path.clicked.connect(self._on_copy_path)

    def _on_target_changed(self, idx: int):
        target = self.cmb_target.currentData()
        if target == "remote":
            self.txt_path.setText(self.current_remote)
        else:
            self.txt_path.setText(self.current_local)

    def _start_search(self):
        target = self.cmb_target.currentData()
        is_remote = (target == "remote")

        if is_remote and not self.sftp_client.is_connected:
            QMessageBox.warning(self, "Not Connected", "Must be connected to search the remote server.")
            return

        self.found_items.clear()
        self.table.setRowCount(0)
        self.btn_find.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.lbl_status.setText("Searching...")

        self.search_worker = FileSearchWorker(
            is_remote=is_remote,
            sftp_client=self.sftp_client,
            start_dir=self.txt_path.text().strip(),
            name_mask=self.txt_mask.text().strip(),
            containing_text=self.txt_content.text().strip(),
            case_sensitive=self.chk_case.isChecked(),
            recursive=self.chk_recursive.isChecked(),
        )
        self.search_worker.file_found.connect(self._on_file_found)
        self.search_worker.progress.connect(self.lbl_status.setText)
        self.search_worker.finished_search.connect(self._on_search_finished)
        self.search_worker.failed.connect(self._on_search_failed)
        self.search_worker.start()

    def _stop_search(self):
        if self.search_worker:
            self.search_worker.stop()
            self.btn_stop.setEnabled(False)

    def _on_file_found(self, item: FoundFileItem):
        self.found_items.append(item)
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(item.name))
        self.table.setItem(row, 1, QTableWidgetItem(item.folder))
        size_str = f"{item.size:,} B" if item.size else "-"
        self.table.setItem(row, 2, QTableWidgetItem(size_str))
        dt_str = datetime.fromtimestamp(item.mtime).strftime("%Y-%m-%d %H:%M:%S") if item.mtime else "-"
        self.table.setItem(row, 3, QTableWidgetItem(dt_str))

    def _on_search_finished(self, total: int):
        self.btn_find.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.lbl_status.setText(f"Done. Found {total} file(s).")

    def _on_search_failed(self, error: str):
        self.btn_find.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.lbl_status.setText("Error occurred.")
        QMessageBox.critical(self, "Search Error", f"Search failed: {error}")

    def _get_selected_item(self) -> Optional[FoundFileItem]:
        selected = self.table.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        if 0 <= row < len(self.found_items):
            return self.found_items[row]
        return None

    def _on_goto(self):
        it = self._get_selected_item()
        if it:
            self.navigate_requested.emit(it.folder, it.is_remote)
            self.accept()

    def _on_open(self):
        it = self._get_selected_item()
        if it:
            self.open_file_requested.emit(it.full_path, it.is_remote)
            self.accept()

    def _on_copy_path(self):
        it = self._get_selected_item()
        if it:
            from PyQt6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(it.full_path)
            QMessageBox.information(self, "Copied", f"Path copied to clipboard:\n{it.full_path}")

    def _on_item_double_clicked(self, item):
        self._on_goto()
