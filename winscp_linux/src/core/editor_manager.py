"""
Editor Preferences Manager for WinSCP-Linux.
Detects installed Linux text editors/IDEs, manages file extension associations,
and persists default application preferences.
"""

from __future__ import annotations

import json
import os
import shutil
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


KNOWN_EDITORS = [
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
