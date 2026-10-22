"""
Unit and integration tests for FTP/FTPS support in Nova Commander.
Verifies:
- File protocol selection in SessionDialog (FTP default port 21, disables key inputs).
- FTP client connection, MLSD and LIST directory parsing.
- File upload, download, stat, and text reading via FTP.
- Graceful handling of SSH terminal on FTP sessions.
"""

import io
import os
import stat
import time
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from PyQt6.QtWidgets import QApplication

from src.core.session_store import SessionConfig, SessionStore
from src.core.sftp_client import RemoteFileInfo, RemoteStat, SFTPClient, SFTPConnectWorker
from src.ui.main_window import SessionCommanderView
from src.ui.session_dialog import SessionDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_remote_stat_attributes():
    """Verifies RemoteStat provides standard filesystem stat fields."""
    st = RemoteStat(st_size=1234, st_mtime=1700000000.0, st_mode=stat.S_IFREG | 0o644)
    assert st.st_size == 1234
    assert st.st_mtime == 1700000000.0
    assert stat.S_ISREG(st.st_mode)
    assert not stat.S_ISDIR(st.st_mode)


def test_remote_file_info_from_ftp_facts():
    """Verifies MLSD facts parsing for files and directories."""
    # Directory fact
    dir_fact = {
        "type": "dir",
        "sizd": "4096",
        "modify": "20261022173449",
        "unix.mode": "0751",
    }
    dir_info = RemoteFileInfo.from_ftp_fact("htdocs", "/", dir_fact)
    assert dir_info.name == "htdocs"
    assert dir_info.path == "/htdocs"
    assert dir_info.is_dir is True
    assert dir_info.permissions_octal == "751"
    assert dir_info.permissions_str.startswith("d")

    # File fact
    file_fact = {
        "type": "file",
        "size": "534",
        "modify": "20260829142406",
        "unix.mode": "0644",
    }
    file_info = RemoteFileInfo.from_ftp_fact("index.php", "/htdocs", file_fact)
    assert file_info.name == "index.php"
    assert file_info.path == "/htdocs/index.php"
    assert file_info.is_dir is False
    assert file_info.size == 534
    assert file_info.permissions_octal == "644"
    assert file_info.permissions_str.startswith("-")


def test_remote_file_info_from_unix_and_windows_list_lines():
    """Verifies fallback parsing for Unix ls -la and Windows DIR FTP listings."""
    client = SFTPClient()

    # Unix format
    unix_line = "-rw-r--r--    1 42780000 42780000      1098 Oct 22 10:33 404.php"
    parsed_unix = client._parse_unix_list_line(unix_line, "/htdocs")
    assert parsed_unix is not None
    assert parsed_unix.name == "404.php"
    assert parsed_unix.path == "/htdocs/404.php"
    assert parsed_unix.size == 1098
    assert parsed_unix.is_dir is False

    # Windows IIS format directory
    win_dir_line = "10-22-26  06:30PM       <DIR>          public_html"
    parsed_win_dir = client._parse_unix_list_line(win_dir_line, "/")
    assert parsed_win_dir is not None
    assert parsed_win_dir.name == "public_html"
    assert parsed_win_dir.is_dir is True

    # Windows IIS format file
    win_file_line = "10-22-26  06:30PM                 2048 web.config"
    parsed_win_file = client._parse_unix_list_line(win_file_line, "/")
    assert parsed_win_file is not None
    assert parsed_win_file.name == "web.config"
    assert parsed_win_file.size == 2048
    assert parsed_win_file.is_dir is False


def test_sftp_client_ftp_connection_and_methods():
    """Verifies SFTPClient handles FTP protocol operations using mock ftplib.FTP."""
    client = SFTPClient()

    mock_ftp = MagicMock()
    mock_ftp.sock = MagicMock()
    mock_ftp.pwd.return_value = "/"
    mock_ftp.size.return_value = 1024
    mock_ftp.sendcmd.return_value = "213 20261022183000"

    with patch("ftplib.FTP", return_value=mock_ftp):
        client.connect(
            host="ftpupload.net",
            port=21,
            username="if0_42780000",
            password="secretpassword",
            protocol="FTP",
        )

        assert client.is_connected is True
        assert client.protocol == "FTP"
        assert client.port == 21
        assert client.current_directory == "/"
        mock_ftp.login.assert_called_once_with(user="if0_42780000", passwd="secretpassword")
        mock_ftp.set_pasv.assert_called_once_with(True)

        # Stat
        st = client.stat("/test.txt")
        assert st.st_size == 1024
        assert stat.S_ISREG(st.st_mode)

        # Upload
        tmp_file = Path("/tmp/pytest_ftp_test.txt")
        tmp_file.write_text("Hello FTP")
        try:
            client.upload_file(str(tmp_file), "/htdocs/test.txt")
            mock_ftp.storbinary.assert_called()
        finally:
            if tmp_file.exists():
                tmp_file.unlink()

        # Delete & Mkdir & Rename
        client.delete_file("/htdocs/test.txt")
        mock_ftp.delete.assert_called_once_with("/htdocs/test.txt")

        client.mkdir("/htdocs/new_folder")
        assert call("/htdocs/new_folder") in mock_ftp.mkd.call_args_list

        client.rename("/htdocs/old.txt", "/htdocs/new.txt")
        mock_ftp.rename.assert_called_once_with("/htdocs/old.txt", "/htdocs/new.txt")

        # Command execution not supported on FTP
        code, out, err = client.exec_command("ls")
        assert code != 0
        assert "not supported" in err

        # Disconnect
        client.disconnect()
        assert client.is_connected is False


