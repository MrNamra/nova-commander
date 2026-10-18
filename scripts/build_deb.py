#!/usr/bin/env python3
"""
Automated Debian Package (.deb) Builder for WinSCP-Linux.
Constructs a standard, production-ready Debian package including:
- /opt/winscp-linux/ application files
- /usr/bin/winscp-linux launcher executable
- /usr/share/applications/winscp-linux.desktop
- /usr/share/icons/hicolor/ & /usr/share/pixmaps/ icons
- /usr/share/doc/winscp-linux/ documentation & copyright
- DEBIAN/ control, postinst, and postrm maintenance scripts
"""

from __future__ import annotations

import gzip
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PACKAGE_NAME = "nova-commander"
VERSION = "1.0.0"
ARCHITECTURE = "amd64"
MAINTAINER = "Nova Commander Team <support@novacommander.org>"
HOMEPAGE = "https://github.com/novacommander/nova-commander"


def create_deb_package(repo_root: Path) -> Path:
    print(f"==> Building {PACKAGE_NAME}_{VERSION}_{ARCHITECTURE}.deb...")

    build_dir = repo_root / "build" / "deb"
    pkg_dir_name = f"{PACKAGE_NAME}_{VERSION}_{ARCHITECTURE}"
    staging_dir = build_dir / pkg_dir_name
    dist_dir = repo_root / "dist"

    # Clean previous build directories
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    dist_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    # 1. Target Directories
    opt_app_dir = staging_dir / "opt" / PACKAGE_NAME
    usr_bin_dir = staging_dir / "usr" / "bin"
    usr_apps_dir = staging_dir / "usr" / "share" / "applications"
    usr_icons_dir = staging_dir / "usr" / "share" / "icons" / "hicolor"
    usr_pixmaps_dir = staging_dir / "usr" / "share" / "pixmaps"
    usr_doc_dir = staging_dir / "usr" / "share" / "doc" / PACKAGE_NAME
    debian_dir = staging_dir / "DEBIAN"

    for d in [opt_app_dir, usr_bin_dir, usr_apps_dir, usr_pixmaps_dir, usr_doc_dir, debian_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # 2. Copy Application Files into /opt/winscp-linux/
    print("  -> Copying application files to /opt/winscp-linux/...")
    src_origin = repo_root / "winscp_linux" / "src"
    lib_origin = repo_root / "winscp_linux" / "lib"

    def ignore_patterns(dir_path, names):
        ignored = []
        for name in names:
            if name in ("__pycache__", ".pytest_cache", ".git"):
                ignored.append(name)
            elif name.endswith((".pyc", ".pyo")):
                ignored.append(name)
        return ignored

    shutil.copytree(src_origin, opt_app_dir / "src", ignore=ignore_patterns, dirs_exist_ok=True)

    if lib_origin.exists():
        shutil.copytree(lib_origin, opt_app_dir / "lib", dirs_exist_ok=True, symlinks=True)

    for item in ["requirements.txt", "README.md"]:
        origin_item = repo_root / "winscp_linux" / item
        if origin_item.exists():
            shutil.copy2(origin_item, opt_app_dir / item)

    # Copy root runner main.py into opt as well
    if (repo_root / "main.py").exists():
        shutil.copy2(repo_root / "main.py", opt_app_dir / "main.py")

    # 3. Create /usr/bin/nova-commander Launcher Script & Symlink
    print(f"  -> Generating /usr/bin/{PACKAGE_NAME} wrapper...")
    launcher_path = usr_bin_dir / PACKAGE_NAME
    launcher_content = f"""#!/usr/bin/env bash
# Launcher wrapper for {PACKAGE_NAME}
set -e

export _NOVA_APP_DIR="/opt/{PACKAGE_NAME}"
export _WINSCP_APP_DIR="/opt/{PACKAGE_NAME}"
if [ -d "/opt/{PACKAGE_NAME}/lib" ]; then
    export LD_LIBRARY_PATH="/opt/{PACKAGE_NAME}/lib${{LD_LIBRARY_PATH:+:${{LD_LIBRARY_PATH}}}}"
fi
export PYTHONPATH="/opt/{PACKAGE_NAME}:/opt/{PACKAGE_NAME}/src${{PYTHONPATH:+:${{PYTHONPATH}}}}"

exec python3 /opt/{PACKAGE_NAME}/src/main.py "$@"
"""
    launcher_path.write_text(launcher_content, encoding="utf-8")
    launcher_path.chmod(0o755)

    # Legacy compatibility symlink /usr/bin/winscp-linux -> nova-commander
    legacy_symlink = usr_bin_dir / "winscp-linux"
    if not legacy_symlink.exists():
        legacy_symlink.symlink_to(PACKAGE_NAME)

    # 4. Create Desktop Entry in /usr/share/applications/
    print("  -> Generating desktop entry...")
    desktop_file = usr_apps_dir / f"{PACKAGE_NAME}.desktop"
    desktop_content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name=Nova Commander
GenericName=SFTP & SSH Commander
Comment=Professional native Linux SFTP & SSH Dual-Pane Commander with Terminal and Auto-Sync
Exec=/usr/bin/{PACKAGE_NAME} %U
Icon={PACKAGE_NAME}
Terminal=false
Categories=Network;FileTransfer;
MimeType=x-scheme-handler/sftp;x-scheme-handler/ssh;
Keywords=sftp;ftp;ssh;commander;transfer;dual-pane;sync;nova;
StartupNotify=true
StartupWMClass=Nova Commander
"""
    desktop_file.write_text(desktop_content, encoding="utf-8")
    desktop_file.chmod(0o644)

    # 5. Install Icons
    print("  -> Installing icons...")
    res_dir = repo_root / "winscp_linux" / "src" / "resources"

    # Vector SVG
    svg_target_dir = usr_icons_dir / "scalable" / "apps"
    svg_target_dir.mkdir(parents=True, exist_ok=True)
    if (res_dir / "winscp-linux.svg").exists():
        shutil.copy2(res_dir / "winscp-linux.svg", svg_target_dir / f"{PACKAGE_NAME}.svg")
        shutil.copy2(res_dir / "winscp-linux.svg", svg_target_dir / "winscp-linux.svg")

    # Standard PNG sizes
    icon_sizes = [16, 32, 48, 64, 128, 256, 512]
    for s in icon_sizes:
        size_png = res_dir / f"winscp-linux-{s}x{s}.png"
        target_size_dir = usr_icons_dir / f"{s}x{s}" / "apps"
        target_size_dir.mkdir(parents=True, exist_ok=True)
        if size_png.exists():
            shutil.copy2(size_png, target_size_dir / f"{PACKAGE_NAME}.png")
            shutil.copy2(size_png, target_size_dir / "winscp-linux.png")
        elif (res_dir / "winscp-linux.png").exists():
            shutil.copy2(res_dir / "winscp-linux.png", target_size_dir / f"{PACKAGE_NAME}.png")
            shutil.copy2(res_dir / "winscp-linux.png", target_size_dir / "winscp-linux.png")

    # Pixmaps for classic menus
    if (res_dir / "winscp-linux-48x48.png").exists():
        shutil.copy2(res_dir / "winscp-linux-48x48.png", usr_pixmaps_dir / f"{PACKAGE_NAME}.png")
        shutil.copy2(res_dir / "winscp-linux-48x48.png", usr_pixmaps_dir / "winscp-linux.png")
    elif (res_dir / "winscp-linux.png").exists():
        shutil.copy2(res_dir / "winscp-linux.png", usr_pixmaps_dir / f"{PACKAGE_NAME}.png")
        shutil.copy2(res_dir / "winscp-linux.png", usr_pixmaps_dir / "winscp-linux.png")

    # 6. Documentation & Copyright
    print("  -> Generating package documentation & changelog...")
    copyright_file = usr_doc_dir / "copyright"
    copyright_content = f"""Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: {PACKAGE_NAME}
Source: {HOMEPAGE}

Files: *
Copyright: 2026 {MAINTAINER}
License: MIT
 Permission is hereby granted, free of charge, to any person obtaining a copy
 of this software and associated documentation files (the "Software"), to deal
 in the Software without restriction, including without limitation the rights
 to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 copies of the Software, and to permit persons to whom the Software is
 furnished to do so, subject to the following conditions:
 .
 The above copyright notice and this permission notice shall be included in all
 copies or substantial portions of the Software.
"""
    copyright_file.write_text(copyright_content, encoding="utf-8")
    copyright_file.chmod(0o644)

    if (repo_root / "winscp_linux" / "README.md").exists():
        shutil.copy2(repo_root / "winscp_linux" / "README.md", usr_doc_dir / "README.md")

    # Gzipped Changelog
    changelog_raw = f"""{PACKAGE_NAME} ({VERSION}) stable; urgency=medium

  * Initial release of WinSCP-Linux.
  * Multi-session tabbed commander architecture.
  * Dual-pane commander with bidirectional drag-and-drop.
  * Remote direct editing with watchdog inotify auto-sync.
  * Embedded interactive SSH terminal.
  * Open With selector and configurable default application associations.
  * AES-256-GCM encrypted WinSCP-compatible .ini export and cloud backup.

 -- {MAINTAINER}  {datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000")}
"""
    with gzip.open(usr_doc_dir / "changelog.Debian.gz", "wt", encoding="utf-8") as gz:
        gz.write(changelog_raw)
    (usr_doc_dir / "changelog.Debian.gz").chmod(0o644)

    # 7. Maintainer Scripts (postinst and postrm)
    print("  -> Generating maintainer scripts...")
    postinst_file = debian_dir / "postinst"
    postinst_content = """#!/bin/sh
set -e

# Update desktop application database
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi

# Update icon theme cache
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
fi

exit 0
"""
    postinst_file.write_text(postinst_content, encoding="utf-8")
    postinst_file.chmod(0o755)

    postrm_file = debian_dir / "postrm"
    postrm_content = """#!/bin/sh
set -e

if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
    fi
fi

exit 0
"""
    postrm_file.write_text(postrm_content, encoding="utf-8")
    postrm_file.chmod(0o755)

    # 8. Compute Installed Size
    total_bytes = 0
    for root, dirs, files in os.walk(staging_dir):
        if Path(root) == debian_dir or debian_dir in Path(root).parents:
            continue
        for f in files:
            p = Path(root) / f
            if not p.is_symlink():
                total_bytes += p.stat().st_size
    installed_size_kb = max(1, total_bytes // 1024)

    # 9. Control File
    print("  -> Writing DEBIAN/control file...")
    control_file = debian_dir / "control"
    control_content = f"""Package: {PACKAGE_NAME}
Version: {VERSION}
Section: net
Priority: optional
Architecture: {ARCHITECTURE}
Installed-Size: {installed_size_kb}
Maintainer: {MAINTAINER}
Depends: python3 (>= 3.10), python3-pyqt6, python3-paramiko, python3-cryptography, python3-watchdog, python3-httpx
Recommends: libxcb-cursor0, xdg-utils
Provides: winscp-linux
Homepage: {HOMEPAGE}
Description: Professional native Linux SFTP & SSH Dual-Pane Commander
 Nova Commander is a full-featured native Linux desktop commander for SFTP and SSH.
 Features include:
  * Multi-session tabbed commander architecture
  * Dual-pane local and remote SFTP/SSH filesystem navigation
  * Bidirectional drag-and-drop for files and directories
  * Direct remote file editing with inotify auto-sync upon saving
  * Embedded interactive VT100/ANSI Linux terminal per connection
  * "Open With" application selector with configurable defaults
  * AES-256-GCM encrypted session backup and WinSCP .ini compatibility
"""
    control_file.write_text(control_content, encoding="utf-8")
    control_file.chmod(0o644)

    # 10. Normalize file permissions across staging directory
    for root, dirs, files in os.walk(staging_dir):
        for d in dirs:
            os.chmod(os.path.join(root, d), 0o755)
        for f in files:
            fp = os.path.join(root, f)
            if not os.path.islink(fp):
                current_mode = os.stat(fp).st_mode
                if current_mode & stat.S_IXUSR:
                    os.chmod(fp, 0o755)
                else:
                    os.chmod(fp, 0o644)

    # Ensure scripts have 755
    launcher_path.chmod(0o755)
    postinst_file.chmod(0o755)
    postrm_file.chmod(0o755)

    # 11. Run dpkg-deb to Build the Package
    deb_filename = f"{PACKAGE_NAME}_{VERSION}_{ARCHITECTURE}.deb"
    output_deb_path = dist_dir / deb_filename
    root_deb_path = repo_root / deb_filename

    print(f"  -> Building package with dpkg-deb: {output_deb_path}...")
    cmd = [
        "dpkg-deb",
        "--root-owner-group",
        "--build",
        str(staging_dir),
        str(output_deb_path),
    ]
    res = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    print(res.stdout.strip())

    # Copy to repo root for instant access
    shutil.copy2(output_deb_path, root_deb_path)
    print(f"✔ Successfully created Debian package at:\n  - {output_deb_path}\n  - {root_deb_path}")

    return output_deb_path


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parent.parent
    create_deb_package(repo_root)
