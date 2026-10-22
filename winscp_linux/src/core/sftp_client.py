"""
Paramiko-based SFTP Client and background QThread workers for WinSCP-Linux.
Ensures network operations never freeze the Qt GUI thread.
"""

from __future__ import annotations

import ftplib
import io
import os
import re
import socket
import stat
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import paramiko
from PyQt6.QtCore import QObject, QThread, pyqtSignal


@dataclass
class RemoteStat:
    """Attributes mimicking paramiko.SFTPAttributes for non-SFTP protocols (FTP/FTPS)."""

    st_size: int = 0
    st_mtime: float = 0.0
    st_mode: int = 0o644
    st_uid: int = 0
    st_gid: int = 0
    st_atime: float = 0.0


@dataclass
class RemoteFileInfo:
    """Detailed file/folder metadata from remote SFTP or FTP server."""

    name: str
    path: str
    size: int
    mode: int
    permissions_octal: str
    permissions_str: str
    modified_time: float
    modified_str: str
    is_dir: bool
    is_symlink: bool
    uid: int = 0
    gid: int = 0

    @classmethod
    def from_sftp_attr(cls, attr: paramiko.SFTPAttributes, name: str, parent_path: str) -> RemoteFileInfo:
        clean_parent = parent_path.rstrip("/")
        full_path = f"{clean_parent}/{name}" if clean_parent else f"/{name}"
        if not full_path.startswith("/"):
            full_path = f"/{full_path}"

        mode = attr.st_mode if attr.st_mode is not None else 0
        is_dir = stat.S_ISDIR(mode)
        is_symlink = stat.S_ISLNK(mode)

        octal_perm = oct(stat.S_IMODE(mode))[-3:].zfill(3)
        file_mode_str = stat.filemode(mode)

        mtime = float(attr.st_mtime) if attr.st_mtime is not None else 0.0
        try:
            mtime_dt = datetime.fromtimestamp(mtime)
            mtime_str = mtime_dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            mtime_str = "Unknown"

        size = int(attr.st_size) if attr.st_size is not None else 0

        return cls(
            name=name,
            path=full_path,
            size=size,
            mode=mode,
            permissions_octal=octal_perm,
            permissions_str=file_mode_str,
            modified_time=mtime,
            modified_str=mtime_str,
            is_dir=is_dir,
            is_symlink=is_symlink,
            uid=attr.st_uid if attr.st_uid is not None else 0,
            gid=attr.st_gid if attr.st_gid is not None else 0,
        )

    @classmethod
    def from_ftp_fact(cls, name: str, parent_path: str, facts: dict) -> RemoteFileInfo:
        clean_parent = parent_path.rstrip("/")
        full_path = f"{clean_parent}/{name}" if clean_parent else f"/{name}"
        if not full_path.startswith("/"):
            full_path = f"/{full_path}"

        ftype = facts.get("type", "file").lower()
        is_dir = ftype in ("dir", "pdir", "cdir")
        is_symlink = ftype == "os.unix=slink"
        size = int(facts.get("size", facts.get("sizd", 0))) if not is_dir else 0

        mod_str = facts.get("modify", "")
        mtime = 0.0
        display_mtime = ""
        if mod_str:
            try:
                dt = datetime.strptime(mod_str[:14], "%Y%m%d%H%M%S")
                mtime = dt.timestamp()
                display_mtime = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        mode_val = facts.get("unix.mode", "")
        if mode_val:
            try:
                raw_mode = int(mode_val, 8)
                mode = (stat.S_IFDIR if is_dir else stat.S_IFREG) | stat.S_IMODE(raw_mode)
                octal_perm = oct(stat.S_IMODE(mode))[-3:].zfill(3)
                file_mode_str = stat.filemode(mode)
            except Exception:
                mode = stat.S_IFDIR | 0o755 if is_dir else stat.S_IFREG | 0o644
                octal_perm = "755" if is_dir else "644"
                file_mode_str = "drwxr-xr-x" if is_dir else "-rw-r--r--"
        else:
            mode = stat.S_IFDIR | 0o755 if is_dir else stat.S_IFREG | 0o644
            octal_perm = "755" if is_dir else "644"
            file_mode_str = "drwxr-xr-x" if is_dir else "-rw-r--r--"

        uid = int(facts.get("unix.uid", 0)) if str(facts.get("unix.uid", "")).isdigit() else 0
        gid = int(facts.get("unix.gid", 0)) if str(facts.get("unix.gid", "")).isdigit() else 0

        return cls(
            name=name,
            path=full_path,
            size=size,
            mode=mode,
            permissions_octal=octal_perm,
            permissions_str=file_mode_str,
            modified_time=mtime,
            modified_str=display_mtime,
            is_dir=is_dir,
            is_symlink=is_symlink,
            uid=uid,
            gid=gid,
        )


