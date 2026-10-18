#!/usr/bin/env python3
"""
macOS Build & Packaging Script for WinSCP (Cross-Platform).
Compiles the application into a native macOS Application Bundle (.app)
and creates a distributable Disk Image (.dmg) with drag-and-drop installer.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT_DIR / "dist"
BUILD_DIR = ROOT_DIR / "build"
APP_NAME = "WinSCP-Linux"
VERSION = "1.0.0"


def check_prerequisites():
    print("==> Checking build prerequisites for macOS...")
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


def build_app_bundle():
    print("==> Running PyInstaller to build macOS Application Bundle (.app)...")
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
    app_path = DIST_DIR / f"{APP_NAME}.app"
    print(f"  ✔ Application bundle created at: {app_path}")
    return app_path


def create_dmg(app_path: Path):
    print("==> Creating macOS Disk Image (.dmg)...")
    dmg_name = f"{APP_NAME}-{VERSION}.dmg"
    dmg_path = DIST_DIR / dmg_name

    if shutil.which("hdiutil"):
        # Native macOS packaging
        temp_dmg_dir = BUILD_DIR / "dmg_root"
        if temp_dmg_dir.exists():
            shutil.rmtree(temp_dmg_dir)
        temp_dmg_dir.mkdir(parents=True, exist_ok=True)

        # Copy .app to staging directory
        dest_app = temp_dmg_dir / app_path.name
        if dest_app.exists():
            shutil.rmtree(dest_app)
        shutil.copytree(app_path, dest_app)

        # Create Applications symlink for drag-and-drop install
        apps_link = temp_dmg_dir / "Applications"
        if not apps_link.exists():
            os.symlink("/Applications", str(apps_link))

        if dmg_path.exists():
            dmg_path.unlink()

        hdiutil_cmd = [
            "hdiutil",
            "create",
            "-volname",
            APP_NAME,
            "-srcfolder",
            str(temp_dmg_dir),
            "-ov",
            "-format",
            "UDZO",
            str(dmg_path),
        ]
        subprocess.check_call(hdiutil_cmd)
        print(f"  ✔ Distributable DMG created at: {dmg_path}")
    else:
        # Fallback zip archive when running outside macOS
        zip_path = DIST_DIR / f"{APP_NAME}-{VERSION}-macos.zip"
        print(f"  ℹ hdiutil not found (non-macOS system). Creating zip archive instead...")
        shutil.make_archive(str(DIST_DIR / f"{APP_NAME}-{VERSION}-macos"), "zip", root_dir=str(DIST_DIR), base_dir=app_path.name)
        print(f"  ✔ Created archive at: {zip_path}")


def main():
    print("=" * 60)
    print(f"Building {APP_NAME} v{VERSION} for macOS")
    print("=" * 60)

    check_prerequisites()
    app_path = build_app_bundle()
    create_dmg(app_path)

    print("\n" + "=" * 60)
    print("✔ macOS Build Process Completed!")
    print("=" * 60)


if __name__ == "__main__":
    main()
