"""
Tests for WinSCP feature parity suite:
- Advanced Site Settings Dialog & SessionConfig parameters
- Directory Bookmarks Store & Dialog
- Find Files Dialog & FileSearchWorker
- Custom Commands Dialog & placeholder substitution
- Generate URL/Code Dialog
- Function Keys Bar Widget (F2-F10)
- Properties & Rights Matrix Dialog
- Symlink Creation Dialog
- Synchronize & Compare Directories
- Transfer Options Dialog
"""

import os
import stat
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from src.core.session_store import SessionConfig, SessionStore
from src.core.sftp_client import RemoteFileInfo, SFTPClient
from src.ui.advanced_site_dialog import AdvancedSiteSettingsDialog
from src.ui.bookmarks_dialog import BookmarkItem, BookmarksDialog, BookmarksStore
from src.ui.custom_commands_dialog import CustomCommandsDialog
from src.ui.find_files_dialog import FileSearchWorker, FindFilesDialog
from src.ui.function_bar import FunctionBarWidget
from src.ui.generate_url_dialog import GenerateUrlDialog
from src.ui.properties_dialog import PropertiesDialog
from src.ui.symlink_dialog import SymlinkDialog
from src.ui.synchronize_dialog import SyncScanWorker, SynchronizeChecklistDialog, SynchronizeDialog
from src.ui.transfer_options_dialog import TransferOptionsDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_session_config_advanced_winscp_properties():
    """Verifies that extended WinSCP session settings serialize and deserialize cleanly."""
    cfg = SessionConfig(
        name="Production Web",
        host="web.example.com",
        port=2222,
        username="deploy",
        remote_dir="/var/www/html",
        local_dir="/home/user/projects/web",
        ping_interval=30,
        compression=True,
        utf8_encoding="On",
        sftp_server_cmd="/usr/lib/openssh/sftp-server",
        proxy_type="SOCKS5",
        proxy_host="proxy.internal",
        proxy_port=1080,
        proxy_user="puser",
        proxy_password="ppassword",
    )

    data = cfg.to_dict()
    assert data["ping_interval"] == 30
    assert data["compression"] is True
    assert data["utf8_encoding"] == "On"
    assert data["sftp_server_cmd"] == "/usr/lib/openssh/sftp-server"
    assert data["proxy_type"] == "SOCKS5"
    assert data["proxy_host"] == "proxy.internal"
    assert data["proxy_port"] == 1080

    reconstructed = SessionConfig.from_dict(data)
    assert reconstructed.ping_interval == 30
    assert reconstructed.compression is True
    assert reconstructed.utf8_encoding == "On"
    assert reconstructed.proxy_type == "SOCKS5"
    assert reconstructed.proxy_port == 1080


def test_advanced_site_settings_dialog_ui(qapp):
    """Verifies the Advanced Site Settings Dialog loads and updates SessionConfig."""
    cfg = SessionConfig(name="TestSite", host="1.2.3.4", port=22, username="root")
    dlg = AdvancedSiteSettingsDialog(cfg)

    # Change settings
    dlg.spin_ping.setValue(45)
    dlg.chk_compression.setChecked(True)
    dlg.txt_remote_dir.setText("/opt/app")
    dlg.txt_local_dir.setText("/tmp/local")
    dlg.cmb_proxy_type.setCurrentText("SOCKS5")
    dlg.txt_proxy_host.setText("127.0.0.1")
    dlg.spin_proxy_port.setValue(9050)

    # Save
    dlg._save_and_accept()
    assert cfg.ping_interval == 45
    assert cfg.compression is True
    assert cfg.remote_dir == "/opt/app"
    assert cfg.local_dir == "/tmp/local"
    assert cfg.proxy_type == "SOCKS5"
    assert cfg.proxy_host == "127.0.0.1"
    assert cfg.proxy_port == 9050


