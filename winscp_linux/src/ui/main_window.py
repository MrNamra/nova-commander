"""
Main Window (Multi-Session Dual-Pane Commander UI) for WinSCP-Linux.
Supports:
- Connecting to multiple SSH/SFTP servers simultaneously in individual tabs.
- Full dual-pane filesystem commander per session (Local on Left, Remote SFTP on Right).
- Bidirectional Drag-and-Drop for files and folders on both sides.
- Live Remote Direct-Edit with Watchdog (inotify) auto-sync.
- Embedded interactive SSH terminal per connection.
- Standard WinSCP toolbars, menus, chmod dialog, and cloud backup actions.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QMimeData, QPoint, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QColor,
    QDrag,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QIcon,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPixmap,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core.session_store import SessionConfig, SessionStore
from ..core.sftp_client import (
    RemoteFileInfo,
    SFTPActionWorker,
    SFTPBatchTransferWorker,
    SFTPClient,
    SFTPConnectWorker,
    SFTPDownloadWorker,
    SFTPListDirWorker,
    SFTPUploadWorker,
)
from ..core.editor_manager import editor_preferences, launch_external_editor
from ..core.ssh_client import SSHShellClient
from ..core.sync_engine import SyncEngine
from ..core.terminal_launcher import launch_system_terminal
from .bookmarks_dialog import BookmarksDialog, BookmarksStore
from .custom_commands_dialog import CustomCommandsDialog
from .export_dialog import ExportDialog
from .find_files_dialog import FindFilesDialog
from .function_bar import FunctionBarWidget
from .generate_url_dialog import GenerateUrlDialog
from .open_with_dialog import EditorPreferencesDialog, OpenWithDialog, SimpleOpenPromptDialog
from .properties_dialog import PropertiesDialog
from .session_dialog import SessionDialog
from .symlink_dialog import SymlinkDialog
from .synchronize_dialog import SynchronizeDialog
from .terminal_widget import SSHTerminalWindow
from .transfer_options_dialog import TransferOptionsDialog


class ChmodDialog(QDialog):
    """WinSCP-style file permission (chmod) editor dialog."""

    def __init__(
        self,
        target_name: str,
        current_octal: str = "755",
        is_dir: bool = False,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.is_dir = is_dir
        self.setWindowTitle(f"Change Permissions - {target_name}")
        self.resize(380, 270 if is_dir else 240)

        layout = QVBoxLayout(self)

        group = QGroupBox("Permissions")
        g_layout = QVBoxLayout(group)

        # 3x3 Checkboxes: Owner, Group, Others
        row_owner = QHBoxLayout()
        row_owner.addWidget(QLabel("Owner: "))
        self.cb_or = QCheckBox("Read (4)")
        self.cb_ow = QCheckBox("Write (2)")
        self.cb_ox = QCheckBox("Execute (1)")
        row_owner.addWidget(self.cb_or)
        row_owner.addWidget(self.cb_ow)
        row_owner.addWidget(self.cb_ox)
        g_layout.addLayout(row_owner)

        row_group = QHBoxLayout()
        row_group.addWidget(QLabel("Group:  "))
        self.cb_gr = QCheckBox("Read (4)")
        self.cb_gw = QCheckBox("Write (2)")
        self.cb_gx = QCheckBox("Execute (1)")
        row_group.addWidget(self.cb_gr)
        row_group.addWidget(self.cb_gw)
        row_group.addWidget(self.cb_gx)
        g_layout.addLayout(row_group)

        row_other = QHBoxLayout()
        row_other.addWidget(QLabel("Others: "))
        self.cb_tr = QCheckBox("Read (4)")
        self.cb_tw = QCheckBox("Write (2)")
        self.cb_tx = QCheckBox("Execute (1)")
        row_other.addWidget(self.cb_tr)
        row_other.addWidget(self.cb_tw)
        row_other.addWidget(self.cb_tx)
        g_layout.addLayout(row_other)

        layout.addWidget(group)

        # Recursive checkbox for folders
        if is_dir:
            self.cb_recursive = QCheckBox("Apply permissions recursively to all subfolders and files")
            layout.addWidget(self.cb_recursive)

        # Octal value input
        oct_layout = QHBoxLayout()
        oct_layout.addWidget(QLabel("Octal Value:"))
        self.txt_octal = QLineEdit(current_octal)
        self.txt_octal.setMaxLength(4)
        oct_layout.addWidget(self.txt_octal)
        layout.addLayout(oct_layout)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btn_ok = QPushButton("OK")
        self.btn_cancel = QPushButton("Cancel")
        btn_layout.addWidget(self.btn_ok)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

        # Checkbox update bindings
        for cb in (self.cb_or, self.cb_ow, self.cb_ox, self.cb_gr, self.cb_gw, self.cb_gx, self.cb_tr, self.cb_tw, self.cb_tx):
            cb.stateChanged.connect(self._update_octal_from_checkboxes)

        self.txt_octal.textEdited.connect(self._update_checkboxes_from_octal)
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)

        self._set_from_octal_string(current_octal)

    def _set_from_octal_string(self, oct_str: str):
        clean = oct_str[-3:].zfill(3)
        try:
            val = int(clean, 8)
            self.cb_or.setChecked(bool(val & 0o400))
            self.cb_ow.setChecked(bool(val & 0o200))
            self.cb_ox.setChecked(bool(val & 0o100))
            self.cb_gr.setChecked(bool(val & 0o040))
            self.cb_gw.setChecked(bool(val & 0o020))
            self.cb_gx.setChecked(bool(val & 0o010))
            self.cb_tr.setChecked(bool(val & 0o004))
            self.cb_tw.setChecked(bool(val & 0o002))
            self.cb_tx.setChecked(bool(val & 0o001))
            self.txt_octal.setText(clean)
        except ValueError:
            pass

    def _update_octal_from_checkboxes(self):
        val = 0
        if self.cb_or.isChecked(): val |= 0o400
        if self.cb_ow.isChecked(): val |= 0o200
        if self.cb_ox.isChecked(): val |= 0o100
        if self.cb_gr.isChecked(): val |= 0o040
        if self.cb_gw.isChecked(): val |= 0o020
        if self.cb_gx.isChecked(): val |= 0o010
        if self.cb_tr.isChecked(): val |= 0o004
        if self.cb_tw.isChecked(): val |= 0o002
        if self.cb_tx.isChecked(): val |= 0o001
        self.txt_octal.blockSignals(True)
        self.txt_octal.setText(oct(val)[2:].zfill(3))
        self.txt_octal.blockSignals(False)

    def _update_checkboxes_from_octal(self, text: str):
        self._set_from_octal_string(text)

    def get_mode_int(self) -> int:
        clean = self.txt_octal.text()[-3:].zfill(3)
        try:
            return int(clean, 8)
        except ValueError:
            return 0o755

    def is_recursive(self) -> bool:
        return getattr(self, "cb_recursive", None) is not None and self.cb_recursive.isChecked()


class CommanderTableWidget(QTableWidget):
    """
    Commander Table supporting seamless bidirectional Drag and Drop
    for both files and folders across Local and Remote panes, as well as
    accepting files dragged from external desktop file managers.
    """

    items_dropped = pyqtSignal(dict)
    rename_requested = pyqtSignal()

    def __init__(self, pane_type: str, parent: Optional[QWidget] = None):
        super().__init__(0, 4, parent)
        self.pane_type = pane_type  # "local" or "remote"
        self._current_directory: str = "/"
        self._drag_start_pos: Optional[QPoint] = None

        self.setHorizontalHeaderLabels(["Name", "Size", "Permissions", "Modified"])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        # Enable Drag and Drop
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def set_current_directory(self, path: str):
        self._current_directory = path

    def get_current_directory(self) -> str:
        return self._current_directory

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if not (event.buttons() & Qt.MouseButton.LeftButton) or not self._drag_start_pos:
            super().mouseMoveEvent(event)
            return

        if (event.pos() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return

        self._start_drag()

    def _start_drag(self):
        selected_rows = sorted(set(idx.row() for idx in self.selectedIndexes()))
        if not selected_rows:
            return

        drag_items = []
        for r in selected_rows:
            item = self.item(r, 0)
            if not item:
                continue
            data = item.data(Qt.ItemDataRole.UserRole)
            if not data or data.get("name") == "..":
                continue
            drag_items.append({
                "name": data["name"],
                "path": data["path"],
                "is_dir": data.get("is_dir", False),
            })

        if not drag_items:
            return

        drag = QDrag(self)
        mime = QMimeData()

        mime_format = f"application/x-winscp-{self.pane_type}-items"
        mime.setData(mime_format, json.dumps(drag_items).encode("utf-8"))

        if self.pane_type == "local":
            urls = [QUrl.fromLocalFile(it["path"]) for it in drag_items]
            mime.setUrls(urls)
        else:
            mime.setText("\n".join(it["path"] for it in drag_items))

        drag.setMimeData(mime)

        first_item = drag_items[0]
        prefix = "📁" if first_item["is_dir"] else "📄"
        count_suffix = f" (+{len(drag_items)-1})" if len(drag_items) > 1 else ""
        label_text = f" {prefix} {first_item['name']}{count_suffix} "

        pixmap = QPixmap(max(160, len(label_text) * 9), 28)
        pixmap.fill(QColor(38, 79, 120, 230))
        painter = QPainter(pixmap)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(6, 18, label_text)
        painter.end()

        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(12, 14))

        drag.exec(Qt.DropAction.CopyAction)

    def dragEnterEvent(self, event: QDragEnterEvent):
        mime = event.mimeData()
        if (
            mime.hasFormat("application/x-winscp-local-items")
            or mime.hasFormat("application/x-winscp-remote-items")
            or mime.hasUrls()
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent):
        mime = event.mimeData()
        if (
            mime.hasFormat("application/x-winscp-local-items")
            or mime.hasFormat("application/x-winscp-remote-items")
            or mime.hasUrls()
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        mime = event.mimeData()

        target_folder = self._current_directory
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        row = self.rowAt(pos.y())
        if row >= 0:
            item = self.item(row, 0)
            if item:
                data = item.data(Qt.ItemDataRole.UserRole)
                if data and data.get("is_dir") and data.get("name") != "..":
                    target_folder = data["path"]

        source_pane = "external"
        items = []

        if mime.hasFormat("application/x-winscp-local-items"):
            source_pane = "local"
            raw = bytes(mime.data("application/x-winscp-local-items")).decode("utf-8")
            items = json.loads(raw)
        elif mime.hasFormat("application/x-winscp-remote-items"):
            source_pane = "remote"
            raw = bytes(mime.data("application/x-winscp-remote-items")).decode("utf-8")
            items = json.loads(raw)
        elif mime.hasUrls():
            source_pane = "external"
            for u in mime.urls():
                p = u.toLocalFile()
                if p:
                    items.append({
                        "name": Path(p).name,
                        "path": p,
                        "is_dir": os.path.isdir(p),
                    })

        if items:
            event.acceptProposedAction()
            self.items_dropped.emit({
                "source_pane": source_pane,
                "target_pane": self.pane_type,
                "target_folder": target_folder,
                "items": items,
            })
        else:
            event.ignore()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            row = self.rowAt(pos.y())
            col = self.columnAt(pos.x())
            if row >= 0:
                self.cellDoubleClicked.emit(row, col)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_F2:
            self.rename_requested.emit()
            event.accept()
            return
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            selected = self.selectedItems()
            if selected:
                row = selected[0].row()
                self.cellDoubleClicked.emit(row, 0)
                event.accept()
                return
        elif event.key() == Qt.Key.Key_Backspace:
            # WinSCP Backspace navigates to parent directory [..]
            view = self.parent()
            while view and not hasattr(view, "_on_local_up") and not hasattr(view, "_on_remote_up"):
                view = view.parent()
            if view:
                if self.pane_type == "local" and hasattr(view, "_on_local_up"):
                    view._on_local_up()
                    event.accept()
                    return
                elif self.pane_type == "remote" and hasattr(view, "_on_remote_up"):
                    view._on_remote_up()
                    event.accept()
                    return
        super().keyPressEvent(event)


class SessionCommanderView(QWidget):
    """
    An independent, self-contained SFTP & SSH commander view for a single server session.
    Features its own local/remote file tables, terminal, sync engine, and workers.
    """

    status_message = pyqtSignal(str)
    progress_changed = pyqtSignal(int)
    session_connected = pyqtSignal(str, str)  # (title, info_string)
    session_disconnected = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None, initial_local_dir: Optional[str] = None):
        super().__init__(parent)
        self.sftp_client = SFTPClient()
        self.ssh_shell = SSHShellClient(self)
        self.sync_engine = SyncEngine(self)

        self.current_session: Optional[SessionConfig] = None
        self.session_id: str = "default"
        self._active_worker = None

        self._setup_ui()
        self._wire_sync_engine()

        # Load initial local directory
        init_dir = initial_local_dir or str(Path.home())
        self._load_local_directory(init_dir)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        # Dual-Pane layout
        self.h_splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self.h_splitter, 1)

        # === Left Panel: Local Filesystem ===
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(2)

        # WinSCP Local Drive / Quick Jump Bar
        local_drive_bar = QHBoxLayout()
        local_drive_bar.setSpacing(3)
        self.cmb_local_drives = QComboBox()
        self.cmb_local_drives.setMinimumWidth(160)
        self.cmb_local_drives.setFixedHeight(30)
        self.cmb_local_drives.setStyleSheet("QComboBox { font-size: 12px; font-weight: 500; padding: 2px 6px; }")
        self.cmb_local_drives.addItem("📁 / (Root)", "/")
        self.cmb_local_drives.addItem(f"🏠 ~ ({Path.home().name})", str(Path.home()))
        for p in ["/tmp", "/var/log", "/etc", "/opt", "/usr"]:
            if os.path.exists(p):
                self.cmb_local_drives.addItem(f"📁 {p}", p)
        self.cmb_local_drives.setCurrentIndex(1)
        self.cmb_local_drives.activated.connect(lambda idx: self._load_local_directory(self.cmb_local_drives.currentData()))

        self.btn_local_up = QPushButton("⬆")
        self.btn_local_up.setToolTip("Go to parent directory [..] (Backspace)")
        self.btn_local_home = QPushButton("🏠")
        self.btn_local_home.setToolTip("Go to Home directory (~)")
        self.btn_local_root = QPushButton("/")
        self.btn_local_root.setToolTip("Go to Root directory (/)")
        self.btn_local_bmark = QPushButton("⭐")
        self.btn_local_bmark.setToolTip("Directory Bookmarks (Ctrl+D)")
        self.btn_local_refresh = QPushButton("🔄")
        self.btn_local_refresh.setToolTip("Refresh local directory (Ctrl+R)")

        for b in [self.btn_local_up, self.btn_local_home, self.btn_local_root, self.btn_local_bmark, self.btn_local_refresh]:
            b.setFixedSize(36, 30)
            b.setStyleSheet("QPushButton { font-size: 16px; font-weight: bold; padding: 0px; }")

        local_drive_bar.addWidget(self.cmb_local_drives, 1)
        local_drive_bar.addWidget(self.btn_local_up)
        local_drive_bar.addWidget(self.btn_local_home)
        local_drive_bar.addWidget(self.btn_local_root)
        local_drive_bar.addWidget(self.btn_local_bmark)
        local_drive_bar.addWidget(self.btn_local_refresh)
        left_layout.addLayout(local_drive_bar)

        # Local Path bar
        local_header = QHBoxLayout()
        local_header.setSpacing(2)
        local_lbl = QLabel("💻 Local:")
        local_lbl.setStyleSheet("font-weight: bold; color: #569cd6;")
        self.txt_local_path = QLineEdit(str(Path.home()))
        self.txt_local_path.setFixedHeight(28)
        self.btn_local_go = QPushButton("Go")
        self.btn_local_go.setFixedSize(40, 28)
        self.btn_local_go.setStyleSheet("font-size: 12px; font-weight: bold;")
        local_header.addWidget(local_lbl)
        local_header.addWidget(self.txt_local_path, 1)
        local_header.addWidget(self.btn_local_go)
        left_layout.addLayout(local_header)

        self.table_local = CommanderTableWidget("local", self)
        left_layout.addWidget(self.table_local)

        # Local Status Bar
        local_sb_layout = QHBoxLayout()
        local_sb_layout.setContentsMargins(4, 2, 4, 2)
        self.lbl_local_status = QLabel("0 B in 0 of 0 files")
        self.lbl_local_status.setStyleSheet("font-size: 11px;")
        self.lbl_local_filter = QLabel("*.*")
        self.lbl_local_filter.setStyleSheet("font-size: 11px;")
        local_sb_layout.addWidget(self.lbl_local_status, 1)
        local_sb_layout.addWidget(self.lbl_local_filter)
        left_layout.addLayout(local_sb_layout)

        self.h_splitter.addWidget(left_widget)

        # === Right Panel: Remote Filesystem ===
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(2)

        # WinSCP Remote Drive / Quick Jump Bar
        remote_drive_bar = QHBoxLayout()
        remote_drive_bar.setSpacing(3)
        self.cmb_remote_locations = QComboBox()
        self.cmb_remote_locations.setMinimumWidth(160)
        self.cmb_remote_locations.setFixedHeight(30)
        self.cmb_remote_locations.setStyleSheet("QComboBox { font-size: 12px; font-weight: 500; padding: 2px 6px; }")
        self.cmb_remote_locations.addItem("📁 / (Root)", "/")
        self.cmb_remote_locations.addItem("🏠 ~ (User Home)", "~")
        self.cmb_remote_locations.addItem("📁 /var/www", "/var/www")
        self.cmb_remote_locations.addItem("📁 /etc", "/etc")
        self.cmb_remote_locations.addItem("📁 /tmp", "/tmp")
        self.cmb_remote_locations.activated.connect(lambda idx: self._load_remote_directory(self.cmb_remote_locations.currentData()) if self.sftp_client.is_connected else None)

        self.btn_remote_up = QPushButton("⬆")
        self.btn_remote_up.setToolTip("Go to parent remote directory [..] (Backspace)")
        self.btn_remote_home = QPushButton("🏠")
        self.btn_remote_home.setToolTip("Go to Home directory (~)")
        self.btn_remote_root = QPushButton("/")
        self.btn_remote_root.setToolTip("Go to Root directory (/)")
        self.btn_remote_bmark = QPushButton("⭐")
        self.btn_remote_bmark.setToolTip("Bookmarks (Ctrl+D)")
        self.btn_remote_refresh = QPushButton("🔄")
        self.btn_remote_refresh.setToolTip("Refresh remote directory (Ctrl+R)")

        for b in [self.btn_remote_up, self.btn_remote_home, self.btn_remote_root, self.btn_remote_bmark, self.btn_remote_refresh]:
            b.setFixedSize(36, 30)
            b.setStyleSheet("QPushButton { font-size: 16px; font-weight: bold; padding: 0px; }")

        remote_drive_bar.addWidget(self.cmb_remote_locations, 1)
        remote_drive_bar.addWidget(self.btn_remote_up)
        remote_drive_bar.addWidget(self.btn_remote_home)
        remote_drive_bar.addWidget(self.btn_remote_root)
        remote_drive_bar.addWidget(self.btn_remote_bmark)
        remote_drive_bar.addWidget(self.btn_remote_refresh)
        right_layout.addLayout(remote_drive_bar)

        # Remote Path bar
        remote_header = QHBoxLayout()
        remote_header.setSpacing(2)
        remote_lbl = QLabel("🌐 Remote:")
        remote_lbl.setStyleSheet("font-weight: bold; color: #4ec9b0;")
        self.txt_remote_path = QLineEdit("/")
        self.txt_remote_path.setFixedHeight(28)
        self.btn_remote_go = QPushButton("Go")
        self.btn_remote_go.setFixedSize(40, 28)
        self.btn_remote_go.setStyleSheet("font-size: 12px; font-weight: bold;")
        remote_header.addWidget(remote_lbl)
        remote_header.addWidget(self.txt_remote_path, 1)
        remote_header.addWidget(self.btn_remote_go)
        right_layout.addLayout(remote_header)

        self.table_remote = CommanderTableWidget("remote", self)
        right_layout.addWidget(self.table_remote)

        # Remote Status Bar
        remote_sb_layout = QHBoxLayout()
        remote_sb_layout.setContentsMargins(4, 2, 4, 2)
        self.lbl_remote_status = QLabel("0 B in 0 of 0 files")
        self.lbl_remote_status.setStyleSheet("font-size: 11px;")
        self.lbl_remote_filter = QLabel("*.*")
        self.lbl_remote_filter.setStyleSheet("font-size: 11px;")
        remote_sb_layout.addWidget(self.lbl_remote_status, 1)
        remote_sb_layout.addWidget(self.lbl_remote_filter)
        right_layout.addLayout(remote_sb_layout)

        self.h_splitter.addWidget(right_widget)

        self.h_splitter.setStretchFactor(0, 1)
        self.h_splitter.setStretchFactor(1, 1)

        # WinSCP Command Line Bar (Commander mode)
        cmd_panel = QHBoxLayout()
        cmd_panel.setContentsMargins(2, 2, 2, 2)
        cmd_panel.setSpacing(4)
        lbl_cmd = QLabel("💻 Command:")
        lbl_cmd.setStyleSheet("font-weight: bold; font-size: 11px;")
        self.txt_cmd_line = QLineEdit()
        self.txt_cmd_line.setPlaceholderText("Enter command to execute (e.g. ls -la, df -h, tar -czvf archive.tar.gz)...")
        self.txt_cmd_line.returnPressed.connect(self._on_execute_cmd_line)
        self.btn_exec_cmd = QPushButton("▶ Execute")
        self.btn_exec_cmd.setFixedWidth(80)
        self.btn_exec_cmd.clicked.connect(self._on_execute_cmd_line)
        cmd_panel.addWidget(lbl_cmd)
        cmd_panel.addWidget(self.txt_cmd_line, 1)
        cmd_panel.addWidget(self.btn_exec_cmd)
        layout.addLayout(cmd_panel)

        self.terminal_widget = None

        # Connections
        self.btn_local_go.clicked.connect(self._on_local_go)
        self.txt_local_path.returnPressed.connect(self._on_local_go)
        self.btn_local_up.clicked.connect(self._on_local_up)
        self.btn_local_home.clicked.connect(lambda: self._load_local_directory(str(Path.home())))
        self.btn_local_root.clicked.connect(lambda: self._load_local_directory("/"))
        self.btn_local_refresh.clicked.connect(lambda: self._load_local_directory(self.txt_local_path.text()))
        self.btn_local_bmark.clicked.connect(self._open_bookmarks)

        self.table_local.cellDoubleClicked.connect(self._on_local_double_clicked)
        self.table_local.customContextMenuRequested.connect(self._show_local_context_menu)
        self.table_local.items_dropped.connect(self._handle_items_dropped)
        self.table_local.rename_requested.connect(lambda: self.rename_selected("local"))
        self._last_active_pane = "local"
        self.table_local.itemSelectionChanged.connect(lambda: (setattr(self, "_last_active_pane", "local"), self._update_local_status()))
        self.table_local.cellClicked.connect(lambda r, c: setattr(self, "_last_active_pane", "local"))

        self.btn_remote_go.clicked.connect(self._on_remote_go)
        self.txt_remote_path.returnPressed.connect(self._on_remote_go)
        self.btn_remote_up.clicked.connect(self._on_remote_up)
        self.btn_remote_home.clicked.connect(self._on_remote_home)
        self.btn_remote_root.clicked.connect(lambda: self._load_remote_directory("/") if self.sftp_client.is_connected else None)
        self.btn_remote_refresh.clicked.connect(lambda: self._load_remote_directory(self.txt_remote_path.text()) if self.sftp_client.is_connected else None)
        self.btn_remote_bmark.clicked.connect(self._open_bookmarks)

        self.table_remote.cellDoubleClicked.connect(self._on_remote_double_clicked)
        self.table_remote.customContextMenuRequested.connect(self._show_remote_context_menu)
        self.table_remote.items_dropped.connect(self._handle_items_dropped)
        self.table_remote.rename_requested.connect(lambda: self.rename_selected("remote"))
        self.table_remote.itemSelectionChanged.connect(lambda: (setattr(self, "_last_active_pane", "remote"), self._update_remote_status()))
        self.table_remote.cellClicked.connect(lambda r, c: setattr(self, "_last_active_pane", "remote"))

    def _wire_sync_engine(self):
        self.sync_engine.sync_started.connect(
            lambda r, l: self.status_message.emit(f"Auto-uploading saved changes to {r}...")
        )
        self.sync_engine.sync_succeeded.connect(self._on_sync_succeeded)
        self.sync_engine.sync_failed.connect(
            lambda r, err: self.status_message.emit(f"❌ Auto-sync failed for {r}: {err}")
        )

    def _open_bookmarks(self):
        main_win = self.window()
        if hasattr(main_win, "open_bookmarks_dialog"):
            main_win.open_bookmarks_dialog()

    def _on_remote_home(self):
        if not self.sftp_client.is_connected:
            return
        home = self.current_session.remote_dir if (self.current_session and self.current_session.remote_dir) else "/"
        self._load_remote_directory(home)

    def _on_execute_cmd_line(self):
        cmd = self.txt_cmd_line.text().strip()
        if not cmd:
            return
        self.txt_cmd_line.clear()
        if self.sftp_client.is_connected:
            self.status_message.emit(f"Executing remote: {cmd}...")
            try:
                code, out, err = self.sftp_client.exec_command(cmd)
                from .custom_commands_dialog import CustomCommandOutputDialog
                dlg = CustomCommandOutputDialog(cmd, code, out, err, parent=self)
                dlg.exec()
            except Exception as e:
                QMessageBox.critical(self, "Command Error", f"Failed to execute command:\n{e}")
        else:
            self.status_message.emit(f"Executing local: {cmd}...")
            try:
                res = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=self.txt_local_path.text())
                from .custom_commands_dialog import CustomCommandOutputDialog
                dlg = CustomCommandOutputDialog(cmd, res.returncode, res.stdout, res.stderr, parent=self)
                dlg.exec()
            except Exception as e:
                QMessageBox.critical(self, "Command Error", f"Failed to execute local command:\n{e}")

    def _update_local_status(self):
        if not hasattr(self, "lbl_local_status"):
            return
        total_rows = self.table_local.rowCount()
        selected_rows = len(set(it.row() for it in self.table_local.selectedItems()))
        total_size = 0
        file_count = 0
        for r in range(total_rows):
            it = self.table_local.item(r, 0)
            if it:
                d = it.data(Qt.ItemDataRole.UserRole)
                if d and not d.get("is_dir") and d.get("name") != "..":
                    file_count += 1
                    try:
                        total_size += os.path.getsize(d["path"])
                    except Exception:
                        pass
        if selected_rows > 0:
            self.lbl_local_status.setText(f"{selected_rows} selected of {total_rows} items")
        else:
            self.lbl_local_status.setText(f"{self._format_size(total_size)} in {file_count} files")

    def _update_remote_status(self):
        if not hasattr(self, "lbl_remote_status"):
            return
        total_rows = self.table_remote.rowCount()
        selected_rows = len(set(it.row() for it in self.table_remote.selectedItems()))
        if selected_rows > 0:
            self.lbl_remote_status.setText(f"{selected_rows} selected of {total_rows} items")
        else:
            self.lbl_remote_status.setText(f"{total_rows} items")

    def connect_to_session(self, session: SessionConfig):
        """Connects to the specified session."""
        self.current_session = session
        self.session_id = f"{session.username}_{session.host}_{session.port}".replace("/", "_")

        self.status_message.emit(f"Connecting to {session.username}@{session.host}:{session.port}...")
        self.progress_changed.emit(-1)

        worker = SFTPConnectWorker(
            sftp_client=self.sftp_client,
            host=session.host,
            port=session.port,
            username=session.username,
            password=session.password,
            private_key=session.private_key,
            passphrase=getattr(session, "passphrase", ""),
            initial_remote_dir=session.remote_dir or "/",
        )
        self._active_worker = worker

        def _on_connected(remote_home: str):
            self.progress_changed.emit(0)
            self.status_message.emit(f"Connected to {session.host}")

            self.txt_remote_path.setText(remote_home)
            self._load_remote_directory(remote_home)

            title = session.name if session.name else session.host
            info = f"{session.username}@{session.host}:{session.port}"
            self.session_connected.emit(title, info)

        def _on_failed(err: str):
            self.progress_changed.emit(0)
            if "passphrase" in err.lower() and session.private_key:
                key_name = Path(session.private_key).name
                phrase, ok = QInputDialog.getText(
                    self,
                    "Key Passphrase Required",
                    f"The private key '{key_name}' is encrypted with a passphrase.\nPlease enter passphrase:",
                    QLineEdit.EchoMode.Password,
                )
                if ok and phrase:
                    session.passphrase = phrase
                    self.connect_to_session(session)
                    return
            self.status_message.emit(f"Connection failed: {err}")
            QMessageBox.critical(self, "Connection Error", f"Could not connect to {session.host}:\n{err}")

        worker.connected.connect(_on_connected)
        worker.failed.connect(_on_failed)
        worker.start()

    def disconnect_session(self):
        """Disconnects SFTP and cleans up cache for this session."""
        self.sftp_client.disconnect()
        self.ssh_shell.close()
        self.sync_engine.cleanup_session(self.session_id)

        self.table_remote.setRowCount(0)
        self.session_disconnected.emit()
        self.status_message.emit("Disconnected.")

    def cleanup(self):
        """Gracefully tears down this session view and all background watchers."""
        self.sftp_client.disconnect()
        self.ssh_shell.close()
        self.sync_engine.cleanup_session(self.session_id)

    def open_terminal(self):
        """Opens the native system terminal connected via SSH (Ctrl+P)."""
        self.open_terminal_window()

    def open_terminal_window(self):
        """Opens a dedicated SSH interactive terminal in a new standalone window (Ctrl+P)."""
        if not self.sftp_client.is_connected:
            reply = QMessageBox.question(
                self,
                "No Active Session",
                "No SSH session is currently connected in this tab.\nWould you like to open the Connection Dialog?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                main_win = self.window()
                if hasattr(main_win, "open_session_dialog"):
                    main_win.open_session_dialog()
            return

        session = self.current_session
        host = session.host if session else self.sftp_client.host
        port = session.port if session else self.sftp_client.port
        username = session.username if session else self.sftp_client.username
        password = session.password if session else ""
        private_key = session.private_key if session else ""
        passphrase = getattr(session, "passphrase", "") if session else ""
        session_name = session.name if session else (f"{username}@{host}" if host else "SSH Terminal")

        remote_dir = self.txt_remote_path.text() if self.txt_remote_path.text() != "/" else (session.remote_dir if session else "")

        try:
            launch_system_terminal(
                host=host,
                port=port,
                username=username,
                password=password,
                private_key=private_key,
                remote_dir=remote_dir,
                session_name=session_name,
            )
            self.status_message.emit(f"Launched system terminal connected to {username}@{host}:{port} (Ctrl+P).")
        except Exception as exc:
            # Fallback to internal terminal window if native OS terminal is unavailable
            try:
                term_win = SSHTerminalWindow(
                    ssh_client=self.sftp_client.ssh_client,
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    private_key=private_key,
                    passphrase=passphrase,
                    session_name=session_name,
                    parent=None,
                )
                if not hasattr(self, "_term_windows"):
                    self._term_windows = []
                self._term_windows.append(term_win)
                term_win.destroyed.connect(lambda: self._term_windows.remove(term_win) if term_win in self._term_windows else None)

                term_win.show()
                term_win.raise_()
                term_win.activateWindow()
                self.status_message.emit("Opened SSH terminal window (Ctrl+P).")
            except Exception as inner_exc:
                QMessageBox.critical(self, "Terminal Launch Error", f"Could not launch terminal:\n{exc}\n{inner_exc}")

    # ----------------- Local Pane -----------------
    def _load_local_directory(self, dir_path: str):
        target = Path(dir_path).expanduser().resolve()
        if not target.is_dir():
            QMessageBox.warning(self, "Invalid Path", f"Local path does not exist: {dir_path}")
            return

        self.txt_local_path.setText(str(target))
        self.table_local.set_current_directory(str(target))
        self.table_local.setRowCount(0)

        if target != target.parent:
            row = self.table_local.rowCount()
            self.table_local.insertRow(row)
            self._set_table_item(self.table_local, row, 0, "..", is_dir=True, path=str(target.parent))
            self._set_table_item(self.table_local, row, 1, "")
            self._set_table_item(self.table_local, row, 2, "drwxr-xr-x")
            self._set_table_item(self.table_local, row, 3, "")
        elif sys.platform == "win32":
            for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
                drive = f"{letter}:\\"
                if os.path.exists(drive) and drive.lower() != str(target).lower():
                    row = self.table_local.rowCount()
                    self.table_local.insertRow(row)
                    self._set_table_item(self.table_local, row, 0, f"Drive ({letter}:)", is_dir=True, path=drive)
                    self._set_table_item(self.table_local, row, 1, "")
                    self._set_table_item(self.table_local, row, 2, "drive")
                    self._set_table_item(self.table_local, row, 3, "")

        try:
            entries = list(target.iterdir())
        except Exception as e:
            QMessageBox.warning(self, "Permission Denied", f"Cannot read directory: {e}")
            return

        entries.sort(key=lambda p: (0 if p.is_dir() else 1, p.name.lower()))

        for entry in entries:
            try:
                st = entry.stat()
                is_dir = entry.is_dir()
                size_str = self._format_size(st.st_size) if not is_dir else ""
                perm_str = stat.filemode(st.st_mode)
                mtime_str = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

                row = self.table_local.rowCount()
                self.table_local.insertRow(row)
                self._set_table_item(self.table_local, row, 0, entry.name, is_dir=is_dir, path=str(entry))
                self._set_table_item(self.table_local, row, 1, size_str)
                self._set_table_item(self.table_local, row, 2, perm_str)
                self._set_table_item(self.table_local, row, 3, mtime_str)
            except Exception:
                continue

        self._update_local_status()

    def _on_local_go(self):
        path = self.txt_local_path.text().strip()
        self._load_local_directory(path)

    def _on_local_up(self):
        cur = Path(self.txt_local_path.text()).resolve()
        parent = cur.parent
        self._load_local_directory(str(parent))

    def _on_local_double_clicked(self, row: int, col: int):
        item = self.table_local.item(row, 0)
        if not item:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if data and data.get("is_dir"):
            self._load_local_directory(data["path"])
        else:
            self.open_selected_local_file()

    def open_selected_local_file(self, force_open_with: bool = False):
        selected = self.table_local.selectedItems()
        if not selected:
            QMessageBox.information(self, "No File Selected", "Please select a local file to open.")
            return

        row = selected[0].row()
        item = self.table_local.item(row, 0)
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data or data.get("is_dir") or data.get("name") == "..":
            return

        local_path = data["path"]

        chosen_editor = None
        if force_open_with:
            dlg = OpenWithDialog(local_path, pref_manager=editor_preferences, parent=self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            chosen_editor = dlg.get_selected_command()
        else:
            chosen_editor = editor_preferences.get_editor_for_file(local_path)
            if not chosen_editor:
                prompt_dlg = SimpleOpenPromptDialog(local_path, pref_manager=editor_preferences, parent=self)
                if prompt_dlg.exec() != QDialog.DialogCode.Accepted:
                    return
                chosen_editor = prompt_dlg.get_selected_command()

        self.status_message.emit(f"Opening local file: {local_path}...")
        try:
            launch_external_editor(chosen_editor, local_path)
            self.status_message.emit(f"Opened {Path(local_path).name} with '{chosen_editor}'.")
        except Exception as exc:
            QMessageBox.critical(self, "Open Failed", f"Could not open local file:\n{exc}")

    # ----------------- Remote Pane -----------------
    def _load_remote_directory(self, dir_path: str):
        if not self.sftp_client.is_connected:
            return

        self.status_message.emit(f"Loading {dir_path}...")
        self.progress_changed.emit(-1)

        worker = SFTPListDirWorker(self.sftp_client, dir_path)
        self._active_worker = worker

        def _on_success(items: List[RemoteFileInfo], current_dir: str):
            self.progress_changed.emit(0)
            self.txt_remote_path.setText(current_dir)
            self.table_remote.set_current_directory(current_dir)
            self.table_remote.setRowCount(0)

            # WinSCP always shows parent [..] item when not at root directory
            if current_dir != "/":
                cur_clean = current_dir.rstrip("/")
                parent_dir = str(Path(cur_clean).parent) if cur_clean else "/"
                if not parent_dir.startswith("/"):
                    parent_dir = "/" + parent_dir
                row = self.table_remote.rowCount()
                self.table_remote.insertRow(row)
                self._set_table_item(self.table_remote, row, 0, "..", is_dir=True, path=parent_dir)
                self._set_table_item(self.table_remote, row, 1, "")
                self._set_table_item(self.table_remote, row, 2, "drwxr-xr-x")
                self._set_table_item(self.table_remote, row, 3, "")

            for item in items:
                row = self.table_remote.rowCount()
                self.table_remote.insertRow(row)
                size_str = self._format_size(item.size) if not item.is_dir else ""
                self._set_table_item(
                    self.table_remote,
                    row,
                    0,
                    item.name,
                    is_dir=item.is_dir,
                    path=item.path,
                    info=item,
                )
                self._set_table_item(self.table_remote, row, 1, size_str)
                self._set_table_item(self.table_remote, row, 2, f"{item.permissions_octal} ({item.permissions_str})")
                self._set_table_item(self.table_remote, row, 3, item.modified_str)

            self._update_remote_status()
            self.status_message.emit(f"Loaded {len(items)} items in {current_dir}")

        def _on_failed(err: str):
            self.progress_changed.emit(0)
            self.status_message.emit("Failed to list remote directory")
            QMessageBox.critical(self, "Remote Directory Error", f"Could not list '{dir_path}':\n{err}")

        worker.success.connect(_on_success)
        worker.failed.connect(_on_failed)
        worker.start()

    def _on_remote_go(self):
        path = self.txt_remote_path.text().strip()
        self._load_remote_directory(path)

    def _on_remote_up(self):
        cur = self.txt_remote_path.text().rstrip("/")
        parent = str(Path(cur).parent) if cur else "/"
        if not parent.startswith("/"):
            parent = "/" + parent
        self._load_remote_directory(parent)

    def _on_remote_double_clicked(self, row: int, col: int):
        item = self.table_remote.item(row, 0)
        if not item:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return

        if data.get("is_dir"):
            self._load_remote_directory(data["path"])
        else:
            self.edit_selected_remote_file()

    # ----------------- Remote Direct Edit -----------------
    def edit_selected_remote_file(self, force_open_with: bool = False):
        if not self.sftp_client.is_connected:
            QMessageBox.warning(self, "Not Connected", "Please connect to a remote server first.")
            return

        selected = self.table_remote.selectedItems()
        if not selected:
            QMessageBox.information(self, "No File Selected", "Please select a remote file to edit.")
            return

        row = selected[0].row()
        item = self.table_remote.item(row, 0)
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data or data.get("is_dir") or data.get("name") == "..":
            return

        remote_path = data["path"]

        # Check editor preference or prompt user
        chosen_editor = None
        if force_open_with:
            dlg = OpenWithDialog(remote_path, pref_manager=editor_preferences, parent=self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            chosen_editor = dlg.get_selected_command()
        else:
            chosen_editor = editor_preferences.get_editor_for_file(remote_path)
            if not chosen_editor:
                prompt_dlg = SimpleOpenPromptDialog(remote_path, pref_manager=editor_preferences, parent=self)
                if prompt_dlg.exec() != QDialog.DialogCode.Accepted:
                    return
                chosen_editor = prompt_dlg.get_selected_command()

        self.status_message.emit(f"Opening remote file for editing: {remote_path}...")

        try:
            self.sync_engine.start_edit_session(
                sftp_client=self.sftp_client,
                session_id=self.session_id,
                remote_path=remote_path,
                editor_command=chosen_editor,
            )
            self.status_message.emit(f"Opened {remote_path} with '{chosen_editor}'. Auto-sync active.")
        except Exception as exc:
            QMessageBox.critical(self, "Edit Failed", f"Could not open file for editing: {exc}")

    def _on_sync_succeeded(self, remote_path: str, local_path: str):
        self.status_message.emit(f"✔ File uploaded successfully: {remote_path}")
        if str(Path(remote_path).parent) == self.txt_remote_path.text().rstrip("/"):
            self._load_remote_directory(self.txt_remote_path.text())

    # ----------------- Transfers & Drag-Drop -----------------
    def _execute_batch_transfer(self, transfers: List[dict], refresh_pane: str):
        if not transfers:
            return

        total_items = len(transfers)
        self.progress_changed.emit(0)

        worker = SFTPBatchTransferWorker(self.sftp_client, transfers)
        self._active_worker = worker

        def _on_started(name: str, idx: int, total: int):
            self.status_message.emit(f"Transferring ({idx}/{total}): {name}...")
            self.progress_changed.emit(int(((idx - 1) / total) * 100))

        def _on_finish(count: int):
            self.progress_changed.emit(100)
            self.status_message.emit(f"✔ Completed transfer of {count} item(s) successfully.")
            if refresh_pane == "remote":
                self._load_remote_directory(self.txt_remote_path.text())
            else:
                self._load_local_directory(self.txt_local_path.text())

        def _on_fail(err: str):
            self.progress_changed.emit(0)
            self.status_message.emit("Transfer failed.")
            QMessageBox.critical(self, "Transfer Error", f"Transfer failed:\n{err}")

        worker.item_started.connect(_on_started)
        worker.finished.connect(_on_finish)
        worker.failed.connect(_on_fail)
        worker.start()

    def _handle_items_dropped(self, drop_data: dict):
        source_pane = drop_data["source_pane"]
        target_pane = drop_data["target_pane"]
        target_folder = drop_data["target_folder"]
        items = drop_data["items"]

        if not items:
            return

        if target_pane == "remote":
            if not self.sftp_client.is_connected:
                QMessageBox.warning(self, "Not Connected", "Please connect to a remote server before uploading.")
                return

            if source_pane == "remote":
                if target_folder == self.txt_remote_path.text().rstrip("/"):
                    return
                reply = QMessageBox.question(
                    self,
                    "Move Remote Items",
                    f"Move {len(items)} remote item(s) to:\n{target_folder}?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    for it in items:
                        src_p = it["path"]
                        dst_p = f"{target_folder.rstrip('/')}/{Path(src_p).name}"
                        try:
                            self.sftp_client.rename(src_p, dst_p)
                        except Exception as e:
                            QMessageBox.critical(self, "Move Error", f"Failed to move {it['name']}: {e}")
                    self._load_remote_directory(self.txt_remote_path.text())
                return

            transfers = []
            for it in items:
                src_p = it["path"]
                name = Path(src_p).name
                dst_p = f"{target_folder.rstrip('/')}/{name}"
                is_dir = it.get("is_dir", False) or os.path.isdir(src_p)
                transfers.append({
                    "source": src_p,
                    "dest": dst_p,
                    "is_dir": is_dir,
                    "direction": "upload",
                })

            prompt_msg = (
                f"Upload '{Path(transfers[0]['source']).name}' to:\n{target_folder}?"
                if len(transfers) == 1
                else f"Upload {len(transfers)} item(s) (files & folders) to:\n{target_folder}?"
            )
            reply = QMessageBox.question(
                self,
                "Confirm Drag & Drop Upload",
                prompt_msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

            self._execute_batch_transfer(transfers, refresh_pane="remote")

        elif target_pane == "local":
            if source_pane == "remote":
                if not self.sftp_client.is_connected:
                    return

                transfers = []
                for it in items:
                    src_p = it["path"]
                    name = Path(src_p).name
                    dst_p = str(Path(target_folder) / name)
                    is_dir = it.get("is_dir", False)
                    transfers.append({
                        "source": src_p,
                        "dest": dst_p,
                        "is_dir": is_dir,
                        "direction": "download",
                    })

                prompt_msg = (
                    f"Download '{Path(transfers[0]['source']).name}' to:\n{target_folder}?"
                    if len(transfers) == 1
                    else f"Download {len(transfers)} item(s) (files & folders) to:\n{target_folder}?"
                )
                reply = QMessageBox.question(
                    self,
                    "Confirm Drag & Drop Download",
                    prompt_msg,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

                self._execute_batch_transfer(transfers, refresh_pane="local")
            else:
                target_path = Path(target_folder)
                for it in items:
                    src_p = Path(it["path"])
                    if src_p.parent == target_path:
                        continue
                    dest_p = target_path / src_p.name
                    try:
                        if src_p.is_dir():
                            shutil.copytree(src_p, dest_p, dirs_exist_ok=True)
                        else:
                            shutil.copy2(src_p, dest_p)
                    except Exception as exc:
                        QMessageBox.critical(self, "Copy Error", f"Could not copy {src_p.name}: {exc}")
                self._load_local_directory(self.txt_local_path.text())

    def upload_selected(self):
        if not self.sftp_client.is_connected:
            QMessageBox.warning(self, "Not Connected", "Connect to a remote server before uploading.")
            return

        selected_rows = sorted(set(idx.row() for idx in self.table_local.selectedIndexes()))
        if not selected_rows:
            QMessageBox.information(self, "Select Item", "Please select a local file or folder to upload.")
            return

        transfers = []
        remote_base = self.txt_remote_path.text().rstrip("/")
        for r in selected_rows:
            item = self.table_local.item(r, 0)
            if not item:
                continue
            data = item.data(Qt.ItemDataRole.UserRole)
            if not data or data.get("name") == "..":
                continue

            local_path = data["path"]
            local_name = Path(local_path).name
            remote_dest = f"{remote_base}/{local_name}"
            is_dir = data.get("is_dir", False) or os.path.isdir(local_path)
            transfers.append({
                "source": local_path,
                "dest": remote_dest,
                "is_dir": is_dir,
                "direction": "upload",
            })

        if not transfers:
            return

        prompt_msg = (
            f"Upload '{Path(transfers[0]['source']).name}' to:\n{remote_base}?"
            if len(transfers) == 1
            else f"Upload {len(transfers)} item(s) (files & folders) to:\n{remote_base}?"
        )
        reply = QMessageBox.question(
            self,
            "Confirm Upload",
            prompt_msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._execute_batch_transfer(transfers, refresh_pane="remote")

    def download_selected(self):
        if not self.sftp_client.is_connected:
            return

        selected_rows = sorted(set(idx.row() for idx in self.table_remote.selectedIndexes()))
        if not selected_rows:
            QMessageBox.information(self, "Select Item", "Please select a remote file or folder to download.")
            return

        transfers = []
        local_base = Path(self.txt_local_path.text())
        for r in selected_rows:
            item = self.table_remote.item(r, 0)
            if not item:
                continue
            data = item.data(Qt.ItemDataRole.UserRole)
            if not data or data.get("name") == "..":
                continue

            remote_path = data["path"]
            remote_name = Path(remote_path).name
            local_dest = str(local_base / remote_name)
            is_dir = data.get("is_dir", False)
            transfers.append({
                "source": remote_path,
                "dest": local_dest,
                "is_dir": is_dir,
                "direction": "download",
            })

        if not transfers:
            return

        prompt_msg = (
            f"Download '{Path(transfers[0]['source']).name}' to:\n{local_base}?"
            if len(transfers) == 1
            else f"Download {len(transfers)} item(s) (files & folders) to:\n{local_base}?"
        )
        reply = QMessageBox.question(
            self,
            "Confirm Download",
            prompt_msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._execute_batch_transfer(transfers, refresh_pane="local")

    def properties_selected(self):
        """WinSCP-style Properties & Rights dialog (F9)."""
        is_remote = self.table_remote.hasFocus() or (
            not self.table_local.hasFocus()
            and self.sftp_client.is_connected
            and bool(self.table_remote.selectedItems())
        )

        if is_remote and self.sftp_client.is_connected:
            selected = self.table_remote.selectedItems()
            if not selected:
                QMessageBox.information(self, "No Item Selected", "Please select a remote file or folder.")
                return
            row = selected[0].row()
            data = self.table_remote.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if not data or data.get("name") == "..":
                return

            info: Optional[RemoteFileInfo] = data.get("info")
            mode = info.mode if info else 0o755
            size = info.size if info else 0
            mtime = info.modified_time if info else 0.0
            is_dir = bool(data.get("is_dir", False))
            uid = info.uid if info else 0
            gid = info.gid if info else 0

            symlink_target = ""
            if info and info.is_symlink:
                try:
                    symlink_target = self.sftp_client.readlink(data["path"])
                except Exception:
                    pass

            dlg = PropertiesDialog(
                name=data["name"],
                path=data["path"],
                size=size,
                mode=mode,
                mtime=mtime,
                is_dir=is_dir,
                is_remote=True,
                sftp_client=self.sftp_client,
                uid=uid,
                gid=gid,
                symlink_target=symlink_target,
                parent=self,
            )

            def _on_remote_perm_changed(new_mode: int, recursive: bool):
                action_fn = (
                    (lambda: self.sftp_client.chmod_recursive(data["path"], new_mode))
                    if recursive and is_dir
                    else (lambda: self.sftp_client.chmod(data["path"], new_mode))
                )
                rec_label = " (recursively)" if (recursive and is_dir) else ""
                worker = SFTPActionWorker(
                    action_fn=action_fn,
                    success_msg=f"Permissions updated to {oct(new_mode)[2:]}{rec_label} for {data['name']}",
                )
                self._active_worker = worker
                worker.finished.connect(
                    lambda msg: (self.status_message.emit(msg), self._load_remote_directory(self.txt_remote_path.text()))
                )
                worker.failed.connect(lambda err: QMessageBox.critical(self, "Chmod Error", f"Could not change permissions:\n{err}"))
                worker.start()

            dlg.permissions_changed.connect(_on_remote_perm_changed)
            dlg.exec()
        else:
            selected = self.table_local.selectedItems()
            if not selected:
                QMessageBox.information(self, "No Item Selected", "Please select a local file or folder.")
                return
            row = selected[0].row()
            data = self.table_local.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if not data or data.get("name") == "..":
                return

            local_path = data["path"]
            if not os.path.exists(local_path):
                return

            is_dir = os.path.isdir(local_path)
            symlink_target = os.readlink(local_path) if os.path.islink(local_path) else ""
            try:
                st = os.stat(local_path)
                mode = st.st_mode
                size = st.st_size
                mtime = st.st_mtime
                uid = st.st_uid
                gid = st.st_gid
            except Exception:
                mode = 0o755
                size = 0
                mtime = 0.0
                uid = 0
                gid = 0

            dlg = PropertiesDialog(
                name=data["name"],
                path=local_path,
                size=size,
                mode=mode,
                mtime=mtime,
                is_dir=is_dir,
                is_remote=False,
                sftp_client=None,
                uid=uid,
                gid=gid,
                symlink_target=symlink_target,
                parent=self,
            )

            def _on_local_perm_changed(new_mode: int, recursive: bool):
                try:
                    if recursive and is_dir:
                        os.chmod(local_path, new_mode)
                        for root, dirs, files in os.walk(local_path):
                            for d in dirs:
                                try:
                                    os.chmod(os.path.join(root, d), new_mode)
                                except Exception:
                                    pass
                            for f in files:
                                try:
                                    os.chmod(os.path.join(root, f), new_mode)
                                except Exception:
                                    pass
                    else:
                        os.chmod(local_path, new_mode)

                    rec_label = " (recursively)" if (recursive and is_dir) else ""
                    self.status_message.emit(f"Local permissions updated to {oct(new_mode)[2:]}{rec_label} for {data['name']}")
                    self._load_local_directory(self.txt_local_path.text())
                except Exception as exc:
                    QMessageBox.critical(self, "Chmod Error", f"Could not change local permissions:\n{exc}")

            dlg.permissions_changed.connect(_on_local_perm_changed)
            dlg.exec()

    def chmod_selected(self):
        # Check whether remote or local pane is active
        is_remote = self.table_remote.hasFocus() or (
            not self.table_local.hasFocus()
            and self.sftp_client.is_connected
            and bool(self.table_remote.selectedItems())
        )

        if is_remote and self.sftp_client.is_connected:
            selected = self.table_remote.selectedItems()
            if not selected:
                QMessageBox.information(self, "No Item Selected", "Please select a remote file or folder to change permissions.")
                return
            row = selected[0].row()
            data = self.table_remote.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if not data or data.get("name") == "..":
                return

            info: Optional[RemoteFileInfo] = data.get("info")
            current_oct = info.permissions_octal if info else "755"
            is_dir = bool(data.get("is_dir", False))

            dlg = ChmodDialog(data["name"], current_oct, is_dir=is_dir, parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                new_mode = dlg.get_mode_int()
                recursive = dlg.is_recursive()
                action_fn = (
                    (lambda: self.sftp_client.chmod_recursive(data["path"], new_mode))
                    if recursive and is_dir
                    else (lambda: self.sftp_client.chmod(data["path"], new_mode))
                )
                rec_label = " (recursively)" if (recursive and is_dir) else ""
                worker = SFTPActionWorker(
                    action_fn=action_fn,
                    success_msg=f"Permissions updated to {oct(new_mode)[2:]}{rec_label} for {data['name']}",
                )
                self._active_worker = worker
                worker.finished.connect(
                    lambda msg: (self.status_message.emit(msg), self._load_remote_directory(self.txt_remote_path.text()))
                )
                worker.failed.connect(lambda err: QMessageBox.critical(self, "Chmod Error", f"Could not change permissions:\n{err}"))
                worker.start()
        else:
            # Local filesystem chmod
            selected = self.table_local.selectedItems()
            if not selected:
                QMessageBox.information(self, "No Item Selected", "Please select a local file or folder to change permissions.")
                return
            row = selected[0].row()
            data = self.table_local.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if not data or data.get("name") == "..":
                return

            local_path = data["path"]
            if not os.path.exists(local_path):
                return

            is_dir = os.path.isdir(local_path)
            try:
                current_oct = oct(os.stat(local_path).st_mode & 0o777)[2:].zfill(3)
            except Exception:
                current_oct = "755"

            dlg = ChmodDialog(data["name"], current_oct, is_dir=is_dir, parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                new_mode = dlg.get_mode_int()
                recursive = dlg.is_recursive()
                try:
                    if recursive and is_dir:
                        os.chmod(local_path, new_mode)
                        for root, dirs, files in os.walk(local_path):
                            for d in dirs:
                                try:
                                    os.chmod(os.path.join(root, d), new_mode)
                                except Exception:
                                    pass
                            for f in files:
                                try:
                                    os.chmod(os.path.join(root, f), new_mode)
                                except Exception:
                                    pass
                    else:
                        os.chmod(local_path, new_mode)

                    rec_label = " (recursively)" if (recursive and is_dir) else ""
                    msg = f"Local permissions updated to {oct(new_mode)[2:]}{rec_label} for {data['name']}"
                    self.status_message.emit(msg)
                    self._load_local_directory(self.txt_local_path.text())
                except Exception as exc:
                    QMessageBox.critical(self, "Chmod Error", f"Could not change local permissions:\n{exc}")

    def create_symlink(self):
        """WinSCP Commands -> New -> Symbolic Link."""
        is_remote = self.table_remote.hasFocus() and self.sftp_client.is_connected
        curr_dir = self.txt_remote_path.text() if is_remote else self.txt_local_path.text()

        table = self.table_remote if is_remote else self.table_local
        default_target = ""
        selected = table.selectedItems()
        if selected:
            row = selected[0].row()
            data = table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if data and data.get("name") != "..":
                default_target = data["name"]

        dlg = SymlinkDialog(default_target=default_target, current_dir=curr_dir, is_remote=is_remote, parent=self)

        def _on_link_created(target: str, link_name: str, is_hard: bool):
            if is_remote:
                remote_link_path = f"{curr_dir.rstrip('/')}/{link_name}"
                try:
                    self.sftp_client.create_symlink(target, remote_link_path)
                    self.status_message.emit(f"Created symbolic link: {link_name} -> {target}")
                    self._load_remote_directory(curr_dir)
                except Exception as exc:
                    QMessageBox.critical(self, "Symlink Error", f"Failed to create remote link:\n{exc}")
            else:
                local_link_path = os.path.join(curr_dir, link_name)
                try:
                    if is_hard:
                        os.link(target, local_link_path)
                    else:
                        os.symlink(target, local_link_path)
                    self.status_message.emit(f"Created local link: {link_name} -> {target}")
                    self._load_local_directory(curr_dir)
                except Exception as exc:
                    QMessageBox.critical(self, "Symlink Error", f"Failed to create local link:\n{exc}")

        dlg.link_created.connect(_on_link_created)
        dlg.exec()

    def compare_directories(self):
        """WinSCP Commands -> Compare Directories (Shift+F2)."""
        local_map = {}
        for r in range(self.table_local.rowCount()):
            it = self.table_local.item(r, 0)
            if not it:
                continue
            data = it.data(Qt.ItemDataRole.UserRole)
            if not data or data.get("name") == "..":
                continue
            local_map[data["name"]] = (r, data)

        remote_map = {}
        for r in range(self.table_remote.rowCount()):
            it = self.table_remote.item(r, 0)
            if not it:
                continue
            data = it.data(Qt.ItemDataRole.UserRole)
            if not data or data.get("name") == "..":
                continue
            remote_map[data["name"]] = (r, data)

        self.table_local.clearSelection()
        self.table_remote.clearSelection()

        diff_count = 0
        for name, (row, data) in local_map.items():
            if name not in remote_map:
                self.table_local.selectRow(row)
                diff_count += 1
            else:
                rem_row, rem_data = remote_map[name]
                loc_size = data.get("size", 0)
                rem_size = rem_data.get("size", 0)
                loc_mtime = data.get("mtime", 0.0)
                rem_mtime = rem_data.get("mtime", 0.0)

                if loc_size != rem_size or loc_mtime > (rem_mtime + 2.0):
                    self.table_local.selectRow(row)
                    diff_count += 1
                elif rem_mtime > (loc_mtime + 2.0):
                    self.table_remote.selectRow(rem_row)
                    diff_count += 1

        for name, (rem_row, rem_data) in remote_map.items():
            if name not in local_map:
                self.table_remote.selectRow(rem_row)
                diff_count += 1

        self.status_message.emit(f"Directory comparison complete: {diff_count} difference(s) detected.")
        QMessageBox.information(
            self,
            "Compare Directories (Shift+F2)",
            f"Comparison completed!\nFound {diff_count} difference(s) (newer, different size, or missing files) highlighted in the active panes.",
        )

    def select_by_mask(self, is_select: bool = True):
        import fnmatch
        prompt = "Select files matching mask (e.g. *.py; *.txt):" if is_select else "Deselect files matching mask:"
        title = "Select by Mask (+)" if is_select else "Deselect by Mask (-)"
        mask, ok = QInputDialog.getText(self, title, prompt, text="*.*")
        if not ok or not mask.strip():
            return

        table = self.table_remote if (self.table_remote.hasFocus() and self.sftp_client.is_connected) else self.table_local
        for r in range(table.rowCount()):
            it = table.item(r, 0)
            if not it:
                continue
            data = it.data(Qt.ItemDataRole.UserRole)
            if not data or data.get("name") == "..":
                continue
            name = data.get("name", "")
            matches = any(fnmatch.fnmatch(name.lower(), p.strip().lower()) for p in mask.split(";") if p.strip())
            if matches:
                if is_select:
                    table.selectRow(r)
                else:
                    for col in range(table.columnCount()):
                        cell = table.item(r, col)
                        if cell:
                            cell.setSelected(False)

    def invert_selection(self):
        table = self.table_remote if (self.table_remote.hasFocus() and self.sftp_client.is_connected) else self.table_local
        for r in range(table.rowCount()):
            it = table.item(r, 0)
            if not it:
                continue
            data = it.data(Qt.ItemDataRole.UserRole)
            if not data or data.get("name") == "..":
                continue
            is_sel = it.isSelected()
            for col in range(table.columnCount()):
                cell = table.item(r, col)
                if cell:
                    cell.setSelected(not is_sel)

    def select_all_items(self, select: bool = True):
        table = self.table_remote if (self.table_remote.hasFocus() and self.sftp_client.is_connected) else self.table_local
        if select:
            for r in range(table.rowCount()):
                it = table.item(r, 0)
                if it and it.data(Qt.ItemDataRole.UserRole) and it.data(Qt.ItemDataRole.UserRole).get("name") != "..":
                    table.selectRow(r)
        else:
            table.clearSelection()

    def open_synchronize_dialog(self):
        dlg = SynchronizeDialog(self.sftp_client, self.txt_local_path.text(), self.txt_remote_path.text(), self)
        dlg.sync_completed.connect(self.refresh_both)
        dlg.exec()

    def open_bookmarks_dialog(self):
        dlg = BookmarksDialog(self.txt_remote_path.text(), self.txt_local_path.text(), self)
        def _on_jump(remote_p, local_p):
            if remote_p and self.sftp_client.is_connected:
                self._load_remote_directory(remote_p)
            if local_p:
                self._load_local_directory(local_p)
        dlg.jump_requested.connect(_on_jump)
        dlg.exec()

    def open_find_files_dialog(self):
        dlg = FindFilesDialog(self.sftp_client, self.txt_remote_path.text(), self.txt_local_path.text(), self)
        def _on_nav(folder, is_rem):
            if is_rem and self.sftp_client.is_connected:
                self._load_remote_directory(folder)
            else:
                self._load_local_directory(folder)
        def _on_open(path, is_rem):
            if is_rem:
                self.edit_selected_remote_file()
            else:
                self.open_selected_local_file()
        dlg.navigate_requested.connect(_on_nav)
        dlg.open_file_requested.connect(_on_open)
        dlg.exec()

    def open_custom_commands_dialog(self):
        files = []
        for r in range(self.table_remote.rowCount()):
            it = self.table_remote.item(r, 0)
            if it and it.isSelected():
                data = it.data(Qt.ItemDataRole.UserRole)
                if data and data.get("name") != "..":
                    files.append(data["name"])
        dlg = CustomCommandsDialog(self.sftp_client, self.txt_remote_path.text(), files, self)
        dlg.exec()

    def open_generate_url_dialog(self):
        if not self.current_session:
            QMessageBox.information(self, "No Active Session", "Connect to a session to generate its URL.")
            return
        dlg = GenerateUrlDialog(self.current_session, current_path=self.txt_remote_path.text(), parent=self)
        dlg.exec()

    def view_selected(self):
        if self.table_remote.hasFocus() and self.sftp_client.is_connected:
            self.edit_selected_remote_file()
        else:
            self.open_selected_local_file()

    def copy_selected(self):
        if self.table_local.hasFocus():
            self.upload_selected()
        else:
            self.download_selected()

    def move_selected(self):
        if self.table_local.hasFocus():
            self.rename_selected("local")
        else:
            self.rename_selected("remote")

    def delete_selected(self):
        if self.table_remote.hasFocus() and self.sftp_client.is_connected:
            selected = self.table_remote.selectedItems()
            if not selected:
                return
            row = selected[0].row()
            data = self.table_remote.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if not data or data.get("name") == "..":
                return

            reply = QMessageBox.question(
                self,
                "Delete Remote Item",
                f"Permanently delete remote item '{data['name']}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

            is_dir = data.get("is_dir", False)
            remote_path = data["path"]

            def _del():
                if is_dir:
                    self.sftp_client.delete_directory_recursive(remote_path)
                else:
                    self.sftp_client.delete_file(remote_path)

            worker = SFTPActionWorker(_del, f"Deleted {data['name']}")
            self._active_worker = worker
            worker.finished.connect(
                lambda msg: (self.status_message.emit(msg), self._load_remote_directory(self.txt_remote_path.text()))
            )
            worker.failed.connect(lambda err: QMessageBox.critical(self, "Delete Failed", err))
            worker.start()
        else:
            selected = self.table_local.selectedItems()
            if not selected:
                return
            row = selected[0].row()
            data = self.table_local.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if not data or data.get("name") == "..":
                return

            reply = QMessageBox.question(
                self,
                "Delete Local File",
                f"Are you sure you want to delete local file '{data['name']}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                p = Path(data["path"])
                try:
                    if p.is_dir():
                        shutil.rmtree(p)
                    else:
                        p.unlink()
                    self._load_local_directory(self.txt_local_path.text())
                except Exception as exc:
                    QMessageBox.critical(self, "Error", f"Could not delete: {exc}")

    def rename_selected(self, pane: Optional[str] = None):
        """Renames the selected file or folder in either local or remote pane."""
        target_pane = pane
        if not target_pane:
            if self.table_remote.hasFocus() or self.table_remote.viewport().hasFocus():
                target_pane = "remote"
            elif self.table_local.hasFocus() or self.table_local.viewport().hasFocus():
                target_pane = "local"
            elif getattr(self, "_last_active_pane", "") in ("local", "remote"):
                target_pane = self._last_active_pane
            elif self.sftp_client.is_connected and len(self.table_remote.selectedItems()) > 0:
                target_pane = "remote"
            else:
                target_pane = "local"

        if target_pane == "remote":
            if not self.sftp_client.is_connected:
                QMessageBox.warning(self, "Not Connected", "Connect to a remote server before renaming remote files.")
                return

            selected = self.table_remote.selectedItems()
            if not selected:
                QMessageBox.information(self, "Rename", "Please select a remote file or folder to rename.")
                return

            row = selected[0].row()
            data = self.table_remote.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if not data or data.get("name") == "..":
                return

            old_name = data["name"]
            old_path = data["path"]

            new_name, ok = QInputDialog.getText(
                self,
                "Rename Remote Item",
                f"Enter new name for '{old_name}':",
                QLineEdit.EchoMode.Normal,
                old_name,
            )
            if not ok or not new_name.strip() or new_name.strip() == old_name:
                return

            clean_name = new_name.strip()
            parent_dir = str(Path(old_path).parent)
            new_path = f"{parent_dir.rstrip('/')}/{clean_name}" if parent_dir != "/" else f"/{clean_name}"

            worker = SFTPActionWorker(
                lambda: self.sftp_client.rename(old_path, new_path),
                f"Renamed '{old_name}' to '{clean_name}'",
            )
            self._active_worker = worker
            worker.finished.connect(
                lambda msg: (
                    self.status_message.emit(msg),
                    self._load_remote_directory(self.txt_remote_path.text()),
                )
            )
            worker.failed.connect(lambda err: QMessageBox.critical(self, "Rename Failed", f"Could not rename '{old_name}':\n{err}"))
            worker.start()

        else:
            selected = self.table_local.selectedItems()
            if not selected:
                QMessageBox.information(self, "Rename", "Please select a local file or folder to rename.")
                return

            row = selected[0].row()
            data = self.table_local.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if not data or data.get("name") == "..":
                return

            old_name = data["name"]
            old_path = Path(data["path"])

            new_name, ok = QInputDialog.getText(
                self,
                "Rename Local Item",
                f"Enter new name for '{old_name}':",
                QLineEdit.EchoMode.Normal,
                old_name,
            )
            if not ok or not new_name.strip() or new_name.strip() == old_name:
                return

            clean_name = new_name.strip()
            new_path = old_path.parent / clean_name
            if new_path.exists():
                reply = QMessageBox.question(
                    self,
                    "Destination Exists",
                    f"An item named '{clean_name}' already exists. Overwrite?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

            try:
                old_path.rename(new_path)
                self.status_message.emit(f"Renamed '{old_name}' to '{clean_name}'")
                self._load_local_directory(self.txt_local_path.text())
            except Exception as exc:
                QMessageBox.critical(self, "Rename Error", f"Could not rename '{old_name}':\n{exc}")

    def create_directory(self):
        name, ok = QInputDialog.getText(self, "New Folder", "Directory Name:")
        if not ok or not name.strip():
            return

        clean_name = name.strip()
        if self.table_remote.hasFocus() and self.sftp_client.is_connected:
            target = f"{self.txt_remote_path.text().rstrip('/')}/{clean_name}"
            worker = SFTPActionWorker(
                lambda: self.sftp_client.mkdir(target),
                f"Created directory {clean_name}",
            )
            self._active_worker = worker
            worker.finished.connect(
                lambda msg: (self.status_message.emit(msg), self._load_remote_directory(self.txt_remote_path.text()))
            )
            worker.failed.connect(lambda err: QMessageBox.critical(self, "Create Directory Failed", err))
            worker.start()
        else:
            target = Path(self.txt_local_path.text()) / clean_name
            try:
                target.mkdir(parents=True, exist_ok=True)
                self._load_local_directory(self.txt_local_path.text())
            except Exception as exc:
                QMessageBox.critical(self, "Error", f"Could not create folder: {exc}")

    def refresh_both(self):
        self._load_local_directory(self.txt_local_path.text())
        if self.sftp_client.is_connected:
            self._load_remote_directory(self.txt_remote_path.text())

    # ----------------- Helpers -----------------
    def _set_table_item(
        self,
        table: QTableWidget,
        row: int,
        col: int,
        text: str,
        is_dir: bool = False,
        path: str = "",
        info: Optional[RemoteFileInfo] = None,
    ):
        prefix = "📁 " if (is_dir and col == 0) else ("📄 " if col == 0 else "")
        display = f"{prefix}{text}" if col == 0 else text
        item = QTableWidgetItem(display)

        if is_dir and col == 0:
            item.setForeground(QColor("#569cd6"))
            item.setFont(QFont("Sans", weight=QFont.Weight.Bold))

        if col == 0:
            item.setData(Qt.ItemDataRole.UserRole, {"name": text, "is_dir": is_dir, "path": path, "info": info})

        table.setItem(row, col, item)

    def _format_size(self, size_bytes: int) -> str:
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size_bytes < 1024.0:
                return f"{size_bytes:3.1f} {unit}" if unit != "B" else f"{size_bytes} B"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} PB"

    def _show_local_context_menu(self, pos: QPoint):
        item = self.table_local.itemAt(pos)
        if item:
            self.table_local.setCurrentItem(item)
            self.table_local.selectRow(item.row())

        menu = QMenu(self)
        act_open = menu.addAction("✏ Open / Edit (F4)")
        act_open.triggered.connect(lambda: self.open_selected_local_file(force_open_with=False))
        act_open_with = menu.addAction("📂 Open With...")
        act_open_with.triggered.connect(lambda: self.open_selected_local_file(force_open_with=True))
        menu.addSeparator()
        act_rename = menu.addAction("✏ Rename (F2)")
        act_rename.triggered.connect(lambda: self.rename_selected("local"))
        act_chmod = menu.addAction("🔒 Properties / Chmod (F9)")
        act_chmod.triggered.connect(self.chmod_selected)
        if self.sftp_client.is_connected:
            act_up = menu.addAction("⬆ Upload to Remote (F5)")
            act_up.triggered.connect(self.upload_selected)
        act_del = menu.addAction("🗑 Delete (F8)")
        act_del.triggered.connect(self.delete_selected)
        act_mkdir = menu.addAction("📁 New Directory (F7)")
        act_mkdir.triggered.connect(self.create_directory)
        menu.addSeparator()
        act_ref = menu.addAction("🔄 Refresh")
        act_ref.triggered.connect(lambda: self._load_local_directory(self.txt_local_path.text()))
        menu.exec(self.table_local.mapToGlobal(pos))

    def _show_remote_context_menu(self, pos: QPoint):
        item = self.table_remote.itemAt(pos)
        if item:
            self.table_remote.setCurrentItem(item)
            self.table_remote.selectRow(item.row())

        menu = QMenu(self)
        act_edit = menu.addAction("✏ Edit & Auto-Sync (F4)")
        act_edit.triggered.connect(lambda: self.edit_selected_remote_file(force_open_with=False))
        act_open_with = menu.addAction("📂 Open With...")
        act_open_with.triggered.connect(lambda: self.edit_selected_remote_file(force_open_with=True))

        act_down = menu.addAction("⬇ Download to Local (F5)")
        act_down.triggered.connect(self.download_selected)

        menu.addSeparator()
        act_rename = menu.addAction("✏ Rename (F2)")
        act_rename.triggered.connect(lambda: self.rename_selected("remote"))
        act_chmod = menu.addAction("🔒 Properties / Chmod (F9)")
        act_chmod.triggered.connect(self.chmod_selected)

        act_del = menu.addAction("🗑 Delete (F8)")
        act_del.triggered.connect(self.delete_selected)

        act_mkdir = menu.addAction("📁 New Directory (F7)")
        act_mkdir.triggered.connect(self.create_directory)

        menu.addSeparator()
        act_sys_term = menu.addAction("🖥 Open in System Terminal (Ctrl+P)")
        act_sys_term.triggered.connect(self.open_terminal_window)

        act_ref = menu.addAction("🔄 Refresh")
        act_ref.triggered.connect(lambda: self._load_remote_directory(self.txt_remote_path.text()))

        menu.exec(self.table_remote.mapToGlobal(pos))


class MainWindow(QMainWindow):
    """
    WinSCP-Linux Commander Main Window with Multi-Session Tabbed Architecture.
    Allows connecting to multiple SSH/SFTP servers simultaneously, one by one.
    """

    def __init__(self, session_store: Optional[SessionStore] = None):
        super().__init__()
        self.session_store = session_store or SessionStore()

        self.setWindowTitle("Nova Commander")
        self.resize(1180, 780)

        self._setup_ui()
        self._setup_menus()
        self._setup_toolbars()
        self._setup_statusbar()
        self._setup_global_shortcuts()

        # Create initial default session tab
        self.create_session_tab()

    def _setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Tab widget for multi-session support
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.setMovable(True)
        self.tab_widget.setDocumentMode(True)

        # Plus button in tab corner for quick connection to new SSH servers
        self.btn_new_tab = QPushButton(" ➕ ")
        self.btn_new_tab.setToolTip("Connect to another SSH/SFTP server (New Tab)...")
        self.btn_new_tab.setStyleSheet(
            "QPushButton { font-weight: bold; padding: 2px 8px; border: none; background: #333333; color: #4ec9b0; }"
            "QPushButton:hover { background: #3e3e42; color: #ffffff; }"
        )
        self.btn_new_tab.clicked.connect(self.open_session_dialog)
        self.tab_widget.setCornerWidget(self.btn_new_tab, Qt.Corner.TopRightCorner)

        self.tab_widget.tabCloseRequested.connect(self._close_session_tab)
        self.tab_widget.currentChanged.connect(self._on_tab_changed)
        self.tab_widget.tabBar().tabMoved.connect(lambda *_: self._update_tab_tooltips())

        main_layout.addWidget(self.tab_widget)

        # Bottom Classic Commander Function Keys Bar (F2..F10)
        self.function_bar = FunctionBarWidget(self)
        self.function_bar.f2_rename_clicked.connect(self.rename_selected)
        self.function_bar.f3_view_clicked.connect(self.view_selected)
        self.function_bar.f4_edit_clicked.connect(self.edit_selected_remote_file)
        self.function_bar.f5_copy_clicked.connect(self.copy_selected)
        self.function_bar.f6_move_clicked.connect(self.move_selected)
        self.function_bar.f7_mkdir_clicked.connect(self.create_directory)
        self.function_bar.f8_delete_clicked.connect(self.delete_selected)
        self.function_bar.f9_properties_clicked.connect(self.properties_selected)
        self.function_bar.f10_quit_clicked.connect(self.close)
        main_layout.addWidget(self.function_bar)

    def _setup_menus(self):
        menubar = self.menuBar()

        # ================= Session Menu =================
        m_session = menubar.addMenu("&Session")
        act_login = m_session.addAction("New Session (Connect)...")
        act_login.setShortcut("Ctrl+N")
        act_login.triggered.connect(self.open_session_dialog)

        act_reconnect = m_session.addAction("Reconnect")
        act_reconnect.triggered.connect(self.reconnect_active_session)

        act_duplicate = m_session.addAction("Duplicate Session in New Tab")
        act_duplicate.triggered.connect(self.duplicate_active_session)

        act_disconnect = m_session.addAction("Disconnect Active Session")
        act_disconnect.triggered.connect(self.disconnect_session)

        act_close_tab = m_session.addAction("Close Active Tab")
        act_close_tab.setShortcut("Ctrl+W")
        act_close_tab.triggered.connect(lambda: self._close_session_tab(self.tab_widget.currentIndex()))

        m_session.addSeparator()
        act_gen_url = m_session.addAction("Generate Session URL / Code...")
        act_gen_url.setShortcut(QKeySequence("Ctrl+Alt+U"))
        act_gen_url.triggered.connect(self.open_generate_url_dialog)
        self.addAction(act_gen_url)

        act_export = m_session.addAction("Export / Import Sessions...")
        act_export.triggered.connect(self.open_export_dialog)

        m_session.addSeparator()
        act_quit = m_session.addAction("Exit")
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)

        # ================= Files Menu =================
        m_files = menubar.addMenu("&Files")
        act_open_edit = m_files.addAction("Open / Edit (F4)")
        act_open_edit.setShortcut(QKeySequence("F4"))
        act_open_edit.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        act_open_edit.triggered.connect(self.edit_selected_remote_file)
        self.addAction(act_open_edit)

        act_open_with = m_files.addAction("Open With...")
        act_open_with.triggered.connect(self.open_with_selected)

        act_view = m_files.addAction("View (F3)")
        act_view.setShortcut(QKeySequence("F3"))
        act_view.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        act_view.triggered.connect(self.view_selected)
        self.addAction(act_view)

        m_files.addSeparator()
        act_copy = m_files.addAction("Copy / Transfer (F5)")
        act_copy.setShortcut(QKeySequence("F5"))
        act_copy.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        act_copy.triggered.connect(self.copy_selected)
        self.addAction(act_copy)

        act_move = m_files.addAction("Move (F6)")
        act_move.setShortcut(QKeySequence("F6"))
        act_move.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        act_move.triggered.connect(self.move_selected)
        self.addAction(act_move)

        act_rename = m_files.addAction("Rename (F2)")
        act_rename.setShortcut(QKeySequence("F2"))
        act_rename.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        act_rename.triggered.connect(self.rename_selected)
        self.addAction(act_rename)

        act_del = m_files.addAction("Delete (F8)")
        act_del.setShortcut(QKeySequence("F8"))
        act_del.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        act_del.triggered.connect(self.delete_selected)
        self.addAction(act_del)

        m_files.addSeparator()
        act_mkdir = m_files.addAction("New Directory (F7)")
        act_mkdir.setShortcut(QKeySequence("F7"))
        act_mkdir.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        act_mkdir.triggered.connect(self.create_directory)
        self.addAction(act_mkdir)

        act_symlink = m_files.addAction("New Symbolic Link...")
        act_symlink.triggered.connect(self.create_symlink)

        m_files.addSeparator()
        act_prop = m_files.addAction("Properties / Rights (F9)")
        act_prop.setShortcut(QKeySequence("F9"))
        act_prop.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        act_prop.triggered.connect(self.properties_selected)
        self.addAction(act_prop)

        # ================= Mark Menu =================
        m_mark = menubar.addMenu("&Mark")
        act_sel_all = m_mark.addAction("Select All (Ctrl+A)")
        act_sel_all.setShortcut(QKeySequence("Ctrl+A"))
        act_sel_all.triggered.connect(lambda: self.select_all_items(True))

        act_desel_all = m_mark.addAction("Deselect All (Ctrl+Shift+A)")
        act_desel_all.setShortcut(QKeySequence("Ctrl+Shift+A"))
        act_desel_all.triggered.connect(lambda: self.select_all_items(False))

        act_invert = m_mark.addAction("Invert Selection (Ctrl+I)")
        act_invert.setShortcut(QKeySequence("Ctrl+I"))
        act_invert.triggered.connect(self.invert_selection)

        m_mark.addSeparator()
        act_sel_mask = m_mark.addAction("Select by Mask... (+)")
        act_sel_mask.triggered.connect(lambda: self.select_by_mask(True))

        act_desel_mask = m_mark.addAction("Deselect by Mask... (-)")
        act_desel_mask.triggered.connect(lambda: self.select_by_mask(False))

        # ================= Commands Menu =================
        m_cmd = menubar.addMenu("&Commands")
        act_sync = m_cmd.addAction("Synchronize... (Ctrl+S)")
        act_sync.setShortcut(QKeySequence("Ctrl+S"))
        act_sync.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        act_sync.triggered.connect(self.open_synchronize_dialog)
        self.addAction(act_sync)

        act_compare = m_cmd.addAction("Compare Directories (Shift+F2)")
        act_compare.setShortcut(QKeySequence("Shift+F2"))
        act_compare.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        act_compare.triggered.connect(self.compare_directories)
        self.addAction(act_compare)

        act_find = m_cmd.addAction("Find Files... (Ctrl+F)")
        act_find.setShortcuts([QKeySequence("Ctrl+F"), QKeySequence("Shift+F7")])
        act_find.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        act_find.triggered.connect(self.open_find_files_dialog)
        self.addAction(act_find)

        act_custom_cmd = m_cmd.addAction("Custom Commands... (Ctrl+Shift+C)")
        act_custom_cmd.setShortcut(QKeySequence("Ctrl+Shift+C"))
        act_custom_cmd.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        act_custom_cmd.triggered.connect(self.open_custom_commands_dialog)
        self.addAction(act_custom_cmd)

        act_bmarks = m_cmd.addAction("Directory Bookmarks... (Ctrl+D)")
        act_bmarks.setShortcut(QKeySequence("Ctrl+D"))
        act_bmarks.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        act_bmarks.triggered.connect(self.open_bookmarks_dialog)
        self.addAction(act_bmarks)

        m_cmd.addSeparator()
        act_open_with = m_cmd.addAction("Open With...")
        act_open_with.triggered.connect(self.open_with_selected)

        act_pref_editor = m_cmd.addAction("Configure Default Applications...")
        act_pref_editor.triggered.connect(self.configure_default_applications)

        m_cmd.addSeparator()
        act_term_win = m_cmd.addAction("Open in System Terminal (Ctrl+P)")
        act_term_win.setShortcut(QKeySequence("Ctrl+P"))
        act_term_win.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        act_term_win.triggered.connect(self.open_terminal_window)
        self.addAction(act_term_win)

        # ================= Tabs Menu =================
        m_tabs = menubar.addMenu("&Tabs")
        self.m_tabs = m_tabs

        self.act_next_tab = m_tabs.addAction("Next Tab (Ctrl+Tab)")
        self.act_next_tab.setShortcuts([QKeySequence("Ctrl+Tab"), QKeySequence("Ctrl+PgDown")])
        self.act_next_tab.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.act_next_tab.triggered.connect(self.next_session_tab)
        self.addAction(self.act_next_tab)

        self.act_prev_tab = m_tabs.addAction("Previous Tab (Ctrl+Shift+Tab)")
        self.act_prev_tab.setShortcuts([QKeySequence("Ctrl+Shift+Tab"), QKeySequence("Ctrl+PgUp")])
        self.act_prev_tab.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.act_prev_tab.triggered.connect(self.prev_session_tab)
        self.addAction(self.act_prev_tab)

        m_tabs.addSeparator()

        self.tab_actions = []
        for i in range(1, 10):
            label = f"Switch to Tab {i}" if i < 9 else "Switch to Tab 9 / Last Tab"
            act_tab = m_tabs.addAction(f"{label} (Ctrl+{i})")
            act_tab.setShortcut(QKeySequence(f"Ctrl+{i}"))
            act_tab.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            target_idx = i - 1
            act_tab.triggered.connect(partial(self._on_switch_tab_action, target_idx))
            self.addAction(act_tab)
            self.tab_actions.append(act_tab)

        m_tabs.aboutToShow.connect(self._refresh_tabs_menu)

        # ================= View Menu =================
        m_view = menubar.addMenu("&View")
        act_refresh = m_view.addAction("Refresh (Ctrl+R)")
        act_refresh.setShortcut(QKeySequence("Ctrl+R"))
        act_refresh.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        act_refresh.triggered.connect(self.refresh_both)
        self.addAction(act_refresh)

        act_toggle_fbar = m_view.addAction("Toggle Function Keys Bar")
        act_toggle_fbar.triggered.connect(self.toggle_function_bar)

        act_toggle_theme = m_view.addAction("🎨 Toggle Theme (Classic WinSCP / Dark)")
        act_toggle_theme.triggered.connect(self.toggle_theme)

        # ================= Options Menu =================
        m_opts = menubar.addMenu("&Options")
        act_transfer_opts = m_opts.addAction("Transfer Options...")
        act_transfer_opts.triggered.connect(self.open_transfer_options_dialog)

        act_pref_editor = m_opts.addAction("Configure Default Applications...")
        act_pref_editor.triggered.connect(self.configure_default_applications)

        # ================= Help Menu =================
        m_help = menubar.addMenu("&Help")
        act_about = m_help.addAction("About Nova Commander")
        act_about.triggered.connect(self._show_about)

    def _setup_toolbars(self):
        toolbar = QToolBar("Main Operations")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.tb_connect = toolbar.addAction("🔌 Connect")
        self.tb_connect.setToolTip("Connect to SSH / SFTP Session (Ctrl+N)")
        self.tb_connect.triggered.connect(self.open_session_dialog)

        self.tb_disconnect = toolbar.addAction("⏹ Disconnect")
        self.tb_disconnect.setEnabled(False)
        self.tb_disconnect.triggered.connect(self.disconnect_session)

        self.tb_term_win = toolbar.addAction("🖥 Terminal")
        self.tb_term_win.setToolTip("Open System Terminal (Ctrl+P)")
        self.tb_term_win.triggered.connect(self.open_terminal_window)

        toolbar.addSeparator()

        self.tb_refresh = toolbar.addAction("🔄 Refresh")
        self.tb_refresh.triggered.connect(self.refresh_both)

        self.tb_sync = toolbar.addAction("🔄 Sync")
        self.tb_sync.setToolTip("Synchronize Directories (Ctrl+S)")
        self.tb_sync.triggered.connect(self.open_synchronize_dialog)

        self.tb_compare = toolbar.addAction("⚖ Compare")
        self.tb_compare.setToolTip("Compare Directories (Shift+F2)")
        self.tb_compare.triggered.connect(self.compare_directories)

        self.tb_find = toolbar.addAction("🔍 Find")
        self.tb_find.setToolTip("Find Files (Ctrl+F)")
        self.tb_find.triggered.connect(self.open_find_files_dialog)

        self.tb_bmarks = toolbar.addAction("⭐ Bookmarks")
        self.tb_bmarks.setToolTip("Directory Bookmarks (Ctrl+D)")
        self.tb_bmarks.triggered.connect(self.open_bookmarks_dialog)

        self.tb_custom_cmd = toolbar.addAction("⚙ Commands")
        self.tb_custom_cmd.setToolTip("Custom Remote Commands (Ctrl+Shift+C)")
        self.tb_custom_cmd.triggered.connect(self.open_custom_commands_dialog)

        toolbar.addSeparator()

        self.tb_upload = toolbar.addAction("⬆ Upload")
        self.tb_upload.triggered.connect(self.upload_selected)

        self.tb_download = toolbar.addAction("⬇ Download")
        self.tb_download.triggered.connect(self.download_selected)

        self.tb_edit = toolbar.addAction("✏ Edit")
        self.tb_edit.triggered.connect(self.edit_selected_remote_file)

        self.tb_open_with = toolbar.addAction("📂 Open With")
        self.tb_open_with.triggered.connect(self.open_with_selected)

        self.tb_rename = toolbar.addAction("✏ Rename")
        self.tb_rename.setToolTip("Rename selected item (F2)")
        self.tb_rename.triggered.connect(self.rename_selected)

        self.tb_chmod = toolbar.addAction("🔒 Properties")
        self.tb_chmod.setToolTip("Properties & Rights (F9)")
        self.tb_chmod.triggered.connect(self.properties_selected)

        self.tb_mkdir = toolbar.addAction("📁 New Folder")
        self.tb_mkdir.triggered.connect(self.create_directory)

        self.tb_symlink = toolbar.addAction("🔗 Symlink")
        self.tb_symlink.setToolTip("New Symbolic Link")
        self.tb_symlink.triggered.connect(self.create_symlink)

        self.tb_delete = toolbar.addAction("🗑 Delete")
        self.tb_delete.triggered.connect(self.delete_selected)

        toolbar.addSeparator()
        self.tb_theme = toolbar.addAction("🎨 Theme")
        self.tb_theme.setToolTip("Toggle Theme (Classic WinSCP / Modern Dark)")
        self.tb_theme.triggered.connect(self.toggle_theme)

    def _setup_statusbar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.lbl_status = QLabel("Ready")
        self.status_bar.addWidget(self.lbl_status, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(200)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        self.status_bar.addPermanentWidget(self.progress_bar)

        self.lbl_session = QLabel("🔴 Not Connected")
        self.lbl_session.setStyleSheet("padding: 2px 8px; font-weight: bold; color: #888888;")
        self.status_bar.addPermanentWidget(self.lbl_session)

    def _setup_global_shortcuts(self):
        """Global shortcuts are registered as window actions in _setup_menubar to avoid ambiguity."""
        pass

    def keyPressEvent(self, event: QKeyEvent):
        """Catches Ctrl+1 .. Ctrl+9 directly to guarantee tab navigation across all focus states."""
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            key = event.key()
            if Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
                idx = key - Qt.Key.Key_1
                self.switch_to_tab(idx)
                event.accept()
                return
            elif key in (Qt.Key.Key_Tab, Qt.Key.Key_PageDown):
                self.next_session_tab()
                event.accept()
                return
            elif key == Qt.Key.Key_PageUp:
                self.prev_session_tab()
                event.accept()
                return
        elif event.modifiers() == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                self.prev_session_tab()
                event.accept()
                return
        super().keyPressEvent(event)

    def _on_switch_tab_action(self, target_idx: int, *args):
        """Action handler for Ctrl+1 through Ctrl+9."""
        self.switch_to_tab(target_idx)

    def switch_to_tab(self, index: int):
        """
        Switches active session tab to the specified 0-based index.
        Ctrl+1 switches to tab index 0, Ctrl+2 to index 1, etc.
        Ctrl+9 (index 8) switches to the last tab when fewer than 9 tabs are open.
        """
        count = self.tab_widget.count()
        if count == 0:
            return

        if index == 8 and count < 9:
            target = count - 1
        else:
            target = index

        if 0 <= target < count:
            self.tab_widget.setCurrentIndex(target)

    def next_session_tab(self):
        """Switches to the next session tab with circular wrap-around."""
        count = self.tab_widget.count()
        if count > 1:
            next_idx = (self.tab_widget.currentIndex() + 1) % count
            self.tab_widget.setCurrentIndex(next_idx)

    def prev_session_tab(self):
        """Switches to the previous session tab with circular wrap-around."""
        count = self.tab_widget.count()
        if count > 1:
            prev_idx = (self.tab_widget.currentIndex() - 1 + count) % count
            self.tab_widget.setCurrentIndex(prev_idx)

    def _refresh_tabs_menu(self):
        """Dynamically updates the Tabs menu items with open session tab titles."""
        count = self.tab_widget.count()
        current_idx = self.tab_widget.currentIndex()

        self.act_next_tab.setEnabled(count > 1)
        self.act_prev_tab.setEnabled(count > 1)

        for i, act in enumerate(self.tab_actions):
            if i < count:
                tab_name = self.tab_widget.tabText(i)
                prefix = "✓ " if i == current_idx else "   "
                act.setText(f"{prefix}{i+1}. {tab_name} (Ctrl+{i+1})")
            elif i == 8 and count > 1:
                last_name = self.tab_widget.tabText(count - 1)
                prefix = "✓ " if (count - 1) == current_idx else "   "
                act.setText(f"{prefix}9. Last Tab ({last_name}) (Ctrl+9)")
            else:
                act.setText(f"   {i+1}. Tab {i+1} (Ctrl+{i+1})")

    def _update_tab_tooltips(self):
        """Updates tooltips on each tab with shortcut hints (e.g. Ctrl+1, Ctrl+2)."""
        count = self.tab_widget.count()
        for i in range(count):
            shortcut_hint = f" (Ctrl+{i+1})" if i < 9 else ""
            tab_text = self.tab_widget.tabText(i)
            clean_text = tab_text.split(" (Ctrl+")[0] if " (Ctrl+" in tab_text else tab_text
            self.tab_widget.setTabToolTip(i, f"{clean_text}{shortcut_hint}")

    # =========================================================================
    # Multi-Session Tab Management
    # =========================================================================

    def active_session_view(self) -> Optional[SessionCommanderView]:
        """Returns the currently active session tab widget."""
        widget = self.tab_widget.currentWidget()
        return widget if isinstance(widget, SessionCommanderView) else None

    def create_session_tab(self, session: Optional[SessionConfig] = None) -> SessionCommanderView:
        """Creates a new independent session commander tab."""
        view = SessionCommanderView(self)
        title = f"🖥 {session.name}" if session else f"🖥 Session {self.tab_widget.count() + 1}"
        idx = self.tab_widget.addTab(view, title)
        self.tab_widget.setCurrentIndex(idx)

        # Wire view signals
        view.status_message.connect(self._on_view_status_message)
        view.progress_changed.connect(self._on_view_progress_changed)
        view.session_connected.connect(lambda t, info: self._on_view_connected(view, t, info))
        view.session_disconnected.connect(lambda: self._on_view_disconnected(view))

        self._update_tab_tooltips()

        if session:
            view.connect_to_session(session)

        return view

    def _close_session_tab(self, index: int):
        """Closes a session tab and disconnects its SSH/SFTP connection."""
        widget = self.tab_widget.widget(index)
        if isinstance(widget, SessionCommanderView):
            if widget.sftp_client.is_connected:
                s_name = widget.current_session.name if widget.current_session else "this session"
                reply = QMessageBox.question(
                    self,
                    "Close Session Tab",
                    f"Disconnect and close session '{s_name}'?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

            widget.cleanup()

        self.tab_widget.removeTab(index)

        # If all tabs closed, create an empty one
        if self.tab_widget.count() == 0:
            self.create_session_tab()
        else:
            self._update_tab_tooltips()

    def _on_tab_changed(self, index: int):
        """Updates toolbar and status bar to reflect the newly active tab."""
        view = self.active_session_view()
        if not view:
            self.lbl_session.setText("🔴 Not Connected")
            self.lbl_session.setStyleSheet("padding: 2px 8px; font-weight: bold; color: #888888;")
            self.tb_disconnect.setEnabled(False)
            return

        if view.sftp_client.is_connected and view.current_session:
            s = view.current_session
            tab_info = f" [Tab {index+1}/{self.tab_widget.count()}]"
            self.lbl_session.setText(f"🟢 {s.username}@{s.host}:{s.port}{tab_info}")
            self.lbl_session.setStyleSheet("padding: 2px 8px; font-weight: bold; color: #4ec9b0;")
            self.tb_disconnect.setEnabled(True)
        else:
            tab_info = f" [Tab {index+1}/{self.tab_widget.count()}]"
            self.lbl_session.setText(f"🔴 Not Connected{tab_info}")
            self.lbl_session.setStyleSheet("padding: 2px 8px; font-weight: bold; color: #888888;")
            self.tb_disconnect.setEnabled(False)

        self.status_bar.showMessage(f"Active Tab: {self.tab_widget.tabText(index)} [Ctrl+{index+1 if index < 9 else 9}]", 2500)

    def _on_view_status_message(self, msg: str):
        self.lbl_status.setText(msg)

    def _on_view_progress_changed(self, val: int):
        if val < 0:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.show()
        elif val >= 100:
            self.progress_bar.setValue(100)
            self.progress_bar.hide()
        elif val == 0:
            self.progress_bar.hide()
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(val)
            self.progress_bar.show()

    def _on_view_connected(self, view: SessionCommanderView, title: str, info: str):
        idx = self.tab_widget.indexOf(view)
        if idx >= 0:
            self.tab_widget.setTabText(idx, f"🖥 {title}")
            self._update_tab_tooltips()
        if self.active_session_view() == view:
            tab_info = f" [Tab {idx+1}/{self.tab_widget.count()}]"
            self.lbl_session.setText(f"🟢 {info}{tab_info}")
            self.lbl_session.setStyleSheet("padding: 2px 8px; font-weight: bold; color: #4ec9b0;")
            self.tb_disconnect.setEnabled(True)

    def _on_view_disconnected(self, view: SessionCommanderView):
        idx = self.tab_widget.indexOf(view)
        if idx >= 0:
            self.tab_widget.setTabText(idx, f"🖥 Disconnected")
            self._update_tab_tooltips()
        if self.active_session_view() == view:
            tab_info = f" [Tab {idx+1}/{self.tab_widget.count()}]"
            self.lbl_session.setText(f"🔴 Not Connected{tab_info}")
            self.lbl_session.setStyleSheet("padding: 2px 8px; font-weight: bold; color: #888888;")
            self.tb_disconnect.setEnabled(False)

    # =========================================================================
    # Session Connection Actions
    # =========================================================================

    def open_session_dialog(self):
        dlg = SessionDialog(self.session_store, self)
        dlg.login_requested.connect(self.connect_to_session)
        dlg.export_requested.connect(self.open_export_dialog)
        dlg.exec()

    def connect_to_session(self, session: SessionConfig):
        """
        Connects to a session.
        If the active tab is not currently connected, connects within it.
        Otherwise, automatically opens a NEW tab for this SSH connection,
        allowing users to connect to multiple SSH servers one by one!
        """
        active = self.active_session_view()
        if active and not active.sftp_client.is_connected and not active.current_session:
            idx = self.tab_widget.currentIndex()
            self.tab_widget.setTabText(idx, f"🖥 {session.name}")
            self._update_tab_tooltips()
            active.connect_to_session(session)
        else:
            # Open in a new session tab
            self.create_session_tab(session)

    def disconnect_session(self):
        if view := self.active_session_view():
            view.disconnect_session()

    def open_export_dialog(self):
        dlg = ExportDialog(self.session_store, self)
        dlg.sessions_updated.connect(lambda: self.status_bar.showMessage("Sessions synchronized.", 3000))
        dlg.exec()

    # =========================================================================
    # Active Session Forwarding Operations
    # =========================================================================

    def upload_selected(self):
        if view := self.active_session_view():
            view.upload_selected()

    def download_selected(self):
        if view := self.active_session_view():
            view.download_selected()

    def edit_selected_remote_file(self, force_open_with: bool = False):
        if view := self.active_session_view():
            view.edit_selected_remote_file(force_open_with=force_open_with)

    def open_selected_local_file(self, force_open_with: bool = False):
        if view := self.active_session_view():
            view.open_selected_local_file(force_open_with=force_open_with)

    def open_with_selected(self):
        """Opens the selected file (local or remote) prompting for application selection."""
        if view := self.active_session_view():
            if view.table_local.hasFocus():
                view.open_selected_local_file(force_open_with=True)
            else:
                view.edit_selected_remote_file(force_open_with=True)

    def configure_default_applications(self):
        """Opens the Application Preferences Dialog to configure default editors."""
        dlg = EditorPreferencesDialog(editor_preferences, self)
        dlg.exec()

    def chmod_selected(self):
        if view := self.active_session_view():
            view.chmod_selected()

    def rename_selected(self):
        if view := self.active_session_view():
            view.rename_selected()

    def delete_selected(self):
        if view := self.active_session_view():
            view.delete_selected()

    def create_directory(self):
        if view := self.active_session_view():
            view.create_directory()

    def open_terminal(self):
        if view := self.active_session_view():
            view.open_terminal_window()

    def open_terminal_window(self):
        if view := self.active_session_view():
            view.open_terminal_window()

    def properties_selected(self):
        if view := self.active_session_view():
            view.properties_selected()

    def view_selected(self):
        if view := self.active_session_view():
            view.view_selected()

    def copy_selected(self):
        if view := self.active_session_view():
            view.copy_selected()

    def move_selected(self):
        if view := self.active_session_view():
            view.move_selected()

    def create_symlink(self):
        if view := self.active_session_view():
            view.create_symlink()

    def compare_directories(self):
        if view := self.active_session_view():
            view.compare_directories()

    def select_by_mask(self, is_select: bool = True):
        if view := self.active_session_view():
            view.select_by_mask(is_select)

    def invert_selection(self):
        if view := self.active_session_view():
            view.invert_selection()

    def select_all_items(self, select: bool = True):
        if view := self.active_session_view():
            view.select_all_items(select)

    def open_synchronize_dialog(self):
        if view := self.active_session_view():
            view.open_synchronize_dialog()

    def open_bookmarks_dialog(self):
        if view := self.active_session_view():
            view.open_bookmarks_dialog()

    def open_find_files_dialog(self):
        if view := self.active_session_view():
            view.open_find_files_dialog()

    def open_custom_commands_dialog(self):
        if view := self.active_session_view():
            view.open_custom_commands_dialog()

    def open_generate_url_dialog(self):
        if view := self.active_session_view():
            view.open_generate_url_dialog()

    def open_transfer_options_dialog(self):
        dlg = TransferOptionsDialog(getattr(self, "_transfer_options", {}), self)
        def _on_save(opts):
            self._transfer_options = opts
            self.status_bar.showMessage("Transfer options updated.", 3000)
        dlg.options_saved.connect(_on_save)
        dlg.exec()

    def toggle_function_bar(self):
        self.function_bar.setVisible(not self.function_bar.isVisible())

    def reconnect_active_session(self):
        if view := self.active_session_view():
            if view.current_session:
                view.connect_to_session(view.current_session)

    def duplicate_active_session(self):
        if view := self.active_session_view():
            if view.current_session:
                self.create_session_tab(view.current_session)

    def refresh_both(self):
        if view := self.active_session_view():
            view.refresh_both()

    def toggle_theme(self):
        """Toggles between Classic WinSCP Light theme and Modern Dark theme live."""
        current = getattr(self, "_current_theme_mode", "classic")
        new_mode = "dark" if current == "classic" else "classic"
        self._current_theme_mode = new_mode
        try:
            from main import apply_theme
        except ImportError:
            from src.main import apply_theme
        app = QApplication.instance()
        if app:
            apply_theme(app, mode=new_mode)
            theme_title = "Classic WinSCP" if new_mode == "classic" else "Modern Dark"
            self.status_bar.showMessage(f"Theme switched to: {theme_title}", 3000)

    # =========================================================================
    # Compatibility Properties for Direct Access & Existing Tests
    # =========================================================================

    @property
    def table_local(self):
        view = self.active_session_view()
        return view.table_local if view else None

    @property
    def table_remote(self):
        view = self.active_session_view()
        return view.table_remote if view else None

    @property
    def sftp_client(self):
        view = self.active_session_view()
        return view.sftp_client if view else None

    @property
    def current_session(self):
        view = self.active_session_view()
        return view.current_session if view else None

    @property
    def terminal_widget(self):
        view = self.active_session_view()
        return view.terminal_widget if view else None

    # =========================================================================
    # Window Lifecycle & About
    # =========================================================================

    def _show_about(self):
        QMessageBox.about(
            self,
            "About Nova Commander",
            "<h3>Nova Commander</h3>"
            "<p>A professional native Linux dual-pane SFTP and SSH commander featuring:</p>"
            "<ul>"
            "<li>Multi-Session Tabbed SSH / SFTP Commander</li>"
            "<li>Bidirectional Drag & Drop Transfers for Files and Folders</li>"
            "<li>Live Remote Direct-Edit with Watchdog (inotify) Auto-Sync</li>"
            "<li>Native OS Terminal Integration (Ctrl+P)</li>"
            "<li>WinSCP-Compatible .ini Export & Import</li>"
            "<li>AES-256-GCM + PBKDF2HMAC (600,000 rounds) Encrypted Backups</li>"
            "</ul>"
            "<p>Built with Python 3, PyQt6, Paramiko, and Cryptography.</p>",
        )

    def closeEvent(self, event):
        """Clean up all active sessions on exit."""
        for i in range(self.tab_widget.count()):
            widget = self.tab_widget.widget(i)
            if isinstance(widget, SessionCommanderView):
                widget.cleanup()
        super().closeEvent(event)
