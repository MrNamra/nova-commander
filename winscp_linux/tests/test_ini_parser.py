"""Unit tests for WinSCP .ini format parsing and session store in session_store.py."""

from pathlib import Path
import pytest
from src.core.session_store import SessionConfig, SessionStore

SAMPLE_WINSCP_INI = """[Sessions\\My_Server]
HostName=192.168.1.100
PortNumber=22
UserName=root
Password=secret123
FSProtocol=0
RemoteDirectory=/var/www
LocalDirectory=/home/user/workspace

[Sessions\\Production/WebServer]
HostName=prod.example.com
PortNumber=2222
UserName=deploy
Password=deployPass!
PublicKeyFile=/home/user/.ssh/id_rsa
FSProtocol=0
RemoteDirectory=/app

[Sessions\\Legacy\\RouterBox]
HostName=192.168.0.1
PortNumber=22
UserName=admin
Password=admin
FSProtocol=1
"""


def test_parse_standard_winscp_ini():
    sessions = SessionStore.parse_ini_content(SAMPLE_WINSCP_INI)
    assert len(sessions) == 3

    # Check My_Server
    s1 = sessions.get("My_Server")
    assert s1 is not None
    assert s1.name == "My_Server"
    assert s1.host == "192.168.1.100"
    assert s1.port == 22
    assert s1.username == "root"
    assert s1.password == "secret123"
    assert s1.protocol == "SFTP"
    assert s1.remote_dir == "/var/www"
    assert s1.local_dir == "/home/user/workspace"

    # Check Production/WebServer with folder
    s2 = sessions.get("Production/WebServer")
    assert s2 is not None
    assert s2.folder == "Production"
    assert s2.name == "WebServer"
    assert s2.port == 2222
    assert s2.private_key == "/home/user/.ssh/id_rsa"
    assert s2.protocol == "SFTP"

    # Check Legacy/RouterBox with SCP protocol
    s3 = sessions.get("Legacy/RouterBox")
    assert s3 is not None
    assert s3.folder == "Legacy"
    assert s3.name == "RouterBox"
    assert s3.protocol == "SCP"


def test_generate_and_reparse():
    cfg1 = SessionConfig(
        name="Alpha",
        host="10.10.10.1",
        port=22,
        username="alpha_user",
        password="alpha_password",
        remote_dir="/home/alpha",
        folder="Cloud",
    )
    cfg2 = SessionConfig(
        name="Beta",
        host="10.10.10.2",
        port=2200,
        username="beta_user",
        private_key="/home/test/.ssh/id_ed25519",
        protocol="SCP",
        remote_dir="/root",
    )

    ini_text = SessionStore.generate_ini_content([cfg1, cfg2])
    assert "[Sessions\\Cloud/Alpha]" in ini_text
    assert "HostName=10.10.10.1" in ini_text
    assert "[Sessions\\Beta]" in ini_text
    assert "PortNumber=2200" in ini_text

    reparsed = SessionStore.parse_ini_content(ini_text)
    assert len(reparsed) == 2
    assert reparsed["Cloud/Alpha"].username == "alpha_user"
    assert reparsed["Beta"].private_key == "/home/test/.ssh/id_ed25519"
    assert reparsed["Beta"].protocol == "SCP"


def test_selective_export(tmp_path: Path):
    store_file = tmp_path / "sessions.ini"
    store = SessionStore(storage_path=store_file)

    store.save_session(SessionConfig(name="ServerA", host="1.1.1.1"))
    store.save_session(SessionConfig(name="ServerB", host="2.2.2.2"))
    store.save_session(SessionConfig(name="ServerC", host="3.3.3.3"))

    # Export only ServerA and ServerC
    exported_ini = store.export_sessions_ini(["ServerA", "ServerC"])
    parsed = SessionStore.parse_ini_content(exported_ini)

    assert len(parsed) == 2
    assert "ServerA" in parsed
    assert "ServerC" in parsed
    assert "ServerB" not in parsed


def test_session_store_crud(tmp_path: Path):
    store_file = tmp_path / "sessions.ini"
    store = SessionStore(storage_path=store_file)

    session = SessionConfig(name="DevBox", host="127.0.0.1", port=2222, username="tester")
    store.save_session(session)

    # Verify memory
    loaded = store.get_session("DevBox")
    assert loaded is not None
    assert loaded.host == "127.0.0.1"

    # Verify persisted on disk
    new_store = SessionStore(storage_path=store_file)
    assert len(new_store.list_sessions()) == 1
    assert new_store.get_session("DevBox").username == "tester"

    # Delete
    assert new_store.delete_session("DevBox") is True
    assert len(new_store.list_sessions()) == 0


def test_import_ini_merging(tmp_path: Path):
    store_file = tmp_path / "sessions.ini"
    store = SessionStore(storage_path=store_file)

    store.save_session(SessionConfig(name="Existing1", host="192.168.1.1"))

    incoming_ini = """[Sessions\\Existing1]
HostName=192.168.1.99
UserName=updated_user

[Sessions\\NewServer]
HostName=10.0.0.1
UserName=new_user
"""

    count = store.import_ini_content(incoming_ini, overwrite=True)
    assert count == 2

    # Verify updated
    existing = store.get_session("Existing1")
    assert existing.host == "192.168.1.99"
    assert existing.username == "updated_user"

    # Verify newly added
    new_s = store.get_session("NewServer")
    assert new_s is not None
    assert new_s.host == "10.0.0.1"


def test_move_session_to_folder(tmp_path: Path):
    """Verifies moving a session into a folder, re-parenting, and returning to root."""
    store_file = tmp_path / "sessions.ini"
    store = SessionStore(storage_path=store_file)

    s = SessionConfig(name="oracle", host="127.0.0.1", port=22, username="admin1")
    store.save_session(s)
    assert store.get_session("oracle") is not None
    assert store.get_session("oracle").folder == ""

    # Move oracle into folder 'aaa'
    moved = store.move_session("oracle", "aaa")
    assert moved is not None
    assert moved.full_path == "aaa/oracle"
    assert moved.folder == "aaa"
    assert store.get_session("aaa/oracle") is not None
    assert store.get_session("oracle", exact_path=True) is None  # Old key removed
    assert "oracle" not in store._sessions

    # Verify on-disk persistence across fresh store reload
    reloaded_store = SessionStore(storage_path=store_file)
    assert reloaded_store.get_session("aaa/oracle") is not None
    assert reloaded_store.get_session("aaa/oracle").username == "admin1"
    assert "aaa" in reloaded_store.get_folders()

    # Move back to root
    reloaded_store.move_session("aaa/oracle", "")
    assert reloaded_store.get_session("oracle") is not None
    assert reloaded_store.get_session("oracle").folder == ""
    assert reloaded_store.get_session("aaa/oracle", exact_path=True) is None


def test_folder_crud_and_empty_folder_persistence(tmp_path: Path):
    """Verifies adding, renaming, and persisting empty folders."""
    store_file = tmp_path / "sessions.ini"
    store = SessionStore(storage_path=store_file)

    # Add empty folder
    assert store.add_folder("aaa") is True
    assert "aaa" in store.get_folders()

    # Persist and reload
    store.save_local()
    reloaded = SessionStore(storage_path=store_file)
    assert "aaa" in reloaded.get_folders()

    # Rename folder
    assert reloaded.rename_folder("aaa", "bbb") is True
    assert "bbb" in reloaded.get_folders()
    assert "aaa" not in reloaded.get_folders()

    # Delete folder
    assert reloaded.delete_folder("bbb") is True
    assert "bbb" not in reloaded.get_folders()
