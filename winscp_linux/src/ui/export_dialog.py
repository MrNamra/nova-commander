"""
Selective Session Export & Cloud Sharing Dialog for WinSCP-Linux.
Supports:
- Selective server checklist export.
- Standard WinSCP .ini generation.
- AES-256-GCM encryption with PBKDF2HMAC (600,000 iterations).
- Cloud upload with 7-day retention and Share ID generation.
- Cloud import and local file import with decryption and merging.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..cloud.cloud_client import CloudAPIError, CloudClient
from ..core.session_store import SessionConfig, SessionStore
from ..crypto.ini_cipher import DecryptionError, InvalidPayloadError, decrypt_from_base64, encrypt_to_base64


class ExportDialog(QDialog):
    """
    Selective export, cloud upload, and cloud import dialog for WinSCP-Linux configurations.
    """

    sessions_updated = pyqtSignal()

    def __init__(self, session_store: SessionStore, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.session_store = session_store
        self.cloud_client = CloudClient()

        self.setWindowTitle("Nova Commander - Session Export & Import")
        self.resize(650, 480)

        self._setup_ui()
        self._populate_sessions()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # Tab 1: Export Sessions (Local Encrypted / INI)
        self.tab_export = QWidget()
        self._setup_export_tab()
        self.tabs.addTab(self.tab_export, "📤 Export Sessions")

        # Tab 2: Import Sessions (Local File)
        self.tab_import = QWidget()
        self._setup_import_tab()
        self.tabs.addTab(self.tab_import, "📥 Import Sessions")

        # Bottom buttons
        bottom_bar = QHBoxLayout()
        bottom_bar.addStretch()
        self.btn_done = QPushButton("Close")
        self.btn_done.clicked.connect(self.accept)
        bottom_bar.addWidget(self.btn_done)
        main_layout.addLayout(bottom_bar)

    def _setup_export_tab(self):
        layout = QVBoxLayout(self.tab_export)

        # 1. Selection checklist
        lbl_select = QLabel("1. Select Sessions to Export:")
        lbl_select.setStyleSheet("font-weight: bold;")
        layout.addWidget(lbl_select)

        self.session_list = QListWidget()
        layout.addWidget(self.session_list)

        sel_btn_layout = QHBoxLayout()
        self.btn_select_all = QPushButton("Select All")
        self.btn_deselect_all = QPushButton("Deselect All")
        sel_btn_layout.addWidget(self.btn_select_all)
        sel_btn_layout.addWidget(self.btn_deselect_all)
        sel_btn_layout.addStretch()
        layout.addLayout(sel_btn_layout)

        self.btn_select_all.clicked.connect(self._select_all_sessions)
        self.btn_deselect_all.clicked.connect(self._deselect_all_sessions)

        # 2. Encryption password
        grp_pwd = QGroupBox("2. Encryption Security (AES-256-GCM)")
        pwd_form = QFormLayout(grp_pwd)

        self.txt_export_pwd = QLineEdit()
        self.txt_export_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_export_pwd.setPlaceholderText("Enter a strong Master Password (optional for plain .ini)")
        pwd_form.addRow("Master Password:", self.txt_export_pwd)

        self.txt_export_pwd_confirm = QLineEdit()
        self.txt_export_pwd_confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_export_pwd_confirm.setPlaceholderText("Confirm Master Password")
        pwd_form.addRow("Confirm Password:", self.txt_export_pwd_confirm)

        layout.addWidget(grp_pwd)

        # 3. Actions
        act_box = QGroupBox("3. Export Destination")
        act_layout = QHBoxLayout(act_box)

        # Cloud upload button hidden for now
        self.btn_upload_cloud = QPushButton("☁ Upload to Cloud (7-Day Retention)")
        self.btn_upload_cloud.setStyleSheet("font-weight: bold; background-color: #0366d6; color: white; padding: 6px 12px;")
        self.btn_upload_cloud.hide()

        self.btn_export_file = QPushButton("🔐 Save Encrypted .enc File...")
        self.btn_export_plain = QPushButton("📄 Save Plain .ini File...")

        act_layout.addWidget(self.btn_export_file)
        act_layout.addWidget(self.btn_export_plain)
        layout.addWidget(act_box)

        # Result display
        self.lbl_result = QLabel("")
        self.lbl_result.setStyleSheet("color: #238636; font-weight: bold;")
        self.lbl_result.setWordWrap(True)
        layout.addWidget(self.lbl_result)

        self.btn_upload_cloud.clicked.connect(self._on_upload_to_cloud)
        self.btn_export_file.clicked.connect(self._on_export_encrypted_file)
        self.btn_export_plain.clicked.connect(self._on_export_plain_ini)

    def _setup_import_tab(self):
        layout = QVBoxLayout(self.tab_import)

        # Group 1: Cloud Import (hidden for now)
        self.grp_cloud = QGroupBox("Import from Cloud")
        c_layout = QFormLayout(self.grp_cloud)

        self.txt_import_share_id = QLineEdit()
        self.txt_import_share_id.setPlaceholderText("Enter Share ID or Share URL (e.g. demo-a1b2c3d4)")
        c_layout.addRow("Share ID / URL:", self.txt_import_share_id)

        self.txt_import_cloud_pwd = QLineEdit()
        self.txt_import_cloud_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_import_cloud_pwd.setPlaceholderText("Master Password used during export")
        c_layout.addRow("Master Password:", self.txt_import_cloud_pwd)

        self.btn_import_cloud = QPushButton("⬇ Download and Decrypt from Cloud")
        self.btn_import_cloud.setStyleSheet("font-weight: bold; background-color: #238636; color: white; padding: 6px;")
        c_layout.addRow("", self.btn_import_cloud)

        self.grp_cloud.hide()

        # Group 2: File Import
        grp_file = QGroupBox("Import from Local File (.enc or .ini)")
        f_layout = QFormLayout(grp_file)

        self.txt_import_file_pwd = QLineEdit()
        self.txt_import_file_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_import_file_pwd.setPlaceholderText("Only required if file is encrypted")
        f_layout.addRow("Password (if encrypted):", self.txt_import_file_pwd)

        self.btn_import_file = QPushButton("📂 Browse and Import File (.enc / .ini)...")
        f_layout.addRow("", self.btn_import_file)

        layout.addWidget(grp_file)

        # Status / Feedback
        self.lbl_import_result = QLabel("")
        self.lbl_import_result.setStyleSheet("color: #238636; font-weight: bold;")
        self.lbl_import_result.setWordWrap(True)
        layout.addWidget(self.lbl_import_result)

        layout.addStretch()

        self.btn_import_cloud.clicked.connect(self._on_import_from_cloud)
        self.btn_import_file.clicked.connect(self._on_import_from_file)

    def _populate_sessions(self):
        self.session_list.clear()
        for s in self.session_store.list_sessions():
            item = QListWidgetItem(f"🖥 {s.full_path}  ({s.username}@{s.host}:{s.port})")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, s.full_path)
            self.session_list.addItem(item)

    def _select_all_sessions(self):
        for i in range(self.session_list.count()):
            self.session_list.item(i).setCheckState(Qt.CheckState.Checked)

    def _deselect_all_sessions(self):
        for i in range(self.session_list.count()):
            self.session_list.item(i).setCheckState(Qt.CheckState.Unchecked)

    def _get_selected_session_keys(self) -> List[str]:
        keys = []
        for i in range(self.session_list.count()):
            item = self.session_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                keys.append(item.data(Qt.ItemDataRole.UserRole))
        return keys

    def _validate_export_passwords(self) -> Optional[str]:
        p1 = self.txt_export_pwd.text()
        p2 = self.txt_export_pwd_confirm.text()
        if not p1:
            QMessageBox.warning(self, "Password Required", "Please enter a Master Password to encrypt the session data.")
            return None
        if p1 != p2:
            QMessageBox.warning(self, "Password Mismatch", "Passwords do not match. Please re-enter.")
            return None
        return p1

    def _on_upload_to_cloud(self):
        selected_keys = self._get_selected_session_keys()
        if not selected_keys:
            QMessageBox.warning(self, "No Sessions Selected", "Please select at least one session to export.")
            return

        password = self._validate_export_passwords()
        if not password:
            return

        ini_content = self.session_store.export_sessions_ini(selected_keys)
        try:
            # AES-256-GCM + PBKDF2HMAC (600,000 iterations)
            b64_payload = encrypt_to_base64(ini_content, password)
            result = self.cloud_client.upload_encrypted_config(b64_payload, retention_days=7)

            msg = (
                f"✅ Successfully uploaded {len(selected_keys)} session(s) to cloud!\n\n"
                f"• Access Code / Share ID: {result.share_id}\n"
                f"• Share URL: {result.share_url}\n"
                f"• Retention: 7 days\n\n"
                f"To import on another Linux machine, open WinSCP-Linux -> Import from Cloud, "
                f"enter this Share ID and your Master Password."
            )
            self.lbl_result.setText(f"Share ID: {result.share_id} (Copied to clipboard)")
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(result.share_id)

            QMessageBox.information(self, "Cloud Upload Succeeded", msg)
        except Exception as exc:
            QMessageBox.critical(self, "Upload Failed", f"Could not upload to cloud: {exc}")

    def _on_export_encrypted_file(self):
        selected_keys = self._get_selected_session_keys()
        if not selected_keys:
            QMessageBox.warning(self, "No Sessions Selected", "Please select at least one session to export.")
            return

        password = self._validate_export_passwords()
        if not password:
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Encrypted Configuration",
            str(Path.home() / "winscp-sessions.enc"),
            "Encrypted Sessions (*.enc *.bin);;All Files (*)",
        )
        if not file_path:
            return

        try:
            ini_content = self.session_store.export_sessions_ini(selected_keys)
            b64_payload = encrypt_to_base64(ini_content, password)
            Path(file_path).write_text(b64_payload, encoding="ascii")
            self.lbl_result.setText(f"Saved encrypted configuration to {file_path}")
            QMessageBox.information(self, "Saved", f"Encrypted file saved to:\n{file_path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", f"Could not write file: {exc}")

    def _on_export_plain_ini(self):
        selected_keys = self._get_selected_session_keys()
        if not selected_keys:
            QMessageBox.warning(self, "No Sessions Selected", "Please select at least one session to export.")
            return

        reply = QMessageBox.question(
            self,
            "Plaintext Warning",
            "Plaintext INI files contain credentials in plain text without AES encryption.\nProceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save WinSCP .ini",
            str(Path.home() / "winscp.ini"),
            "WinSCP INI files (*.ini);;All Files (*)",
        )
        if not file_path:
            return

        try:
            ini_content = self.session_store.export_sessions_ini(selected_keys)
            Path(file_path).write_text(ini_content, encoding="utf-8")
            self.lbl_result.setText(f"Saved plaintext INI to {file_path}")
            QMessageBox.information(self, "Saved", f"WinSCP .ini saved to:\n{file_path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", f"Could not write file: {exc}")

    def _on_import_from_cloud(self):
        share_input = self.txt_import_share_id.text().strip()
        password = self.txt_import_cloud_pwd.text()

        if not share_input:
            QMessageBox.warning(self, "Share ID Required", "Please enter the Share ID or Access Code.")
            return
        if not password:
            QMessageBox.warning(self, "Password Required", "Please enter the Master Password used when encrypting.")
            return

        # Handle full URL if pasted
        share_id = share_input.rstrip("/").split("/")[-1]

        try:
            b64_payload = self.cloud_client.download_encrypted_config(share_id)
            ini_content = decrypt_from_base64(b64_payload, password)
            count = self.session_store.import_ini_content(ini_content, overwrite=True)

            self.lbl_import_result.setText(f"✅ Successfully imported {count} session(s) from cloud!")
            self._populate_sessions()
            self.sessions_updated.emit()
            QMessageBox.information(self, "Import Successful", f"Successfully imported and decrypted {count} session(s)!")
        except DecryptionError:
            QMessageBox.critical(self, "Decryption Error", "Incorrect Master Password or corrupted cloud payload.")
        except InvalidPayloadError as exc:
            QMessageBox.critical(self, "Invalid Payload", f"The cloud payload was corrupted or not valid Base64: {exc}")
        except CloudAPIError as exc:
            QMessageBox.critical(self, "Cloud Download Failed", str(exc))
        except Exception as exc:
            QMessageBox.critical(self, "Import Error", f"Unexpected error during import: {exc}")

    def _on_import_from_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Configuration File",
            str(Path.home()),
            "WinSCP Configurations (*.ini *.enc *.bin *.txt);;All Files (*)",
        )
        if not file_path:
            return

        password = self.txt_import_file_pwd.text()
        try:
            raw_text = Path(file_path).read_text(encoding="utf-8").strip()

            # Check if file is plaintext INI (contains [Sessions)
            if "[Sessions" in raw_text or "HostName=" in raw_text:
                count = self.session_store.import_ini_content(raw_text, overwrite=True)
                msg = f"Imported {count} session(s) from plain INI file."
            else:
                # Encrypted Base64
                if not password:
                    QMessageBox.warning(self, "Password Required", "This file appears to be encrypted. Please enter the Master Password.")
                    return
                ini_content = decrypt_from_base64(raw_text, password)
                count = self.session_store.import_ini_content(ini_content, overwrite=True)
                msg = f"Decrypted and imported {count} session(s) from encrypted file."

            self.lbl_import_result.setText(f"✅ {msg}")
            self._populate_sessions()
            self.sessions_updated.emit()
            QMessageBox.information(self, "Import Successful", msg)
        except DecryptionError:
            QMessageBox.critical(self, "Decryption Error", "Incorrect password or corrupted file.")
        except Exception as exc:
            QMessageBox.critical(self, "Import Failed", f"Failed to import file: {exc}")
