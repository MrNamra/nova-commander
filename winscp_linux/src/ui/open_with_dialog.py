"""
Open With & Application Preferences Dialog for WinSCP-Linux.
Prompts the user to select which editor/application to open a file with,
and allows setting default applications globally or per file extension.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.editor_manager import EditorInfo, EditorPreferencesManager, editor_preferences


class OpenWithDialog(QDialog):
    """
    Dialog asking user which application they want to open a file with,
    with options to persist defaults globally or per file extension.
    """

    def __init__(
        self,
        filename_or_path: str,
        pref_manager: Optional[EditorPreferencesManager] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.filename = Path(filename_or_path).name
        self.extension = Path(filename_or_path).suffix.lower()
        self.prefs = pref_manager or editor_preferences

        self.setWindowTitle(f"Open With - {self.filename}")
        self.resize(540, 500)

        self._selected_command: str = "xdg-open"
        self._setup_ui()
        self._populate_editors()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Header description
        lbl_header = QLabel(f"Choose an application to open and edit:\n<b>{self.filename}</b>")
        lbl_header.setStyleSheet("font-size: 13px; color: #ffffff;")
        lbl_header.setWordWrap(True)
        layout.addWidget(lbl_header)

        # Application list
        grp_apps = QGroupBox("Available Applications")
        app_layout = QVBoxLayout(grp_apps)

        self.list_editors = QListWidget()
        self.list_editors.setStyleSheet("""
            QListWidget::item { padding: 6px; }
            QListWidget::item:selected { background-color: #264f78; color: #ffffff; }
        """)
        self.list_editors.itemSelectionChanged.connect(self._on_editor_selected)
        self.list_editors.itemDoubleClicked.connect(self._on_item_double_clicked)
        app_layout.addWidget(self.list_editors)

        layout.addWidget(grp_apps)

        # Custom Command Section
        grp_custom = QGroupBox("Custom Application / Command")
        c_layout = QHBoxLayout(grp_custom)

        self.txt_custom_cmd = QLineEdit()
        self.txt_custom_cmd.setPlaceholderText("e.g. /usr/bin/code, gedit, subl")
        self.btn_browse = QPushButton("Browse...")
        self.btn_browse.clicked.connect(self._browse_executable)

        c_layout.addWidget(self.txt_custom_cmd)
        c_layout.addWidget(self.btn_browse)
        layout.addWidget(grp_custom)

        self.txt_custom_cmd.textChanged.connect(self._on_custom_cmd_changed)

        # Default Settings Checkboxes
        grp_defaults = QGroupBox("Preferences")
        def_layout = QVBoxLayout(grp_defaults)

        ext_label = (
            f"Always use this application for *{self.extension} files"
            if self.extension
            else "Always use for files without extension"
        )
        self.cb_set_default_ext = QCheckBox(ext_label)
        def_layout.addWidget(self.cb_set_default_ext)

        self.cb_set_default_global = QCheckBox("Set as default application for all files")
        def_layout.addWidget(self.cb_set_default_global)

        self.cb_always_ask = QCheckBox("Always ask before opening files")
        self.cb_always_ask.setChecked(self.prefs.always_ask)
        def_layout.addWidget(self.cb_always_ask)

        # Mutual exclusivity logic for defaults
        self.cb_set_default_global.stateChanged.connect(self._on_global_default_toggled)
        self.cb_set_default_ext.stateChanged.connect(self._on_ext_default_toggled)
        self.cb_always_ask.stateChanged.connect(self._on_always_ask_toggled)

        layout.addWidget(grp_defaults)

        # Bottom Buttons
        btn_bar = QHBoxLayout()
        btn_bar.addStretch()

        self.btn_open = QPushButton("Open")
        self.btn_open.setDefault(True)
        self.btn_open.setStyleSheet("font-weight: bold; background-color: #238636; color: white; padding: 6px 16px;")
        self.btn_cancel = QPushButton("Cancel")

        btn_bar.addWidget(self.btn_open)
        btn_bar.addWidget(self.btn_cancel)
        layout.addLayout(btn_bar)

        self.btn_open.clicked.connect(self._on_accept)
        self.btn_cancel.clicked.connect(self.reject)

    def _populate_editors(self):
        """Populates the list with detected installed editors."""
        installed = self.prefs.get_installed_editors()

        preferred_cmd = self.prefs.get_configured_editor(self.filename) or self.prefs.default_editor

        selected_row = 0
        for idx, editor in enumerate(installed):
            item = QListWidgetItem()
            prefix = "💻 " if editor.id != "xdg-open" else "🌐 "
            item.setText(f"{prefix}{editor.name}\n   Command: {editor.command}")
            item.setData(Qt.ItemDataRole.UserRole, editor.command)
            self.list_editors.addItem(item)

            if preferred_cmd and (
                editor.command == preferred_cmd or editor.command.startswith(preferred_cmd.split()[0])
            ):
                selected_row = idx

        if self.list_editors.count() > 0:
            self.list_editors.setCurrentRow(selected_row)
            self._selected_command = self.list_editors.item(selected_row).data(Qt.ItemDataRole.UserRole)
            self.txt_custom_cmd.setText(self._selected_command)

    def _on_editor_selected(self):
        selected = self.list_editors.selectedItems()
        if selected:
            cmd = selected[0].data(Qt.ItemDataRole.UserRole)
            self._selected_command = cmd
            self.txt_custom_cmd.blockSignals(True)
            self.txt_custom_cmd.setText(cmd)
            self.txt_custom_cmd.blockSignals(False)

    def _on_custom_cmd_changed(self, text: str):
        if text.strip():
            self._selected_command = text.strip()
            self.list_editors.clearSelection()

    def _browse_executable(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Application Executable",
            "/usr/bin",
            "Executables (*);;All Files (*)",
        )
        if path:
            self.txt_custom_cmd.setText(path)
            self._selected_command = path

    def _on_global_default_toggled(self, state: int):
        if state == Qt.CheckState.Checked.value or state == 2:
            self.cb_set_default_ext.setChecked(False)
            self.cb_always_ask.setChecked(False)

    def _on_ext_default_toggled(self, state: int):
        if state == Qt.CheckState.Checked.value or state == 2:
            self.cb_set_default_global.setChecked(False)
            self.cb_always_ask.setChecked(False)

    def _on_always_ask_toggled(self, state: int):
        if state == Qt.CheckState.Checked.value or state == 2:
            self.cb_set_default_global.setChecked(False)
            self.cb_set_default_ext.setChecked(False)

    def _on_item_double_clicked(self, item: QListWidgetItem):
        self._on_accept()

    def _on_accept(self):
        cmd = self.txt_custom_cmd.text().strip() or self._selected_command
        if not cmd:
            QMessageBox.warning(self, "No Application Selected", "Please select or type an application command.")
            return

        self._selected_command = cmd

        # Save preferences if requested
        always_ask = self.cb_always_ask.isChecked()
        if self.cb_set_default_global.isChecked():
            self.prefs.set_default_editor(cmd, for_extension=None, always_ask=False)
        elif self.cb_set_default_ext.isChecked():
            target_ext = self.extension if self.extension else "<none>"
            self.prefs.set_default_editor(cmd, for_extension=target_ext, always_ask=False)
        else:
            self.prefs.always_ask = always_ask
            self.prefs.save()

        self.accept()

    def get_selected_command(self) -> str:
        """Returns the chosen editor command."""
        return self._selected_command


class SimpleOpenPromptDialog(QDialog):
    """
    Simplified File Open Dialog giving 2 options only:
    1. Default Application (e.g. Default: Visual Studio Code)
    2. Other Application... (Opens system software manager where user selects which software to open)
    Includes option to persist default choice for file extension.
    """

    def __init__(
        self,
        filename_or_path: str,
        pref_manager: Optional[EditorPreferencesManager] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.filename = Path(filename_or_path).name
        self.extension = Path(filename_or_path).suffix.lower()
        self.prefs = pref_manager or editor_preferences

        self.setWindowTitle(f"Open File - {self.filename}")
        self.resize(480, 270)

        # Determine default application
        self._default_cmd = self.prefs.get_configured_editor(self.filename)
        if not self._default_cmd:
            installed = self.prefs.get_installed_editors()
            if installed:
                self._default_cmd = installed[0].command
                self._default_name = installed[0].name
            else:
                self._default_cmd = "xdg-open"
                self._default_name = "System Default Application"
        else:
            self._default_name = self._resolve_name(self._default_cmd)

        self._selected_command: str = self._default_cmd
        self._set_as_default: bool = False
        self._setup_ui()

    def _resolve_name(self, cmd: str) -> str:
        for ed in self.prefs.get_installed_editors():
            if ed.command == cmd:
                return f"{ed.name} ({ed.command})"
        base = Path(cmd.split()[0]).name
        return f"{base} ({cmd})"

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        lbl_desc = QLabel(f"How would you like to open <b>{self.filename}</b>?")
        lbl_desc.setStyleSheet("font-size: 13px; color: #ffffff;")
        lbl_desc.setWordWrap(True)
        layout.addWidget(lbl_desc)

        # Options group (2 options only)
        grp_options = QGroupBox("Select Option")
        opt_layout = QVBoxLayout(grp_options)
        opt_layout.setSpacing(10)

        self.btn_group = QButtonGroup(self)

        # Option 1: Default Application
        self.rb_default = QRadioButton(f"1. Default Application ({self._default_name})")
        self.rb_default.setChecked(True)
        self.btn_group.addButton(self.rb_default)
        opt_layout.addWidget(self.rb_default)

        # Option 2: Other Application...
        h_other = QHBoxLayout()
        self.rb_other = QRadioButton("2. Other Application... (Select from system software list)")
        self.btn_group.addButton(self.rb_other)
        h_other.addWidget(self.rb_other)

        self.btn_browse_other = QPushButton("Choose Software...")
        self.btn_browse_other.setToolTip("Open system application selector to choose software")
        self.btn_browse_other.clicked.connect(self._open_system_manager)
        h_other.addWidget(self.btn_browse_other)
        opt_layout.addLayout(h_other)

        layout.addWidget(grp_options)

        # Persistence option
        ext_label = (
            f"Always use this choice for *{self.extension} files"
            if self.extension
            else "Always use this choice for files without extension"
        )
        self.cb_set_default = QCheckBox(ext_label)
        layout.addWidget(self.cb_set_default)

        # Dialog buttons
        btn_bar = QHBoxLayout()
        btn_bar.addStretch()

        self.btn_open = QPushButton("Open")
        self.btn_open.setDefault(True)
        self.btn_open.setStyleSheet("font-weight: bold; background-color: #238636; color: white; padding: 6px 16px;")
        self.btn_cancel = QPushButton("Cancel")

        btn_bar.addWidget(self.btn_open)
        btn_bar.addWidget(self.btn_cancel)
        layout.addLayout(btn_bar)

        self.btn_open.clicked.connect(self._on_open_clicked)
        self.btn_cancel.clicked.connect(self.reject)

    def _open_system_manager(self):
        self.rb_other.setChecked(True)
        dlg = OpenWithDialog(self.filename, pref_manager=self.prefs, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._selected_command = dlg.get_selected_command()
            self._set_as_default = self.cb_set_default.isChecked() or dlg.cb_set_default_ext.isChecked() or dlg.cb_set_default_global.isChecked()
            self.accept()

    def _on_open_clicked(self):
        if self.rb_other.isChecked():
            self._open_system_manager()
            return

        self._selected_command = self._default_cmd
        if self.cb_set_default.isChecked():
            self.prefs.set_default_editor(
                self._selected_command,
                for_extension=self.extension or None,
                always_ask=False,
            )
        self.accept()

    def get_selected_command(self) -> str:
        return self._selected_command

    def should_set_default(self) -> bool:
        return self._set_as_default


class EditorPreferencesDialog(QDialog):
    """
    Dedicated Preferences Dialog for managing global default editors,
    file extension associations, and prompt settings.
    """

    def __init__(
        self,
        pref_manager: Optional[EditorPreferencesManager] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.prefs = pref_manager or editor_preferences
        self.setWindowTitle("Configure File Applications & Editors")
        self.resize(560, 480)

        self._setup_ui()
        self._load_data()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Global Default Application
        grp_global = QGroupBox("Global Default Application")
        glayout = QVBoxLayout(grp_global)

        self.combo_global = QComboBox()
        self.installed_editors = self.prefs.get_installed_editors()
        for ed in self.installed_editors:
            self.combo_global.addItem(f"{ed.name} ({ed.command})", ed.command)

        glayout.addWidget(QLabel("Default Editor for All Files:"))
        glayout.addWidget(self.combo_global)

        h_custom = QHBoxLayout()
        self.txt_custom_global = QLineEdit()
        self.txt_custom_global.setPlaceholderText("Or custom command / path...")
        self.btn_browse_global = QPushButton("Browse...")
        self.btn_browse_global.clicked.connect(self._browse_custom_global)
        h_custom.addWidget(self.txt_custom_global)
        h_custom.addWidget(self.btn_browse_global)
        glayout.addLayout(h_custom)

        self.cb_always_ask = QCheckBox("Always ask which application to use before opening files")
        glayout.addWidget(self.cb_always_ask)

        layout.addWidget(grp_global)

        # Extension Associations Table
        grp_assoc = QGroupBox("File Extension Associations")
        alayout = QVBoxLayout(grp_assoc)

        self.table_assoc = QTableWidget(0, 2)
        self.table_assoc.setHorizontalHeaderLabels(["Extension", "Application Command"])
        self.table_assoc.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table_assoc.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table_assoc.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        alayout.addWidget(self.table_assoc)

        btn_row = QHBoxLayout()
        self.btn_add_assoc = QPushButton("➕ Add Association")
        self.btn_add_assoc.clicked.connect(self._add_association)
        self.btn_del_assoc = QPushButton("🗑 Remove Association")
        self.btn_del_assoc.clicked.connect(self._remove_association)
        btn_row.addWidget(self.btn_add_assoc)
        btn_row.addWidget(self.btn_del_assoc)
        btn_row.addStretch()
        alayout.addLayout(btn_row)

        layout.addWidget(grp_assoc)

        # Dialog Buttons
        btn_bar = QHBoxLayout()
        btn_bar.addStretch()

        self.btn_save = QPushButton("Save Preferences")
        self.btn_save.setStyleSheet("font-weight: bold; background-color: #238636; color: white; padding: 6px 16px;")
        self.btn_save.clicked.connect(self._save_preferences)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)

        btn_bar.addWidget(self.btn_save)
        btn_bar.addWidget(self.btn_cancel)
        layout.addLayout(btn_bar)

    def _load_data(self):
        # Global default
        current_def = self.prefs.default_editor or "xdg-open"
        idx = self.combo_global.findData(current_def)
        if idx >= 0:
            self.combo_global.setCurrentIndex(idx)
        else:
            self.txt_custom_global.setText(current_def)

        self.cb_always_ask.setChecked(self.prefs.always_ask)

        # Associations
        self.table_assoc.setRowCount(0)
        for ext, cmd in sorted(self.prefs.associations.items()):
            row = self.table_assoc.rowCount()
            self.table_assoc.insertRow(row)
            self.table_assoc.setItem(row, 0, QTableWidgetItem(ext))
            self.table_assoc.setItem(row, 1, QTableWidgetItem(cmd))

    def _browse_custom_global(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Executable", "/usr/bin", "Executables (*);;All Files (*)")
        if path:
            self.txt_custom_global.setText(path)

    def _add_association(self):
        ext, ok = QInputDialog.getText(self, "Add Extension Association", "File extension (e.g. .py, .json, .txt):")
        if not ok or not ext.strip():
            return
        clean_ext = ext.strip().lower()
        if not clean_ext.startswith("."):
            clean_ext = f".{clean_ext}"

        cmd, ok2 = QInputDialog.getText(self, "Editor Command", f"Command or application for {clean_ext} (e.g. code, gedit):")
        if not ok2 or not cmd.strip():
            return

        row = self.table_assoc.rowCount()
        self.table_assoc.insertRow(row)
        self.table_assoc.setItem(row, 0, QTableWidgetItem(clean_ext))
        self.table_assoc.setItem(row, 1, QTableWidgetItem(cmd.strip()))

    def _remove_association(self):
        selected = self.table_assoc.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        self.table_assoc.removeRow(row)

    def _save_preferences(self):
        custom_cmd = self.txt_custom_global.text().strip()
        global_cmd = custom_cmd if custom_cmd else self.combo_global.currentData()
        self.prefs.default_editor = global_cmd
        self.prefs.always_ask = self.cb_always_ask.isChecked()

        # Update associations
        new_assoc = {}
        for r in range(self.table_assoc.rowCount()):
            ext_item = self.table_assoc.item(r, 0)
            cmd_item = self.table_assoc.item(r, 1)
            if ext_item and cmd_item:
                new_assoc[ext_item.text().strip().lower()] = cmd_item.text().strip()

        self.prefs.associations = new_assoc
        self.prefs.save()
        self.accept()
