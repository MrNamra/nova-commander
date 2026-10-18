"""
Tests for EditorPreferencesManager, OpenWithDialog, and EditorPreferencesDialog.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from src.core.editor_manager import (
    EditorInfo,
    EditorPreferencesManager,
)
from src.ui.main_window import MainWindow
from src.ui.open_with_dialog import (
    EditorPreferencesDialog,
    OpenWithDialog,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_editor_manager_detection(tmp_path: Path):
    cfg_file = tmp_path / "editors.json"
    mgr = EditorPreferencesManager(config_file=cfg_file)

    installed = mgr.get_installed_editors()
    assert len(installed) > 0

    # Ensure xdg-open or standard Linux editors are detected
    editor_ids = [e.id for e in installed]
    assert "xdg-open" in editor_ids or "code" in editor_ids or "nano" in editor_ids


def test_editor_manager_preferences_and_associations(tmp_path: Path):
    cfg_file = tmp_path / "editors.json"
    mgr = EditorPreferencesManager(config_file=cfg_file)

    # Initially always_ask is True and no default is set
    assert mgr.always_ask is True
    assert mgr.get_editor_for_file("test.py") is None

    # Set extension default for .py
    mgr.set_default_editor("code", for_extension=".py", always_ask=False)
    assert mgr.always_ask is False
    assert mgr.associations.get(".py") == "code"
    assert mgr.get_editor_for_file("test.py") == "code"
    assert mgr.get_editor_for_file("another_file.PY") == "code"
    assert mgr.get_configured_editor("test.py") == "code"

    # Other extensions should still return None if no global default
    assert mgr.get_editor_for_file("notes.txt") is None

    # Set global default
    mgr.set_default_editor("gedit", for_extension=None, always_ask=False)
    assert mgr.default_editor == "gedit"
    # .py should still return code
    assert mgr.get_editor_for_file("test.py") == "code"
    # notes.txt should fall back to gedit
    assert mgr.get_editor_for_file("notes.txt") == "gedit"

    # If always_ask is set to True, get_editor_for_file returns None to trigger dialog
    mgr.always_ask = True
    mgr.save()
    assert mgr.get_editor_for_file("test.py") is None
    # But get_configured_editor still knows the configured choice
    assert mgr.get_configured_editor("test.py") == "code"
    assert mgr.get_configured_editor("notes.txt") == "gedit"


def test_editor_manager_persistence(tmp_path: Path):
    cfg_file = tmp_path / "editors.json"
    mgr1 = EditorPreferencesManager(config_file=cfg_file)
    mgr1.set_default_editor("/usr/bin/subl", for_extension=None, always_ask=False)
    mgr1.set_default_editor("code", for_extension="json", always_ask=False)

    # Load in new instance
    mgr2 = EditorPreferencesManager(config_file=cfg_file)
    assert mgr2.default_editor == "/usr/bin/subl"
    assert mgr2.associations.get(".json") == "code"
    assert mgr2.always_ask is False
    assert "/usr/bin/subl" in mgr2.custom_commands

    # Remove association
    mgr2.remove_association(".json")
    assert ".json" not in mgr2.associations

    # Reset
    mgr2.reset_to_defaults()
    assert mgr2.default_editor is None
    assert mgr2.always_ask is True
    assert len(mgr2.associations) == 0


def test_open_with_dialog_ui(qapp, tmp_path: Path):
    cfg_file = tmp_path / "editors.json"
    mgr = EditorPreferencesManager(config_file=cfg_file)

    dlg = OpenWithDialog("server_script.py", pref_manager=mgr)
    assert dlg.filename == "server_script.py"
    assert dlg.extension == ".py"
    assert dlg.list_editors.count() > 0

    # Test selecting custom command
    dlg.txt_custom_cmd.setText("my_custom_ide")
    assert dlg.get_selected_command() == "my_custom_ide"

    # Test checking default for extension
    dlg.cb_set_default_ext.setChecked(True)
    assert dlg.cb_set_default_global.isChecked() is False
    assert dlg.cb_always_ask.isChecked() is False

    # Simulate accept
    dlg._on_accept()
    assert mgr.associations.get(".py") == "my_custom_ide"
    assert mgr.always_ask is False


def test_open_with_dialog_global_default(qapp, tmp_path: Path):
    cfg_file = tmp_path / "editors.json"
    mgr = EditorPreferencesManager(config_file=cfg_file)

    dlg = OpenWithDialog("document.pdf", pref_manager=mgr)
    dlg.txt_custom_cmd.setText("code")
    dlg.cb_set_default_global.setChecked(True)
    assert dlg.cb_set_default_ext.isChecked() is False

    dlg._on_accept()
    assert mgr.default_editor == "code"
    assert mgr.always_ask is False


def test_editor_preferences_dialog(qapp, tmp_path: Path):
    cfg_file = tmp_path / "editors.json"
    mgr = EditorPreferencesManager(config_file=cfg_file)
    mgr.set_default_editor("gedit", for_extension=".txt", always_ask=False)

    pref_dlg = EditorPreferencesDialog(pref_manager=mgr)
    assert pref_dlg.table_assoc.rowCount() == 1
    assert pref_dlg.table_assoc.item(0, 0).text() == ".txt"
    assert pref_dlg.table_assoc.item(0, 1).text() == "gedit"

    # Remove the association
    pref_dlg.table_assoc.selectRow(0)
    pref_dlg._remove_association()
    assert pref_dlg.table_assoc.rowCount() == 0

    pref_dlg.txt_custom_global.setText("kate")
    pref_dlg.cb_always_ask.setChecked(False)
    pref_dlg._save_preferences()

    assert mgr.default_editor == "kate"
    assert mgr.always_ask is False
    assert len(mgr.associations) == 0


def test_main_window_open_with_wiring(qapp):
    win = MainWindow()
    view = win.active_session_view()
    assert view is not None

    # Check menu items under Commands menu
    cmd_menu = None
    for action in win.menuBar().actions():
        if action.text() == "&Commands":
            cmd_menu = action.menu()
            break
    assert cmd_menu is not None
    cmd_actions = [a.text() for a in cmd_menu.actions()]
    assert "Open With..." in cmd_actions
    assert "Configure Default Applications..." in cmd_actions

    # Check toolbar
    assert hasattr(win, "tb_open_with")
    assert win.tb_open_with.text() == "📂 Open With"

    win.close()


def test_commander_view_open_local_file(qapp, tmp_path: Path, monkeypatch):
    from unittest.mock import MagicMock
    from src.core.editor_manager import editor_preferences

    # Create dummy local file
    test_file = tmp_path / "greeting.txt"
    test_file.write_text("hello world")

    win = MainWindow()
    view = win.active_session_view()
    view._load_local_directory(str(tmp_path))

    # Select greeting.txt
    target_row = -1
    for r in range(view.table_local.rowCount()):
        item = view.table_local.item(r, 0)
        if item and "greeting.txt" in item.text():
            target_row = r
            break
    assert target_row >= 0
    view.table_local.selectRow(target_row)

    # 1. When always_ask is False and default_editor is set, it directly opens without prompting
    editor_preferences.default_editor = "echo"
    editor_preferences.always_ask = False

    launched_cmds = []
    def mock_popen(cmd, **kwargs):
        launched_cmds.append(cmd)
        mock_proc = MagicMock()
        return mock_proc

    monkeypatch.setattr("subprocess.Popen", mock_popen)
    view.open_selected_local_file(force_open_with=False)
    assert len(launched_cmds) == 1
    assert launched_cmds[0][0] == "echo"
    assert str(test_file) in launched_cmds[0][1]

    # 2. When force_open_with is True, it prompts with OpenWithDialog
    dialog_opened = []
    def mock_dlg_exec(self):
        dialog_opened.append(True)
        self._selected_command = "code"
        return 1  # Accepted

    monkeypatch.setattr("src.ui.open_with_dialog.OpenWithDialog.exec", mock_dlg_exec)
    view.open_selected_local_file(force_open_with=True)
    assert len(dialog_opened) == 1
    assert len(launched_cmds) == 2
    assert launched_cmds[1][0] == "code"

    win.close()