class SFTPClient:
    """
    Unified Remote Client wrapping Paramiko SFTP/SSH and Python ftplib.FTP/FTP_TLS.
    Supports connecting seamlessly to SFTP, SCP, standard FTP, and FTPS servers.
    """

    def __init__(self):
        self._ssh: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None
        self._ftp: Optional[ftplib.FTP] = None
        self._protocol: str = "SFTP"
        self._current_directory: str = "/"
        self._session_info: dict = {}
        self._lock = threading.RLock()

    @property
    def is_connected(self) -> bool:
        if self._protocol in ("FTP", "FTPS"):
            return bool(self._session_info.get("host")) and (
                (self._ftp is not None and getattr(self._ftp, "sock", None) is not None)
                or bool(self._session_info.get("password") or self._session_info.get("username"))
            )
        if self._ssh and self._sftp:
            transport = self._ssh.get_transport()
            return transport is not None and transport.is_active()
        return False

    @property
    def protocol(self) -> str:
        return getattr(self, "_protocol", "SFTP")

    @property
    def current_directory(self) -> str:
        return self._current_directory

    @property
    def ssh_client(self) -> Optional[paramiko.SSHClient]:
        return self._ssh

    @property
    def ftp_client(self) -> Optional[ftplib.FTP]:
        return self._ftp

    @property
    def host(self) -> str:
        return self._session_info.get("host", "")

    @property
    def port(self) -> int:
        default_p = 21 if self._protocol in ("FTP", "FTPS") else 22
        return self._session_info.get("port", default_p)

    @property
    def username(self) -> str:
        return self._session_info.get("username", "")

    def connect(
        self,
        host: str,
        port: int = 22,
        username: str = "",
        password: str = "",
        private_key_path: str = "",
        passphrase: Optional[str] = None,
        timeout: float = 12.0,
        keepalive_interval: int = 30,
        compression: bool = False,
        protocol: str = "SFTP",
    ) -> None:
        """
        Establishes connection to remote server via SFTP (SSH) or standard FTP/FTPS.
        """
        self.disconnect()

        proto = (protocol or "SFTP").upper()
        self._protocol = proto

        # ================= FTP / FTPS Connection =================
        if proto in ("FTP", "FTPS"):
            ftp_port = int(port) if port else 21
            if proto == "FTPS":
                ftp = ftplib.FTP_TLS(timeout=timeout)
            else:
                ftp = ftplib.FTP(timeout=timeout)

            try:
                ftp.connect(host, ftp_port, timeout=timeout)
                if proto == "FTPS":
                    ftp.auth()
                ftp.login(user=username or "anonymous", passwd=password or "")
                if proto == "FTPS" and isinstance(ftp, ftplib.FTP_TLS):
                    ftp.prot_p()
                ftp.set_pasv(True)
                try:
                    ftp.encoding = "utf-8"
                except Exception:
                    pass
            except Exception as e:
                try:
                    ftp.close()
                except Exception:
                    pass
                raise ConnectionError(f"Failed to connect to {host}:{ftp_port} via {proto} - {e}") from e

            self._ftp = ftp
            self._session_info = {
                "host": host,
                "port": ftp_port,
                "username": username,
                "password": password,
                "protocol": proto,
                "timeout": timeout,
            }
            if hasattr(self, "_known_remote_dirs"):
                self._known_remote_dirs.clear()
            try:
                pwd = ftp.pwd()
                self._current_directory = pwd if pwd else "/"
            except Exception:
                self._current_directory = "/"
            return

        # ================= Standard SSH / SFTP Connection =================
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        pkey = None
        if private_key_path and os.path.exists(private_key_path):
            key_classes = [
                paramiko.RSAKey,
                paramiko.Ed25519Key,
                paramiko.ECDSAKey,
                paramiko.DSSKey,
            ]
            key_load_err = None
            for key_cls in key_classes:
                try:
                    pkey = key_cls.from_private_key_file(private_key_path, password=passphrase or None)
                    break
                except (paramiko.PasswordRequiredException, paramiko.ssh_exception.PasswordRequiredException) as pe:
                    key_load_err = pe
                    break
                except Exception as e:
                    key_load_err = e
                    continue

            if pkey is None and isinstance(key_load_err, (paramiko.PasswordRequiredException, paramiko.ssh_exception.PasswordRequiredException)):
                raise ConnectionError("Private key is encrypted and requires a passphrase.") from key_load_err

            if pkey is None:
                # Try OpenSSH format
                try:
                    pkey = paramiko.PKey.from_path(private_key_path, password=passphrase or None)
                except Exception:
                    pass

        connect_kwargs = {
            "hostname": host,
            "port": int(port) if port else 22,
            "username": username or None,
            "timeout": timeout,
            "look_for_keys": False,
            "allow_agent": False,
            "compress": compression,
        }

        if pkey:
            connect_kwargs["pkey"] = pkey
            if password:
                connect_kwargs["password"] = password
        elif password:
            connect_kwargs["password"] = password

        try:
            ssh.connect(**connect_kwargs)
            transport = ssh.get_transport()
            if transport and keepalive_interval > 0:
                transport.set_keepalive(keepalive_interval)
            sftp = ssh.open_sftp()
        except Exception as e:
            ssh.close()
            raise ConnectionError(f"Failed to connect to {host}:{port} via {proto} - {e}") from e

        self._ssh = ssh
        self._sftp = sftp
        self._session_info = {
            "host": host,
            "port": port,
            "username": username,
            "password": password,
            "private_key_path": private_key_path,
            "passphrase": passphrase,
            "timeout": timeout,
            "keepalive_interval": keepalive_interval,
            "compression": compression,
            "protocol": proto,
        }

        # Resolve initial default directory
        try:
            home = sftp.normalize(".")
            self._current_directory = home if home else "/"
        except Exception:
            self._current_directory = "/"

    def disconnect(self) -> None:
        """Closes active SFTP, SSH, or FTP connections."""
        with self._lock:
            self._session_info.clear()
            if hasattr(self, "_known_remote_dirs"):
                self._known_remote_dirs.clear()

            if self._ftp:
                try:
                    self._ftp.quit()
                except Exception:
                    try:
                        self._ftp.close()
                    except Exception:
                        pass
                self._ftp = None

            if self._sftp:
                try:
                    self._sftp.close()
                except Exception:
                    pass
                self._sftp = None

            if self._ssh:
                try:
                    self._ssh.close()
                except Exception:
                    pass
                self._ssh = None

    def _reconnect_ftp(self) -> None:
        """Silently re-establishes FTP/FTPS connection if lost."""
        with self._lock:
            if not self._session_info or self._protocol not in ("FTP", "FTPS"):
                raise ConnectionError("No active FTP session parameters available to reconnect.")

            if self._ftp:
                try:
                    self._ftp.close()
                except Exception:
                    pass
                self._ftp = None

            host = self._session_info.get("host", "")
            port = self._session_info.get("port", 21)
            username = self._session_info.get("username", "")
            password = self._session_info.get("password", "")
            proto = self._session_info.get("protocol", "FTP")
            timeout = self._session_info.get("timeout", 15.0)

            if proto == "FTPS":
                ftp = ftplib.FTP_TLS(timeout=timeout)
            else:
                ftp = ftplib.FTP(timeout=timeout)

            ftp.connect(host, port, timeout=timeout)
            if proto == "FTPS":
                ftp.auth()
            ftp.login(user=username or "anonymous", passwd=password or "")
            if proto == "FTPS" and isinstance(ftp, ftplib.FTP_TLS):
                ftp.prot_p()
            ftp.set_pasv(True)
            try:
                ftp.encoding = "utf-8"
            except Exception:
                pass

            self._ftp = ftp
            cur = self._current_directory
            if cur and cur != "/":
                try:
                    ftp.cwd(cur)
                except Exception:
                    pass

    def _ensure_ftp_connected(self) -> None:
        """Verifies that the FTP socket is active, auto-reconnecting on timeout or broken pipe."""
        if self._protocol not in ("FTP", "FTPS"):
            return

        need_reconnect = False
        if not self._ftp or getattr(self._ftp, "sock", None) is None:
            need_reconnect = True
        else:
            try:
                self._ftp.voidcmd("NOOP")
            except Exception:
                need_reconnect = True

        if need_reconnect:
            self._reconnect_ftp()

    def clone_client(self) -> Optional["SFTPClient"]:
        """Creates an independent connected client instance sharing session credentials."""
        with self._lock:
            if not self._session_info:
                return None
            info = dict(self._session_info)
        try:
            client = SFTPClient()
            client.connect(**info)
            client._is_clone = True
            return client
        except Exception:
            return None

    def get_worker_client(self) -> "SFTPClient":
        """
        Returns a dedicated client or SFTP channel for parallel/concurrent worker transfers.
        Falls back to self if cloning is not possible.
        """
        if not self.is_connected and self._protocol not in ("FTP", "FTPS"):
            return self

        if self._protocol in ("FTP", "FTPS"):
            clone = self.clone_client()
            if clone:
                clone._is_worker = True
                return clone
            return self

        elif self._protocol == "SFTP" and self._ssh:
            transport = self._ssh.get_transport()
            if transport and transport.is_active():
                try:
                    sftp_chan = self._ssh.open_sftp()
                    worker = SFTPClient()
                    worker._protocol = "SFTP"
                    worker._ssh = self._ssh
                    worker._sftp = sftp_chan
                    worker._session_info = dict(self._session_info)
                    worker._is_worker = True
                    return worker
                except Exception:
                    pass
            clone = self.clone_client()
            if clone:
                clone._is_worker = True
                return clone

        return self

    def close_worker(self):
        """Closes a worker client or secondary SFTP channel without closing the primary session."""
        if not getattr(self, "_is_worker", False):
            return
        if getattr(self, "_sftp", None):
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None
        if getattr(self, "_is_clone", False) and getattr(self, "_ssh", None):
            try:
                self._ssh.close()
            except Exception:
                pass
            self._ssh = None
        if getattr(self, "_ftp", None):
            try:
                self._ftp.quit()
            except Exception:
                try:
                    self._ftp.close()
                except Exception:
                    pass
            self._ftp = None

    def ensure_remote_dir(self, remote_dir: str) -> None:
        """
        Recursively creates remote directory if it does not already exist.
        Uses in-memory cache to eliminate redundant FTP/SFTP roundtrips.
        """
        clean_dir = remote_dir.rstrip("/")
        if not clean_dir or clean_dir == "/":
            return

        if not hasattr(self, "_known_remote_dirs"):
            self._known_remote_dirs = set()

        if clean_dir in self._known_remote_dirs:
            return

        # Ensure parent directory first
        parent = str(Path(clean_dir).parent)
        if parent and parent not in ("/", "."):
            self.ensure_remote_dir(parent)

        if self._protocol in ("FTP", "FTPS"):
            with self._lock:
                self._ensure_ftp_connected()
                if self._ftp:
                    try:
                        self._ftp.mkd(clean_dir)
                    except Exception:
                        pass
                self._known_remote_dirs.add(clean_dir)
        elif self._sftp:
            try:
                self._sftp.stat(clean_dir)
            except Exception:
                try:
                    self._sftp.mkdir(clean_dir)
                except Exception:
                    pass
            self._known_remote_dirs.add(clean_dir)


    def listdir(self, remote_dir: Optional[str] = None) -> List[RemoteFileInfo]:
        """
        Lists files in remote directory with detailed attributes.
        Includes '..' entry if not at root '/'.
        Works seamlessly for both SFTP and FTP.
        """
        if not self.is_connected:
            raise RuntimeError(f"{self._protocol} client is not connected.")

        target_dir = remote_dir or self._current_directory
        if self._protocol in ("FTP", "FTPS") and self._ftp:
            return self._listdir_ftp(target_dir)

        # SFTP logic
        try:
            target_dir = self._sftp.normalize(target_dir)
        except Exception:
            pass

        self._current_directory = target_dir

        try:
            raw_attrs = self._sftp.listdir_attr(target_dir)
        except Exception as e:
            raise IOError(f"Could not list directory '{target_dir}': {e}") from e

        items: List[RemoteFileInfo] = []

        # If not at root, add '..' parent directory item
        if target_dir != "/":
            parent_path = str(Path(target_dir).parent)
            if not parent_path.startswith("/"):
                parent_path = "/" + parent_path
            parent_item = RemoteFileInfo(
                name="..",
                path=parent_path,
                size=0,
                mode=stat.S_IFDIR | 0o755,
                permissions_octal="755",
                permissions_str="drwxr-xr-x",
                modified_time=time.time(),
                modified_str="",
                is_dir=True,
                is_symlink=False,
            )
            items.append(parent_item)

        for attr in raw_attrs:
            if attr.filename in (".", ".."):
                continue
            item = RemoteFileInfo.from_sftp_attr(attr, attr.filename, target_dir)
            items.append(item)

        # Sort: directories first (parent '..' first), then case-insensitive alphabetically
        def sort_key(item: RemoteFileInfo):
            if item.name == "..":
                return (0, "")
            return (1 if not item.is_dir else 0, item.name.lower())

        items.sort(key=sort_key)
        return items

    def _listdir_ftp(self, target_dir: str) -> List[RemoteFileInfo]:
        """Lists files on FTP/FTPS server using MLSD with LIST fallback and broken pipe retry."""
        with self._lock:
            self._ensure_ftp_connected()
            clean_dir = target_dir.rstrip("/")
            if not clean_dir.startswith("/"):
                clean_dir = f"/{clean_dir}"
            if not clean_dir:
                clean_dir = "/"

            items: List[RemoteFileInfo] = []

            # If not at root, add '..' parent directory item
            if clean_dir != "/":
                parent_path = str(Path(clean_dir).parent)
                if not parent_path.startswith("/"):
                    parent_path = "/" + parent_path
                parent_item = RemoteFileInfo(
                    name="..",
                    path=parent_path,
                    size=0,
                    mode=stat.S_IFDIR | 0o755,
                    permissions_octal="755",
                    permissions_str="drwxr-xr-x",
                    modified_time=time.time(),
                    modified_str="",
                    is_dir=True,
                    is_symlink=False,
                )
                items.append(parent_item)

            for attempt in range(2):
                try:
                    use_mlsd = True
                    try:
                        for name, facts in self._ftp.mlsd(path=clean_dir):
                            if name in (".", ".."):
                                continue
                            item = RemoteFileInfo.from_ftp_fact(name, clean_dir, facts)
                            items.append(item)
                    except Exception:
                        use_mlsd = False

                    if not use_mlsd:
                        lines: List[str] = []
                        prev_pwd = None
                        try:
                            prev_pwd = self._ftp.pwd()
                            if clean_dir != prev_pwd:
                                self._ftp.cwd(clean_dir)
                            self._ftp.dir(lines.append)
                        except Exception as e:
                            if prev_pwd:
                                try:
                                    self._ftp.cwd(prev_pwd)
                                except Exception:
                                    pass
                            raise IOError(f"Could not list FTP directory '{clean_dir}': {e}") from e

                        for line in lines:
                            it = self._parse_unix_list_line(line, clean_dir)
                            if it and it.name not in (".", ".."):
                                items.append(it)
                    break
                except (BrokenPipeError, ConnectionResetError, EOFError, socket.error, ftplib.error_temp):
                    if attempt == 0:
                        self._reconnect_ftp()
                        items = [it for it in items if it.name == ".."]
                    else:
                        raise

            def sort_key(item: RemoteFileInfo):
                if item.name == "..":
                    return (0, "")
                return (1 if not item.is_dir else 0, item.name.lower())

            items.sort(key=sort_key)
            self._current_directory = clean_dir
            return items

    def _parse_unix_list_line(self, line: str, parent_dir: str) -> Optional[RemoteFileInfo]:
        """Parses a single Unix ls -la / Windows DIR output line from FTP LIST."""
        parts = line.split()
        if len(parts) < 4:
            return None

        # 1. Standard Unix format: drwxr-xr-x 2 user group 4096 Oct 22 18:30 name
        is_unix_perm = (
            len(parts[0]) >= 10
            and parts[0][0] in ("-", "d", "l", "c", "b", "p", "s")
            and all(c in "rwxstST-ugoal" for c in parts[0][1:10])
        )
        if is_unix_perm and len(parts) >= 9:
            perms = parts[0]
            is_dir = perms.startswith("d")
            is_symlink = perms.startswith("l")

            try:
                size = int(parts[4])
            except (ValueError, IndexError):
                size = 0

            mode_val = stat.S_IFDIR if is_dir else (stat.S_IFLNK if is_symlink else stat.S_IFREG)
            try:
                oct_val = 0
                for i, ch in enumerate(perms[1:10]):
                    if ch in ("r", "w", "x", "s", "t"):
                        oct_val |= 1 << (8 - i)
                mode_val |= oct_val
                oct_str = oct(oct_val)[-3:].zfill(3)
            except Exception:
                oct_str = "755" if is_dir else "644"

            name = " ".join(parts[8:])
            if is_symlink and " -> " in name:
                name = name.split(" -> ")[0]

            clean_parent = parent_dir.rstrip("/")
            full_path = f"{clean_parent}/{name}" if clean_parent else f"/{name}"
            date_str = f"{parts[5]} {parts[6]} {parts[7]}"

            return RemoteFileInfo(
                name=name,
                path=full_path,
                size=size if not is_dir else 0,
                mode=mode_val,
                permissions_octal=oct_str,
                permissions_str=perms[:10],
                modified_time=time.time(),
                modified_str=date_str,
                is_dir=is_dir,
                is_symlink=is_symlink,
            )

        # 2. Check Windows IIS format: 10-22-26 06:30PM <DIR> name
        if len(parts) >= 4 and parts[2].upper() == "<DIR>":
            name = " ".join(parts[3:])
            clean_parent = parent_dir.rstrip("/")
            full_p = f"{clean_parent}/{name}" if clean_parent else f"/{name}"
            return RemoteFileInfo(
                name=name,
                path=full_p,
                size=0,
                mode=stat.S_IFDIR | 0o755,
                permissions_octal="755",
                permissions_str="drwxr-xr-x",
                modified_time=time.time(),
                modified_str=f"{parts[0]} {parts[1]}",
                is_dir=True,
                is_symlink=False,
            )
        elif len(parts) >= 4 and parts[2].isdigit():
            name = " ".join(parts[3:])
            clean_parent = parent_dir.rstrip("/")
            full_p = f"{clean_parent}/{name}" if clean_parent else f"/{name}"
            return RemoteFileInfo(
                name=name,
                path=full_p,
                size=int(parts[2]),
                mode=stat.S_IFREG | 0o644,
                permissions_octal="644",
                permissions_str="-rw-r--r--",
                modified_time=time.time(),
                modified_str=f"{parts[0]} {parts[1]}",
                is_dir=False,
                is_symlink=False,
            )

        return None

    def download_file(
        self,
        remote_path: str,
        local_path: str,
        callback: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """Downloads a single file from remote server to local filesystem with broken pipe retry."""
        if not self.is_connected and self._protocol not in ("FTP", "FTPS"):
            raise RuntimeError(f"{self._protocol} client is not connected.")

        os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)

        if self._protocol in ("FTP", "FTPS"):
            with self._lock:
                self._ensure_ftp_connected()
                for attempt in range(2):
                    try:
                        try:
                            total_size = self.stat(remote_path).st_size
                        except Exception:
                            total_size = 0

                        transferred = 0
                        with open(local_path, "wb") as f:
                            def _write_chunk(chunk: bytes):
                                nonlocal transferred
                                f.write(chunk)
                                transferred += len(chunk)
                                if callback:
                                    callback(transferred, total_size)

                            self._ftp.retrbinary(f"RETR {remote_path}", _write_chunk, blocksize=65536)
                        break
                    except (BrokenPipeError, ConnectionResetError, EOFError, socket.error, ftplib.error_temp):
                        if attempt == 0:
                            self._reconnect_ftp()
                            time.sleep(0.3)
                        else:
                            raise
        else:
            if not self._sftp:
                raise RuntimeError("SFTP client is not connected.")
            self._sftp.get(remote_path, local_path, callback=callback)

    def download_directory(
        self,
        remote_dir: str,
        local_dir: str,
        callback: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """Recursively downloads a remote directory to local filesystem."""
        if not self.is_connected and self._protocol not in ("FTP", "FTPS"):
            raise RuntimeError(f"{self._protocol} client is not connected.")

        os.makedirs(local_dir, exist_ok=True)

        for item in self.listdir(remote_dir):
            if item.name in (".", ".."):
                continue
            sub_remote = f"{remote_dir.rstrip('/')}/{item.name}"
            sub_local = os.path.join(local_dir, item.name)

            if item.is_dir:
                self.download_directory(sub_remote, sub_local, callback=callback)
            else:
                self.download_file(sub_remote, sub_local, callback=callback)

    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        callback: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """Uploads a local file to remote server with auto-reconnect and broken pipe recovery."""
        if not self.is_connected and self._protocol not in ("FTP", "FTPS"):
            raise RuntimeError(f"{self._protocol} client is not connected.")

        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file '{local_path}' does not exist.")

        total_size = os.path.getsize(local_path)

        if self._protocol in ("FTP", "FTPS"):
            with self._lock:
                self._ensure_ftp_connected()
                parent = os.path.dirname(remote_path)
                if parent and parent != "/":
                    self.ensure_remote_dir(parent)

                for attempt in range(2):
                    try:
                        transferred = 0
                        with open(local_path, "rb") as f:
                            def _cb(chunk: bytes):
                                nonlocal transferred
                                transferred += len(chunk)
                                if callback:
                                    callback(transferred, total_size)

                            self._ftp.storbinary(f"STOR {remote_path}", f, blocksize=65536, callback=_cb)
                        break
                    except (BrokenPipeError, ConnectionResetError, EOFError, socket.error, ftplib.error_temp):
                        if attempt == 0:
                            self._reconnect_ftp()
                            time.sleep(0.3)
                        else:
                            raise
        else:
            if not self._sftp:
                raise RuntimeError("SFTP client is not connected.")
            self._sftp.put(local_path, remote_path, callback=callback)

    def upload_directory(
        self,
        local_dir: str,
        remote_dir: str,
        callback: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """Recursively uploads a local directory to remote server with directory caching and zero redundant stats."""
        if not self.is_connected and self._protocol not in ("FTP", "FTPS"):
            raise RuntimeError(f"{self._protocol} client is not connected.")

        local_path = Path(local_dir).resolve()
        if not local_path.is_dir():
            raise NotADirectoryError(f"'{local_dir}' is not a directory.")

        self.ensure_remote_dir(remote_dir)

        for root, dirs, files in os.walk(str(local_path)):
            rel_path = os.path.relpath(root, str(local_path))
            if rel_path == ".":
                curr_remote_dir = remote_dir
            else:
                curr_remote_dir = f"{remote_dir.rstrip('/')}/{rel_path.replace(os.sep, '/')}"

            self.ensure_remote_dir(curr_remote_dir)

            for d in dirs:
                target_sub = f"{curr_remote_dir.rstrip('/')}/{d}"
                self.ensure_remote_dir(target_sub)

            for f in files:
                source_file = os.path.join(root, f)
                dest_file = f"{curr_remote_dir.rstrip('/')}/{f}"
                try:
                    self.upload_file(source_file, dest_file, callback=callback)
                except Exception as exc:
                    if self._protocol in ("FTP", "FTPS"):
                        time.sleep(0.02)
                    raise exc

    def delete_file(self, remote_path: str) -> None:
        """Deletes a file on remote server with broken pipe retry."""
        if not self.is_connected and self._protocol not in ("FTP", "FTPS"):
            raise RuntimeError(f"{self._protocol} client is not connected.")
        if self._protocol in ("FTP", "FTPS"):
            with self._lock:
                self._ensure_ftp_connected()
                if self._ftp:
                    for attempt in range(2):
                        try:
                            self._ftp.delete(remote_path)
                            break
                        except (BrokenPipeError, ConnectionResetError, EOFError, socket.error, ftplib.error_temp):
                            if attempt == 0:
                                self._reconnect_ftp()
                            else:
                                raise
        else:
            if not self._sftp:
                raise RuntimeError("SFTP client is not connected.")
            self._sftp.remove(remote_path)

    def delete_directory_recursive(self, remote_dir: str) -> None:
        """Recursively removes a directory and all its contents on the remote server."""
        if not self.is_connected and self._protocol not in ("FTP", "FTPS"):
            raise RuntimeError(f"{self._protocol} client is not connected.")

        for item in self.listdir(remote_dir):
            if item.name in (".", ".."):
                continue
            subpath = f"{remote_dir.rstrip('/')}/{item.name}"
            if item.is_dir:
                self.delete_directory_recursive(subpath)
            else:
                self.delete_file(subpath)

        if self._protocol in ("FTP", "FTPS"):
            with self._lock:
                self._ensure_ftp_connected()
                if self._ftp:
                    for attempt in range(2):
                        try:
                            self._ftp.rmd(remote_dir)
                            break
                        except (BrokenPipeError, ConnectionResetError, EOFError, socket.error, ftplib.error_temp):
                            if attempt == 0:
                                self._reconnect_ftp()
                            else:
                                raise
                        except Exception:
                            break
        else:
            if not self._sftp:
                raise RuntimeError("SFTP client is not connected.")
            self._sftp.rmdir(remote_dir)

    def mkdir(self, remote_path: str, mode: int = 0o755) -> None:
        """Creates a directory on the remote server."""
        if not self.is_connected and self._protocol not in ("FTP", "FTPS"):
            raise RuntimeError(f"{self._protocol} client is not connected.")
        if self._protocol in ("FTP", "FTPS"):
            with self._lock:
                self._ensure_ftp_connected()
                if self._ftp:
                    for attempt in range(2):
                        try:
                            self._ftp.mkd(remote_path)
                            break
                        except (BrokenPipeError, ConnectionResetError, EOFError, socket.error, ftplib.error_temp):
                            if attempt == 0:
                                self._reconnect_ftp()
                            else:
                                raise
                        except Exception:
                            # EEXIST / 550 Directory already exists
                            break
        else:
            if not self._sftp:
                raise RuntimeError("SFTP client is not connected.")
            self._sftp.mkdir(remote_path, mode=mode)

    def rename(self, old_path: str, new_path: str) -> None:
        """Renames or moves a file/folder on remote server."""
        if not self.is_connected and self._protocol not in ("FTP", "FTPS"):
            raise RuntimeError(f"{self._protocol} client is not connected.")
        if self._protocol in ("FTP", "FTPS"):
            with self._lock:
                self._ensure_ftp_connected()
                if self._ftp:
                    for attempt in range(2):
                        try:
                            self._ftp.rename(old_path, new_path)
                            break
                        except (BrokenPipeError, ConnectionResetError, EOFError, socket.error, ftplib.error_temp):
                            if attempt == 0:
                                self._reconnect_ftp()
                            else:
                                raise
        else:
            if not self._sftp:
                raise RuntimeError("SFTP client is not connected.")
            self._sftp.rename(old_path, new_path)

    def chmod(self, remote_path: str, mode: int) -> None:
        """Changes permissions of a remote file or folder."""
        if not self.is_connected:
            raise RuntimeError(f"{self._protocol} client is not connected.")
        if self._protocol in ("FTP", "FTPS") and self._ftp:
            with self._lock:
                try:
                    oct_perm = oct(mode)[-3:].zfill(3)
                    self._ftp.sendcmd(f"SITE CHMOD {oct_perm} {remote_path}")
                except Exception:
                    pass
        else:
            if not self._sftp:
                raise RuntimeError("SFTP client is not connected.")
            self._sftp.chmod(remote_path, mode)

    def chmod_recursive(self, remote_path: str, mode: int) -> None:
        """Recursively changes permissions of a remote directory and its contents."""
        self.chmod(remote_path, mode)
        if self._protocol in ("FTP", "FTPS") and self._ftp:
            try:
                for item in self.listdir(remote_path):
                    if item.name in (".", ".."):
                        continue
                    child_path = f"{remote_path.rstrip('/')}/{item.name}"
                    if item.is_dir:
                        self.chmod_recursive(child_path, mode)
                    else:
                        self.chmod(child_path, mode)
            except Exception:
                pass
        elif self._sftp:
            try:
                for item in self._sftp.listdir_attr(remote_path):
                    child_path = f"{remote_path.rstrip('/')}/{item.filename}"
                    if stat.S_ISDIR(item.st_mode):
                        self.chmod_recursive(child_path, mode)
                    else:
                        self.chmod(child_path, mode)
            except Exception:
                pass

    def stat(self, remote_path: str):
        """Returns metadata for remote path (paramiko.SFTPAttributes or RemoteStat)."""
        if not self.is_connected:
            raise RuntimeError(f"{self._protocol} client is not connected.")

        if self._protocol in ("FTP", "FTPS") and self._ftp:
            return self._stat_ftp(remote_path)

        if not self._sftp:
            raise RuntimeError("SFTP client is not connected.")
        return self._sftp.stat(remote_path)

    def _stat_ftp(self, remote_path: str) -> RemoteStat:
        """Inspects remote path metadata via FTP SIZE and MDTM commands with auto-reconnect."""
        with self._lock:
            self._ensure_ftp_connected()
            p = remote_path.rstrip("/") or "/"
            if p == "/":
                return RemoteStat(st_size=4096, st_mtime=time.time(), st_mode=stat.S_IFDIR | 0o755)

            for attempt in range(2):
                try:
                    # 1. Try SIZE first (fastest for files)
                    try:
                        sz = self._ftp.size(p)
                        if sz is not None:
                            mtime = 0.0
                            try:
                                mdtm = self._ftp.sendcmd(f"MDTM {p}")
                                parts = mdtm.split()
                                if len(parts) >= 2:
                                    dt = datetime.strptime(parts[1][:14], "%Y%m%d%H%M%S")
                                    mtime = dt.timestamp()
                            except Exception:
                                pass
                            return RemoteStat(st_size=int(sz), st_mtime=mtime, st_mode=stat.S_IFREG | 0o644)
                    except Exception:
                        pass

                    # 2. Try CWD (checks if it's a directory)
                    cur = None
                    try:
                        cur = self._ftp.pwd()
                        self._ftp.cwd(p)
                        self._ftp.cwd(cur)
                        return RemoteStat(st_size=4096, st_mtime=time.time(), st_mode=stat.S_IFDIR | 0o755)
                    except Exception:
                        if cur:
                            try:
                                self._ftp.cwd(cur)
                            except Exception:
                                pass

                    # 3. Try MLSx on parent
                    parent = str(Path(p).parent)
                    target_name = os.path.basename(p)
                    try:
                        for name, facts in self._ftp.mlsd(path=parent):
                            if name == target_name:
                                ftype = facts.get("type", "file").lower()
                                is_dir = ftype in ("dir", "pdir", "cdir")
                                sz = int(facts.get("size", facts.get("sizd", 0)))
                                mod_str = facts.get("modify", "")
                                mtime = 0.0
                                if mod_str:
                                    try:
                                        mtime = datetime.strptime(mod_str[:14], "%Y%m%d%H%M%S").timestamp()
                                    except Exception:
                                        pass
                                mode = stat.S_IFDIR | 0o755 if is_dir else stat.S_IFREG | 0o644
                                return RemoteStat(st_size=sz, st_mtime=mtime, st_mode=mode)
                    except Exception:
                        pass

                    raise FileNotFoundError(f"Remote path '{remote_path}' not found on FTP server.")
                except (BrokenPipeError, ConnectionResetError, EOFError, socket.error, ftplib.error_temp):
                    if attempt == 0:
                        self._reconnect_ftp()
                    else:
                        raise

    def read_file_text(self, remote_path: str, encoding: str = "utf-8", max_bytes: int = 2_000_000) -> str:
        """Reads content of a remote file as text (supports both SFTP and FTP)."""
        if not self.is_connected and self._protocol not in ("FTP", "FTPS"):
            raise RuntimeError(f"{self._protocol} client is not connected.")

        if self._protocol in ("FTP", "FTPS"):
            with self._lock:
                self._ensure_ftp_connected()
                for attempt in range(2):
                    try:
                        buf = io.BytesIO()
                        def _cb(chunk: bytes):
                            if buf.tell() < max_bytes:
                                buf.write(chunk)

                        self._ftp.retrbinary(f"RETR {remote_path}", _cb, blocksize=32768)
                        buf.seek(0)
                        return buf.read(max_bytes).decode(encoding, errors="replace")
                    except (BrokenPipeError, ConnectionResetError, EOFError, socket.error, ftplib.error_temp):
                        if attempt == 0:
                            self._reconnect_ftp()
                        else:
                            raise
        elif self._sftp:
            with self._sftp.open(remote_path, "rb") as rf:
                raw = rf.read(max_bytes)
                return raw.decode(encoding, errors="replace")
        else:
            raise RuntimeError("No active SFTP or FTP session.")

    def create_symlink(self, target: str, linkpath: str) -> None:
        """Creates a symbolic link on the remote server."""
        if not self.is_connected:
            raise RuntimeError(f"{self._protocol} client is not connected.")
        if self._protocol in ("FTP", "FTPS"):
            raise NotImplementedError("Symlinks are not supported on FTP servers.")
        if not self._sftp:
            raise RuntimeError("SFTP client is not connected.")
        self._sftp.symlink(target, linkpath)

    def readlink(self, linkpath: str) -> str:
        """Reads destination of a symbolic link on the remote server."""
        if not self.is_connected:
            raise RuntimeError(f"{self._protocol} client is not connected.")
        if self._protocol in ("FTP", "FTPS"):
            raise NotImplementedError("Symlinks are not supported on FTP servers.")
        if not self._sftp:
            raise RuntimeError("SFTP client is not connected.")
        return self._sftp.readlink(linkpath)

    def exec_command(self, command: str, timeout: int = 30) -> tuple[int, str, str]:
        """
        Executes a remote shell command via SSH and returns (exit_status, stdout, stderr).
        """
        if not self.is_connected:
            raise RuntimeError(f"{self._protocol} client is not connected.")
        if self._protocol in ("FTP", "FTPS"):
            return (1, "", "Remote shell execution is not supported on standard FTP servers.")
        if not self._ssh:
            raise RuntimeError("SSH client is not connected.")
        stdin, stdout, stderr = self._ssh.exec_command(command, timeout=timeout)
        exit_status = stdout.channel.recv_exit_status()
        out_str = stdout.read().decode("utf-8", errors="replace")
        err_str = stderr.read().decode("utf-8", errors="replace")
        return exit_status, out_str, err_str

    def calculate_checksum(self, remote_path: str, algorithm: str = "sha256") -> str:
        """Calculates remote file checksum using remote hash utility or stream."""
        algo = algorithm.lower()
        if self._protocol in ("FTP", "FTPS") and self._ftp:
            with self._lock:
                import hashlib
                h = getattr(hashlib, algo, hashlib.sha256)()
                self._ftp.retrbinary(f"RETR {remote_path}", h.update, blocksize=65536)
                return h.hexdigest()

        cmd_map = {"sha256": "sha256sum", "md5": "md5sum", "sha1": "sha1sum"}
        binary = cmd_map.get(algo, "sha256sum")

        if self.is_connected and self._ssh:
            try:
                import shlex
                code, out, _ = self.exec_command(f"{binary} {shlex.quote(remote_path)}", timeout=15)
                if code == 0 and out:
                    return out.split()[0].strip()
            except Exception:
                pass

        if not self.is_connected or not self._sftp:
            raise RuntimeError("SFTP client is not connected.")

        import hashlib
        h = getattr(hashlib, algo, hashlib.sha256)()
        with self._sftp.open(remote_path, "rb") as rf:
            while chunk := rf.read(65536):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def calculate_local_checksum(local_path: str, algorithm: str = "sha256") -> str:
        """Calculates local file checksum."""
        import hashlib
        algo = algorithm.lower()
        h = getattr(hashlib, algo, hashlib.sha256)()
        with open(local_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()


# =============================================================================
# QThread Workers to prevent UI freezing
# =============================================================================


class SFTPConnectWorker(QThread):
    """Background worker to establish SSH/SFTP or FTP connection."""

    connected = pyqtSignal(str)  # home directory path
    failed = pyqtSignal(str)  # error message

    def __init__(
        self,
        sftp_client: SFTPClient,
        host: str,
        port: int = 22,
        username: str = "",
        password: str = "",
        private_key: str = "",
        passphrase: str = "",
        initial_remote_dir: str = "/",
        keepalive_interval: int = 30,
        compression: bool = False,
        protocol: str = "SFTP",
    ):
        super().__init__()
        self.sftp_client = sftp_client
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.private_key = private_key
        self.passphrase = passphrase
        self.initial_remote_dir = initial_remote_dir
        self.keepalive_interval = keepalive_interval
        self.compression = compression
        self.protocol = protocol

    def run(self):
        try:
            self.sftp_client.connect(
                host=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                private_key_path=self.private_key,
                passphrase=self.passphrase,
                keepalive_interval=self.keepalive_interval,
                compression=self.compression,
                protocol=self.protocol,
            )
            # Try setting initial directory if provided
            if self.initial_remote_dir and self.initial_remote_dir != "/":
                try:
                    self.sftp_client.listdir(self.initial_remote_dir)
                except Exception:
                    pass
            self.connected.emit(self.sftp_client.current_directory)
        except Exception as exc:
            self.failed.emit(str(exc))


class SFTPListDirWorker(QThread):
    """Background worker to list files in a remote directory."""

    success = pyqtSignal(list, str)  # items, current_directory
    failed = pyqtSignal(str)

    def __init__(self, sftp_client: SFTPClient, directory: str):
        super().__init__()
        self.sftp_client = sftp_client
        self.directory = directory

    def run(self):
        try:
            items = self.sftp_client.listdir(self.directory)
            self.success.emit(items, self.sftp_client.current_directory)
        except Exception as exc:
            self.failed.emit(str(exc))


class SFTPDownloadWorker(QThread):
    """Background worker for file or directory download with progress tracking."""

    progress = pyqtSignal(int, int)  # transferred, total
    finished = pyqtSignal(str, str)  # remote_path, local_path
    failed = pyqtSignal(str)

    def __init__(self, sftp_client: SFTPClient, remote_path: str, local_path: str, is_dir: bool = False):
        super().__init__()
        self.sftp_client = sftp_client
        self.remote_path = remote_path
        self.local_path = local_path
        self.is_dir = is_dir

    def run(self):
        try:
            def _cb(transferred: int, total: int):
                self.progress.emit(transferred, total)

            if self.is_dir:
                self.sftp_client.download_directory(self.remote_path, self.local_path, callback=_cb)
            else:
                self.sftp_client.download_file(self.remote_path, self.local_path, callback=_cb)
            self.finished.emit(self.remote_path, self.local_path)
        except Exception as exc:
            self.failed.emit(str(exc))


class SFTPUploadWorker(QThread):
    """Background worker for file or directory upload with progress tracking."""

    progress = pyqtSignal(int, int)  # transferred, total
    finished = pyqtSignal(str, str)  # local_path, remote_path
    failed = pyqtSignal(str)

    def __init__(self, sftp_client: SFTPClient, local_path: str, remote_path: str, is_dir: bool = False):
        super().__init__()
        self.sftp_client = sftp_client
        self.local_path = local_path
        self.remote_path = remote_path
        self.is_dir = is_dir or os.path.isdir(local_path)

    def run(self):
        try:
            def _cb(transferred: int, total: int):
                self.progress.emit(transferred, total)

            if self.is_dir:
                self.sftp_client.upload_directory(self.local_path, self.remote_path, callback=_cb)
            else:
                self.sftp_client.upload_file(self.local_path, self.remote_path, callback=_cb)
            self.finished.emit(self.local_path, self.remote_path)
        except Exception as exc:
            self.failed.emit(str(exc))


class SFTPBatchTransferWorker(QThread):
    """
    Background worker for transferring multiple files and folders in batch.
    Supports both upload and download operations with per-item progress.
    """

    item_started = pyqtSignal(str, int, int)  # item_name, current_index (1-based), total_items
    progress = pyqtSignal(int, int)          # transferred, total
    finished = pyqtSignal(int)               # total_transferred
    failed = pyqtSignal(str)

    def __init__(self, sftp_client: SFTPClient, transfers: List[dict]):
        """
        transfers: List of dicts, each containing:
            - source: str (path)
            - dest: str (path)
            - is_dir: bool
            - direction: "upload" or "download"
        """
        super().__init__()
        self.sftp_client = sftp_client
        self.transfers = transfers

    def run(self):
        total = len(self.transfers)
        try:
            for idx, item in enumerate(self.transfers, start=1):
                source = item["source"]
                dest = item["dest"]
                is_dir = item.get("is_dir", False)
                direction = item.get("direction", "upload")
                name = Path(source).name or source

                self.item_started.emit(name, idx, total)

                def _cb(transferred: int, tot: int):
                    self.progress.emit(transferred, tot)

                if direction == "upload":
                    if is_dir or os.path.isdir(source):
                        self.sftp_client.upload_directory(source, dest, callback=_cb)
                    else:
                        self.sftp_client.upload_file(source, dest, callback=_cb)
                else:  # download
                    if is_dir:
                        self.sftp_client.download_directory(source, dest, callback=_cb)
                    else:
                        self.sftp_client.download_file(source, dest, callback=_cb)

            self.finished.emit(total)
        except Exception as exc:
            self.failed.emit(str(exc))


class SFTPActionWorker(QThread):
    """General background worker for chmod, delete, rename, mkdir."""

    finished = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, action_fn: Callable[[], None], success_msg: str):
        super().__init__()
        self.action_fn = action_fn
        self.success_msg = success_msg

    def run(self):
        try:
            self.action_fn()
            self.finished.emit(self.success_msg)
        except Exception as exc:
            self.failed.emit(str(exc))
