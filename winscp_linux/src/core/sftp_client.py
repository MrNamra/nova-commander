"""
Paramiko-based SFTP Client and background QThread workers for WinSCP-Linux.
Ensures network operations never freeze the Qt GUI thread.
"""

from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

import paramiko
from PyQt6.QtCore import QObject, QThread, pyqtSignal


@dataclass
class RemoteFileInfo:
    """Detailed file/folder metadata from remote SFTP server."""

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


class SFTPClient:
    """
    Synchronous SFTP Client wrapping Paramiko SSHClient and SFTPClient.
    Provides robust connection handling, file inspection, directory navigation, and file transfers.
    """

    def __init__(self):
        self._ssh: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None
        self._current_directory: str = "/"
        self._session_info: dict = {}

    @property
    def is_connected(self) -> bool:
        if self._ssh and self._sftp:
            transport = self._ssh.get_transport()
            return transport is not None and transport.is_active()
        return False

    @property
    def current_directory(self) -> str:
        return self._current_directory

    @property
    def ssh_client(self) -> Optional[paramiko.SSHClient]:
        return self._ssh

    def connect(
        self,
        host: str,
        port: int = 22,
        username: str = "",
        password: str = "",
        private_key_path: str = "",
        passphrase: Optional[str] = None,
        timeout: float = 12.0,
    ) -> None:
        """
        Establishes SSH transport and SFTP session.
        """
        self.disconnect()

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
            for key_cls in key_classes:
                try:
                    pkey = key_cls.from_private_key_file(private_key_path, password=passphrase)
                    break
                except Exception:
                    continue

            if pkey is None:
                # Try OpenSSH format
                try:
                    pkey = paramiko.PKey.from_path(private_key_path)
                except Exception:
                    pass

        connect_kwargs = {
            "hostname": host,
            "port": port,
            "username": username or None,
            "timeout": timeout,
            "look_for_keys": False,
            "allow_agent": False,
        }

        if pkey:
            connect_kwargs["pkey"] = pkey
            if password:
                # In case server requires both or key fails
                connect_kwargs["password"] = password
        elif password:
            connect_kwargs["password"] = password

        try:
            ssh.connect(**connect_kwargs)
            transport = ssh.get_transport()
            if transport:
                transport.set_keepalive(30)
            sftp = ssh.open_sftp()
        except Exception as e:
            ssh.close()
            raise ConnectionError(f"Failed to connect to {host}:{port} - {e}") from e

        self._ssh = ssh
        self._sftp = sftp
        self._session_info = {
            "host": host,
            "port": port,
            "username": username,
        }

        # Resolve initial default directory
        try:
            home = sftp.normalize(".")
            self._current_directory = home if home else "/"
        except Exception:
            self._current_directory = "/"

    def disconnect(self) -> None:
        """Closes SFTP and SSH connections."""
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

    def listdir(self, remote_dir: Optional[str] = None) -> List[RemoteFileInfo]:
        """
        Lists files in remote directory with detailed attributes.
        Includes '..' entry if not at root '/'.
        """
        if not self.is_connected or not self._sftp:
            raise RuntimeError("SFTP client is not connected.")

        target_dir = remote_dir or self._current_directory
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

    def download_file(
        self,
        remote_path: str,
        local_path: str,
        callback: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """Downloads a single file from remote SFTP to local filesystem."""
        if not self.is_connected or not self._sftp:
            raise RuntimeError("SFTP client is not connected.")

        os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)
        self._sftp.get(remote_path, local_path, callback=callback)

    def download_directory(
        self,
        remote_dir: str,
        local_dir: str,
        callback: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """Recursively downloads a remote directory to local filesystem."""
        if not self.is_connected or not self._sftp:
            raise RuntimeError("SFTP client is not connected.")

        os.makedirs(local_dir, exist_ok=True)

        for item in self._sftp.listdir_attr(remote_dir):
            if item.filename in (".", ".."):
                continue
            sub_remote = f"{remote_dir.rstrip('/')}/{item.filename}"
            sub_local = os.path.join(local_dir, item.filename)

            if stat.S_ISDIR(item.st_mode):
                self.download_directory(sub_remote, sub_local, callback=callback)
            else:
                self.download_file(sub_remote, sub_local, callback=callback)

    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        callback: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """Uploads a local file to remote SFTP."""
        if not self.is_connected or not self._sftp:
            raise RuntimeError("SFTP client is not connected.")

        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file '{local_path}' does not exist.")

        self._sftp.put(local_path, remote_path, callback=callback)

    def upload_directory(
        self,
        local_dir: str,
        remote_dir: str,
        callback: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        """Recursively uploads a local directory to remote SFTP."""
        if not self.is_connected or not self._sftp:
            raise RuntimeError("SFTP client is not connected.")

        local_path = Path(local_dir).resolve()
        if not local_path.is_dir():
            raise NotADirectoryError(f"'{local_dir}' is not a directory.")

        try:
            self._sftp.stat(remote_dir)
        except IOError:
            try:
                self.mkdir(remote_dir)
            except Exception:
                pass

        for root, dirs, files in os.walk(str(local_path)):
            rel_path = os.path.relpath(root, str(local_path))
            if rel_path == ".":
                curr_remote_dir = remote_dir
            else:
                curr_remote_dir = f"{remote_dir.rstrip('/')}/{rel_path.replace(os.sep, '/')}"

            for d in dirs:
                target_sub = f"{curr_remote_dir.rstrip('/')}/{d}"
                try:
                    self._sftp.stat(target_sub)
                except IOError:
                    try:
                        self.mkdir(target_sub)
                    except Exception:
                        pass

            for f in files:
                source_file = os.path.join(root, f)
                dest_file = f"{curr_remote_dir.rstrip('/')}/{f}"
                self.upload_file(source_file, dest_file, callback=callback)

    def delete_file(self, remote_path: str) -> None:
        """Deletes a file on remote server."""
        if not self.is_connected or not self._sftp:
            raise RuntimeError("SFTP client is not connected.")
        self._sftp.remove(remote_path)

    def delete_directory_recursive(self, remote_dir: str) -> None:
        """Recursively removes a directory and all its contents on the remote server."""
        if not self.is_connected or not self._sftp:
            raise RuntimeError("SFTP client is not connected.")

        for item in self._sftp.listdir_attr(remote_dir):
            if item.filename in (".", ".."):
                continue
            subpath = f"{remote_dir.rstrip('/')}/{item.filename}"
            if stat.S_ISDIR(item.st_mode):
                self.delete_directory_recursive(subpath)
            else:
                self._sftp.remove(subpath)
        self._sftp.rmdir(remote_dir)

    def mkdir(self, remote_path: str, mode: int = 0o755) -> None:
        """Creates a directory on the remote server."""
        if not self.is_connected or not self._sftp:
            raise RuntimeError("SFTP client is not connected.")
        self._sftp.mkdir(remote_path, mode=mode)

    def rename(self, old_path: str, new_path: str) -> None:
        """Renames or moves a file/folder on remote server."""
        if not self.is_connected or not self._sftp:
            raise RuntimeError("SFTP client is not connected.")
        self._sftp.rename(old_path, new_path)

    def chmod(self, remote_path: str, mode: int) -> None:
        """Changes permissions of a remote file or folder."""
        if not self.is_connected or not self._sftp:
            raise RuntimeError("SFTP client is not connected.")
        self._sftp.chmod(remote_path, mode)

    def stat(self, remote_path: str) -> paramiko.SFTPAttributes:
        if not self.is_connected or not self._sftp:
            raise RuntimeError("SFTP client is not connected.")
        return self._sftp.stat(remote_path)


# =============================================================================
# QThread Workers to prevent UI freezing
# =============================================================================


class SFTPConnectWorker(QThread):
    """Background worker to establish SSH/SFTP connection."""

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
        initial_remote_dir: str = "/",
    ):
        super().__init__()
        self.sftp_client = sftp_client
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.private_key = private_key
        self.initial_remote_dir = initial_remote_dir

    def run(self):
        try:
            self.sftp_client.connect(
                host=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                private_key_path=self.private_key,
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
