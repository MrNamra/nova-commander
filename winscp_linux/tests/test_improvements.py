"""
Unit tests for WinSCP-Linux Improvement Points:
1. Standalone SSH Terminal Window and Ctrl+P shortcut.
2. File and directory permissions (chmod) with right-click selection and recursive support.
3. SSH Private Key passphrase storage, parsing, and authentication support.
4. Two-option file open prompt (Default vs Other) and safe external editor launch.
"""

import os
import stat
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

os.environ["QT_QPA_PLATFORM"] = "offscreen"

src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

try:
    from src.core.editor_manager import (
        EditorPreferencesManager,
        get_clean_editor_env,
        launch_external_editor,
    )
    from src.core.session_store import SessionConfig, SessionStore
    from src.core.sftp_client import RemoteFileInfo, SFTPClient
    from src.core.terminal_launcher import (
        build_ssh_command,
        get_available_linux_terminals,
        launch_system_terminal,
    )
    from src.ui.main_window import ChmodDialog, MainWindow
    from src.ui.open_with_dialog import SimpleOpenPromptDialog
    from src.ui.terminal_widget import SSHTerminalWindow
except ImportError:
    from core.editor_manager import (
        EditorPreferencesManager,
        get_clean_editor_env,
        launch_external_editor,
    )
    from core.session_store import SessionConfig, SessionStore
    from core.sftp_client import RemoteFileInfo, SFTPClient
    from core.terminal_launcher import (
        build_ssh_command,
        get_available_linux_terminals,
        launch_system_terminal,
    )
    from ui.main_window import ChmodDialog, MainWindow
    from ui.open_with_dialog import SimpleOpenPromptDialog
    from ui.terminal_widget import SSHTerminalWindow


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_session_config_and_store_passphrase():
    """Point 3: Verifies passphrase field in SessionConfig, INI generation, and parsing."""
    cfg = SessionConfig(
        name="EncryptedKeyServer",
        host="192.168.1.50",
        port=2222,
        username="deploy",
        private_key="/home/user/.ssh/id_ed25519_enc",
        passphrase="mySecretPassphrase123!",
    )

    data = cfg.to_dict()
    assert data["passphrase"] == "mySecretPassphrase123!"

    rebuilt = SessionConfig.from_dict(data)
    assert rebuilt.passphrase == "mySecretPassphrase123!"

    store = SessionStore()
    ini_text = store.generate_ini_content([cfg])
    assert "Passphrase=mySecretPassphrase123!" in ini_text

    parsed_sessions = store.parse_ini_content(ini_text)
    assert "EncryptedKeyServer" in parsed_sessions
    assert parsed_sessions["EncryptedKeyServer"].passphrase == "mySecretPassphrase123!"
    assert parsed_sessions["EncryptedKeyServer"].private_key == "/home/user/.ssh/id_ed25519_enc"


def test_sftp_client_chmod_recursive():
    """Point 2: Verifies recursive chmod traversal on remote directory."""
    client = SFTPClient()
    mock_ssh = MagicMock()
    mock_transport = MagicMock()
    mock_transport.is_active.return_value = True
    mock_ssh.get_transport.return_value = mock_transport
    mock_sftp = MagicMock()
    client._ssh = mock_ssh
    client._sftp = mock_sftp

    item_dir = MagicMock()
    item_dir.filename = "subdir"
    item_dir.st_mode = stat.S_IFDIR | 0o755

    item_file = MagicMock()
    item_file.filename = "script.py"
    item_file.st_mode = stat.S_IFREG | 0o644

    # listdir_attr returns items for /var/www, empty for child
    mock_sftp.listdir_attr.side_effect = lambda path: [item_dir, item_file] if path == "/var/www" else []

    client.chmod_recursive("/var/www", 0o750)

    # Verify chmod called on root dir, child dir, and child file
    mock_sftp.chmod.assert_any_call("/var/www", 0o750)
    mock_sftp.chmod.assert_any_call("/var/www/subdir", 0o750)
    mock_sftp.chmod.assert_any_call("/var/www/script.py", 0o750)


