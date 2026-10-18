"""
System / OS Terminal Launcher for Nova Commander.
Launches the native operating system terminal emulator (gnome-terminal, konsole, xterm,
Terminal.app on macOS, wt.exe/cmd.exe on Windows) running an interactive SSH session.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from .editor_manager import get_clean_editor_env


def build_ssh_command(
    host: str,
    port: int = 22,
    username: str = "",
    password: str = "",
    private_key: str = "",
    remote_dir: str = "",
) -> str:
    """Builds a portable SSH command line string with optional sshpass support."""
    ssh_parts = ["ssh"]
    if port and int(port) != 22:
        ssh_parts.extend(["-p", str(port)])
    if private_key and os.path.exists(private_key):
        ssh_parts.extend(["-i", shlex.quote(private_key)])

    user_host = f"{username}@{host}" if username else host
    ssh_parts.append(shlex.quote(user_host))

    if remote_dir and remote_dir not in ("/", "~"):
        cd_cmd = f"cd {shlex.quote(remote_dir)} 2>/dev/null || true; exec $SHELL -l"
        ssh_parts.extend(["-t", shlex.quote(cd_cmd)])

    base_cmd = " ".join(ssh_parts)

    if password and shutil.which("sshpass"):
        return f"sshpass -p {shlex.quote(password)} {base_cmd}"

    return base_cmd


def get_available_linux_terminals() -> List[str]:
    """Returns detected Linux terminal emulators in order of preference."""
    candidates = [
        "gnome-terminal",
        "x-terminal-emulator",
        "konsole",
        "xfce4-terminal",
        "mate-terminal",
        "tilix",
        "terminator",
        "alacritty",
        "kitty",
        "xterm",
    ]
    return [c for c in candidates if shutil.which(c)]


def launch_system_terminal(
    host: str,
    port: int = 22,
    username: str = "",
    password: str = "",
    private_key: str = "",
    remote_dir: str = "",
    session_name: str = "",
    custom_terminal_cmd: Optional[str] = None,
) -> bool:
    """
    Spawns the native OS terminal (gnome-terminal, konsole, wt.exe, Terminal.app)
    connecting directly to the remote server via SSH.
    """
    clean_env = get_clean_editor_env()
    title = session_name or (f"SSH: {username}@{host}" if username else f"SSH: {host}")
    ssh_cmd = build_ssh_command(
        host=host,
        port=port,
        username=username,
        password=password,
        private_key=private_key,
        remote_dir=remote_dir,
    )

    # 1. macOS (Darwin)
    if sys.platform == "darwin":
        escaped_cmd = ssh_cmd.replace("\\", "\\\\").replace('"', '\\"')
        apple_script = f'''
        tell application "Terminal"
            activate
            do script "{escaped_cmd}"
        end tell
        '''
        try:
            subprocess.Popen(["osascript", "-e", apple_script], env=clean_env, start_new_session=True)
            return True
        except Exception:
            return False

    # 2. Windows (Win32)
    if sys.platform == "win32":
        wt_path = shutil.which("wt.exe") or shutil.which("wt")
        if wt_path:
            try:
                subprocess.Popen(
                    [wt_path, "--title", title, "cmd.exe", "/k", ssh_cmd],
                    env=clean_env,
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
                return True
            except Exception:
                pass

        try:
            cmd_line = f'start "{title}" cmd.exe /k {ssh_cmd}'
            subprocess.Popen(
                cmd_line,
                shell=True,
                env=clean_env,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            return True
        except Exception:
            return False

    # 3. Linux / BSD
    term_bin = custom_terminal_cmd or None
    if not term_bin:
        installed = get_available_linux_terminals()
        if not installed:
            raise FileNotFoundError("No Linux terminal emulator (gnome-terminal, konsole, xterm, etc.) found.")
        term_bin = installed[0]

    # Command wrapped with bash to pause with error message if SSH exits with an error
    run_bash = f'{ssh_cmd} || (echo -e "\\n\\033[31m[SSH connection exited with error]\\033[0m Press Enter to close..." && read -n 1)'

    cmd: List[str] = []
    base_name = Path(term_bin).name

    if base_name == "gnome-terminal":
        cmd = [term_bin, f"--title={title}", "--", "bash", "-c", run_bash]
    elif base_name == "konsole":
        cmd = [term_bin, "-p", f"tabtitle={title}", "-e", "bash", "-c", run_bash]
    elif base_name in ("xfce4-terminal", "mate-terminal"):
        cmd = [term_bin, f"--title={title}", "-e", f"bash -c {shlex.quote(run_bash)}"]
    elif base_name in ("tilix", "terminator"):
        cmd = [term_bin, "-T", title, "-e", f"bash -c {shlex.quote(run_bash)}"]
    elif base_name == "alacritty":
        cmd = [term_bin, "-t", title, "-e", "bash", "-c", run_bash]
    elif base_name == "kitty":
        cmd = [term_bin, "--title", title, "bash", "-c", run_bash]
    elif base_name in ("xterm", "uxterm"):
        cmd = [term_bin, "-title", title, "-e", "bash", "-c", run_bash]
    elif base_name == "x-terminal-emulator":
        cmd = [term_bin, "-T", title, "-e", f"bash -c {shlex.quote(run_bash)}"]
    else:
        cmd = [term_bin, "-e", f"bash -c {shlex.quote(run_bash)}"]

    subprocess.Popen(
        cmd,
        env=clean_env,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return True
