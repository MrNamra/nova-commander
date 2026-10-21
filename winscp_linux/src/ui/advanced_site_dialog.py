"""
Advanced Site Settings Dialog for WinSCP-Linux.
Matches the official WinSCP Advanced Site Settings modal (SiteAdvanced.dfm) with
categorized tabs for Environment, Directories, SSH / Security, and Proxy / Jump Host.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.session_store import SessionConfig


class AdvancedSiteSettingsDialog(QDialog):
    """
    WinSCP-style Advanced Site Settings Dialog.
    Allows configuring SFTP server command, UTF-8 filename encoding, initial directories,
    SSH keepalive intervals, compression, and proxy / jump host parameters.
    """

    def __init__(self, session_config: SessionConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.session_config = session_config
        self.setWindowTitle(f"Advanced Site Settings - {session_config.name}")
        self.resize(650, 420)

        self._setup_ui()
        self._load_from_config()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)

        content_layout = QHBoxLayout()

        # Left Category Navigation List
        self.category_list = QListWidget()
        self.category_list.setFixedWidth(160)
        categories = [
            ("📁 Environment", 0),
            ("📂 Directories", 1),
            ("🔒 SSH / Security", 2),
            ("🌐 Proxy / Jump Host", 3),
        ]
        for title, idx in categories:
            item = QListWidgetItem(title)
            self.category_list.addItem(item)
        self.category_list.setCurrentRow(0)
        self.category_list.currentRowChanged.connect(self._on_category_changed)
        content_layout.addWidget(self.category_list)

        # Right Stacked Pages
        self.stack = QStackedWidget()

        # ---------------- Page 0: Environment ----------------
        page_env = QWidget()
        layout_env = QVBoxLayout(page_env)
        group_sftp = QGroupBox("SFTP / SCP Server Options")
        form_sftp = QFormLayout(group_sftp)

        self.txt_sftp_server = QLineEdit()
        self.txt_sftp_server.setPlaceholderText("default (or e.g. sudo su -c /usr/lib/openssh/sftp-server)")
        form_sftp.addRow("SFTP Server Command:", self.txt_sftp_server)

        self.cmb_utf8 = QComboBox()
        self.cmb_utf8.addItems(["Auto", "On", "Off"])
        form_sftp.addRow("UTF-8 Filenames:", self.cmb_utf8)

        layout_env.addWidget(group_sftp)
        layout_env.addStretch()
        self.stack.addWidget(page_env)

        # ---------------- Page 1: Directories ----------------
        page_dirs = QWidget()
        layout_dirs = QVBoxLayout(page_dirs)
        group_dirs = QGroupBox("Initial Directories")
        form_dirs = QFormLayout(group_dirs)

        self.txt_remote_dir = QLineEdit()
        self.txt_remote_dir.setPlaceholderText("/home/username or /var/www")
        form_dirs.addRow("Remote Directory:", self.txt_remote_dir)

        self.txt_local_dir = QLineEdit()
        form_dirs.addRow("Local Directory:", self.txt_local_dir)

        self.chk_remember_last = QCheckBox("Remember last used directories across sessions")
        self.chk_remember_last.setChecked(True)
        form_dirs.addRow("", self.chk_remember_last)

        layout_dirs.addWidget(group_dirs)
        layout_dirs.addStretch()
        self.stack.addWidget(page_dirs)

        # ---------------- Page 2: SSH / Security ----------------
        page_ssh = QWidget()
        layout_ssh = QVBoxLayout(page_ssh)
        group_ssh = QGroupBox("SSH Transport & Keep-Alive")
        form_ssh = QFormLayout(group_ssh)

        self.spin_ping = QSpinBox()
        self.spin_ping.setRange(0, 3600)
        self.spin_ping.setValue(30)
        self.spin_ping.setSuffix(" seconds (0 = disabled)")
        form_ssh.addRow("Keepalive Interval:", self.spin_ping)

        self.chk_compression = QCheckBox("Enable SSH compression (zlib)")
        form_ssh.addRow("", self.chk_compression)

        layout_ssh.addWidget(group_ssh)
        layout_ssh.addStretch()
        self.stack.addWidget(page_ssh)

        # ---------------- Page 3: Proxy / Jump Host ----------------
        page_proxy = QWidget()
        layout_proxy = QVBoxLayout(page_proxy)
        group_proxy = QGroupBox("Connection Proxy / Bastion Host")
        form_proxy = QFormLayout(group_proxy)

        self.cmb_proxy_type = QComboBox()
        self.cmb_proxy_type.addItems(["None", "SOCKS5", "HTTP", "SSH_JUMP"])
        form_proxy.addRow("Proxy Type:", self.cmb_proxy_type)

        self.txt_proxy_host = QLineEdit()
        self.txt_proxy_host.setPlaceholderText("bastion.example.com")
        form_proxy.addRow("Proxy Host:", self.txt_proxy_host)

        self.spin_proxy_port = QSpinBox()
        self.spin_proxy_port.setRange(1, 65535)
        self.spin_proxy_port.setValue(1080)
        form_proxy.addRow("Proxy Port:", self.spin_proxy_port)

        self.txt_proxy_user = QLineEdit()
        form_proxy.addRow("Proxy User:", self.txt_proxy_user)

        self.txt_proxy_pwd = QLineEdit()
        self.txt_proxy_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        form_proxy.addRow("Proxy Password:", self.txt_proxy_pwd)

        layout_proxy.addWidget(group_proxy)
        layout_proxy.addStretch()
        self.stack.addWidget(page_proxy)

        content_layout.addWidget(self.stack, 1)
        main_layout.addLayout(content_layout)

        # Dialog Buttons
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self._save_and_accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

    def _on_category_changed(self, index: int):
        self.stack.setCurrentIndex(index)

    def _load_from_config(self):
        cfg = self.session_config
        self.txt_sftp_server.setText(cfg.sftp_server_cmd)
        idx_utf8 = self.cmb_utf8.findText(cfg.utf8_encoding)
        if idx_utf8 >= 0:
            self.cmb_utf8.setCurrentIndex(idx_utf8)

        self.txt_remote_dir.setText(cfg.remote_dir)
        self.txt_local_dir.setText(cfg.local_dir)

        self.spin_ping.setValue(cfg.ping_interval)
        self.chk_compression.setChecked(cfg.compression)

        idx_proxy = self.cmb_proxy_type.findText(cfg.proxy_type)
        if idx_proxy >= 0:
            self.cmb_proxy_type.setCurrentIndex(idx_proxy)
        self.txt_proxy_host.setText(cfg.proxy_host)
        self.spin_proxy_port.setValue(cfg.proxy_port)
        self.txt_proxy_user.setText(cfg.proxy_user)
        self.txt_proxy_pwd.setText(cfg.proxy_password)

    def _save_and_accept(self):
        cfg = self.session_config
        cfg.sftp_server_cmd = self.txt_sftp_server.text().strip()
        cfg.utf8_encoding = self.cmb_utf8.currentText()
        cfg.remote_dir = self.txt_remote_dir.text().strip() or "/"
        cfg.local_dir = self.txt_local_dir.text().strip()
        cfg.ping_interval = self.spin_ping.value()
        cfg.compression = self.chk_compression.isChecked()
        cfg.proxy_type = self.cmb_proxy_type.currentText()
        cfg.proxy_host = self.txt_proxy_host.text().strip()
        cfg.proxy_port = self.spin_proxy_port.value()
        cfg.proxy_user = self.txt_proxy_user.text().strip()
        cfg.proxy_password = self.txt_proxy_pwd.text().strip()
        self.accept()