def test_chmod_dialog_file_vs_folder(qapp):
    """Point 2: Verifies ChmodDialog UI options for files vs directories."""
    # File: is_dir=False -> no recursive checkbox
    dlg_file = ChmodDialog("file.txt", "644", is_dir=False)
    assert not dlg_file.is_recursive()
    assert not hasattr(dlg_file, "cb_recursive")
    assert dlg_file.get_mode_int() == 0o644

    # Directory: is_dir=True -> recursive checkbox exists and works
    dlg_dir = ChmodDialog("my_folder", "755", is_dir=True)
    assert hasattr(dlg_dir, "cb_recursive")
    assert not dlg_dir.is_recursive()

    dlg_dir.cb_recursive.setChecked(True)
    assert dlg_dir.is_recursive()
    assert dlg_dir.get_mode_int() == 0o755

    # Test octal calculation toggle
    dlg_dir._set_from_octal_string("700")
    assert dlg_dir.get_mode_int() == 0o700
    assert dlg_dir.cb_or.isChecked()
    assert dlg_dir.cb_ow.isChecked()
    assert dlg_dir.cb_ox.isChecked()
    assert not dlg_dir.cb_gr.isChecked()
    assert not dlg_dir.cb_tr.isChecked()


def test_local_chmod_execution(qapp):
    """Point 2: Verifies chmod_selected on local file and directory."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_file = Path(tmp_dir) / "test_script.sh"
        test_file.write_text("#!/bin/sh\necho hello\n")
        os.chmod(test_file, 0o644)

        store = SessionStore()
        win = MainWindow(store)
        view = win.active_session_view()
        view._load_local_directory(tmp_dir)

        # Select the test file row
        found_row = -1
        for r in range(view.table_local.rowCount()):
            item = view.table_local.item(r, 0)
            if item and item.text().endswith("test_script.sh"):
                found_row = r
                break

        assert found_row >= 0
        view.table_local.selectRow(found_row)
        view.table_local.setFocus()

        from src.ui import main_window

        # Mock ChmodDialog to simulate user setting 755
        with patch.object(main_window, "ChmodDialog") as MockDlg:
            mock_inst = MagicMock()
            mock_inst.exec.return_value = 1  # Accepted
            mock_inst.get_mode_int.return_value = 0o755
            mock_inst.is_recursive.return_value = False
            MockDlg.return_value = mock_inst

            view.chmod_selected()

        # Verify permissions updated locally on disk
        mode = os.stat(test_file).st_mode & 0o777
        assert mode == 0o755
        win.close()


def test_simple_open_prompt_dialog_two_options(qapp):
    """Point 4: Verifies SimpleOpenPromptDialog presents only 2 options: Default vs Other."""
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
        f.write(b"print('hello')\n")
        temp_path = f.name

    try:
        prefs = EditorPreferencesManager()
        prefs.default_editor = "code"

        dlg = SimpleOpenPromptDialog(temp_path, pref_manager=prefs)
        assert dlg.rb_default.isChecked()
        assert not dlg.rb_other.isChecked()

        # Check default resolution
        assert "code" in dlg._default_name.lower() or "visual studio code" in dlg._default_name.lower()

        # Accept with default
        dlg._on_open_clicked()
        assert dlg.get_selected_command() == "code"
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def test_launch_external_editor_clean_env():
    """Point 4: Verifies environment sanitation and safe external editor launch."""
    # Set polluting winscp library path
    os.environ["LD_LIBRARY_PATH"] = "/home/admin1/Desktop/sshApp/winscp_linux/lib:/usr/lib"
    os.environ["_WINSCP_XCB_LOADED"] = "1"

    clean = get_clean_editor_env()

    # The bundled winscp lib must be stripped to prevent Electron/VS Code crashes
    assert "winscp" not in clean.get("LD_LIBRARY_PATH", "").lower()
    assert "_WINSCP_XCB_LOADED" not in clean

    with patch("subprocess.Popen") as mock_popen:
        launch_external_editor("code", "/tmp/sample.txt")
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        assert args[0] == ["code", "/tmp/sample.txt"]
        assert kwargs["start_new_session"] is True
        assert kwargs["stdin"] is not None


def test_standalone_ssh_terminal_window(qapp):
    """Point 1: Verifies SSHTerminalWindow standalone window instantiation and properties."""
    from src.core.ssh_client import SSHShellClient
    with patch.object(SSHShellClient, "open_shell"):
        term_win = SSHTerminalWindow(
            host="192.168.1.100",
            port=22,
            username="admin",
            session_name="Production Server",
        )
        assert "Production Server" in term_win.windowTitle()
        assert term_win.terminal_widget is not None
        assert term_win.shell_client is not None

        # Verify toolbar actions exist
        actions = [a.text() for a in term_win.findChildren(object) if hasattr(a, "text") and a.text()]
        assert any("Copy" in a for a in actions)
        assert any("Paste" in a for a in actions)
        assert any("Clear" in a for a in actions)

        term_win.close()


def test_ctrl_p_terminal_shortcut_wiring(qapp):
    """Point 1: Verifies Ctrl+P shortcut is wired in MainWindow to open_terminal_window."""
    store = SessionStore()
    win = MainWindow(store)

    # Check Commands menu for Ctrl+P action
    found_ctrl_p = False
    for action in win.menuBar().actions():
        menu = action.menu()
        if menu:
            for sub_act in menu.actions():
                if sub_act.shortcut().toString() == "Ctrl+P":
                    found_ctrl_p = True
                    assert "Terminal" in sub_act.text()
                    break

    assert found_ctrl_p, "Ctrl+P shortcut action was not found in menus!"

    # Verify Ctrl+T built-in dock shortcut is removed
    found_ctrl_t = False
    for action in win.menuBar().actions():
        menu = action.menu()
        if menu:
            for sub_act in menu.actions():
                if sub_act.shortcut().toString() == "Ctrl+T":
                    found_ctrl_t = True
    assert not found_ctrl_t, "Ctrl+T should be removed from menus!"
    assert not hasattr(win, "sc_term_dock"), "sc_term_dock (Ctrl+T) should be removed!"

    # Verify open_terminal_window method exists and can be called safely
    assert hasattr(win, "open_terminal_window")
    view = win.active_session_view()
    assert hasattr(view, "open_terminal_window")

    win.close()


def test_open_terminal_window_when_sftp_connected(qapp):
    """Verifies Ctrl+P native OS terminal launch when SFTP is connected, plus graceful fallback."""
    from src.core.ssh_client import SSHShellClient
    from src.ui import main_window as mw_mod

    store = SessionStore()
    win = MainWindow(store)
    view = win.active_session_view()

    # Mock an active SFTP connection
    mock_ssh = MagicMock()
    mock_transport = MagicMock()
    mock_transport.is_active.return_value = True
    mock_ssh.get_transport.return_value = mock_transport
    mock_sftp = MagicMock()

    view.sftp_client._ssh = mock_ssh
    view.sftp_client._sftp = mock_sftp
    view.sftp_client._session_info = {
        "host": "192.168.1.200",
        "port": 2222,
        "username": "developer",
    }
    view.current_session = SessionConfig(
        name="DevServer",
        host="192.168.1.200",
        port=2222,
        username="developer",
    )
    view.txt_remote_path.setText("/var/www/project")

    # Verify sftp_client properties
    assert view.sftp_client.host == "192.168.1.200"
    assert view.sftp_client.port == 2222
    assert view.sftp_client.username == "developer"
    assert view.sftp_client.is_connected is True

    # 1. Primary path: Native system terminal is spawned
    with patch.object(mw_mod, "launch_system_terminal", return_value=True) as mock_launch:
        view.open_terminal_window()
        mock_launch.assert_called_once_with(
            host="192.168.1.200",
            port=2222,
            username="developer",
            password="",
            private_key="",
            remote_dir="/var/www/project",
            session_name="DevServer",
        )

    # 2. Fallback path: If native launcher throws, fallback to internal SSHTerminalWindow
    with patch.object(mw_mod, "launch_system_terminal", side_effect=RuntimeError("No terminal emulator found")), \
         patch.object(SSHShellClient, "open_shell"):
        view.open_terminal_window()
        assert len(view._term_windows) == 1
        tw = view._term_windows[0]
        assert "DevServer" in tw.windowTitle()
        tw.close()

    win.close()


def test_f2_rename_local_file(qapp, tmp_path):
    """Verifies F2 rename for local files."""
    test_file = tmp_path / "old_name.txt"
    test_file.write_text("Hello World", encoding="utf-8")

    store = SessionStore()
    win = MainWindow(store)
    view = win.active_session_view()
    view.txt_local_path.setText(str(tmp_path))
    view._load_local_directory(str(tmp_path))

    # Select the row with old_name.txt
    target_row = -1
    for r in range(view.table_local.rowCount()):
        item = view.table_local.item(r, 0)
        if item and "old_name.txt" in item.text():
            target_row = r
            break
    assert target_row != -1
    view.table_local.selectRow(target_row)

    # Mock QInputDialog.getText to provide new name
    with patch("PyQt6.QtWidgets.QInputDialog.getText", return_value=("new_name.txt", True)):
        view.rename_selected("local")

    assert not (tmp_path / "old_name.txt").exists()
    assert (tmp_path / "new_name.txt").exists()
    assert (tmp_path / "new_name.txt").read_text(encoding="utf-8") == "Hello World"

    win.close()


def test_f2_key_press_event_emits_signal(qapp):
    """Verifies that pressing F2 in CommanderTableWidget emits rename_requested."""
    from src.ui.main_window import CommanderTableWidget
    table = CommanderTableWidget("local")

    signal_received = []
    table.rename_requested.connect(lambda: signal_received.append(True))

    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent

    key_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_F2, Qt.KeyboardModifier.NoModifier)
    table.keyPressEvent(key_event)

    assert len(signal_received) == 1, "F2 key press did not emit rename_requested signal!"
    table.close()


def test_terminal_launcher_build_command():
    """Verifies SSH command line generation with port, key, sshpass, and cd options."""
    # Basic
    cmd1 = build_ssh_command(host="192.168.1.10")
    assert "ssh" in cmd1
    assert "192.168.1.10" in cmd1
    assert "StrictHostKeyChecking=accept-new" in cmd1

    # Custom port and username and remote directory
    cmd2 = build_ssh_command(
        host="server.internal",
        port=2222,
        username="devops",
        remote_dir="/var/www/site",
    )
    assert "-p 2222" in cmd2
    assert "devops@server.internal" in cmd2
    assert "cd /var/www/site" in cmd2

    # Password with sshpass
    with patch("shutil.which", return_value="/usr/bin/sshpass"):
        cmd3 = build_ssh_command(host="10.0.0.5", username="admin", password="mypassword123")
        assert "export SSHPASS=" in cmd3
        assert "sshpass -e" in cmd3
        assert "admin@10.0.0.5" in cmd3


def test_terminal_launcher_get_available_linux_terminals():
    """Verifies discovery of installed Linux terminal emulators."""
    with patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}" if x in ("konsole", "xterm") else None):
        terms = get_available_linux_terminals()
        assert terms == ["konsole", "xterm"]


def test_launch_system_terminal_linux():
    """Verifies launch_system_terminal spawns terminal process with clean env."""
    import sys
    from unittest.mock import MagicMock, patch

    with patch.object(sys, "platform", "linux"), \
         patch("src.core.terminal_launcher.get_available_linux_terminals", return_value=["gnome-terminal"]), \
         patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        ok = launch_system_terminal(host="192.168.1.1", port=22, username="root")
        assert ok is True
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        cmd_list = args[0]
        assert cmd_list[0] == "gnome-terminal"
        assert any("SSH: root@192.168.1.1" in a for a in cmd_list)
        assert kwargs.get("start_new_session") is True


def test_launch_system_terminal_windows():
    """Verifies Windows terminal launch branching."""
    import sys
    from unittest.mock import MagicMock, patch

    with patch.object(sys, "platform", "win32"), \
         patch("shutil.which", return_value="C:\\Windows\\System32\\wt.exe"), \
         patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        ok = launch_system_terminal(host="10.0.0.2", port=22, username="user")
        assert ok is True
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        assert args[0][0] == "C:\\Windows\\System32\\wt.exe"


def test_launch_system_terminal_darwin():
    """Verifies macOS Terminal.app launch via osascript."""
    import sys
    from unittest.mock import MagicMock, patch

    with patch.object(sys, "platform", "darwin"), \
         patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock()
        ok = launch_system_terminal(host="10.0.0.3", port=22, username="macuser")
        assert ok is True
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        assert args[0][0] == "osascript"
        assert "Terminal" in args[0][2]


def test_session_dialog_move_session_to_folder(qapp, tmp_path):
    """Verifies moving a session into a folder inside SessionDialog via drag-and-drop / move action."""
    from src.ui.session_dialog import SessionDialog

    store_file = tmp_path / "sessions.ini"
    store = SessionStore(storage_path=store_file)

    s1 = SessionConfig(name="oracle", host="127.0.0.1", port=22, username="admin1")
    s2 = SessionConfig(name="serv-Raju", host="s2.serv00.com", port=22, username="Raju")
    store.save_session(s1)
    store.save_session(s2)
    store.add_folder("aaa")

    dlg = SessionDialog(store)

    # Verify initial tree setup
    assert dlg.tree.topLevelItemCount() == 1  # Root item
    root_item = dlg.tree.topLevelItem(0)
    assert "Saved Sessions" in root_item.text(0)

    # Check child items: folder aaa and root sessions oracle, serv-Raju
    child_texts = [root_item.child(i).text(0) for i in range(root_item.childCount())]
    assert any("aaa" in t for t in child_texts)
    assert any("oracle" in t for t in child_texts)
    assert any("serv-Raju" in t for t in child_texts)

    # 1. Trigger move via drag-and-drop signal handler
    dlg._on_tree_session_moved("oracle", "aaa")

    # Verify session is moved into folder aaa
    assert store.get_session("aaa/oracle") is not None
    assert store.get_session("oracle", exact_path=True) is None
    assert dlg.cmb_folder.currentText() == "aaa"

    # Verify tree reflects the move: oracle is now a child of aaa
    root_item = dlg.tree.topLevelItem(0)
    folder_item = None
    for i in range(root_item.childCount()):
        if "aaa" in root_item.child(i).text(0):
            folder_item = root_item.child(i)
            break
    assert folder_item is not None
    assert folder_item.childCount() >= 1
    assert any("oracle" in folder_item.child(i).text(0) for i in range(folder_item.childCount()))

    # 2. Test moving back to root via folder combobox and saving
    dlg.cmb_folder.setCurrentText("(Root / None)")
    with patch("PyQt6.QtWidgets.QMessageBox.information"):
        dlg._save_current_session()

    assert store.get_session("oracle") is not None
    assert store.get_session("oracle").folder == ""
    assert store.get_session("aaa/oracle", exact_path=True) is None

    dlg.close()


def test_serv_raju_terminal_command():
    """Verifies that serv-Raju with complex symbols in password generates safe export SSHPASS."""
    cmd = build_ssh_command(
        host="s2.serv00.com",
        port=22,
        username="Raju",
        password="C**fjvdDPGB&wfM#3GSO",
        remote_dir="/",
    )
    assert "-o StrictHostKeyChecking=accept-new" in cmd
    assert "Raju@s2.serv00.com" in cmd
    with patch("shutil.which", return_value="/usr/bin/sshpass"):
        cmd_with_sshpass = build_ssh_command(
            host="s2.serv00.com",
            port=22,
            username="Raju",
            password="C**fjvdDPGB&wfM#3GSO",
            remote_dir="/",
        )
        assert "export SSHPASS=" in cmd_with_sshpass
        assert "sshpass -e" in cmd_with_sshpass


def test_toolbar_terminal_position_for_smaller_screens(qapp):
    """Verifies terminal button is positioned early in the toolbar to prevent overflow on small screens."""
    from PyQt6.QtWidgets import QToolBar
    win = MainWindow(SessionStore())
    toolbar_actions = win.findChild(QToolBar).actions()
    action_texts = [a.text() for a in toolbar_actions if a.text()]
    
    # Terminal must be among the first 4 toolbar actions
    assert "🖥 Terminal" in action_texts[:4], f"🖥 Terminal action was not near front of toolbar: {action_texts}"
    win.close()


def test_f2_and_ctrl_p_window_actions(qapp):
    """Verifies F2 and Ctrl+P are properly registered as window actions without ambiguous duplicates."""
    win = MainWindow(SessionStore())
    window_actions = win.actions()
    
    shortcuts = [a.shortcut().toString() for a in window_actions if not a.shortcut().isEmpty()]
    assert "F2" in shortcuts, "F2 shortcut missing from window actions!"
    assert "Ctrl+P" in shortcuts, "Ctrl+P shortcut missing from window actions!"
    
    # Verify no duplicate shortcuts on window
    f2_count = shortcuts.count("F2")
    ctrl_p_count = shortcuts.count("Ctrl+P")
    assert f2_count == 1, f"Expected 1 F2 shortcut, found {f2_count} (causes ambiguity)!"
    assert ctrl_p_count == 1, f"Expected 1 Ctrl+P shortcut, found {ctrl_p_count} (causes ambiguity)!"
    win.close()


def test_session_dialog_opens_by_default_on_startup(qapp, tmp_path, monkeypatch):
    """Verifies that the Login & Saved Sessions dialog is opened by default on startup, even with saved sessions."""
    from src.core.session_store import SessionConfig, SessionStore
    from src.main import main
    from PyQt6.QtCore import QTimer

    store_file = tmp_path / "sessions.ini"
    store = SessionStore(storage_path=store_file)
    store.save_session(SessionConfig(name="test-server", host="192.168.1.50"))
    assert len(store.list_sessions()) == 1

    dialog_opened = []

    # Intercept SessionStore instantiation to point to test store
    monkeypatch.setattr("src.main.SessionStore", lambda: store)

    # Intercept open_session_dialog on MainWindow to verify it gets called
    from src.ui.main_window import MainWindow
    original_open = MainWindow.open_session_dialog
    def mock_open(self):
        dialog_opened.append(True)
    monkeypatch.setattr(MainWindow, "open_session_dialog", mock_open)

    captured_windows = []
    original_init = MainWindow.__init__
    def mock_init(self, *args, **kwargs):
        captured_windows.append(self)
        original_init(self, *args, **kwargs)
    monkeypatch.setattr(MainWindow, "__init__", mock_init)

    # Intercept app.exec to avoid blocking event loop
    monkeypatch.setattr("PyQt6.QtWidgets.QApplication.exec", lambda self: 0)
    monkeypatch.setattr("sys.exit", lambda code=0: None)

    main()

    # Process pending events so QTimer.singleShot(0, ...) fires
    qapp.processEvents()

    assert len(dialog_opened) == 1, "Session dialog should have been opened by default on app startup!"
    for w in captured_windows:
        w.close()



