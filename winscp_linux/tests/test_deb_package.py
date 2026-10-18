"""
Tests for Debian Package (.deb) generation, control metadata, and filesystem structure.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest


def test_deb_package_exists_and_valid():
    repo_root = Path(__file__).resolve().parent.parent.parent
    deb_path = repo_root / "dist" / "nova-commander_1.0.0_amd64.deb"

    if not deb_path.exists():
        # Trigger build if not built yet
        build_script = repo_root / "scripts" / "build_deb.py"
        subprocess.run(["python3", str(build_script)], check=True, cwd=str(repo_root))

    assert deb_path.exists(), f"Debian package not found at {deb_path}"
    assert deb_path.stat().st_size > 50000, "Debian package is too small"


def test_deb_package_control_metadata():
    repo_root = Path(__file__).resolve().parent.parent.parent
    deb_path = repo_root / "dist" / "nova-commander_1.0.0_amd64.deb"

    res = subprocess.run(
        ["dpkg-deb", "-I", str(deb_path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    output = res.stdout

    assert "Package: nova-commander" in output
    assert "Version: 1.0.0" in output
    assert "Architecture: amd64" in output
    assert "python3-pyqt6" in output
    assert "python3-paramiko" in output
    assert "python3-cryptography" in output
    assert "postinst" in output
    assert "postrm" in output


def test_deb_package_contents_and_structure(tmp_path: Path):
    repo_root = Path(__file__).resolve().parent.parent.parent
    deb_path = repo_root / "dist" / "nova-commander_1.0.0_amd64.deb"

    # Inspect table of contents
    res = subprocess.run(
        ["dpkg-deb", "-c", str(deb_path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    contents = res.stdout

    assert "./usr/bin/nova-commander" in contents
    assert "./usr/bin/winscp-linux" in contents
    assert "./usr/share/applications/nova-commander.desktop" in contents
    assert "./usr/share/icons/hicolor/scalable/apps/nova-commander.svg" in contents
    assert "./usr/share/icons/hicolor/256x256/apps/nova-commander.png" in contents
    assert "./usr/share/pixmaps/nova-commander.png" in contents
    assert "./opt/nova-commander/src/main.py" in contents
    assert "./opt/nova-commander/lib/libxcb-cursor.so.0" in contents

    # Extract to tmp_path to verify file permissions and executable bits
    extract_dir = tmp_path / "extracted"
    subprocess.run(["dpkg-deb", "-x", str(deb_path), str(extract_dir)], check=True)

    launcher = extract_dir / "usr" / "bin" / "nova-commander"
    assert launcher.exists()
    launcher_mode = launcher.stat().st_mode
    assert bool(launcher_mode & stat.S_IXUSR), "Launcher must be executable by user"

    desktop_file = extract_dir / "usr" / "share" / "applications" / "nova-commander.desktop"
    assert desktop_file.exists()
    desktop_text = desktop_file.read_text(encoding="utf-8")
    assert "Exec=/usr/bin/nova-commander %U" in desktop_text
    assert "Icon=nova-commander" in desktop_text
    assert "Categories=Network;FileTransfer;" in desktop_text

    app_main = extract_dir / "opt" / "nova-commander" / "src" / "main.py"
    assert app_main.exists()
