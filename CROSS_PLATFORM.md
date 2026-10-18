# WinSCP Cross-Platform: macOS & Windows Support Guide

WinSCP-Linux has been built from the ground up on modern, portable standards (**Python 3.10+**, **PyQt6**, **Paramiko**, **Watchdog**, and **Cryptography**). Because of this architecture, the entire application can run natively and be packaged on **macOS** and **Windows** in addition to **Linux**.

---

## 1. Architectural Matrix

| Feature | Linux | macOS | Windows |
| :--- | :--- | :--- | :--- |
| **GUI Framework** | PyQt6 (Fusion / Native) | PyQt6 (Native Cocoa) | PyQt6 (Native Windows) |
| **SSH / SFTP Engine** | Paramiko | Paramiko | Paramiko |
| **Inotify / Auto-Sync** | Watchdog (`inotify`) | Watchdog (`FSEvents` / `kqueue`) | Watchdog (`ReadDirectoryChangesW`) |
| **Embedded Terminal** | Pyte + QPainter (`DejaVu Sans Mono`) | Pyte + QPainter (`Menlo`) | Pyte + QPainter (`Consolas`) |
| **Default File Opener** | `xdg-open <file>` | `open <file>` | `os.startfile(file)` / `start` |
| **Local File Roots** | `/` (POSIX root) | `/` (POSIX root) | Drive Letters (`C:\`, `D:\`, ...) |
| **Packaging Target** | `.deb` / AppImage / Binary | `.dmg` / `.app` Bundle | `.exe` / Inno Setup Installer |

---

## 2. Platform-Specific Optimizations Made

### macOS Support
1. **Application Opener:** Configured to invoke native macOS `open` handler or application bundles (e.g., `open -a TextEdit`, `code`, `subl`).
2. **Terminal Font:** Automatically selects Apple's native monospace font (`Menlo` or `Monaco`).
3. **Packaging (`scripts/build_macos.py` / `scripts/build_macos.sh`):**
   - Bundles the application into `WinSCP-Linux.app` with proper `Info.plist`, High-DPI Retina support (`NSHighResolutionCapable`), and custom `.icns` icon.
   - Generates a drag-and-drop installer disk image: `WinSCP-Linux-1.0.0.dmg`.

### Windows Support
1. **Application Opener:** Uses Python's native `os.startfile(path)` for seamless opening with whatever software is registered in the Windows Shell registry.
2. **Local Drive Navigation:** Automatically detects and displays Windows drive roots (`C:\`, `D:\`, etc.) when navigating at drive root level.
3. **Terminal Font:** Automatically selects Windows native monospace font (`Consolas`).
4. **Process Isolation:** Uses `creationflags=CREATE_NEW_PROCESS_GROUP` so external applications (like VS Code, Notepad++, etc.) detach safely without crashing the main application.
5. **Packaging (`scripts/build_windows.py` / `scripts/build_windows.bat`):**
   - Bundles into a standalone `WinSCP.exe` with `winscp-linux.ico`.
   - Generates an Inno Setup script (`scripts/windows_installer.iss`) to compile `WinSCP-Setup-1.0.0.exe` with desktop shortcut and start menu entry.

---

## 3. How to Run & Build on macOS

### Prerequisites
- macOS 11.0 (Big Sur) or newer
- Python 3.10+ (`brew install python@3.12`)

### Running from Source
```bash
git clone https://github.com/winscp-linux/winscp-linux.git
cd winscp-linux

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run application
python3 winscp_linux/src/main.py
```

### Building `.app` and `.dmg`
```bash
chmod +x scripts/build_macos.sh
./scripts/build_macos.sh
```
*Outputs:*
- `dist/WinSCP-Linux.app` (macOS Application Bundle)
- `dist/WinSCP-Linux-1.0.0.dmg` (macOS Drag-and-Drop Disk Image)

---

## 4. How to Run & Build on Windows

### Prerequisites
- Windows 10 or Windows 11 (64-bit)
- Python 3.10+ (Installed from [python.org](https://www.python.org/downloads/) with *"Add Python to PATH"* checked)
- Optional: [Inno Setup 6](https://jrsoftware.org/isdl.php) (for building the `.exe` installer)

### Running from Source
Open PowerShell or Command Prompt:
```cmd
git clone https://github.com/winscp-linux/winscp-linux.git
cd winscp-linux

REM Create virtual environment
python -m venv venv
call venv\Scripts\activate

REM Install dependencies
pip install -r requirements.txt

REM Run application
python winscp_linux\src\main.py
```

### Building Standalone Executable & Installer
Double-click `scripts\build_windows.bat` or run:
```cmd
python scripts\build_windows.py
```
*Outputs:*
- `dist\WinSCP.exe` (Standalone Windows Executable)
- `dist\WinSCP-Setup-1.0.0.exe` (Windows Installer via Inno Setup)

---

## 5. Automated CI/CD (GitHub Actions)

A complete multi-platform GitHub Actions workflow is provided at [`ci/build_all_platforms.yml`](ci/build_all_platforms.yml).
*(To enable automated CI runs on GitHub, move this file to `.github/workflows/build_all_platforms.yml`)*

Whenever code is pushed or a tag (`v*`) is created:
1. **Ubuntu Runner:** Builds `winscp-linux_1.0.0_amd64.deb` and standalone Linux binary.
2. **macOS Runner:** Builds `WinSCP-Linux-1.0.0.dmg` and `WinSCP.app.zip`.
3. **Windows Runner:** Builds `WinSCP.exe` and `WinSCP-Setup-1.0.0.exe`.
4. **GitHub Releases:** Automatically publishes all 3 platform packages to the release page.
