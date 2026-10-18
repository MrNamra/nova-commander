"""
Interactive SSH Shell Handler for WinSCP-Linux.
Multiplexes with existing Paramiko SSHClient or creates a dedicated session,
streaming raw I/O for the embedded terminal widget.
"""

from __future__ import annotations

import select
import socket
import threading
import time
from typing import Optional

import paramiko
from PyQt6.QtCore import QObject, pyqtSignal


class SSHShellClient(QObject):
    """
    Manages an interactive SSH pseudo-terminal (pty) session.
    Emits Qt signals when output is received or the session closes.
    """

    data_received = pyqtSignal(bytes)
    session_closed = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._ssh: Optional[paramiko.SSHClient] = None
        self._channel: Optional[paramiko.Channel] = None
        self._running: bool = False
        self._reader_thread: Optional[threading.Thread] = None
        self._owns_ssh: bool = False

    @property
    def is_active(self) -> bool:
        return bool(self._channel and not self._channel.closed and self._running)

    def open_shell(
        self,
        ssh_client: Optional[paramiko.SSHClient] = None,
        host: str = "",
        port: int = 22,
        username: str = "",
        password: str = "",
        private_key: str = "",
        cols: int = 80,
        rows: int = 24,
    ) -> None:
        """
        Opens an interactive shell either using an existing active SSHClient or by connecting anew.
        """
        self.close()

        if ssh_client and ssh_client.get_transport() and ssh_client.get_transport().is_active():
            self._ssh = ssh_client
            self._owns_ssh = False
        else:
            # Create dedicated SSH client
            self._ssh = paramiko.SSHClient()
            self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self._owns_ssh = True

            pkey = None
            if private_key:
                for kcls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
                    try:
                        pkey = kcls.from_private_key_file(private_key)
                        break
                    except Exception:
                        pass

            self._ssh.connect(
                hostname=host,
                port=port,
                username=username or None,
                password=password or None,
                pkey=pkey,
                timeout=10,
            )

        transport = self._ssh.get_transport()
        if not transport:
            raise ConnectionError("SSH Transport is unavailable.")

        # Request pseudo-terminal and invoke shell
        self._channel = transport.open_session()
        self._channel.get_pty(term="xterm-256color", width=cols, height=rows)
        self._channel.invoke_shell()
        self._channel.setblocking(0)

        self._running = True
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def send(self, data: bytes | str) -> None:
        """Sends data (keystrokes / paste) to the remote SSH shell."""
        if not self.is_active or not self._channel:
            return

        payload = data.encode("utf-8") if isinstance(data, str) else data
        try:
            self._channel.sendall(payload)
        except Exception as exc:
            self.error_occurred.emit(f"Write error: {exc}")

    def resize_pty(self, cols: int, rows: int) -> None:
        """Notifies the remote host of a terminal window size change."""
        if self.is_active and self._channel:
            try:
                self._channel.resize_pty(width=cols, height=rows)
            except Exception:
                pass

    def _read_loop(self) -> None:
        """Background loop continuously polling and reading bytes from SSH channel."""
        while self._running and self._channel and not self._channel.closed:
            try:
                r, _, _ = select.select([self._channel], [], [], 0.05)
                if self._channel in r:
                    data = self._channel.recv(4096)
                    if not data:
                        break
                    self.data_received.emit(data)
                elif self._channel.exit_status_ready():
                    break
            except (socket.error, OSError):
                break
            except Exception:
                time.sleep(0.01)

        self._running = False
        self.session_closed.emit()

    def close(self) -> None:
        """Closes the interactive channel and frees resources."""
        self._running = False
        if self._channel:
            try:
                self._channel.close()
            except Exception:
                pass
            self._channel = None

        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=0.3)
            self._reader_thread = None

        if self._owns_ssh and self._ssh:
            try:
                self._ssh.close()
            except Exception:
                pass
            self._ssh = None
