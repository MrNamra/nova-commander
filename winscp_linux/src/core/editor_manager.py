"""
Editor Preferences Manager for WinSCP-Linux.
Detects installed Linux text editors/IDEs, manages file extension associations,
and persists default application preferences.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class EditorInfo:
    """Represents a discovered or configured editor application."""

    id: str
    name: str
    command: str
    description: str = ""
    is_installed: bool = False


def get_default_system_opener() -> str:
    """Returns standard system default opener for current platform."""
    if sys.platform == "darwin":
        return "open"
    elif sys.platform == "win32":
        return "start"
    else:
        return "xdg-open"


def get_known_editors() -> List[dict]:
    """Returns platform-specific known editor application definitions."""
    if sys.platform == "darwin":
        return [
            {"id": "open", "name": "System Default Application", "command": "open", "desc": "macOS default file handler"},
            {"id": "code", "name": "Visual Studio Code", "command": "code", "desc": "Microsoft VS Code IDE"},
            {"id": "textedit", "name": "TextEdit", "command": "open -a TextEdit", "desc": "Apple TextEdit editor"},
            {"id": "subl", "name": "Sublime Text", "command": "subl", "desc": "Sublime Text editor"},
            {"id": "bbedit", "name": "BBEdit", "command": "bbedit", "desc": "BBEdit text editor"},
            {"id": "nano", "name": "GNU nano", "command": "nano", "desc": "Terminal text editor"},
            {"id": "vim", "name": "Vim", "command": "vim", "desc": "Vi IMproved editor"},
        ]
    elif sys.platform == "win32":
        return [
            {"id": "start", "name": "System Default Application", "command": "start", "desc": "Windows default file handler"},
            {"id": "notepad", "name": "Notepad", "command": "notepad", "desc": "Windows standard Notepad"},
            {"id": "code", "name": "Visual Studio Code", "command": "code", "desc": "Microsoft VS Code IDE"},
            {"id": "notepad++", "name": "Notepad++", "command": "notepad++", "desc": "Notepad++ source code editor"},
            {"id": "subl", "name": "Sublime Text", "command": "subl", "desc": "Sublime Text editor"},
        ]
    else:
        return [
            {"id": "xdg-open", "name": "System Default Application", "command": "xdg-open", "desc": "Linux desktop default handler"},
            {"id": "code", "name": "Visual Studio Code", "command": "code", "desc": "Microsoft VS Code IDE"},
            {"id": "gedit", "name": "GNOME Text Editor (gedit)", "command": "gedit", "desc": "Standard GNOME text editor"},
            {"id": "kate", "name": "Kate Advanced Editor", "command": "kate", "desc": "KDE multi-document text editor"},
            {"id": "subl", "name": "Sublime Text", "command": "subl", "desc": "Sublime Text editor"},
            {"id": "geany", "name": "Geany IDE", "command": "geany", "desc": "Fast and lightweight IDE"},
            {"id": "mousepad", "name": "Mousepad", "command": "mousepad", "desc": "XFCE simple text editor"},
            {"id": "pluma", "name": "Pluma", "command": "pluma", "desc": "MATE desktop text editor"},
            {"id": "nano", "name": "GNU nano", "command": "x-terminal-emulator -e nano", "desc": "Terminal text editor in emulator"},
            {"id": "vim", "name": "Vim", "command": "x-terminal-emulator -e vim", "desc": "Vi IMproved in terminal emulator"},
        ]


KNOWN_EDITORS = get_known_editors()


class EditorPreferencesManager:
    """
    Manages default editors, filetype associations, and auto-detects installed editors.
    Persisted to ~/.config/winscp-linux/editor_settings.json.
    """

    def __init__(self, config_file: Optional[Path | str] = None):
        if config_file:
            self.config_path = Path(config_file)
        else:
            self.config_path = Path.home() / ".config" / "winscp-linux" / "editor_settings.json"

        self.default_editor: Optional[str] = None
        self.always_ask: bool = True  # By default, ask user until they check a default
        self.associations: Dict[str, str] = {}  # e.g. {".py": "code", ".txt": "gedit"}
        self.custom_commands: List[str] = []

        self.load()

    def get_installed_editors(self) -> List[EditorInfo]:
        """Scans the system for installed editors."""
        installed: List[EditorInfo] = []

        # Find terminal emulator if available
        term_cmd = None
        for cand in ["x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal", "xterm"]:
            if shutil.which(cand):
                term_cmd = cand
                break

        for e in KNOWN_EDITORS:
            cmd = e["command"]
            binary = cmd.split()[0]

            if e["id"] in ("nano", "vim"):
                # Special handling for terminal editors
                editor_bin = e["id"]
                if shutil.which(editor_bin):
                    if term_cmd:
                        run_cmd = f"{term_cmd} -e {editor_bin}" if term_cmd != "gnome-terminal" else f"gnome-terminal -- {editor_bin}"
                        installed.append(EditorInfo(
                            id=e["id"],
                            name=e["name"],
                            command=run_cmd,
                            description=e["desc"],
                            is_installed=True,
                        ))
                    else:
                        installed.append(EditorInfo(
                            id=e["id"],
                            name=e["name"],
                            command=editor_bin,
                            description=e["desc"],
                            is_installed=True,
                        ))
            else:
                if shutil.which(binary):
                    installed.append(EditorInfo(
                        id=e["id"],
                        name=e["name"],
                        command=cmd,
                        description=e["desc"],
                        is_installed=True,
                    ))

        # Include custom commands configured by user
        for custom_cmd in self.custom_commands:
            first = custom_cmd.split()[0]
            if shutil.which(first) or os.path.exists(first):
                installed.append(EditorInfo(
                    id=custom_cmd,
                    name=f"Custom: {Path(first).name}",
                    command=custom_cmd,
                    description=custom_cmd,
                    is_installed=True,
                ))

        return installed

    def get_configured_editor(self, filename_or_path: str) -> Optional[str]:
        """
        Returns the configured editor for this file (extension association or global default),
        regardless of always_ask setting.
        """
        ext = Path(filename_or_path).suffix.lower()
        if ext and ext in self.associations:
            return self.associations[ext]
        return self.default_editor

    def get_editor_for_file(self, filename_or_path: str) -> Optional[str]:
        """
        Determines which editor command to use for a given file.
        Returns None if user should be prompted.
        """
        if self.always_ask:
            return None

        ext = Path(filename_or_path).suffix.lower()
        if ext and ext in self.associations:
            return self.associations[ext]

        if self.default_editor:
            return self.default_editor

        return None

    def set_default_editor(self, command: str, for_extension: Optional[str] = None, always_ask: bool = False):
        """Sets the default editor command globally or for a specific extension."""
        clean_cmd = command.strip()
        if for_extension:
            ext = for_extension.strip().lower()
            if not ext.startswith("."):
                ext = f".{ext}"
            self.associations[ext] = clean_cmd
        else:
            self.default_editor = clean_cmd

        self.always_ask = always_ask

        known_cmds = [e["command"] for e in KNOWN_EDITORS]
        if clean_cmd not in self.custom_commands and clean_cmd not in known_cmds:
            self.custom_commands.append(clean_cmd)

        self.save()

    def remove_association(self, extension: str):
        """Removes association for a specific extension."""
        ext = extension.strip().lower()
        if not ext.startswith("."):
            ext = f".{ext}"
        if ext in self.associations:
            del self.associations[ext]
            self.save()

    def clear_associations(self):
        """Clears all extension associations."""
        self.associations.clear()
        self.save()

    def reset_to_defaults(self):
        """Resets all settings to fresh initial defaults."""
        self.default_editor = None
        self.always_ask = True
        self.associations.clear()
        self.custom_commands.clear()
        self.save()

    def load(self):
        """Loads editor settings from JSON file."""
        if not self.config_path.exists():
            return

        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            self.default_editor = data.get("default_editor")
            self.always_ask = data.get("always_ask", True)
            self.associations = data.get("associations", {})
            self.custom_commands = data.get("custom_commands", [])
        except Exception:
            pass

    def save(self):
        """Saves editor settings to JSON file."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "default_editor": self.default_editor,
            "always_ask": self.always_ask,
            "associations": self.associations,
            "custom_commands": self.custom_commands,
        }
        try:
            self.config_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass


