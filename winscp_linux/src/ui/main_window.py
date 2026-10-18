"""
Main Window (Dual-Pane Commander UI) for WinSCP-Linux.
Implements:
- Dual-pane filesystem commander (Local Linux filesystem on Left, Remote SFTP on Right).
- WinSCP toolbar and context menus.
- Remote file direct-edit with watchdog auto-sync.
- Embedded SSH terminal panel.
- Drag-and-drop transfers, chmod dialog, rename, mkdir, delete.
"""

from __future__ import annotations

import os
import shutil
import stat
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
    SFTPClient,
    SFTPConnectWorker,
    SFTPDownloadWorker,
    SFTPListDirWorker,
    SFTPUploadWorker,
)
from ..core.ssh_client import SSHShellClient
from ..core.sync_engine import SyncEngine
from .export_dialog import ExportDialog
from .session_dialog import SessionDialog
from .terminal_widget import TerminalWidget


class ChmodDialog(QDialog):
    """WinSCP-style file permission (chmod) editor dialog."""

    def __init__(self, target_name: str, current_octal: str = "755", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(f"Change Permissions - {target_name}")
        self.resize(360, 240)

        layout = QVBoxLayout(self)

        group = QGroupBox("Permissions")
        g_layout = QVBoxLayout(group)

        # 3x3 Checkboxes: Owner, Group, Others (Read, Write, Execute)
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


class MainWindow(QMainWindow):
    """
    WinSCP-Linux Commander Main Window.
    """

    def __init__(self, session_store: Optional[SessionStore] = None):
        super().__init__()
        self.session_store = session_store or SessionStore()
        self.sftp_client = SFTPClient()
        self.ssh_shell = SSHShellClient(self)
        self.sync_engine = SyncEngine(self)

        self.current_session: Optional[SessionConfig] = None
        self.session_id: str = "default"

        self.setWindowTitle("WinSCP-Linux")
        self.resize(1180, 780)

        self._active_worker = None

        self._setup_ui()
        self._setup_menus()
        self._setup_toolbars()
        self._setup_statusbar()
        self._wire_sync_engine()

        # Load initial local directory
        self._load_local_directory(str(Path.home()))

    def _setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # Vertical splitter dividing Dual-Pane Commander and Embedded Terminal
        self.v_splitter = QSplitter(Qt.Orientation.Vertical)
        main_layout.addWidget(self.v_splitter)

        # ----------------- Dual-Pane Commander -----------------
        pane_container = QWidget()
        pane_layout = QHBoxLayout(pane_container)
        pane_layout.setContentsMargins(0, 0, 0, 0)
        pane_layout.setSpacing(4)

        self.h_splitter = QSplitter(Qt.Orientation.Horizontal)
        pane_layout.addWidget(self.h_splitter)

        # === Left Panel: Local Filesystem ===
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(2)

        # Local Path Header
        local_header = QHBoxLayout()
        local_lbl = QLabel("💻 Local:")
        local_lbl.setStyleSheet("font-weight: bold; color: #569cd6;")
        self.txt_local_path = QLineEdit(str(Path.home()))
        self.btn_local_up = QPushButton("▲ ..")
        self.btn_local_up.setFixedWidth(40)
        self.btn_local_go = QPushButton("Go")
        self.btn_local_go.setFixedWidth(40)
        local_header.addWidget(local_lbl)
        local_header.addWidget(self.txt_local_path)
        local_header.addWidget(self.btn_local_up)
        local_header.addWidget(self.btn_local_go)
        left_layout.addLayout(local_header)

        # Local Table
        self.table_local = QTableWidget(0, 4)
        self.table_local.setHorizontalHeaderLabels(["Name", "Size", "Permissions", "Modified"])
        self.table_local.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table_local.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_local.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_local.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        left_layout.addWidget(self.table_local)

        self.h_splitter.addWidget(left_widget)

        # === Right Panel: Remote Filesystem ===
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(2)

        # Remote Path Header
        remote_header = QHBoxLayout()
        remote_lbl = QLabel("🌐 Remote:")
        remote_lbl.setStyleSheet("font-weight: bold; color: #4ec9b0;")
        self.txt_remote_path = QLineEdit("/")
        self.btn_remote_up = QPushButton("▲ ..")
        self.btn_remote_up.setFixedWidth(40)
        self.btn_remote_go = QPushButton("Go")
        self.btn_remote_go.setFixedWidth(40)
        remote_header.addWidget(remote_lbl)
        remote_header.addWidget(self.txt_remote_path)
        remote_header.addWidget(self.btn_remote_up)
        remote_header.addWidget(self.btn_remote_go)
        right_layout.addLayout(remote_header)

        # Remote Table
        self.table_remote = QTableWidget(0, 4)
        self.table_remote.setHorizontalHeaderLabels(["Name", "Size", "Permissions", "Modified"])
        self.table_remote.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table_remote.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_remote.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_remote.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        right_layout.addWidget(self.table_remote)

        self.h_splitter.addWidget(right_widget)
        self.h_splitter.setStretchFactor(0, 1)
        self.h_splitter.setStretchFactor(1, 1)

        self.v_splitter.addWidget(pane_container)

        # ----------------- Embedded SSH Terminal Panel -----------------
        self.terminal_widget = TerminalWidget(self.ssh_shell, self)
        self.terminal_widget.close_requested.connect(self._toggle_terminal)
        self.v_splitter.addWidget(self.terminal_widget)

        # Initially terminal is hidden or collapsed
        self.terminal_widget.hide()
        self.v_splitter.setStretchFactor(0, 3)
        self.v_splitter.setStretchFactor(1, 1)

        # Navigation & Event Connections
        self.btn_local_go.clicked.connect(self._on_local_go)
        self.txt_local_path.returnPressed.connect(self._on_local_go)
        self.btn_local_up.clicked.connect(self._on_local_up)
        self.table_local.cellDoubleClicked.connect(self._on_local_double_clicked)
        self.table_local.customContextMenuRequested.connect(self._show_local_context_menu)

        self.btn_remote_go.clicked.connect(self._on_remote_go)
        self.txt_remote_path.returnPressed.connect(self._on_remote_go)
        self.btn_remote_up.clicked.connect(self._on_remote_up)
        self.table_remote.cellDoubleClicked.connect(self._on_remote_double_clicked)
        self.table_remote.customContextMenuRequested.connect(self._show_remote_context_menu)

    def _setup_menus(self):
        menubar = self.menuBar()

        # Session Menu
        m_session = menubar.addMenu("&Session")
        act_login = m_session.addAction("New Session...")
        act_login.setShortcut("Ctrl+N")
        act_login.triggered.connect(self.open_session_dialog)

        act_disconnect = m_session.addAction("Disconnect")
        act_disconnect.setShortcut("Ctrl+D")
        act_disconnect.triggered.connect(self.disconnect_session)

        m_session.addSeparator()
        act_export = m_session.addAction("Export / Cloud Backup...")
        act_export.triggered.connect(self.open_export_dialog)

        m_session.addSeparator()
        act_quit = m_session.addAction("Exit")
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)

        # Commands Menu
        m_cmd = menubar.addMenu("&Commands")
        act_upload = m_cmd.addAction("Upload (F5)")
        act_upload.setShortcut("F5")
        act_upload.triggered.connect(self.upload_selected)

        act_download = m_cmd.addAction("Download (F5)")
        act_download.triggered.connect(self.download_selected)

        act_edit = m_cmd.addAction("Edit Remote File (F4)")
        act_edit.setShortcut("F4")
        act_edit.triggered.connect(self.edit_selected_remote_file)

        act_del = m_cmd.addAction("Delete (F8)")
        act_del.setShortcut("F8")
        act_del.triggered.connect(self.delete_selected)

        act_mkdir = m_cmd.addAction("New Directory (F7)")
        act_mkdir.setShortcut("F7")
        act_mkdir.triggered.connect(self.create_directory)

        act_chmod = m_cmd.addAction("Properties / Chmod (F9)")
        act_chmod.setShortcut("F9")
        act_chmod.triggered.connect(self.chmod_selected)

        m_cmd.addSeparator()
        act_term = m_cmd.addAction("Open Terminal (Ctrl+T)")
        act_term.setShortcut("Ctrl+T")
        act_term.triggered.connect(self.open_terminal)

        # View Menu
        m_view = menubar.addMenu("&View")
        act_refresh = m_view.addAction("Refresh (Ctrl+R)")
        act_refresh.setShortcut("Ctrl+R")
        act_refresh.triggered.connect(self.refresh_both)

        self.act_toggle_term = m_view.addAction("Toggle Terminal")
        self.act_toggle_term.triggered.connect(self._toggle_terminal)

        # Help Menu
        m_help = menubar.addMenu("&Help")
        act_about = m_help.addAction("About WinSCP-Linux")
        act_about.triggered.connect(self._show_about)

    def _setup_toolbars(self):
        toolbar = QToolBar("Main Operations")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.tb_connect = toolbar.addAction("🔌 Connect")
        self.tb_connect.triggered.connect(self.open_session_dialog)

        self.tb_disconnect = toolbar.addAction("⏹ Disconnect")
        self.tb_disconnect.setEnabled(False)
        self.tb_disconnect.triggered.connect(self.disconnect_session)

        toolbar.addSeparator()

        self.tb_refresh = toolbar.addAction("🔄 Refresh")
        self.tb_refresh.triggered.connect(self.refresh_both)

        self.tb_upload = toolbar.addAction("⬆ Upload")
        self.tb_upload.triggered.connect(self.upload_selected)

        self.tb_download = toolbar.addAction("⬇ Download")
        self.tb_download.triggered.connect(self.download_selected)

        self.tb_edit = toolbar.addAction("✏ Edit")
        self.tb_edit.triggered.connect(self.edit_selected_remote_file)

        self.tb_chmod = toolbar.addAction("🔒 Chmod")
        self.tb_chmod.triggered.connect(self.chmod_selected)

        self.tb_delete = toolbar.addAction("🗑 Delete")
        self.tb_delete.triggered.connect(self.delete_selected)

        self.tb_mkdir = toolbar.addAction("📁 New Folder")
        self.tb_mkdir.triggered.connect(self.create_directory)

        toolbar.addSeparator()

        self.tb_term = toolbar.addAction("💻 Open Terminal")
        self.tb_term.triggered.connect(self.open_terminal)

        self.tb_cloud = toolbar.addAction("☁ Cloud Backup")
        self.tb_cloud.triggered.connect(self.open_export_dialog)

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

    def _wire_sync_engine(self):
        self.sync_engine.sync_started.connect(self._on_sync_started)
        self.sync_engine.sync_succeeded.connect(self._on_sync_succeeded)
        self.sync_engine.sync_failed.connect(self._on_sync_failed)

    # =========================================================================
    # Session Management
    # =========================================================================

    def open_session_dialog(self):
        dlg = SessionDialog(self.session_store, self)
        dlg.login_requested.connect(self.connect_to_session)
        dlg.export_requested.connect(self.open_export_dialog)
        dlg.exec()

    def open_export_dialog(self):
        dlg = ExportDialog(self.session_store, self)
        dlg.sessions_updated.connect(lambda: self.status_bar.showMessage("Sessions synchronized.", 3000))
        dlg.exec()

    def connect_to_session(self, session: SessionConfig):
        """Connects to the given session via background QThread worker."""
        self.current_session = session
        self.session_id = f"{session.username}_{session.host}_{session.port}".replace("/", "_")

        self.lbl_status.setText(f"Connecting to {session.username}@{session.host}:{session.port}...")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()

        worker = SFTPConnectWorker(
            sftp_client=self.sftp_client,
            host=session.host,
            port=session.port,
            username=session.username,
            password=session.password,
            private_key=session.private_key,
            initial_remote_dir=session.remote_dir or "/",
        )
        self._active_worker = worker

        def _on_connected(remote_home: str):
            self.progress_bar.hide()
            self.tb_disconnect.setEnabled(True)
            self.lbl_session.setText(f"🟢 {session.username}@{session.host}:{session.port}")
            self.lbl_session.setStyleSheet("padding: 2px 8px; font-weight: bold; color: #4ec9b0;")
            self.lbl_status.setText(f"Connected to {session.host}")

            # Refresh remote file list
            self.txt_remote_path.setText(remote_home)
            self._load_remote_directory(remote_home)

            # Auto-open or initialize terminal if requested
            self._init_terminal_channel()

        def _on_failed(err: str):
            self.progress_bar.hide()
            self.lbl_status.setText("Connection failed")
            QMessageBox.critical(self, "Connection Error", f"Could not connect to {session.host}:\n{err}")

        worker.connected.connect(_on_connected)
        worker.failed.connect(_on_failed)
        worker.start()

    def disconnect_session(self):
        """Disconnects SFTP and SSH sessions and cleans up cache."""
        self.sftp_client.disconnect()
        self.ssh_shell.close()
        self.sync_engine.cleanup_session(self.session_id)

        self.table_remote.setRowCount(0)
        self.tb_disconnect.setEnabled(False)
        self.terminal_widget.hide()
        self.lbl_session.setText("🔴 Not Connected")
        self.lbl_session.setStyleSheet("padding: 2px 8px; font-weight: bold; color: #888888;")
        self.lbl_status.setText("Disconnected.")

    # =========================================================================
    # Terminal Integration
    # =========================================================================

    def _init_terminal_channel(self):
        if not self.sftp_client.is_connected or not self.sftp_client.ssh_client:
            return

        cols = self.terminal_widget.canvas.cols
        rows = self.terminal_widget.canvas.rows
        try:
            self.ssh_shell.open_shell(
                ssh_client=self.sftp_client.ssh_client,
                cols=cols,
                rows=rows,
            )
            if self.current_session:
                self.terminal_widget.set_session_info(
                    self.current_session.username, self.current_session.host
                )
        except Exception as exc:
            self.status_bar.showMessage(f"Terminal error: {exc}", 5000)

    def open_terminal(self):
        if not self.sftp_client.is_connected:
            QMessageBox.information(self, "No Active Session", "Please connect to a remote session first.")
            return

        if not self.ssh_shell.is_active:
            self._init_terminal_channel()

        self.terminal_widget.show()
        self.terminal_widget.canvas.setFocus()
        self.status_bar.showMessage("SSH Terminal active.", 3000)

    def _toggle_terminal(self):
        if self.terminal_widget.isVisible():
            self.terminal_widget.hide()
        else:
            self.open_terminal()

    # =========================================================================
    # Local Filesystem Pane
    # =========================================================================

    def _load_local_directory(self, dir_path: str):
        target = Path(dir_path).expanduser().resolve()
        if not target.is_dir():
            QMessageBox.warning(self, "Invalid Path", f"Local path does not exist: {dir_path}")
            return

        self.txt_local_path.setText(str(target))
        self.table_local.setRowCount(0)

        # Parent directory ..
        if target != target.parent:
            row = self.table_local.rowCount()
            self.table_local.insertRow(row)
            self._set_table_item(self.table_local, row, 0, "..", is_dir=True, path=str(target.parent))
            self._set_table_item(self.table_local, row, 1, "")
            self._set_table_item(self.table_local, row, 2, "drwxr-xr-x")
            self._set_table_item(self.table_local, row, 3, "")

        try:
            entries = list(target.iterdir())
        except Exception as e:
            QMessageBox.warning(self, "Permission Denied", f"Cannot read directory: {e}")
            return

        # Sort: directories first, then alphabetical
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
            # If double click file in local pane, offer to upload
            if self.sftp_client.is_connected:
                self.upload_selected()

    # =========================================================================
    # Remote Filesystem Pane
    # =========================================================================

    def _load_remote_directory(self, dir_path: str):
        if not self.sftp_client.is_connected:
            return

        self.lbl_status.setText(f"Loading {dir_path}...")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()

        worker = SFTPListDirWorker(self.sftp_client, dir_path)
        self._active_worker = worker

        def _on_success(items: List[RemoteFileInfo], current_dir: str):
            self.progress_bar.hide()
            self.txt_remote_path.setText(current_dir)
            self.table_remote.setRowCount(0)

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

            self.lbl_status.setText(f"Loaded {len(items)} items in {current_dir}")

        def _on_failed(err: str):
            self.progress_bar.hide()
            self.lbl_status.setText("Failed to list remote directory")
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
            # Double click file -> Direct Edit & Auto Sync
            self.edit_selected_remote_file()

    # =========================================================================
    # Remote Direct-Edit and Auto-Sync
    # =========================================================================

    def edit_selected_remote_file(self):
        """
        Triggers Remote Direct-Edit:
        1. Downloads file into /tmp/winscp-linux-cache/<session_id>/<remote_path>
        2. Launches default Linux editor (xdg-open or $EDITOR)
        3. Attaches inotify watcher via watchdog
        4. Auto-uploads on save
        """
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
        if not data or data.get("is_dir"):
            return

        remote_path = data["path"]
        self.lbl_status.setText(f"Opening remote file for editing: {remote_path}...")

        try:
            cache_path = self.sync_engine.start_edit_session(
                sftp_client=self.sftp_client,
                session_id=self.session_id,
                remote_path=remote_path,
            )
            self.status_bar.showMessage(f"Opened {remote_path} in local editor. Auto-sync active.", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "Edit Failed", f"Could not open file for editing: {exc}")

    def _on_sync_started(self, remote_path: str, local_path: str):
        self.lbl_status.setText(f"Auto-uploading saved changes to {remote_path}...")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()

    def _on_sync_succeeded(self, remote_path: str, local_path: str):
        self.progress_bar.hide()
        self.status_bar.showMessage(f"✔ File uploaded successfully: {remote_path}", 4000)
        # Refresh remote table if file is in current directory
        if str(Path(remote_path).parent) == self.txt_remote_path.text().rstrip("/"):
            self._load_remote_directory(self.txt_remote_path.text())

    def _on_sync_failed(self, remote_path: str, error_msg: str):
        self.progress_bar.hide()
        self.status_bar.showMessage(f"❌ Auto-sync failed for {remote_path}: {error_msg}", 8000)

    # =========================================================================
    # Transfers: Upload & Download
    # =========================================================================

    def upload_selected(self):
        """Uploads selected local file(s) to current remote directory."""
        if not self.sftp_client.is_connected:
            QMessageBox.warning(self, "Not Connected", "Connect to a remote server before uploading.")
            return

        selected = self.table_local.selectedItems()
        if not selected:
            QMessageBox.information(self, "Select File", "Please select a local file or folder to upload.")
            return

        row = selected[0].row()
        data = self.table_local.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not data:
            return

        local_path = data["path"]
        local_name = Path(local_path).name
        remote_dest = f"{self.txt_remote_path.text().rstrip('/')}/{local_name}"

        reply = QMessageBox.question(
            self,
            "Confirm Upload",
            f"Upload '{local_name}' to:\n{remote_dest}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.lbl_status.setText(f"Uploading {local_name}...")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.show()

        worker = SFTPUploadWorker(self.sftp_client, local_path, remote_dest)
        self._active_worker = worker

        def _on_prog(transferred: int, total: int):
            if total > 0:
                self.progress_bar.setValue(int((transferred / total) * 100))

        def _on_finish(lp: str, rp: str):
            self.progress_bar.hide()
            self.status_bar.showMessage(f"Uploaded {local_name} successfully.", 4000)
            self._load_remote_directory(self.txt_remote_path.text())

        def _on_fail(err: str):
            self.progress_bar.hide()
            QMessageBox.critical(self, "Upload Error", f"Upload failed:\n{err}")

        worker.progress.connect(_on_prog)
        worker.finished.connect(_on_finish)
        worker.failed.connect(_on_fail)
        worker.start()

    def download_selected(self):
        """Downloads selected remote file(s) to current local directory."""
        if not self.sftp_client.is_connected:
            return

        selected = self.table_remote.selectedItems()
        if not selected:
            QMessageBox.information(self, "Select File", "Please select a remote file to download.")
            return

        row = selected[0].row()
        data = self.table_remote.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not data or data.get("is_dir"):
            return

        remote_path = data["path"]
        remote_name = Path(remote_path).name
        local_dest = str(Path(self.txt_local_path.text()) / remote_name)

        reply = QMessageBox.question(
            self,
            "Confirm Download",
            f"Download '{remote_name}' to:\n{local_dest}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.lbl_status.setText(f"Downloading {remote_name}...")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.show()

        worker = SFTPDownloadWorker(self.sftp_client, remote_path, local_dest)
        self._active_worker = worker

        def _on_prog(transferred: int, total: int):
            if total > 0:
                self.progress_bar.setValue(int((transferred / total) * 100))

        def _on_finish(rp: str, lp: str):
            self.progress_bar.hide()
            self.status_bar.showMessage(f"Downloaded {remote_name} successfully.", 4000)
            self._load_local_directory(self.txt_local_path.text())

        def _on_fail(err: str):
            self.progress_bar.hide()
            QMessageBox.critical(self, "Download Error", f"Download failed:\n{err}")

        worker.progress.connect(_on_prog)
        worker.finished.connect(_on_finish)
        worker.failed.connect(_on_fail)
        worker.start()

    # =========================================================================
    # File Operations: Chmod, Rename, Delete, Mkdir
    # =========================================================================

    def chmod_selected(self):
        """Opens permission dialog for remote or local file."""
        if self.table_remote.hasFocus() or (self.sftp_client.is_connected and self.table_remote.selectedItems()):
            selected = self.table_remote.selectedItems()
            if not selected:
                return
            row = selected[0].row()
            data = self.table_remote.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if not data or data.get("name") == "..":
                return

            info: Optional[RemoteFileInfo] = data.get("info")
            current_oct = info.permissions_octal if info else "755"

            dlg = ChmodDialog(data["name"], current_oct, self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                new_mode = dlg.get_mode_int()
                worker = SFTPActionWorker(
                    action_fn=lambda: self.sftp_client.chmod(data["path"], new_mode),
                    success_msg=f"Permissions updated to {oct(new_mode)[2:]} for {data['name']}",
                )
                self._active_worker = worker
                worker.finished.connect(lambda msg: (self.status_bar.showMessage(msg, 3000), self._load_remote_directory(self.txt_remote_path.text())))
                worker.failed.connect(lambda err: QMessageBox.critical(self, "Chmod Error", err))
                worker.start()

    def delete_selected(self):
        """Deletes selected item in focused pane."""
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
            worker.finished.connect(lambda msg: (self.status_bar.showMessage(msg, 3000), self._load_remote_directory(self.txt_remote_path.text())))
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

    def create_directory(self):
        """Creates a new directory in active pane."""
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
            worker.finished.connect(lambda msg: (self.status_bar.showMessage(msg, 3000), self._load_remote_directory(self.txt_remote_path.text())))
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

    # =========================================================================
    # Helpers & Context Menus
    # =========================================================================

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
        menu = QMenu(self)
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
        menu = QMenu(self)
        act_edit = menu.addAction("✏ Edit & Auto-Sync (F4)")
        act_edit.triggered.connect(self.edit_selected_remote_file)

        act_down = menu.addAction("⬇ Download to Local (F5)")
        act_down.triggered.connect(self.download_selected)

        menu.addSeparator()
        act_chmod = menu.addAction("🔒 Properties / Chmod (F9)")
        act_chmod.triggered.connect(self.chmod_selected)

        act_del = menu.addAction("🗑 Delete (F8)")
        act_del.triggered.connect(self.delete_selected)

        act_mkdir = menu.addAction("📁 New Directory (F7)")
        act_mkdir.triggered.connect(self.create_directory)

        menu.addSeparator()
        act_ref = menu.addAction("🔄 Refresh")
        act_ref.triggered.connect(lambda: self._load_remote_directory(self.txt_remote_path.text()))

        menu.exec(self.table_remote.mapToGlobal(pos))

    def _show_about(self):
        QMessageBox.about(
            self,
            "About WinSCP-Linux",
            "<h3>WinSCP-Linux</h3>"
            "<p>A native Linux alternative to WinSCP featuring:</p>"
            "<ul>"
            "<li>Dual-Pane Commander UI (Local & Remote SFTP)</li>"
            "<li>Live Remote Direct-Edit with Watchdog (inotify) Auto-Sync</li>"
            "<li>Embedded Interactive SSH Shell Terminal</li>"
            "<li>WinSCP-Compatible .ini Export & Import</li>"
            "<li>AES-256-GCM + PBKDF2HMAC (600,000 rounds) Encrypted Cloud Sharing</li>"
            "</ul>"
            "<p>Built with Python 3, PyQt6, Paramiko, and Cryptography.</p>",
        )

    def closeEvent(self, event):
        """Clean up on exit."""
        self.sftp_client.disconnect()
        self.ssh_shell.close()
        self.sync_engine.cleanup_all()
        super().closeEvent(event)
