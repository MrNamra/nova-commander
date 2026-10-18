# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller Cross-Platform Specification for WinSCP (Linux, macOS, Windows).
Generates:
  - Linux: Standalone binary (dist/winscp-linux)
  - Windows: Standalone executable (dist/WinSCP-Linux.exe)
  - macOS: Application Bundle (dist/WinSCP-Linux.app)
"""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None
project_root = Path.cwd()

# Collect resources and submodules
datas = [
    (str(project_root / "winscp_linux" / "src" / "resources"), "resources"),
]

hiddenimports = [
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "paramiko",
    "cryptography",
    "cryptography.hazmat.primitives.ciphers.aead",
    "watchdog",
    "watchdog.observers",
    "httpx",
    "pyte",
]

# Pick icon for target platform
icon_file = None
if sys.platform == "win32":
    ico_path = project_root / "winscp_linux" / "src" / "resources" / "winscp-linux.ico"
    if ico_path.exists():
        icon_file = str(ico_path)
elif sys.platform == "darwin":
    icns_path = project_root / "winscp_linux" / "src" / "resources" / "winscp-linux.icns"
    if icns_path.exists():
        icon_file = str(icns_path)

if not icon_file:
    png_path = project_root / "winscp_linux" / "src" / "resources" / "winscp-linux.png"
    if png_path.exists():
        icon_file = str(png_path)

a = Analysis(
    [str(project_root / "winscp_linux" / "src" / "main.py")],
    pathex=[
        str(project_root / "winscp_linux" / "src"),
        str(project_root / "winscp_linux"),
    ],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="NovaCommander" if sys.platform == "win32" else "nova-commander",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)

# macOS Application Bundle (.app)
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="NovaCommander.app",
        icon=icon_file,
        bundle_identifier="org.novacommander.app",
        info_plist={
            "CFBundleName": "Nova Commander",
            "CFBundleDisplayName": "Nova Commander",
            "CFBundleGetInfoString": "Professional SFTP & SSH Commander",
            "CFBundleIdentifier": "org.novacommander.app",
            "CFBundleVersion": "1.0.0",
            "CFBundleShortVersionString": "1.0.0",
            "NSHighResolutionCapable": "True",
            "LSMinimumSystemVersion": "11.0",
        },
    )