def test_bookmarks_store_and_dialog(qapp, tmp_path: Path, monkeypatch):
    """Verifies bookmark persistence and BookmarksDialog."""
    storage_file = tmp_path / "bookmarks.json"
    monkeypatch.setattr(BookmarksStore, "get_path", lambda: storage_file)

    sample_bmarks = [
        BookmarkItem("Web Root", "/var/www/html", "/home/user/web"),
        BookmarkItem("Logs", "/var/log", "/home/user/logs"),
    ]
    BookmarksStore.save(sample_bmarks)

    loaded = BookmarksStore.load()
    assert len(loaded) == 2
    assert loaded[0].name == "Web Root"
    assert loaded[0].remote_path == "/var/www/html"
    assert loaded[1].name == "Logs"

    # Dialog test
    dlg = BookmarksDialog(current_remote="/var/www", current_local="/tmp")
    assert dlg.table.rowCount() == 2

    selected_data = {}

    def _on_jump(rem, loc):
        selected_data["remote"] = rem
        selected_data["local"] = loc

    dlg.jump_requested.connect(_on_jump)
    dlg.table.selectRow(0)
    dlg._on_jump()
    assert selected_data.get("remote") == "/var/www/html"


def test_find_files_worker(tmp_path: Path):
    """Verifies FileSearchWorker finds files matching wildcard and content."""
    sub1 = tmp_path / "sub1"
    sub1.mkdir()
    f1 = sub1 / "config.ini"
    f1.write_text("user = admin\nport = 8080\n")

    f2 = tmp_path / "readme.txt"
    f2.write_text("Hello world\nWelcome to Nova Commander\n")

    worker = FileSearchWorker(
        is_remote=False,
        sftp_client=None,
        start_dir=str(tmp_path),
        name_mask="*.ini",
        containing_text="admin",
        case_sensitive=False,
        recursive=True,
    )

    found_matches = []
    worker.file_found.connect(lambda item: found_matches.append(item.full_path))
    worker.run()

    assert len(found_matches) == 1
    assert "config.ini" in found_matches[0]


def test_custom_commands_dialog(qapp):
    """Verifies WinSCP custom command placeholders substitution."""
    dlg = CustomCommandsDialog(
        sftp_client=None,
        current_remote_dir="/var/www",
        selected_files=["index.html"],
    )

    # Test substitution
    dlg.txt_cmd.setText("tar -czf !&.tar.gz !; ls -la !/")
    final_cmd = dlg._build_final_command()
    assert "index.html" in final_cmd
    assert "/var/www" in final_cmd


def test_generate_url_dialog(qapp):
    """Verifies Generate URL / Code Dialog produces SFTP URLs, scripts, and commands."""
    cfg = SessionConfig(
        name="MyServer",
        host="ftp.example.com",
        port=22,
        username="john",
        password="secretpassword",
        remote_dir="/var/data",
    )
    dlg = GenerateUrlDialog(cfg, current_path="/var/data/archive.zip")
    dlg.chk_include_password.setChecked(True)

    # SFTP URL
    dlg.cmb_format.setCurrentIndex(0)
    url = dlg.txt_output.toPlainText()
    assert "sftp://john:secretpassword@ftp.example.com" in url

    # SSH Command
    dlg.cmb_format.setCurrentIndex(1)
    ssh_cmd = dlg.txt_output.toPlainText()
    assert "ssh " in ssh_cmd
    assert "john@ftp.example.com" in ssh_cmd

    # WinSCP Script
    dlg.cmb_format.setCurrentIndex(3)
    script = dlg.txt_output.toPlainText()
    assert "open sftp://john:secretpassword@ftp.example.com" in script

    # Python Script
    dlg.cmb_format.setCurrentIndex(4)
    py_code = dlg.txt_output.toPlainText()
    assert "paramiko.SSHClient()" in py_code


