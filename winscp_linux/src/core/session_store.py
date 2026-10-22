"""
Session management and WinSCP-compatible .ini parser/serializer for WinSCP-Linux.
Maintains backward and forward compatibility with standard WinSCP INI configuration files.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class SessionConfig:
    """Represents a single SSH/SFTP session configuration."""

    name: str
    host: str
    port: int = 22
    username: str = ""
    password: str = ""
    private_key: str = ""
    passphrase: str = ""
    protocol: str = "SFTP"  # SFTP or SCP
    remote_dir: str = "/"
    local_dir: str = field(default_factory=lambda: os.path.expanduser("~"))
    folder: str = ""
    comment: str = ""
    ping_interval: int = 30
    compression: bool = False
    utf8_encoding: str = "Auto"
    sftp_server_cmd: str = ""
    proxy_type: str = "None"
    proxy_host: str = ""
    proxy_port: int = 1080
    proxy_user: str = ""
    proxy_password: str = ""

    @property
    def full_path(self) -> str:
        """Returns Folder/Name path representation."""
        if self.folder and self.folder.strip():
            clean_folder = self.folder.strip().strip("/")
            return f"{clean_folder}/{self.name}"
        return self.name

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> SessionConfig:
        return cls(
            name=data.get("name", "Unnamed"),
            host=data.get("host", ""),
            port=int(data.get("port", 22)),
            username=data.get("username", ""),
            password=data.get("password", ""),
            private_key=data.get("private_key", ""),
            passphrase=data.get("passphrase", ""),
            protocol=data.get("protocol", "SFTP"),
            remote_dir=data.get("remote_dir", "/"),
            local_dir=data.get("local_dir", os.path.expanduser("~")),
            folder=data.get("folder", ""),
            comment=data.get("comment", ""),
            ping_interval=int(data.get("ping_interval", 30)),
            compression=bool(data.get("compression", False)),
            utf8_encoding=str(data.get("utf8_encoding", "Auto")),
            sftp_server_cmd=str(data.get("sftp_server_cmd", "")),
            proxy_type=str(data.get("proxy_type", "None")),
            proxy_host=str(data.get("proxy_host", "")),
            proxy_port=int(data.get("proxy_port", 1080)),
            proxy_user=str(data.get("proxy_user", "")),
            proxy_password=str(data.get("proxy_password", "")),
        )


class SessionStore:
    """
    Manages saved sessions and serializes/deserializes standard WinSCP .ini files.
    """

    def __init__(self, storage_path: Optional[Path | str] = None):
        if storage_path:
            self.storage_path = Path(storage_path)
        else:
            self.storage_path = self.get_default_storage_path()

        self._sessions: Dict[str, SessionConfig] = {}
        self._folders: set[str] = set()
        self.load_local()

    @staticmethod
    def get_default_storage_path() -> Path:
        """Returns standard ~/.config/winscp-linux/sessions.ini path."""
        config_dir = Path.home() / ".config" / "winscp-linux"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "sessions.ini"

    def list_sessions(self) -> List[SessionConfig]:
        """Returns list of all saved sessions sorted by full path."""
        return sorted(self._sessions.values(), key=lambda s: s.full_path.lower())

    def get_session(self, full_or_simple_name: str, exact_path: bool = False) -> Optional[SessionConfig]:
        """Retrieves session by full_path or simple name."""
        if full_or_simple_name in self._sessions:
            return self._sessions[full_or_simple_name]

        if exact_path:
            return None

        for s in self._sessions.values():
            if s.name == full_or_simple_name or s.full_path == full_or_simple_name:
                return s
        return None

    def save_session(self, session: SessionConfig) -> None:
        """Adds or updates a session and persists to disk."""
        key = session.full_path
        self._sessions[key] = session
        if session.folder.strip():
            self._folders.add(session.folder.strip())
        self.save_local()

    def delete_session(self, full_or_simple_name: str) -> bool:
        """Deletes a session by key or simple name."""
        target_key = None
        if full_or_simple_name in self._sessions:
            target_key = full_or_simple_name
        else:
            for key, s in self._sessions.items():
                if s.name == full_or_simple_name or s.full_path == full_or_simple_name:
                    target_key = key
                    break

        if target_key:
            del self._sessions[target_key]
            self.save_local()
            return True
        return False

    def clear(self) -> None:
        """Clears all sessions in memory and deletes or clears file."""
        self._sessions.clear()
        self._folders.clear()
        self.save_local()

    def get_folders(self) -> List[str]:
        """Returns sorted list of unique folder names."""
        folders = set(self._folders)
        for s in self._sessions.values():
            if s.folder.strip():
                folders.add(s.folder.strip())
        return sorted(folders)

    def add_folder(self, folder_name: str) -> bool:
        """Adds a new folder name and persists."""
        clean = folder_name.strip().strip("/")
        if not clean:
            return False
        self._folders.add(clean)
        self.save_local()
        return True

    def delete_folder(self, folder_name: str, delete_sessions: bool = False) -> bool:
        """Deletes a folder and optionally all its contained sessions."""
        clean = folder_name.strip().strip("/")
        if not clean:
            return False
        self._folders.discard(clean)
        for key, s in list(self._sessions.items()):
            if s.folder == clean or s.folder.startswith(clean + "/"):
                if delete_sessions:
                    del self._sessions[key]
                else:
                    del self._sessions[key]
                    s.folder = ""
                    self._sessions[s.full_path] = s
        self.save_local()
        return True

    def rename_folder(self, old_folder: str, new_folder: str) -> bool:
        """Renames a folder and re-parents all contained sessions."""
        old_c = old_folder.strip().strip("/")
        new_c = new_folder.strip().strip("/")
        if not old_c or not new_c or old_c == new_c:
            return False
        self._folders.discard(old_c)
        self._folders.add(new_c)
        for key, s in list(self._sessions.items()):
            if s.folder == old_c:
                del self._sessions[key]
                s.folder = new_c
                self._sessions[s.full_path] = s
            elif s.folder.startswith(old_c + "/"):
                del self._sessions[key]
                s.folder = new_c + s.folder[len(old_c):]
                self._sessions[s.full_path] = s
        self.save_local()
        return True

    def move_session(self, session_or_key: str | SessionConfig, new_folder: str) -> Optional[SessionConfig]:
        """Moves a session to a target folder (or empty string for root)."""
        if isinstance(session_or_key, SessionConfig):
            session = session_or_key
            old_key = session.full_path
        else:
            old_key = session_or_key
            session = self.get_session(old_key)

        if not session:
            return None

        clean_folder = new_folder.strip().strip("/")

        # Clean up dummy placeholder in target folder if it exists
        if clean_folder:
            dummy_key = f"{clean_folder}/Example"
            if dummy_key in self._sessions:
                dummy = self._sessions[dummy_key]
                if dummy.host == "127.0.0.1" and not dummy.username and not dummy.password:
                    del self._sessions[dummy_key]

        # Remove old key
        if old_key in self._sessions:
            del self._sessions[old_key]
        else:
            for k, s in list(self._sessions.items()):
                if s is session or s.full_path == old_key:
                    del self._sessions[k]
                    break

        session.folder = clean_folder
        self._sessions[session.full_path] = session
        if clean_folder:
            self._folders.add(clean_folder)

        self.save_local()
        return session

    def rename_session(self, old_full_path: str, new_name: str) -> Optional[SessionConfig]:
        """Renames a session preserving its folder location."""
        clean_name = new_name.strip().strip("/")
        if not clean_name:
            return None
        session = self.get_session(old_full_path)
        if not session:
            return None
        if old_full_path in self._sessions:
            del self._sessions[old_full_path]
        session.name = clean_name
        self._sessions[session.full_path] = session
        self.save_local()
        return session

    def duplicate_session(self, full_path: str) -> Optional[SessionConfig]:
        """Creates a duplicate copy of an existing session."""
        orig = self.get_session(full_path)
        if not orig:
            return None
        data = orig.to_dict()
        data["name"] = f"{orig.name} (Copy)"
        copy_cfg = SessionConfig.from_dict(data)
        self.save_session(copy_cfg)
        return copy_cfg

    # -------------------------------------------------------------------------
    # WinSCP .ini Parsing & Serialization
    # -------------------------------------------------------------------------

    @staticmethod
    def parse_ini_content(ini_content: str) -> Dict[str, SessionConfig]:
        """
        Parses INI content string according to WinSCP [Sessions\\Session_Name] specification.
        """
        # We use ConfigParser with RawConfigParser options and case preservation
        parser = configparser.RawConfigParser()
        parser.optionxform = str  # preserve case for keys
        parser.read_string(ini_content)

        parsed_sessions: Dict[str, SessionConfig] = {}

        for section in parser.sections():
            # Check if section starts with Sessions\ or Sessions/
            prefix = ""
            if section.startswith("Sessions\\"):
                prefix = "Sessions\\"
            elif section.startswith("Sessions/"):
                prefix = "Sessions/"
            else:
                continue

            session_id = section[len(prefix) :].strip()
            if not session_id:
                continue

            if parser.get(section, "IsFolder", fallback="0") == "1":
                continue

            # Check if folder is embedded in the session name (e.g., "WebServers/Prod1")
            folder = ""
            name = session_id
            if "/" in session_id:
                folder, name = session_id.rsplit("/", 1)
            elif "\\" in session_id:
                folder, name = session_id.rsplit("\\", 1)

            # WinSCP INI field mapping
            host = parser.get(section, "HostName", fallback="")
            port_val = parser.get(section, "PortNumber", fallback="22")
            try:
                port = int(port_val)
            except ValueError:
                port = 22

            username = parser.get(section, "UserName", fallback="")
            password = parser.get(section, "Password", fallback="")
            private_key = parser.get(
                section, "PublicKeyFile", fallback=parser.get(section, "PrivateKeyFile", fallback="")
            )

            # Protocol in WinSCP: FSProtocol 0=SFTP, 1=SCP, 5=FTP, etc.
            fsprotocol = parser.get(section, "FSProtocol", fallback="")
            protocol_str = parser.get(section, "Protocol", fallback="")
            if fsprotocol == "1" or protocol_str.upper() == "SCP":
                protocol = "SCP"
            elif fsprotocol in ("2", "5") or protocol_str.upper() in ("FTP", "FTPS"):
                protocol = protocol_str.upper() if protocol_str.upper() in ("FTP", "FTPS") else "FTP"
            else:
                protocol = "SFTP"

            remote_dir = parser.get(section, "RemoteDirectory", fallback="/")
            local_dir = parser.get(section, "LocalDirectory", fallback=os.path.expanduser("~"))
            comment = parser.get(section, "Comment", fallback="")
            passphrase = parser.get(section, "Passphrase", fallback="")

            ping_val = parser.get(section, "PingInterval", fallback="30")
            try:
                ping_interval = int(ping_val)
            except ValueError:
                ping_interval = 30

            compression = parser.get(section, "Compression", fallback="0") in ("1", "True", "true")
            utf8_encoding = parser.get(section, "Utf", fallback="Auto")
            sftp_server_cmd = parser.get(section, "SftpServer", fallback="")
            proxy_type = parser.get(section, "ProxyMethod", fallback="None")
            proxy_host = parser.get(section, "ProxyHost", fallback="")
            proxy_port_val = parser.get(section, "ProxyPort", fallback="1080")
            try:
                proxy_port = int(proxy_port_val)
            except ValueError:
                proxy_port = 1080
            proxy_user = parser.get(section, "ProxyUsername", fallback="")
            proxy_password = parser.get(section, "ProxyPassword", fallback="")

            cfg = SessionConfig(
                name=name,
                host=host,
                port=port,
                username=username,
                password=password,
                private_key=private_key,
                passphrase=passphrase,
                protocol=protocol,
                remote_dir=remote_dir if remote_dir else "/",
                local_dir=local_dir if local_dir else os.path.expanduser("~"),
                folder=folder,
                comment=comment,
                ping_interval=ping_interval,
                compression=compression,
                utf8_encoding=utf8_encoding,
                sftp_server_cmd=sftp_server_cmd,
                proxy_type=proxy_type,
                proxy_host=proxy_host,
                proxy_port=proxy_port,
                proxy_user=proxy_user,
                proxy_password=proxy_password,
            )
            parsed_sessions[cfg.full_path] = cfg

        return parsed_sessions

    @staticmethod
    def generate_ini_content(sessions: List[SessionConfig], folders: Optional[List[str]] = None) -> str:
        """
        Generates standard WinSCP INI content strictly formatted:
        [Sessions\\FolderName/SessionName]
        HostName=...
        PortNumber=22
        UserName=...
        Password=...
        FSProtocol=0
        PublicKeyFile=...
        RemoteDirectory=...
        LocalDirectory=...
        """
        lines: List[str] = [
            "; WinSCP-Linux Configuration Export",
            "; Generated by WinSCP-Linux",
            "",
        ]

        # Emit explicit folders that have no sessions
        existing_session_folders = {s.folder.strip() for s in sessions if s.folder.strip()}
        if folders:
            for f in sorted(folders):
                clean_f = f.strip().strip("/")
                if clean_f and clean_f not in existing_session_folders:
                    lines.append(f"[Sessions\\{clean_f}]")
                    lines.append("IsFolder=1")
                    lines.append("")

        for s in sorted(sessions, key=lambda x: x.full_path.lower()):
            section_name = f"Sessions\\{s.full_path}"
            lines.append(f"[{section_name}]")
            lines.append(f"HostName={s.host}")
            lines.append(f"PortNumber={s.port}")
            lines.append(f"UserName={s.username}")
            if s.password:
                lines.append(f"Password={s.password}")
            if s.private_key:
                lines.append(f"PublicKeyFile={s.private_key}")
            if s.passphrase:
                lines.append(f"Passphrase={s.passphrase}")
            # WinSCP FSProtocol: 0 = SFTP, 1 = SCP, 5 = FTP
            if s.protocol.upper() == "SCP":
                fsprotocol = 1
            elif s.protocol.upper() in ("FTP", "FTPS"):
                fsprotocol = 5
            else:
                fsprotocol = 0
            lines.append(f"FSProtocol={fsprotocol}")
            lines.append(f"Protocol={s.protocol}")
            if s.remote_dir:
                lines.append(f"RemoteDirectory={s.remote_dir}")
            if s.local_dir:
                lines.append(f"LocalDirectory={s.local_dir}")
            if s.comment:
                lines.append(f"Comment={s.comment}")
            if s.ping_interval != 30:
                lines.append(f"PingInterval={s.ping_interval}")
            if s.compression:
                lines.append("Compression=1")
            if s.utf8_encoding and s.utf8_encoding != "Auto":
                lines.append(f"Utf={s.utf8_encoding}")
            if s.sftp_server_cmd:
                lines.append(f"SftpServer={s.sftp_server_cmd}")
            if s.proxy_type and s.proxy_type != "None":
                lines.append(f"ProxyMethod={s.proxy_type}")
                if s.proxy_host:
                    lines.append(f"ProxyHost={s.proxy_host}")
                if s.proxy_port:
                    lines.append(f"ProxyPort={s.proxy_port}")
                if s.proxy_user:
                    lines.append(f"ProxyUsername={s.proxy_user}")
                if s.proxy_password:
                    lines.append(f"ProxyPassword={s.proxy_password}")
            lines.append("")

        return "\n".join(lines)

    def export_sessions_ini(self, selected_keys: Optional[List[str]] = None) -> str:
        """
        Exports all or selected sessions as a WinSCP INI string.
        If selected_keys is None or empty, all sessions are exported.
        """
        if not selected_keys:
            sessions_to_export = list(self._sessions.values())
        else:
            sessions_to_export = []
            selected_set = set(selected_keys)
            for s in self._sessions.values():
                if s.full_path in selected_set or s.name in selected_set:
                    sessions_to_export.append(s)

        return self.generate_ini_content(sessions_to_export, folders=list(self._folders))

    def import_ini_content(self, ini_content: str, overwrite: bool = True) -> int:
        """
        Parses INI content and merges imported sessions into local store.
        Returns count of newly added or updated sessions.
        """
        imported = self.parse_ini_content(ini_content)
        count = 0
        for key, s in imported.items():
            if overwrite or key not in self._sessions:
                self._sessions[key] = s
                count += 1

        if count > 0:
            self.save_local()
        return count

    # -------------------------------------------------------------------------
    # Disk Persistence
    # -------------------------------------------------------------------------

    def load_local(self) -> None:
        """Loads sessions and folders from local storage INI file."""
        if not self.storage_path.exists():
            return

        try:
            content = self.storage_path.read_text(encoding="utf-8")
            self._sessions = self.parse_ini_content(content)
            for s in self._sessions.values():
                if s.folder.strip():
                    self._folders.add(s.folder.strip())

            # Load explicit folders marked with IsFolder=1
            parser = configparser.RawConfigParser()
            parser.optionxform = str
            parser.read_string(content)
            for section in parser.sections():
                if parser.get(section, "IsFolder", fallback="0") == "1":
                    sec_name = section
                    for p in ("Sessions\\", "Sessions/"):
                        if sec_name.startswith(p):
                            sec_name = sec_name[len(p):]
                            break
                    if sec_name.strip():
                        self._folders.add(sec_name.strip())
        except Exception:
            self._sessions = {}

    def save_local(self) -> None:
        """Saves all current sessions and folders to local storage INI file."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        ini_text = self.generate_ini_content(list(self._sessions.values()), folders=list(self._folders))
        self.storage_path.write_text(ini_text, encoding="utf-8")
