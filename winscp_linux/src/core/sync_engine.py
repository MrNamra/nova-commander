"""
Remote File Direct-Edit and Auto-Sync Engine for WinSCP-Linux.
Watches cached local files with watchdog (inotify) and automatically uploads on save.
"""

from __future__ import annotations

import atexit
import os
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

from PyQt6.QtCore import QObject, pyqtSignal
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .editor_manager import launch_external_editor
from .sftp_client import SFTPClient


@dataclass
class WatchedFile:
    session_id: str
    remote_path: str
    local_path: str
    observer: Observer
    last_uploaded_mtime: float
    last_event_time: float = 0.0


class _DebouncedFileEventHandler(FileSystemEventHandler):
    """
    Inotify event handler with debouncing to prevent multiple uploads during rapid file writes.
    """

    def __init__(self, target_local_path: str, on_modified_callback: Callable[[str], None], debounce_seconds: float = 0.4):
        super().__init__()
        self.target_local_path = os.path.realpath(target_local_path)
        self.on_modified_callback = on_modified_callback
        self.debounce_seconds = debounce_seconds
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def _trigger(self):
        with self._lock:
            self._timer = None
        self.on_modified_callback(self.target_local_path)

    def _schedule(self):
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce_seconds, self._trigger)
            self._timer.daemon = True
            self._timer.start()

    def on_modified(self, event: FileSystemEvent):
        if not event.is_directory and os.path.realpath(event.src_path) == self.target_local_path:
            self._schedule()

    def on_created(self, event: FileSystemEvent):
        if not event.is_directory and os.path.realpath(event.src_path) == self.target_local_path:
            self._schedule()

    def on_moved(self, event: FileSystemEvent):
        # Many editors do atomic writes by writing to a temporary file and renaming it
        if hasattr(event, "dest_path") and os.path.realpath(event.dest_path) == self.target_local_path:
            self._schedule()


class SyncEngine(QObject):
    """
    Orchestrates downloading remote files to temporary cache, opening with user's editor,
    and monitoring for modifications to auto-upload back to the remote SFTP server.
    """

    sync_started = pyqtSignal(str, str)  # remote_path, local_path
    sync_succeeded = pyqtSignal(str, str)  # remote_path, local_path
    sync_failed = pyqtSignal(str, str)  # remote_path, error_message

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.base_cache_dir = Path("/tmp/winscp-linux-cache")
        self.base_cache_dir.mkdir(parents=True, exist_ok=True)
        self._watches: Dict[str, WatchedFile] = {}  # key: local_path
        self._lock = threading.Lock()

        # Register exit hook to cleanup temporary files
        atexit.register(self.cleanup_all)

    def get_cache_path(self, session_id: str, remote_path: str) -> Path:
        """Computes local cache path: /tmp/winscp-linux-cache/<session_id>/<remote_path>."""
        sanitized_session = "".join(c if c.isalnum() or c in "-_." else "_" for c in session_id)
        rel_remote = remote_path.lstrip("/")
        return self.base_cache_dir / sanitized_session / rel_remote

    def start_edit_session(
        self,
        sftp_client: SFTPClient,
        session_id: str,
        remote_path: str,
        editor_command: Optional[str] = None,
    ) -> Path:
        """
        1. Downloads file to cache.
        2. Attaches inotify watchdog watcher.
        3. Launches editor via xdg-open or configured editor.
        """
        local_cache_path = self.get_cache_path(session_id, remote_path)
        local_cache_path.parent.mkdir(parents=True, exist_ok=True)
        local_str = str(local_cache_path.resolve())

        # 1. Download file from SFTP
        sftp_client.download_file(remote_path, local_str)
        initial_mtime = os.path.getmtime(local_str) if os.path.exists(local_str) else time.time()

        # 2. Attach inotify watcher if not already watching this file
        with self._lock:
            if local_str in self._watches:
                watched = self._watches[local_str]
                watched.last_uploaded_mtime = initial_mtime
            else:
                observer = Observer()

                def _on_save(target_path: str):
                    self._handle_file_saved(sftp_client, target_path)

                handler = _DebouncedFileEventHandler(local_str, _on_save)
                # Watch the parent directory so we catch file modifications and atomic renames
                watch_dir = str(local_cache_path.parent)
                observer.schedule(handler, watch_dir, recursive=False)
                observer.start()

                watched = WatchedFile(
                    session_id=session_id,
                    remote_path=remote_path,
                    local_path=local_str,
                    observer=observer,
                    last_uploaded_mtime=initial_mtime,
                )
                self._watches[local_str] = watched

        # 3. Launch editor
        self._launch_editor(local_str, editor_command)

        return local_cache_path

    def _launch_editor(self, local_path: str, editor_command: Optional[str] = None) -> None:
        """Launches default editor via safe process detachment and clean environment."""
        try:
            launch_external_editor(editor_command, local_path)
        except Exception as exc:
            self.sync_failed.emit(local_path, f"Could not launch editor: {exc}")

    def _handle_file_saved(self, sftp_client: SFTPClient, local_path: str) -> None:
        """Uploads modified file back to remote server via SFTP."""
        with self._lock:
            watched = self._watches.get(local_path)
            if not watched:
                return
            remote_path = watched.remote_path

        if not os.path.exists(local_path):
            return

        try:
            current_mtime = os.path.getmtime(local_path)
            # Check if file has actually been updated since last upload
            if current_mtime <= watched.last_uploaded_mtime:
                return

            self.sync_started.emit(remote_path, local_path)

            # Upload back to SFTP server
            sftp_client.upload_file(local_path, remote_path)
            watched.last_uploaded_mtime = current_mtime

            self.sync_succeeded.emit(remote_path, local_path)
        except Exception as exc:
            self.sync_failed.emit(remote_path, str(exc))

    def cleanup_session(self, session_id: str) -> None:
        """Stops observers and cleans up cache for a specific session."""
        sanitized_session = "".join(c if c.isalnum() or c in "-_." else "_" for c in session_id)
        session_dir = self.base_cache_dir / sanitized_session

        with self._lock:
            keys_to_remove = []
            for path, watched in self._watches.items():
                if watched.session_id == session_id:
                    try:
                        watched.observer.stop()
                        watched.observer.join(timeout=0.5)
                    except Exception:
                        pass
                    keys_to_remove.append(path)

            for key in keys_to_remove:
                del self._watches[key]

        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=True)

    def cleanup_all(self) -> None:
        """Stops all active watchdog observers and removes all cached files."""
        with self._lock:
            for watched in self._watches.values():
                try:
                    watched.observer.stop()
                    watched.observer.join(timeout=0.3)
                except Exception:
                    pass
            self._watches.clear()

        if self.base_cache_dir.exists():
            shutil.rmtree(self.base_cache_dir, ignore_errors=True)