# Global singleton instance
editor_preferences = EditorPreferencesManager()


def get_clean_editor_env() -> dict:
    """
    Returns a sanitized copy of os.environ without internal winscp LD_LIBRARY_PATH
    pollution, preventing crashes when launching external editors like VS Code.
    """
    clean_env = os.environ.copy()
    if "LD_LIBRARY_PATH" in clean_env:
        paths = [
            p for p in clean_env["LD_LIBRARY_PATH"].split(":")
            if p and "winscp" not in p.lower() and not p.endswith("/lib")
        ]
        if paths:
            clean_env["LD_LIBRARY_PATH"] = ":".join(paths)
        else:
            clean_env.pop("LD_LIBRARY_PATH", None)

    clean_env.pop("_WINSCP_XCB_LOADED", None)
    return clean_env


def launch_external_editor(editor_command: Optional[str], file_path: str) -> None:
    """
    Safely launches an external editor on file_path across Linux, macOS, and Windows.
    Detaches the process into a new session, sets stdin to DEVNULL,
    and removes library path pollution so applications like VS Code / Electron run reliably.
    """
    cmd_str = (editor_command or "").strip()

    # Windows native default opener
    if sys.platform == "win32" and (not cmd_str or cmd_str.lower() in ("start", "default", "os.startfile")):
        try:
            if hasattr(os, "startfile"):
                os.startfile(file_path)
                return
        except Exception:
            pass

    clean_env = get_clean_editor_env()
    cmd: List[str] = []

    if cmd_str:
        try:
            cmd = shlex.split(cmd_str) + [file_path]
        except Exception:
            cmd = cmd_str.split() + [file_path]
    elif os.environ.get("EDITOR"):
        cmd = [os.environ["EDITOR"], file_path]
    else:
        opener = get_default_system_opener()
        cmd = [opener, file_path]

    # Special handling for macOS 'open'
    if sys.platform == "darwin" and cmd and cmd[0] == "open" and "-a" not in cmd and len(cmd) == 2:
        cmd = ["open", file_path]

    kwargs = {
        "env": clean_env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    else:
        kwargs["start_new_session"] = True

    try:
        subprocess.Popen(cmd, **kwargs)
    except Exception as exc:
        # Fallback to system default opener
        opener = get_default_system_opener()
        try:
            if sys.platform == "win32" and hasattr(os, "startfile"):
                os.startfile(file_path)
            else:
                fallback_kwargs = kwargs.copy()
                subprocess.Popen([opener, file_path], **fallback_kwargs)
        except Exception as e:
            raise RuntimeError(f"Could not launch editor '{cmd}': {exc} / {e}")
