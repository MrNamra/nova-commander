"""
Custom Commands Dialog and Execution Runner for WinSCP-Linux.
Matches WinSCP Custom Commands (CustomCommand.dfm / Ctrl+Shift+C).
Allows executing remote shell commands with parameter placeholders:
- !  = First selected file
- !& = All selected files
- !/ = Current remote directory
Includes presets for tar compression, extraction, disk usage, search, and processes.
"""

from __future__ import annotations

import shlex
from typing import List, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.sftp_client import SFTPClient


class CustomCommandWorker(QThread):
    """Executes a remote shell command asynchronously."""

    output_received = pyqtSignal(int, str, str)  # exit_status, stdout, stderr
    failed = pyqtSignal(str)

    def __init__(self, sftp_client: SFTPClient, command: str):
        super().__init__()
        self.sftp_client = sftp_client
        self.command = command

    def run(self):
        try:
            status, stdout, stderr = self.sftp_client.exec_command(self.command, timeout=60)
            self.output_received.emit(status, stdout, stderr)
        except Exception as e:
            self.failed.emit(str(e))


class CustomCommandOutputDialog(QDialog):
    """Terminal output window displaying execution results of a remote command."""

    def __init__(self, command: str, status: int, stdout: str, stderr: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Command Output")
        self.resize(720, 480)

        layout = QVBoxLayout(self)

        lbl_cmd = QLabel(f"<b>Command:</b> <code>{command}</code>")
        status_color = "#238636" if status == 0 else "#da3633"
        lbl_status = QLabel(f"<b>Exit Status:</b> <span style='color:{status_color};'>{status}</span>")
        layout.addWidget(lbl_cmd)
        layout.addWidget(lbl_status)

        self.txt_output = QTextEdit()
        self.txt_output.setReadOnly(True)
        self.txt_output.setFont(QFont("Monospace", 10))
        self.txt_output.setStyleSheet(
            "background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #3c3c3c; border-radius: 4px;"
        )

        full_text = ""
        if stdout:
            full_text += stdout
        if stderr:
            if full_text:
                full_text += "\n--- STDERR ---\n"
            full_text += stderr
        if not full_text.strip():
            full_text = "(Command completed with no output)"

        self.txt_output.setPlainText(full_text)
        layout.addWidget(self.txt_output)

        btn_layout = QHBoxLayout()
        btn_copy = QPushButton("📋 Copy Output")
        btn_copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(full_text))
        btn_layout.addWidget(btn_copy)
        btn_layout.addStretch()

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)


class CustomCommandsDialog(QDialog):
    """
    WinSCP-style Custom Commands Dialog.
    Allows executing preconfigured and custom shell commands on the remote server.
    """

    PRESETS = [
        ("📦 Tar & Gzip selected files", "tar -czvf archive.tar.gz !&"),
        ("📂 Extract archive (.tar.gz, .tgz)", "tar -xzvf !"),
        ("💾 Disk Free Space (df -h)", "df -h !/"),
        ("📊 Directory / File Size (du -sh)", "du -sh !&"),
        ("🔍 Grep inside directory", "grep -rn 'pattern' !/"),
        ("⚙ Process List (ps aux)", "ps aux | head -n 30"),
        ("ℹ System Info & Uptime", "uname -a && uptime"),
    ]

    def __init__(
        self,
        sftp_client: SFTPClient,
        current_remote_dir: str,
        selected_files: List[str],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.sftp_client = sftp_client
        self.current_remote_dir = current_remote_dir
        self.selected_files = selected_files

        self.setWindowTitle("Custom Commands (Ctrl+Shift+C)")
        self.resize(600, 320)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        form_group = QGroupBox("Select or Enter Remote Command")
        form = QFormLayout(form_group)

        self.cmb_preset = QComboBox()
        self.cmb_preset.addItem("-- Select Preset --", "")
        for title, cmd in self.PRESETS:
            self.cmb_preset.addItem(title, cmd)
        self.cmb_preset.currentIndexChanged.connect(self._on_preset_selected)
        form.addRow("Presets:", self.cmb_preset)

        self.txt_cmd = QLineEdit()
        self.txt_cmd.setPlaceholderText("e.g. tar -czvf backup.tar.gz !&")
        form.addRow("Command:", self.txt_cmd)

        layout.addWidget(form_group)

        # Placeholders Info
        lbl_info = QLabel(
            "<b>Placeholders supported:</b><br>"
            "<code>!</code>  = First selected file<br>"
            "<code>!&</code> = All selected files (space separated)<br>"
            "<code>!/</code> = Current remote directory path"
        )
        lbl_info.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(lbl_info)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        # Buttons
        btn_layout = QHBoxLayout()
        self.btn_run = QPushButton("🚀 Execute Command")
        self.btn_run.setDefault(True)
        self.btn_cancel = QPushButton("Cancel")
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_run)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

        self.btn_run.clicked.connect(self._on_execute)
        self.btn_cancel.clicked.connect(self.reject)

    def _on_preset_selected(self, index: int):
        cmd = self.cmb_preset.currentData()
        if cmd:
            self.txt_cmd.setText(cmd)

    def _build_final_command(self) -> str:
        raw_cmd = self.txt_cmd.text().strip()
        if not raw_cmd:
            return ""

        first_file = shlex.quote(self.selected_files[0]) if self.selected_files else ""
        all_files = " ".join(shlex.quote(f) for f in self.selected_files) if self.selected_files else ""
        curr_dir = shlex.quote(self.current_remote_dir)

        final_cmd = raw_cmd.replace("!&", all_files).replace("!/", curr_dir).replace("!", first_file)
        return final_cmd

    def _on_execute(self):
        if not self.sftp_client.is_connected:
            QMessageBox.warning(self, "Not Connected", "Must be connected to a remote server to execute commands.")
            return

        cmd = self._build_final_command()
        if not cmd:
            QMessageBox.information(self, "No Command", "Please enter a command to execute.")
            return

        self.btn_run.setEnabled(False)
        self.progress_bar.show()

        self.worker = CustomCommandWorker(self.sftp_client, cmd)
        self.worker.output_received.connect(lambda status, out, err: self._on_output(cmd, status, out, err))
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_output(self, command: str, status: int, stdout: str, stderr: str):
        self.progress_bar.hide()
        self.btn_run.setEnabled(True)
        self.accept()
        dlg = CustomCommandOutputDialog(command, status, stdout, stderr, self.parentWidget())
        dlg.exec()

    def _on_failed(self, error: str):
        self.progress_bar.hide()
        self.btn_run.setEnabled(True)
        QMessageBox.critical(self, "Command Failed", f"Could not execute remote command: {error}")
