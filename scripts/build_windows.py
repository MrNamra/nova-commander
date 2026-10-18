#!/usr/bin/env python3
"""
Windows Build & Packaging Script for WinSCP (Cross-Platform).
Compiles the application into a standalone Windows executable (.exe)
using PyInstaller and creates an Inno Setup installer script.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT_DIR / "dist"
BUILD_DIR = ROOT_DIR / "build"
APP_NAME = "WinSCP-CrossPlatform"
VERSION = "1.0.0"


def check_prerequisites():
    print("==> Checking build prerequisites for Windows...")
    try:
        import PyQt6
        import paramiko
        import cryptography
        import watchdog
        import httpx
        import pyte
        import PyInstaller
        print("  ✔ All Python dependencies and PyInstaller found.")
    except ImportError as e:
        print(f"  ❌ Missing required package: {e}")
        print("  Please run: pip install -r requirements.txt pyinstaller")
        sys.exit(1)


def build_pyinstaller():
    print("==> Running PyInstaller to build Windows executable...")
    spec_file = ROOT_DIR / "winscp.spec"
    
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        str(spec_file),
    ]

    subprocess.check_call(cmd, cwd=str(ROOT_DIR))
    print(f"  ✔ PyInstaller build completed successfully. Output at: {DIST_DIR}")


def generate_inno_setup_script():
    print("==> Generating Inno Setup script (scripts/windows_installer.iss)...")
    iss_content = f"""
; Inno Setup Script for WinSCP Cross-Platform
#define MyAppName "{APP_NAME}"
#define MyAppVersion "{VERSION}"
#define MyAppPublisher "WinSCP Team"
#define MyAppURL "https://github.com/winscp-linux/winscp-linux"
#define MyAppExeName "WinSCP.exe"

[Setup]
AppId={{{{C5E89B7D-4F2A-4D39-8472-E420C739D7F1}}}}
AppName={{#MyAppName}}
AppVersion={{#MyAppVersion}}
AppPublisher={{#MyAppPublisher}}
AppPublisherURL={{#MyAppURL}}
AppSupportURL={{#MyAppURL}}
AppUpdatesURL={{#MyAppURL}}
DefaultDirName={{autopf}}\\{{#MyAppName}}
DisableProgramGroupPage=yes
OutputBaseFilename=WinSCP-Setup-{{#MyAppVersion}}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\\winscp_linux\\src\\resources\\winscp-linux.ico
UninstallDisplayIcon={{app}}\\{{#MyAppExeName}}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{{cm:CreateDesktopIcon}}"; GroupDescription: "{{cm:AdditionalIcons}}"; Flags: unchecked

[Files]
Source: "..\\dist\\WinSCP.exe"; DestDir: "{{app}}"; Flags: ignoreversion
Source: "..\\winscp_linux\\src\\resources\\*"; DestDir: "{{app}}\\resources"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{{autoprograms}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"
Name: "{{autodesktop}}\\{{#MyAppName}}"; Filename: "{{app}}\\{{#MyAppExeName}}"; Tasks: desktopicon

[Run]
Filename: "{{app}}\\{{#MyAppExeName}}"; Description: "{{cm:LaunchProgram,{{#StringChange(MyAppName, '&', '&&')}}}}"; Flags: nowait postinstall skipifsilent
"""
    iss_path = ROOT_DIR / "scripts" / "windows_installer.iss"
    iss_path.write_text(iss_content.strip(), encoding="utf-8")
    print(f"  ✔ Created Inno Setup script at: {iss_path}")


def main():
    print("=" * 60)
    print(f"Building {APP_NAME} v{VERSION} for Windows")
    print("=" * 60)

    check_prerequisites()
    build_pyinstaller()
    generate_inno_setup_script()

    print("\n" + "=" * 60)
    print("✔ Windows Build Process Completed!")
    print(f"Standalone executable: {DIST_DIR / 'WinSCP.exe'}")
    print(f"Inno Setup script:     {ROOT_DIR / 'scripts' / 'windows_installer.iss'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
