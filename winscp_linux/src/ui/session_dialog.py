"""
WinSCP-style Session Login Dialog and Saved Session Manager for WinSCP-Linux.
Features dual-pane session tree, credential inputs, SSH key manager, and cloud backup actions.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
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
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.session_store import SessionConfig, SessionStore


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
        self.setWindowTitle("WinSCP-Linux - Login & Saved Sessions")
        self.resize(760, 520)

        self._active_config: Optional[SessionConfig] = None
        self._setup_ui()
        self.refresh_session_tree()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)

        # Splitter: Left (Sessions Tree), Right (Credentials Editor)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ----------------- Left Panel -----------------
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        lbl_sites = QLabel("Saved Sites:")
        lbl_sites.setStyleSheet("font-weight: bold;")
        left_layout.addWidget(lbl_sites)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        left_layout.addWidget(self.tree)

        # Left buttons: New Site, New Folder, Delete
        left_btn_layout = QHBoxLayout()
        self.btn_new_site = QPushButton("+ Site")
        self.btn_new_folder = QPushButton("+ Folder")
        self.btn_del_site = QPushButton("Delete")
        left_btn_layout.addWidget(self.btn_new_site)
        left_btn_layout.addWidget(self.btn_new_folder)
        left_btn_layout.addWidget(self.btn_del_site)
        left_layout.addLayout(left_btn_layout)

        splitter.addWidget(left_widget)

        # ----------------- Right Panel -----------------
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(10, 0, 0, 0)

        # Session properties group
        group_box = QGroupBox("Session Parameters")
        form = QFormLayout(group_box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.txt_session_name = QLineEdit("New Session")
        form.addRow("Session Name:", self.txt_session_name)

        # Protocol & Port
        proto_layout = QHBoxLayout()
        self.cmb_protocol = QComboBox()
        self.cmb_protocol.addItems(["SFTP", "SCP"])
        self.spin_port = QSpinBox()
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(22)
        proto_layout.addWidget(self.cmb_protocol, 2)
        proto_layout.addWidget(QLabel("Port:"), 0)
        proto_layout.addWidget(self.spin_port, 1)
        form.addRow("File Protocol:", proto_layout)

        self.txt_host = QLineEdit()
        self.txt_host.setPlaceholderText("example.com or 192.168.1.100")
        form.addRow("Host Name:", self.txt_host)

        self.txt_user = QLineEdit()
        self.txt_user.setPlaceholderText("e.g. root, ubuntu, admin")
        form.addRow("User Name:", self.txt_user)

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
        form.addRow("Private Key:", key_layout)

        # Directory settings
        self.txt_remote_dir = QLineEdit("/")
        form.addRow("Remote Dir:", self.txt_remote_dir)

        self.txt_local_dir = QLineEdit(str(Path.home()))
        form.addRow("Local Dir:", self.txt_local_dir)

        self.txt_comment = QLineEdit()
        form.addRow("Comment:", self.txt_comment)

        right_layout.addWidget(group_box)
        right_layout.addStretch()

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        main_layout.addWidget(splitter)

        # ----------------- Bottom Action Bar -----------------
        bottom_layout = QHBoxLayout()

        self.btn_cloud = QPushButton("☁ Export / Cloud Backup")
        self.btn_manage_keys = QPushButton("🔑 Manage Keys")
        self.btn_save = QPushButton("💾 Save Session")
        self.btn_login = QPushButton("🚀 Login")
        self.btn_login.setDefault(True)
        self.btn_login.setStyleSheet("font-weight: bold; padding: 6px 16px; background-color: #238636; color: white;")
        self.btn_cancel = QPushButton("Close")

        bottom_layout.addWidget(self.btn_cloud)
        bottom_layout.addWidget(self.btn_manage_keys)
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.btn_save)
        bottom_layout.addWidget(self.btn_login)
        bottom_layout.addWidget(self.btn_cancel)

        main_layout.addLayout(bottom_layout)

        # Connections
        self.btn_new_site.clicked.connect(self._create_new_site)
        self.btn_new_folder.clicked.connect(self._create_new_folder)
        self.btn_del_site.clicked.connect(self._delete_selected)
        self.btn_save.clicked.connect(self._save_current_session)
        self.btn_login.clicked.connect(self._on_login)
        self.btn_cloud.clicked.connect(self.export_requested.emit)
        self.btn_manage_keys.clicked.connect(self._open_key_manager)
        self.btn_cancel.clicked.connect(self.reject)

    def refresh_session_tree(self, select_session_name: Optional[str] = None):
        """Populates the tree widget with folders and sessions."""
        self.tree.clear()
        sessions = self.session_store.list_sessions()

        folder_nodes: dict[str, QTreeWidgetItem] = {}

        # Root folder item
        root_node = QTreeWidgetItem(self.tree, ["📁 Saved Sessions"])
        root_node.setExpanded(True)

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
                self.tree.setCurrentItem(s_item)

    def _on_tree_selection_changed(self):
        items = self.tree.selectedItems()
        if not items:
            return

        data = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        if data.get("type") == "session":
            s: SessionConfig = data["config"]
            self._load_session_into_fields(s)

    def _load_session_into_fields(self, s: SessionConfig):
        self._active_config = s
        self.txt_session_name.setText(s.name)
        self.txt_host.setText(s.host)
        self.spin_port.setValue(s.port)
        self.txt_user.setText(s.username)
        self.txt_password.setText(s.password)
        self.txt_key_path.setText(s.private_key)
        self.cmb_protocol.setCurrentText(s.protocol)
        self.txt_remote_dir.setText(s.remote_dir or "/")
        self.txt_local_dir.setText(s.local_dir or str(Path.home()))
        self.txt_comment.setText(s.comment or "")

    def _create_new_site(self):
        self.txt_session_name.setText("New Session")
        self.txt_host.clear()
        self.txt_user.clear()
        self.txt_password.clear()
        self.txt_key_path.clear()
        self.spin_port.setValue(22)
        self.cmb_protocol.setCurrentText("SFTP")
        self.txt_remote_dir.setText("/")
        self.txt_local_dir.setText(str(Path.home()))
        self.txt_comment.clear()
        self.txt_host.setFocus()
        self._active_config = None

    def _create_new_folder(self):
        name, ok = QInputDialog.getText(self, "New Folder", "Folder Name:")
        if ok and name.strip():
            # Add dummy session to establish folder
            dummy = SessionConfig(name="Example", host="127.0.0.1", folder=name.strip())
            self.session_store.save_session(dummy)
            self.refresh_session_tree()

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

    def _toggle_password_visibility(self, checked: bool):
        if checked:
            self.txt_password.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_toggle_pwd.setText("🔒")
        else:
            self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_toggle_pwd.setText("👁")

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
        folder = self._active_config.folder if self._active_config else ""
        return SessionConfig(
            name=name,
            host=self.txt_host.text().strip(),
            port=self.spin_port.value(),
            username=self.txt_user.text().strip(),
            password=self.txt_password.text(),
            private_key=self.txt_key_path.text().strip(),
            protocol=self.cmb_protocol.currentText(),
            remote_dir=self.txt_remote_dir.text().strip() or "/",
            local_dir=self.txt_local_dir.text().strip() or str(Path.home()),
            folder=folder,
            comment=self.txt_comment.text().strip(),
        )

    def _save_current_session(self):
        cfg = self._get_current_fields_config()
        if not cfg.host:
            QMessageBox.warning(self, "Missing Host", "Please specify a Host Name.")
            return

        self.session_store.save_session(cfg)
        self.refresh_session_tree(select_session_name=cfg.full_path)
        QMessageBox.information(self, "Saved", f"Session '{cfg.full_path}' saved successfully.")

    def _on_login(self):
        cfg = self._get_current_fields_config()
        if not cfg.host:
            QMessageBox.warning(self, "Missing Host", "Please specify a Host Name.")
            return

        self.login_requested.emit(cfg)
        self.accept()
