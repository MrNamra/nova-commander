"""
WinSCP-style Session Login Dialog and Saved Session Manager for WinSCP-Linux.
Features dual-pane session tree, credential inputs, SSH key manager, and cloud backup actions.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.session_store import SessionConfig, SessionStore
from .advanced_site_dialog import AdvancedSiteSettingsDialog
from .generate_url_dialog import GenerateUrlDialog


class KeyManagerDialog(QDialog):
    """Dialog to inspect and select existing SSH keys from ~/.ssh/ directory."""

    key_selected = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Manage SSH Keys")
        self.resize(550, 320)

        layout = QVBoxLayout(self)

        info = QLabel("Available SSH Keys detected in ~/.ssh/:")
        layout.addWidget(info)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Key Name", "File Path", "Size (bytes)"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.btn_select = QPushButton("Use Selected Key")
        self.btn_browse = QPushButton("Browse Another Key...")
        self.btn_close = QPushButton("Close")

        btn_layout.addWidget(self.btn_browse)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_select)
        btn_layout.addWidget(self.btn_close)
        layout.addLayout(btn_layout)

        self.btn_select.clicked.connect(self._on_select)
        self.btn_browse.clicked.connect(self._on_browse)
        self.btn_close.clicked.connect(self.reject)

        self._populate_keys()

    def _populate_keys(self):
        ssh_dir = Path.home() / ".ssh"
        if not ssh_dir.exists():
            return

        for p in ssh_dir.glob("*"):
            if p.is_file() and not p.name.endswith(".pub") and p.name not in ("known_hosts", "config", "authorized_keys"):
                row = self.table.rowCount()
                self.table.insertRow(row)
                self.table.setItem(row, 0, QTableWidgetItem(p.name))
                self.table.setItem(row, 1, QTableWidgetItem(str(p)))
                self.table.setItem(row, 2, QTableWidgetItem(str(p.stat().st_size)))

    def _on_select(self):
        selected = self.table.selectedItems()
        if selected:
            path = self.table.item(selected[0].row(), 1).text()
            self.key_selected.emit(path)
            self.accept()
        else:
            QMessageBox.information(self, "No Key Selected", "Please select an SSH key from the list.")

    def _on_browse(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select SSH Private Key",
            str(Path.home() / ".ssh"),
            "Private Keys (*.pem *.key id_* id_rsa id_ed25519);;All Files (*)",
        )
        if file_path:
            self.key_selected.emit(file_path)
            self.accept()


class SessionTreeWidget(QTreeWidget):
    """
    Drag-and-drop enabled TreeWidget for Saved Sites and Folders.
    Allows moving sessions between folders by dragging, right-click actions,
    and inline rename (F2).
    """

    session_moved = pyqtSignal(str, str)  # (source_session_full_path, target_folder)
    rename_requested = pyqtSignal()
    delete_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F2:
            self.rename_requested.emit()
            return
        elif event.key() == Qt.Key.Key_Delete:
            self.delete_requested.emit()
            return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event):
        if event.source() == self:
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.source() == self:
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.source() != self:
            super().dropEvent(event)
            return

        dragged_items = self.selectedItems()
        if not dragged_items:
            event.ignore()
            return

        dragged_item = dragged_items[0]
        dragged_data = dragged_item.data(0, Qt.ItemDataRole.UserRole)
        if not dragged_data:
            event.ignore()
            return

        # Target item where user dropped
        target_item = self.itemAt(event.position().toPoint())
        target_folder = ""

        if target_item:
            target_data = target_item.data(0, Qt.ItemDataRole.UserRole)
            if target_data:
                target_type = target_data.get("type")
                if target_type == "folder":
                    target_folder = target_data.get("name", "")
                elif target_type == "session":
                    target_cfg = target_data.get("config")
                    target_folder = target_cfg.folder if target_cfg else ""
                elif target_type == "root":
                    target_folder = ""

        item_type = dragged_data.get("type")
        from PyQt6.QtCore import QTimer
        if item_type == "session":
            cfg: SessionConfig = dragged_data.get("config")
            if not cfg:
                event.ignore()
                return
            if cfg.folder == target_folder:
                event.ignore()
                return

            event.acceptProposedAction()
            src_full_path = cfg.full_path
            dest_folder = target_folder
            QTimer.singleShot(0, lambda: self.session_moved.emit(src_full_path, dest_folder))
            return

        elif item_type == "folder":
            folder_name = dragged_data.get("name", "")
            if not folder_name or target_folder == folder_name or target_folder.startswith(folder_name + "/"):
                event.ignore()
                return
            new_f_path = f"{target_folder}/{folder_name}" if target_folder else folder_name
            if new_f_path != folder_name:
                event.acceptProposedAction()
                f_name = folder_name
                dest_f = target_folder
                QTimer.singleShot(0, lambda: self.session_moved.emit(f"__FOLDER__:{f_name}", dest_f))
                return

        event.ignore()


class SessionDialog(QDialog):
    """
    WinSCP-Style Session Login Dialog.
    Left: Tree of saved sessions grouped by folder.
    Right: Connection credentials and options.
    """

    login_requested = pyqtSignal(SessionConfig)
    export_requested = pyqtSignal()
    import_requested = pyqtSignal()

    def __init__(self, session_store: SessionStore, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.session_store = session_store
        self.setWindowTitle("Nova Commander - Login")
        self.resize(780, 520)

        self._active_config: Optional[SessionConfig] = None
        self._setup_ui()
        self.refresh_session_tree()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)

        # Splitter: Left (Sessions Tree), Right (Credentials Editor)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ----------------- Left Panel (Sites) -----------------
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        lbl_sites = QLabel("Sites:")
        lbl_sites.setStyleSheet("font-weight: bold;")
        left_layout.addWidget(lbl_sites)

        # WinSCP Incremental Search
        self.txt_filter = QLineEdit()
        self.txt_filter.setPlaceholderText("🔍 Filter sites...")
        self.txt_filter.setClearButtonEnabled(True)
        self.txt_filter.textChanged.connect(self._filter_tree)
        left_layout.addWidget(self.txt_filter)

        self.tree = SessionTreeWidget(self)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self.tree.session_moved.connect(self._on_tree_session_moved)
        self.tree.rename_requested.connect(self._rename_selected)
        self.tree.delete_requested.connect(self._delete_selected)
        self.tree.customContextMenuRequested.connect(self._show_tree_context_menu)
        self.tree.itemDoubleClicked.connect(self._on_tree_double_clicked)
        left_layout.addWidget(self.tree)

        # WinSCP Left Action Buttons: Tools & Manage menus + direct buttons
        left_btn_layout = QHBoxLayout()
        left_btn_layout.setSpacing(4)

        self.btn_tools = QPushButton("Tools ▾")
        tools_menu = QMenu(self)
        tools_menu.addAction("📁 Export / Import Sessions...").triggered.connect(self.export_requested.emit)
        tools_menu.addAction("🔗 Generate Session URL / Code...").triggered.connect(self._open_generate_url)
        tools_menu.addAction("🔑 Manage SSH Keys...").triggered.connect(self._open_key_manager)
        tools_menu.addAction("⚙ Advanced Site Settings...").triggered.connect(self._open_advanced_settings)
        self.btn_tools.setMenu(tools_menu)
        left_btn_layout.addWidget(self.btn_tools)

        self.btn_manage = QPushButton("Manage ▾")
        manage_menu = QMenu(self)
        manage_menu.addAction("➕ New Site").triggered.connect(self._create_new_site)
        manage_menu.addAction("📁 New Folder").triggered.connect(self._create_new_folder)
        manage_menu.addAction("✏ Rename (F2)").triggered.connect(self._rename_selected)
        manage_menu.addAction("📋 Clone / Duplicate Site").triggered.connect(self._clone_selected)
        manage_menu.addAction("🗑 Delete (Del)").triggered.connect(self._delete_selected)
        self.btn_manage.setMenu(manage_menu)
        left_btn_layout.addWidget(self.btn_manage)

        # Direct quick buttons
        self.btn_new_site = QPushButton("+ Site")
        self.btn_new_folder = QPushButton("+ Folder")
        self.btn_move = QPushButton("Move...")
        self.btn_move.setToolTip("Move selected session into a folder")
        self.btn_del_site = QPushButton("Delete")
        left_btn_layout.addWidget(self.btn_new_site)
        left_btn_layout.addWidget(self.btn_new_folder)
        left_btn_layout.addWidget(self.btn_move)
        left_btn_layout.addWidget(self.btn_del_site)
        left_layout.addLayout(left_btn_layout)

        splitter.addWidget(left_widget)

        # ----------------- Right Panel (Session Parameters) -----------------
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(10, 0, 0, 0)

        # Session properties group matching WinSCP "Session" group
        group_box = QGroupBox("Session")
        form = QFormLayout(group_box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        # Protocol & Port
        proto_layout = QHBoxLayout()
        self.cmb_protocol = QComboBox()
        self.cmb_protocol.addItems(["SFTP", "SCP", "FTP", "FTPS", "WebDAV", "Amazon S3"])
        self.cmb_protocol.currentIndexChanged.connect(self._on_protocol_changed)
        self.spin_port = QSpinBox()
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(22)
        proto_layout.addWidget(self.cmb_protocol, 2)
        proto_layout.addWidget(QLabel("Port number:"), 0)
        proto_layout.addWidget(self.spin_port, 1)
        form.addRow("File protocol:", proto_layout)

        self.txt_host = QLineEdit()
        self.txt_host.setPlaceholderText("example.com or 192.168.1.100")
        form.addRow("Host name:", self.txt_host)

        user_pwd_layout = QHBoxLayout()
        self.txt_user = QLineEdit()
        self.txt_user.setPlaceholderText("e.g. root, ubuntu, deploy")
        user_pwd_layout.addWidget(self.txt_user, 1)
        form.addRow("User name:", user_pwd_layout)

        # Password with show/hide toggle
        pwd_layout = QHBoxLayout()
        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.btn_toggle_pwd = QPushButton("👁")
        self.btn_toggle_pwd.setFixedWidth(32)
        self.btn_toggle_pwd.setCheckable(True)
        self.btn_toggle_pwd.clicked.connect(self._toggle_password_visibility)
        pwd_layout.addWidget(self.txt_password)
        pwd_layout.addWidget(self.btn_toggle_pwd)
        form.addRow("Password:", pwd_layout)

        # Private Key
        key_layout = QHBoxLayout()
        self.txt_key_path = QLineEdit()
        self.txt_key_path.setPlaceholderText("Path to .pem, .key, or OpenSSH key")
        self.btn_browse_key = QPushButton("...")
        self.btn_browse_key.setFixedWidth(32)
        self.btn_browse_key.clicked.connect(self._browse_key_file)
        key_layout.addWidget(self.txt_key_path)
        key_layout.addWidget(self.btn_browse_key)
        form.addRow("Private key file:", key_layout)

        # Key Passphrase
        passphrase_layout = QHBoxLayout()
        self.txt_passphrase = QLineEdit()
        self.txt_passphrase.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_passphrase.setPlaceholderText("Passphrase (if key is encrypted)")
        self.btn_toggle_passphrase = QPushButton("👁")
        self.btn_toggle_passphrase.setFixedWidth(32)
        self.btn_toggle_passphrase.setCheckable(True)
        self.btn_toggle_passphrase.clicked.connect(self._toggle_passphrase_visibility)
        passphrase_layout.addWidget(self.txt_passphrase)
        passphrase_layout.addWidget(self.btn_toggle_passphrase)
        form.addRow("Key passphrase:", passphrase_layout)

        self.txt_session_name = QLineEdit("New Site")
        form.addRow("Site Name:", self.txt_session_name)

        self.cmb_folder = QComboBox()
        self.cmb_folder.setEditable(True)
        form.addRow("Folder:", self.cmb_folder)

        # Initial Directories
        self.txt_remote_dir = QLineEdit("/")
        form.addRow("Remote directory:", self.txt_remote_dir)

        self.txt_local_dir = QLineEdit(str(Path.home()))
        form.addRow("Local directory:", self.txt_local_dir)

        self.txt_comment = QLineEdit()
        form.addRow("Comment:", self.txt_comment)

        # Session actions inside group box matching WinSCP
        session_btn_layout = QHBoxLayout()
        self.btn_advanced = QPushButton("⚙ Advanced...")
        self.btn_advanced.setToolTip("Advanced site settings (SSH, keepalive, compression, proxy)")
        self.btn_save = QPushButton("💾 Save...")
        self.btn_save.setToolTip("Save session parameters")
        session_btn_layout.addStretch()
        session_btn_layout.addWidget(self.btn_advanced)
        session_btn_layout.addWidget(self.btn_save)
        form.addRow("", session_btn_layout)

        right_layout.addWidget(group_box)

        # WinSCP NoteGroup
        note_group = QGroupBox("Note")
        note_layout = QVBoxLayout(note_group)
        note_layout.setContentsMargins(6, 6, 6, 6)
        self.txt_note = QTextEdit()
        self.txt_note.setPlaceholderText("Notes or comments for this session...")
        self.txt_note.setMaximumHeight(70)
        note_layout.addWidget(self.txt_note)
        right_layout.addWidget(note_group)
        right_layout.addStretch()

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        main_layout.addWidget(splitter)

        # WinSCP Show Again Checkbox
        self.chk_show_on_startup = QCheckBox("&Show Login dialog on startup and when the last session is closed")
        self.chk_show_on_startup.setChecked(True)
        main_layout.addWidget(self.chk_show_on_startup)

        # ----------------- Bottom Action Bar -----------------
        bottom_layout = QHBoxLayout()

        self.btn_cloud = QPushButton("📁 Export / Import")
        self.btn_manage_keys = QPushButton("🔑 Manage Keys")
        self.btn_gen_url = QPushButton("🔗 URL")
        self.btn_gen_url.setToolTip("Generate Session URL / Code")

        self.btn_login = QPushButton("🚀 Login")
        self.btn_login.setDefault(True)
        self.btn_login.setStyleSheet("font-weight: bold; padding: 6px 18px; background-color: #238636; color: white;")
        self.btn_cancel = QPushButton("Close")

        bottom_layout.addWidget(self.btn_cloud)
        bottom_layout.addWidget(self.btn_manage_keys)
        bottom_layout.addWidget(self.btn_gen_url)
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.btn_login)
        bottom_layout.addWidget(self.btn_cancel)

        main_layout.addLayout(bottom_layout)

        # Connections
        self.btn_new_site.clicked.connect(self._create_new_site)
        self.btn_new_folder.clicked.connect(self._create_new_folder)
        self.btn_move.clicked.connect(self._move_selected)
        self.btn_del_site.clicked.connect(self._delete_selected)
        self.btn_save.clicked.connect(self._save_current_session)
        self.btn_login.clicked.connect(self._on_login)
        self.btn_cloud.clicked.connect(self.export_requested.emit)
        self.btn_manage_keys.clicked.connect(self._open_key_manager)
        self.btn_advanced.clicked.connect(self._open_advanced_settings)
        self.btn_gen_url.clicked.connect(self._open_generate_url)
        self.btn_cancel.clicked.connect(self.reject)

    def _on_protocol_changed(self, index: int):
        proto = self.cmb_protocol.currentText().upper()
        if proto in ("FTP", "FTPS"):
            if self.spin_port.value() in (22, 0):
                self.spin_port.setValue(21)
            self.txt_key_path.setEnabled(False)
            self.btn_browse_key.setEnabled(False)
            self.txt_passphrase.setEnabled(False)
        elif proto in ("SFTP", "SCP"):
            if self.spin_port.value() in (21, 0):
                self.spin_port.setValue(22)
            self.txt_key_path.setEnabled(True)
            self.btn_browse_key.setEnabled(True)
            self.txt_passphrase.setEnabled(True)

    def _filter_tree(self, text: str):
        query = text.strip().lower()

        def _filter_item(item: QTreeWidgetItem) -> bool:
            match = False
            item_text = item.text(0).lower()
            if query in item_text:
                match = True
            child_matches = False
            for i in range(item.childCount()):
                child = item.child(i)
                if _filter_item(child):
                    child_matches = True
            is_visible = match or child_matches
            item.setHidden(not is_visible)
            if child_matches:
                item.setExpanded(True)
            return is_visible

        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            _filter_item(root.child(i))

    def _clone_selected(self):
        cfg = self._get_current_fields_config()
        if not cfg.host:
            QMessageBox.information(self, "No Site Selected", "Please select or configure a site to clone.")
            return
        new_name = f"{cfg.name} (Copy)"
        clone_cfg = SessionConfig.from_dict(cfg.to_dict())
        clone_cfg.name = new_name
        self.session_store.save_session(clone_cfg)
        self.refresh_session_tree(select_session_name=new_name)

    def _refresh_folder_combo(self, selected_folder: str = ""):
        """Populates the Folder dropdown with all existing folders and selects the current one."""
        if not hasattr(self, "cmb_folder"):
            return
        self.cmb_folder.blockSignals(True)
        self.cmb_folder.clear()
        self.cmb_folder.addItem("(Root / None)", "")
        for f in self.session_store.get_folders():
            self.cmb_folder.addItem(f, f)

        if selected_folder:
            idx = self.cmb_folder.findData(selected_folder)
            if idx >= 0:
                self.cmb_folder.setCurrentIndex(idx)
            else:
                self.cmb_folder.setEditText(selected_folder)
        else:
            self.cmb_folder.setCurrentIndex(0)
        self.cmb_folder.blockSignals(False)

    def refresh_session_tree(self, select_session_name: Optional[str] = None):
        """Populates the tree widget with folders and sessions."""
        self.tree.clear()
        sessions = self.session_store.list_sessions()
        folders = self.session_store.get_folders()

        folder_nodes: dict[str, QTreeWidgetItem] = {}

        # Root folder item
        root_node = QTreeWidgetItem(self.tree, ["📁 Saved Sessions"])
        root_node.setData(0, Qt.ItemDataRole.UserRole, {"type": "root", "name": ""})
        root_node.setExpanded(True)

        # "➕ New Site" item matching WinSCP Login.dfm
        new_site_node = QTreeWidgetItem(root_node, ["➕ New Site"])
        new_site_node.setData(0, Qt.ItemDataRole.UserRole, {"type": "new_site", "name": "New Site"})
        ns_font = new_site_node.font(0)
        ns_font.setBold(True)
        new_site_node.setFont(0, ns_font)

        # 1. Register all folder items (even empty ones)
        for folder_name in folders:
            clean_f = folder_name.strip()
            if clean_f and clean_f not in folder_nodes:
                f_item = QTreeWidgetItem(root_node, [f"📁 {clean_f}"])
                f_item.setExpanded(True)
                f_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "folder", "name": clean_f})
                folder_nodes[clean_f] = f_item

        # 2. Add all sessions
        target_item_to_select = None
        for s in sessions:
            if s.folder.strip():
                folder_name = s.folder.strip()
                if folder_name not in folder_nodes:
                    f_item = QTreeWidgetItem(root_node, [f"📁 {folder_name}"])
                    f_item.setExpanded(True)
                    f_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "folder", "name": folder_name})
                    folder_nodes[folder_name] = f_item
                parent = folder_nodes[folder_name]
            else:
                parent = root_node

            s_item = QTreeWidgetItem(parent, [f"🖥 {s.name}"])
            s_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "session", "config": s})

            if select_session_name and (s.name == select_session_name or s.full_path == select_session_name):
                target_item_to_select = s_item

        self.tree.expandAll()
        if target_item_to_select:
            self.tree.setCurrentItem(target_item_to_select)
            self.tree.scrollToItem(target_item_to_select, QAbstractItemView.ScrollHint.EnsureVisible)

        self._refresh_folder_combo(self._active_config.folder if self._active_config else "")

    def _on_tree_selection_changed(self):
        items = self.tree.selectedItems()
        if not items:
            return

        data = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        if data.get("type") in ("root", "new_site"):
            self._create_new_site()
        elif data.get("type") == "session":
            s: SessionConfig = data["config"]
            self._load_session_into_fields(s)

    def _load_session_into_fields(self, s: SessionConfig):
        self._active_config = s
        self.txt_session_name.setText(s.name)
        self._refresh_folder_combo(s.folder)
        self.txt_host.setText(s.host)
        self.spin_port.setValue(s.port)
        self.txt_user.setText(s.username)
        self.txt_password.setText(s.password)
        self.txt_key_path.setText(s.private_key)
        self.txt_passphrase.setText(getattr(s, "passphrase", ""))
        self.cmb_protocol.setCurrentText(s.protocol)
        self._on_protocol_changed(0)
        self.txt_remote_dir.setText(s.remote_dir or "/")
        self.txt_local_dir.setText(s.local_dir or str(Path.home()))
        self.txt_comment.setText(s.comment or "")
        if hasattr(self, "txt_note"):
            self.txt_note.setPlainText(s.comment or "")

    def _create_new_site(self, prefill_folder: str = ""):
        self.txt_session_name.setText("New Session")
        self._refresh_folder_combo(prefill_folder)
        self.txt_host.clear()
        self.txt_user.clear()
        self.txt_password.clear()
        self.txt_key_path.clear()
        self.txt_passphrase.clear()
        self.spin_port.setValue(22)
        self.cmb_protocol.setCurrentText("SFTP")
        self._on_protocol_changed(0)
        self.txt_remote_dir.setText("/")
        self.txt_local_dir.setText(str(Path.home()))
        self.txt_comment.clear()
        if hasattr(self, "txt_note"):
            self.txt_note.clear()
        self.txt_host.setFocus()
        self._active_config = None

    def _create_new_folder(self):
        name, ok = QInputDialog.getText(self, "New Folder", "Folder Name:")
        if ok and name.strip():
            clean_name = name.strip().strip("/")
            self.session_store.add_folder(clean_name)
            self.refresh_session_tree()
            self._refresh_folder_combo(clean_name)

    # ----------------- Move & Reorganize -----------------
    def _on_tree_session_moved(self, source_path: str, target_folder: str):
        """Called by SessionTreeWidget when an item is dragged and dropped."""
        if source_path.startswith("__FOLDER__:"):
            folder_name = source_path[len("__FOLDER__:"): ]
            new_f_path = f"{target_folder}/{folder_name}" if target_folder else folder_name
            self.session_store.rename_folder(folder_name, new_f_path)
            self.refresh_session_tree()
            return

        moved = self.session_store.move_session(source_path, target_folder)
        if moved:
            self.refresh_session_tree(select_session_name=moved.full_path)
            self._load_session_into_fields(moved)

    def _move_selected(self):
        """Triggered by the 'Move...' button under the tree."""
        items = self.tree.selectedItems()
        if not items:
            QMessageBox.information(self, "No Selection", "Please select a session or folder to move.")
            return

        data = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        if data.get("type") == "session":
            s: SessionConfig = data["config"]
            self._prompt_move_session(s.full_path, s.folder)
        elif data.get("type") == "folder":
            folder_name = data.get("name", "")
            self._prompt_move_folder(folder_name)

    def _prompt_move_session(self, session_path: str, current_folder: str):
        """Displays a dialog allowing the user to select destination folder for a session."""
        existing_folders = self.session_store.get_folders()
        options = ["(Root / No Folder)"] + [f for f in existing_folders if f != current_folder] + ["+ New Folder..."]

        choice, ok = QInputDialog.getItem(
            self,
            "Move Session",
            f"Select destination folder for '{Path(session_path).name}':",
            options,
            0,
            False,
        )
        if not ok or not choice:
            return

        if choice == "+ New Folder...":
            new_name, ok_n = QInputDialog.getText(self, "New Folder", "Enter new folder name:")
            if ok_n and new_name.strip():
                dest_folder = new_name.strip().strip("/")
                self.session_store.add_folder(dest_folder)
            else:
                return
        elif choice == "(Root / No Folder)":
            dest_folder = ""
        else:
            dest_folder = choice

        moved = self.session_store.move_session(session_path, dest_folder)
        if moved:
            self.refresh_session_tree(select_session_name=moved.full_path)
            self._load_session_into_fields(moved)

    def _prompt_move_folder(self, folder_name: str):
        """Displays a dialog allowing the user to re-parent a folder."""
        existing_folders = [f for f in self.session_store.get_folders() if f != folder_name and not f.startswith(folder_name + "/")]
        options = ["(Root / Top Level)"] + existing_folders + ["+ New Parent Folder..."]

        choice, ok = QInputDialog.getItem(
            self,
            "Move Folder",
            f"Select parent destination for folder '{folder_name}':",
            options,
            0,
            False,
        )
        if not ok or not choice:
            return

        if choice == "+ New Parent Folder...":
            new_name, ok_n = QInputDialog.getText(self, "New Folder", "Enter new parent folder name:")
            if ok_n and new_name.strip():
                dest_parent = new_name.strip().strip("/")
                self.session_store.add_folder(dest_parent)
            else:
                return
        elif choice == "(Root / Top Level)":
            dest_parent = ""
        else:
            dest_parent = choice

        base_name = folder_name.split("/")[-1]
        new_folder_path = f"{dest_parent}/{base_name}" if dest_parent else base_name
        self.session_store.rename_folder(folder_name, new_folder_path)
        self.refresh_session_tree()

    def _show_tree_context_menu(self, pos: QPoint):
        """Right-click context menu for sessions and folders in the tree."""
        item = self.tree.itemAt(pos)
        if not item:
            return

        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        menu = QMenu(self)
        item_type = data.get("type")

        if item_type == "session":
            s: SessionConfig = data["config"]

            act_login = menu.addAction("🚀 Connect / Login")
            act_login.triggered.connect(self._on_login)
            menu.addSeparator()

            # Move submenu
            move_menu = menu.addMenu("📁 Move to Folder")
            if s.folder:
                act_root = move_menu.addAction("📂 (Root / No Folder)")
                act_root.triggered.connect(lambda: self._on_tree_session_moved(s.full_path, ""))

            for f in self.session_store.get_folders():
                if f != s.folder:
                    act_f = move_menu.addAction(f"📁 {f}")
                    act_f.triggered.connect(lambda checked=False, target_f=f: self._on_tree_session_moved(s.full_path, target_f))

            move_menu.addSeparator()
            act_new_f = move_menu.addAction("➕ Move to New Folder...")
            act_new_f.triggered.connect(lambda: self._prompt_move_session(s.full_path, s.folder))

            menu.addSeparator()
            act_rename = menu.addAction("✏ Rename (F2)")
            act_rename.triggered.connect(lambda: self._rename_session(s.full_path))

            act_dup = menu.addAction("📋 Duplicate Site")
            act_dup.triggered.connect(lambda: self._duplicate_session(s.full_path))

            menu.addSeparator()
            act_del = menu.addAction("🗑 Delete")
            act_del.triggered.connect(self._delete_selected)

        elif item_type == "folder":
            folder_name = data.get("name", "")

            act_new_site = menu.addAction(f"➕ New Site in '{folder_name}'...")
            act_new_site.triggered.connect(lambda: self._create_new_site(prefill_folder=folder_name))

            menu.addSeparator()
            act_rename_f = menu.addAction("✏ Rename Folder (F2)")
            act_rename_f.triggered.connect(lambda: self._rename_folder(folder_name))

            act_del_f = menu.addAction("🗑 Delete Folder")
            act_del_f.triggered.connect(lambda: self._delete_folder(folder_name))

        menu.exec(self.tree.mapToGlobal(pos))

    def _on_tree_double_clicked(self, item: QTreeWidgetItem, column: int):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        if data.get("type") == "session":
            self._on_login()
        elif data.get("type") in ("folder", "root"):
            item.setExpanded(not item.isExpanded())

    def _rename_selected(self):
        items = self.tree.selectedItems()
        if not items:
            return
        data = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        if data.get("type") == "session":
            self._rename_session(data["config"].full_path)
        elif data.get("type") == "folder":
            self._rename_folder(data.get("name", ""))

    def _rename_session(self, full_path: str):
        session = self.session_store.get_session(full_path)
        if not session:
            return
        new_name, ok = QInputDialog.getText(self, "Rename Session", "Session Name:", text=session.name)
        if ok and new_name.strip() and new_name.strip() != session.name:
            renamed = self.session_store.rename_session(full_path, new_name.strip())
            if renamed:
                self.refresh_session_tree(select_session_name=renamed.full_path)
                self._load_session_into_fields(renamed)

    def _rename_folder(self, folder_name: str):
        new_name, ok = QInputDialog.getText(self, "Rename Folder", "Folder Name:", text=folder_name)
        if ok and new_name.strip() and new_name.strip() != folder_name:
            self.session_store.rename_folder(folder_name, new_name.strip())
            self.refresh_session_tree()

    def _duplicate_session(self, full_path: str):
        copy_cfg = self.session_store.duplicate_session(full_path)
        if copy_cfg:
            self.refresh_session_tree(select_session_name=copy_cfg.full_path)
            self._load_session_into_fields(copy_cfg)

    def _delete_selected(self):
        items = self.tree.selectedItems()
        if not items:
            return

        data = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        if data.get("type") == "session":
            s: SessionConfig = data["config"]
            reply = QMessageBox.question(
                self,
                "Delete Session",
                f"Are you sure you want to delete session '{s.full_path}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.session_store.delete_session(s.full_path)
                self.refresh_session_tree()
                self._create_new_site()
        elif data.get("type") == "folder":
            self._delete_folder(data.get("name", ""))

    def _delete_folder(self, folder_name: str):
        reply = QMessageBox.question(
            self,
            "Delete Folder",
            f"Are you sure you want to delete folder '{folder_name}' and all its sessions?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.session_store.delete_folder(folder_name, delete_sessions=True)
            self.refresh_session_tree()
            self._create_new_site()

    def _toggle_password_visibility(self, checked: bool):
        if checked:
            self.txt_password.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_toggle_pwd.setText("🔒")
        else:
            self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_toggle_pwd.setText("👁")

    def _toggle_passphrase_visibility(self, checked: bool):
        if checked:
            self.txt_passphrase.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_toggle_passphrase.setText("🔒")
        else:
            self.txt_passphrase.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_toggle_passphrase.setText("👁")

    def _browse_key_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select SSH Private Key",
            str(Path.home() / ".ssh"),
            "Private Keys (*.pem *.key id_* id_rsa id_ed25519);;All Files (*)",
        )
        if file_path:
            self.txt_key_path.setText(file_path)

    def _open_key_manager(self):
        dlg = KeyManagerDialog(self)
        dlg.key_selected.connect(self.txt_key_path.setText)
        dlg.exec()

    def _get_current_fields_config(self) -> SessionConfig:
        name = self.txt_session_name.text().strip() or "Unnamed"
        folder_text = self.cmb_folder.currentText().strip()
        if folder_text in ("(Root / None)", "(Root)", "None", "/"):
            folder_text = ""
        folder = folder_text.strip("/")

        cfg = self._active_config
        ping_interval = cfg.ping_interval if cfg else 30
        compression = cfg.compression if cfg else False
        utf8_encoding = cfg.utf8_encoding if cfg else "Auto"
        sftp_server_cmd = cfg.sftp_server_cmd if cfg else ""
        proxy_type = cfg.proxy_type if cfg else "None"
        proxy_host = cfg.proxy_host if cfg else ""
        proxy_port = cfg.proxy_port if cfg else 1080
        proxy_user = cfg.proxy_user if cfg else ""
        proxy_password = cfg.proxy_password if cfg else ""

        return SessionConfig(
            name=name,
            host=self.txt_host.text().strip(),
            port=self.spin_port.value(),
            username=self.txt_user.text().strip(),
            password=self.txt_password.text(),
            private_key=self.txt_key_path.text().strip(),
            passphrase=self.txt_passphrase.text(),
            protocol=self.cmb_protocol.currentText(),
            remote_dir=self.txt_remote_dir.text().strip() or "/",
            local_dir=self.txt_local_dir.text().strip() or str(Path.home()),
            folder=folder,
            comment=self.txt_note.toPlainText().strip() if hasattr(self, "txt_note") and self.txt_note.toPlainText().strip() else self.txt_comment.text().strip(),
            ping_interval=ping_interval,
            compression=compression,
            utf8_encoding=utf8_encoding,
            sftp_server_cmd=sftp_server_cmd,
            proxy_type=proxy_type,
            proxy_host=proxy_host,
            proxy_port=proxy_port,
            proxy_user=proxy_user,
            proxy_password=proxy_password,
        )

    def _open_advanced_settings(self):
        cfg = self._get_current_fields_config()
        dlg = AdvancedSiteSettingsDialog(cfg, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._load_session_into_fields(cfg)

    def _open_generate_url(self):
        cfg = self._get_current_fields_config()
        dlg = GenerateUrlDialog(cfg, current_path=cfg.remote_dir, parent=self)
        dlg.exec()

    def _save_current_session(self):
        cfg = self._get_current_fields_config()
        if not cfg.host:
            QMessageBox.warning(self, "Missing Host", "Please specify a Host Name.")
            return

        # If existing session changed full_path (folder or name changed), remove old key
        if self._active_config and self._active_config.full_path != cfg.full_path:
            self.session_store.delete_session(self._active_config.full_path)

        self.session_store.save_session(cfg)
        self._active_config = cfg
        self.refresh_session_tree(select_session_name=cfg.full_path)
        QMessageBox.information(self, "Saved", f"Session '{cfg.full_path}' saved successfully.")

    def _on_login(self):
        cfg = self._get_current_fields_config()
        if not cfg.host:
            QMessageBox.warning(self, "Missing Host", "Please specify a Host Name.")
            return

        self.login_requested.emit(cfg)
        self.accept()