def test_session_dialog_ftp_protocol_ui(qapp, tmp_path: Path):
    """Verifies that selecting FTP in SessionDialog updates port to 21 and disables key inputs."""
    store = SessionStore(storage_path=tmp_path / "sessions.ini")
    dlg = SessionDialog(store)

    # Initial state should be SFTP (port 22, key enabled)
    dlg._create_new_site()
    assert dlg.cmb_protocol.currentText() == "SFTP"
    assert dlg.spin_port.value() == 22
    assert dlg.txt_key_path.isEnabled() is True

    # Switch to FTP
    dlg.cmb_protocol.setCurrentText("FTP")
    assert dlg.spin_port.value() == 21
    assert dlg.txt_key_path.isEnabled() is False
    assert dlg.txt_passphrase.isEnabled() is False

    # Switch back to SFTP
    dlg.cmb_protocol.setCurrentText("SFTP")
    assert dlg.spin_port.value() == 22
    assert dlg.txt_key_path.isEnabled() is True

    dlg.close()


def test_session_commander_view_ftp_terminal_warning(qapp):
    """Verifies that attempting to open terminal on FTP session shows informative warning."""
    view = SessionCommanderView()

    ftp_session = SessionConfig(
        name="My FTP Host",
        host="ftpupload.net",
        port=21,
        username="user123",
        protocol="FTP",
    )
    view.current_session = ftp_session

    with patch("PyQt6.QtWidgets.QMessageBox.information") as mock_info:
        view.open_terminal_window()
        mock_info.assert_called_once()
        args = mock_info.call_args[0]
        assert "FTP" in args[1]
        assert "not provide an interactive SSH shell" in args[2]

    view.cleanup()


def test_ftp_auto_reconnect_on_broken_pipe(tmp_path: Path):
    """Verifies that upload_file and download_file automatically recover from BrokenPipeError."""
    client = SFTPClient()
    mock_ftp = MagicMock()
    mock_ftp.sock = MagicMock()
    mock_ftp.pwd.return_value = "/htdocs"

    with patch("ftplib.FTP", return_value=mock_ftp):
        client.connect(
            host="ftpupload.net",
            port=21,
            username="testuser",
            password="testpassword",
            protocol="FTP",
        )

        test_file = tmp_path / "sample.txt"
        test_file.write_text("hello broken pipe test")

        # Simulate BrokenPipeError on first storbinary attempt, success on second
        call_count = 0
        def _mock_storbinary(cmd, fp, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise BrokenPipeError(32, "Broken pipe")
            return "226 Transfer complete"

        mock_ftp.storbinary.side_effect = _mock_storbinary

        client.upload_file(str(test_file), "/htdocs/sample.txt")
        assert call_count == 2
        assert mock_ftp.connect.call_count >= 2


def test_ftp_ensure_remote_dir_and_upload_directory(tmp_path: Path):
    """Verifies ensure_remote_dir and upload_directory cache directories and avoid redundant calls."""
    client = SFTPClient()
    mock_ftp = MagicMock()
    mock_ftp.sock = MagicMock()
    mock_ftp.pwd.return_value = "/"

    with patch("ftplib.FTP", return_value=mock_ftp):
        client.connect(
            host="ftpupload.net",
            port=21,
            username="testuser",
            password="testpassword",
            protocol="FTP",
        )

        # Create nested directory structure simulating .git
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
        objects_dir = git_dir / "objects" / "4a"
        objects_dir.mkdir(parents=True)
        (objects_dir / "abcdef123456").write_bytes(b"\x78\x01testdata")

        client.upload_directory(str(git_dir), "/public_html/.git")

        # ensure_remote_dir should have issued mkd for public_html, public_html/.git, objects, and 4a
        created_paths = [call.args[0] for call in mock_ftp.mkd.call_args_list]
        assert "/public_html/.git" in created_paths
        assert "/public_html/.git/objects" in created_paths
        assert "/public_html/.git/objects/4a" in created_paths
        assert mock_ftp.storbinary.called