def test_function_bar_widget(qapp):
    """Verifies bottom FunctionBarWidget emits corresponding F-key signals."""
    bar = FunctionBarWidget()
    emitted = []

    bar.f2_rename_clicked.connect(lambda: emitted.append("F2"))
    bar.f3_view_clicked.connect(lambda: emitted.append("F3"))
    bar.f4_edit_clicked.connect(lambda: emitted.append("F4"))
    bar.f5_copy_clicked.connect(lambda: emitted.append("F5"))
    bar.f6_move_clicked.connect(lambda: emitted.append("F6"))
    bar.f7_mkdir_clicked.connect(lambda: emitted.append("F7"))
    bar.f8_delete_clicked.connect(lambda: emitted.append("F8"))
    bar.f9_properties_clicked.connect(lambda: emitted.append("F9"))
    bar.f10_quit_clicked.connect(lambda: emitted.append("F10"))

    for btn in bar.buttons:
        btn.click()

    assert emitted == ["F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10"]


def test_properties_dialog_octal_calculation(qapp, tmp_path: Path):
    """Verifies PropertiesDialog octal permissions calculation and rights matrix."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("Sample file content for checksum calculation")
    os.chmod(test_file, 0o644)

    dlg = PropertiesDialog(
        name="test.txt",
        path=str(test_file),
        size=len(test_file.read_bytes()),
        mode=0o644,
        mtime=test_file.stat().st_mtime,
        is_dir=False,
        is_remote=False,
    )

    assert dlg.txt_octal.text() == "0644"
    assert dlg.chk_ur.isChecked() is True  # Owner Read
    assert dlg.chk_uw.isChecked() is True  # Owner Write
    assert dlg.chk_ux.isChecked() is False  # Owner Exec

    # Toggle execute for owner -> 0744
    dlg.chk_ux.setChecked(True)
    assert dlg.txt_octal.text() == "0744"

    # Set via octal input
    dlg.txt_octal.setText("0755")
    assert dlg.chk_gx.isChecked() is True  # Group Exec
    assert dlg.chk_ox.isChecked() is True  # Others Exec


def test_properties_dialog_local_checksum_calculation(qapp, tmp_path: Path):
    """Verifies PropertiesDialog calculates accurate MD5 and SHA-256."""
    import hashlib

    test_file = tmp_path / "checksum_test.dat"
    content = b"WinSCP Parity Test Checksum Payload 123456"
    test_file.write_bytes(content)

    expected_sha256 = hashlib.sha256(content).hexdigest()
    expected_md5 = hashlib.md5(content).hexdigest()

    dlg = PropertiesDialog(
        name="checksum_test.dat",
        path=str(test_file),
        size=len(content),
        mode=0o644,
        mtime=test_file.stat().st_mtime,
        is_dir=False,
        is_remote=False,
    )

    import time

    dlg.cmb_algo.setCurrentText("SHA-256")
    dlg._calculate_hash()
    if hasattr(dlg, "worker") and dlg.worker:
        dlg.worker.wait(2000)
    for _ in range(10):
        qapp.processEvents()
        time.sleep(0.01)
    res = dlg.txt_hash.text()
    assert expected_sha256 == res

    # Switch to MD5
    dlg.cmb_algo.setCurrentText("MD5")
    dlg._calculate_hash()
    if hasattr(dlg, "worker") and dlg.worker:
        dlg.worker.wait(2000)
    for _ in range(10):
        qapp.processEvents()
        time.sleep(0.01)
    res_md5 = dlg.txt_hash.text()
    assert expected_md5 == res_md5


def test_symlink_dialog(qapp):
    """Verifies SymlinkDialog input validation and signal emission."""
    dlg = SymlinkDialog(
        default_target="target_file.txt",
        current_dir="/var/log",
        is_remote=True,
    )

    emitted_links = []
    dlg.link_created.connect(lambda t, n, h: emitted_links.append((t, n, h)))

    dlg.txt_target.setText("/opt/target.so")
    dlg.txt_link_name.setText("target.so")
    dlg.cmb_type.setCurrentIndex(0)  # Symbolic

    dlg._on_accept()
    assert len(emitted_links) == 1
    target, name, is_hard = emitted_links[0]
    assert target == "/opt/target.so"
    assert name == "target.so"
    assert is_hard is False


def test_transfer_options_dialog(qapp):
    """Verifies TransferOptionsDialog configuration."""
    dlg = TransferOptionsDialog(
        current_options={
            "mode": "Binary",
            "preserve_timestamps": True,
            "speed_limit_kb": 2048,
        }
    )

    assert dlg.cmb_mode.currentText() == "Binary"
    assert dlg.chk_preserve_time.isChecked() is True
    assert dlg.spin_speed_limit.value() == 2048
    assert dlg.spin_parallel.value() >= 1

    dlg.cmb_mode.setCurrentText("Text / ASCII")
    dlg.spin_speed_limit.setValue(512)
    dlg.spin_parallel.setValue(4)

    saved = {}
    dlg.options_saved.connect(lambda opts: saved.update(opts))
    dlg._on_accept()

    assert saved["mode"] == "Text / ASCII"
    assert saved["speed_limit_kb"] == 512
    assert saved["max_concurrent"] == 4


def test_folder_single_click_selects_and_double_click_enters(qapp, tmp_path: Path):
    """Verifies that single click only selects folders and strictly does NOT navigate,
    while double-click or Enter key enters the folder, and Backspace navigates up."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from src.ui.main_window import SessionCommanderView

    # Create dummy folder structure
    subfolder = tmp_path / "my_test_subfolder"
    subfolder.mkdir()
    (subfolder / "file_inside.txt").write_text("inside")

    view = SessionCommanderView(initial_local_dir=str(tmp_path))
    initial_dir = view.txt_local_path.text()

    # Find the subfolder row
    subfolder_row = None
    for r in range(view.table_local.rowCount()):
        it = view.table_local.item(r, 0)
        d = it.data(Qt.ItemDataRole.UserRole)
        if d and d.get("name") == "my_test_subfolder":
            subfolder_row = r
            break

    assert subfolder_row is not None, "my_test_subfolder not found in table_local"

    # 1. Single click on the folder
    item = view.table_local.item(subfolder_row, 0)
    rect = view.table_local.visualItemRect(item)
    QTest.mouseClick(view.table_local.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    qapp.processEvents()

    # Verify single click did NOT navigate
    assert view.txt_local_path.text() == initial_dir
    assert view.table_local.get_current_directory() == initial_dir
    assert any(it.row() == subfolder_row for it in view.table_local.selectedItems())

    # 2. Double click on the folder navigates inside
    QTest.mouseDClick(view.table_local.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    qapp.processEvents()

    # Verify double click entered the subfolder
    assert view.txt_local_path.text() == str(subfolder)
    assert view.table_local.get_current_directory() == str(subfolder)

    # 3. Backspace navigates to parent [..]
    QTest.keyClick(view.table_local, Qt.Key.Key_Backspace)
    qapp.processEvents()
    assert view.txt_local_path.text() == initial_dir

    # 4. Enter key navigates into folder
    view.table_local.selectRow(subfolder_row)
    QTest.keyClick(view.table_local, Qt.Key.Key_Return)
    qapp.processEvents()
    assert view.txt_local_path.text() == str(subfolder)


def test_main_window_theme_toggle_action(qapp):
    """Verifies that MainWindow live theme toggle changes theme mode."""
    from src.ui.main_window import MainWindow

    win = MainWindow()
    assert hasattr(win, "tb_theme")
    assert hasattr(win, "toggle_theme")

    win.toggle_theme()
    assert getattr(win, "_current_theme_mode", None) == "dark"

    win.toggle_theme()
    assert getattr(win, "_current_theme_mode", None) == "classic"
    win.close()

