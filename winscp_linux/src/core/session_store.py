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

    def get_session(self, full_or_simple_name: str) -> Optional[SessionConfig]:
        """Retrieves session by full_path or simple name."""
        if full_or_simple_name in self._sessions:
            return self._sessions[full_or_simple_name]

        for s in self._sessions.values():
            if s.name == full_or_simple_name or s.full_path == full_or_simple_name:
                return s
        return None

    def save_session(self, session: SessionConfig) -> None:
        """Adds or updates a session and persists to disk."""
        key = session.full_path
        self._sessions[key] = session
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
        self.save_local()

    def get_folders(self) -> List[str]:
        """Returns sorted list of unique folder names."""
        folders = set()
        for s in self._sessions.values():
            if s.folder.strip():
                folders.add(s.folder.strip())
        return sorted(folders)

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

            # Protocol in WinSCP: FSProtocol 0=SFTP, 1=SCP, 2=FTP, etc.
            fsprotocol = parser.get(section, "FSProtocol", fallback="")
            protocol_str = parser.get(section, "Protocol", fallback="")
            if fsprotocol == "1" or protocol_str.upper() == "SCP":
                protocol = "SCP"
            else:
                protocol = "SFTP"

            remote_dir = parser.get(section, "RemoteDirectory", fallback="/")
            local_dir = parser.get(section, "LocalDirectory", fallback=os.path.expanduser("~"))
            comment = parser.get(section, "Comment", fallback="")
            passphrase = parser.get(section, "Passphrase", fallback="")

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
            )
            parsed_sessions[cfg.full_path] = cfg

        return parsed_sessions

    @staticmethod
    def generate_ini_content(sessions: List[SessionConfig]) -> str:
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
            # WinSCP FSProtocol: 0 = SFTP, 1 = SCP
            fsprotocol = 1 if s.protocol.upper() == "SCP" else 0
            lines.append(f"FSProtocol={fsprotocol}")
            lines.append(f"Protocol={s.protocol}")
            if s.remote_dir:
                lines.append(f"RemoteDirectory={s.remote_dir}")
            if s.local_dir:
                lines.append(f"LocalDirectory={s.local_dir}")
            if s.comment:
                lines.append(f"Comment={s.comment}")
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

        return self.generate_ini_content(sessions_to_export)

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
        """Loads sessions from local storage INI file."""
        if not self.storage_path.exists():
            return

        try:
            content = self.storage_path.read_text(encoding="utf-8")
            self._sessions = self.parse_ini_content(content)
        except Exception:
            self._sessions = {}

    def save_local(self) -> None:
        """Saves all current sessions to local storage INI file."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        ini_text = self.generate_ini_content(list(self._sessions.values()))
        self.storage_path.write_text(ini_text, encoding="utf-8")
