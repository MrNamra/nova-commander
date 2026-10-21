"""
Generate Session URL / Code Dialog for WinSCP-Linux.
Matches WinSCP Generate URL (GenerateUrl.dfm / Ctrl+Alt+U).
Generates:
- Standard SFTP / SSH URL
- SSH Command
- SFTP CLI Command
- WinSCP Script command
- Python (Paramiko) connection code snippet
"""

from __future__ import annotations

import urllib.parse
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.session_store import SessionConfig


class GenerateUrlDialog(QDialog):
    """
    WinSCP-style Generate Session URL / Code Dialog.
    Produces copyable URLs and connection command lines.
    """

    def __init__(self, config: SessionConfig, current_path: str = "/", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config = config
        self.current_path = current_path or "/"

        self.setWindowTitle("Generate Session URL / Code")
        self.resize(650, 420)

        self._setup_ui()
        self._update_output()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        group = QGroupBox("Format Selection")
        form = QFormLayout(group)

        self.cmb_format = QComboBox()
        self.cmb_format.addItems([
            "SFTP URL (sftp://...)",
            "SSH Terminal Command (ssh -p ...)",
            "SFTP CLI Command (sftp -P ...)",
            "WinSCP Script Command (open sftp://...)",
            "Python (Paramiko) Script",
        ])
        self.cmb_format.currentIndexChanged.connect(self._update_output)
        form.addRow("Output Format:", self.cmb_format)

        self.chk_include_password = QCheckBox("Include password in URL / commands")
        self.chk_include_password.setChecked(False)
        self.chk_include_password.toggled.connect(self._update_output)
        form.addRow("", self.chk_include_password)

        layout.addWidget(group)

        # Output text
        self.txt_output = QTextEdit()
        self.txt_output.setFont(QFont("Monospace", 10))
        self.txt_output.setStyleSheet(
            "background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #3c3c3c; border-radius: 4px;"
        )
        layout.addWidget(self.txt_output)

        # Buttons
        btn_layout = QHBoxLayout()
        self.btn_copy = QPushButton("📋 Copy to Clipboard")
        self.btn_copy.setDefault(True)
        self.btn_copy.clicked.connect(self._copy_to_clipboard)
        btn_layout.addWidget(self.btn_copy)
        btn_layout.addStretch()

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

    def _update_output(self):
        fmt_idx = self.cmb_format.currentIndex()
        include_pwd = self.chk_include_password.isChecked()

        user = self.config.username
        host = self.config.host
        port = self.config.port
        pwd = self.config.password if include_pwd else ""
        path = self.current_path.lstrip("/")

        if fmt_idx == 0:
            # SFTP URL
            auth = f"{user}:{pwd}@" if (user and pwd) else (f"{user}@" if user else "")
            port_str = f":{port}" if port != 22 else ""
            url = f"sftp://{auth}{host}{port_str}/{path}"
            self.txt_output.setPlainText(url)

        elif fmt_idx == 1:
            # SSH Command
            user_host = f"{user}@{host}" if user else host
            port_flag = f"-p {port} " if port != 22 else ""
            key_flag = f"-i {self.config.private_key} " if self.config.private_key else ""
            cmd = f"ssh {port_flag}{key_flag}{user_host}"
            self.txt_output.setPlainText(cmd)

        elif fmt_idx == 2:
            # SFTP CLI
            user_host = f"{user}@{host}" if user else host
            port_flag = f"-P {port} " if port != 22 else ""
            key_flag = f"-i {self.config.private_key} " if self.config.private_key else ""
            cmd = f"sftp {port_flag}{key_flag}{user_host}"
            self.txt_output.setPlainText(cmd)

        elif fmt_idx == 3:
            # WinSCP Script
            auth = f"{user}:{pwd}@" if (user and pwd) else (f"{user}@" if user else "")
            port_flag = f" -rawsettings PortNumber={port}" if port != 22 else ""
            key_flag = f' -privatekey="{self.config.private_key}"' if self.config.private_key else ""
            cmd = f'open sftp://{auth}{host}/{key_flag}{port_flag}'
            self.txt_output.setPlainText(cmd)

        elif fmt_idx == 4:
            # Python Paramiko
            pwd_repr = repr(pwd) if pwd else "None"
            key_repr = repr(self.config.private_key) if self.config.private_key else "None"
            py_code = f"""import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(
    hostname={repr(host)},
    port={port},
    username={repr(user)},
    password={pwd_repr},
    key_filename={key_repr},
)
sftp = ssh.open_sftp()
print("SFTP Connected successfully!")
"""
            self.txt_output.setPlainText(py_code)

    def _copy_to_clipboard(self):
        text = self.txt_output.toPlainText()
        if text:
            QGuiApplication.clipboard().setText(text)
            QMessageBox.information(self, "Copied", "Copied to clipboard!")
