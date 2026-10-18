# WinSCP-Linux

A full-featured, native Linux desktop alternative to WinSCP designed for high-productivity SFTP and SSH workflows.

WinSCP-Linux mirrors the classic dual-pane commander interface, delivers live remote file direct-editing with automatic inotify sync, embeds an interactive Linux terminal, and provides AES-256-GCM encrypted `.ini` session export/import with 7-day cloud sharing.

---

## 🌟 Key Features

### 1. Multi-Session Tabbed Architecture
- **Simultaneous SSH Connections:** Connect to multiple SSH/SFTP servers one by one. Each session runs independently in its own tab.
- **Dedicated Environment per Server:** Each tab maintains its own dual-pane commander, remote directory navigation, auto-sync editor watcher, and embedded interactive SSH terminal.
- **Quick Tab Actions:** 
  - Add new connection tab via **➕** corner button or `Ctrl+N`.
  - Close active session tab via `✕` button or `Ctrl+W`.
  - Switch smoothly between active servers without disconnecting.

### 2. Dual-Pane Commander UI
- **Dual Filesystem Navigation:** Local Linux filesystem on the left and remote SFTP server on the right.
- **Classic Commander Layout:** Columns for Name, Size, Permissions (`0755` / `rwxr-xr-x`), and Last Modified date.
- **Navigation Controls:** Editable path bars with instant "Go" and parent directory (`..`) navigation.
- **Comprehensive Toolbar:** Quick actions for Connect, Disconnect, Refresh, Upload, Download, Edit, Chmod, Delete, New Folder, Terminal, and Cloud Backup.
- **Interactive Permissions Editor (`chmod`):** Visual 3x3 permission grid (Read, Write, Execute for Owner, Group, Others) with bidirectional octal calculator.
- **Bidirectional Drag & Drop:**
  - Drag files or entire folders from the **Local pane (Left)** and drop onto the **Remote pane (Right)** to initiate background SFTP upload.
  - Drag files or entire folders from the **Remote pane (Right)** and drop onto the **Local pane (Left)** to initiate background download.
  - Drop onto specific subfolders in either pane to upload or download directly into that subfolder.
  - Drag and drop files directly from your Linux desktop file managers (Nautilus, Dolphin, Thunar, etc.) onto either pane.
- **Context Menus & Hotkeys:** Right-click context menus on both panes and standard function keys (`F4` Edit, `F5` Transfer, `F7` New Folder, `F8` Delete, `F9` Permissions).

### 2. Live Remote Direct-Edit & Auto-Sync
- **Seamless Workflow:** Double-click or right-click any remote file and select **Edit (F4)**.
- **Cache Management:** Automatically downloads the file to `/tmp/winscp-linux-cache/<session_id>/<remote_path>`.
- **System Editor Launch:** Launches your preferred Linux editor or IDE (`xdg-open`, `$EDITOR`, VS Code, GEdit, Kate, Nano, etc.).
- **Inotify File Watching:** Hooks Linux kernel `inotify` events via `watchdog` to monitor the cached file.
- **Instant Auto-Upload:** Whenever you save (`Ctrl+S`) in your editor, WinSCP-Linux debounces the event and automatically uploads the updated file back to the remote server via SFTP.
- **Status Feedback & Cleanup:** Visual status bar notifications for transfer progress and automated cache cleanup on session disconnect or application exit.

### 3. Embedded Linux Terminal
- **Zero External Dependencies:** Built-in terminal directly in the bottom dock panel without launching external terminal emulators like xterm.
- **Live SSH Multiplexing:** Multiplexes the existing SSH connection credentials into an interactive pseudo-terminal (`pty`).
- **VT100 / ANSI Emulation:** Powered by `pyte` virtual terminal screen buffer and PyQt6 `QPainter` rendering with an authentic 16-color dark palette and blinking cursor.
- **Full Shell Interactivity:** Supports arrow keys, Enter, Backspace, Tab completion, Ctrl combinations (Ctrl+C, Ctrl+D, Ctrl+Z, etc.), screen clearing, copy-paste, and dynamic window resizing (`resize_pty`).

### 4. Encrypted .ini Export & Selective Cloud Sharing
- **Selective Session Export:** Check off specific servers or select all saved sessions to export.
- **Strict WinSCP Compatibility:** Generates standard WinSCP `.ini` configuration sections:
  ```ini
  [Sessions\Production/WebServer]
  HostName=prod.example.com
  PortNumber=22
  UserName=deploy
  Password=...
  FSProtocol=0
  PublicKeyFile=/home/user/.ssh/id_rsa
  RemoteDirectory=/var/www
  ```
- **Military-Grade AES-256-GCM Encryption:**
  - Key derivation: `PBKDF2HMAC` using SHA-256 with a 16-byte cryptographically secure random salt and **600,000 iterations**.
  - Symmetric cipher: AES-256 in GCM authenticated mode with a 16-byte random IV/nonce.
  - Binary layout: `[SALT: 16 bytes] + [IV: 16 bytes] + [CIPHERTEXT] + [GCM_TAG: 16 bytes]`.
- **Cloud Backup with 7-Day Retention:**
  - Encodes the encrypted payload to Base64 and transmits via `POST /api/v1/config/upload` (`{"payload": "...", "expires_in_days": 7}`).
  - Generates a unique Share ID and Access Code with 1-click clipboard copy.
- **Cloud & File Import:**
  - Easily restore or migrate sessions on any machine: enter the Share ID and Master Password to decrypt, parse, and seamlessly merge configurations into the local session manager.

---

## 📁 Project Directory Structure

```
winscp_linux/
├── src/
│   ├── ui/
│   │   ├── main_window.py          # Dual-pane UI and toolbar
│   │   ├── session_dialog.py       # Login and saved session manager
│   │   ├── terminal_widget.py      # Embedded SSH terminal
│   │   └── export_dialog.py        # Selective session export & cloud dialog
│   ├── core/
│   │   ├── sftp_client.py          # Paramiko SFTP operations & QThread workers
│   │   ├── ssh_client.py           # Interactive SSH shell handler
│   │   ├── sync_engine.py          # Watchdog file watcher and auto-sync
│   │   └── session_store.py        # Local storage & WinSCP .ini parser
│   ├── crypto/
│   │   └── ini_cipher.py           # AES-256-GCM encryption & decryption
│   ├── cloud/
│   │   └── cloud_client.py         # Upload/download API endpoints
│   └── main.py                     # Application entry point
├── tests/
│   ├── test_crypto.py              # Tests for AES encryption/decryption
│   ├── test_ini_parser.py          # Tests for WinSCP .ini read/write
│   ├── test_cloud_client.py        # Tests for cloud client operations
│   ├── test_sync_engine.py         # Tests for cache and sync paths
│   └── test_ui_smoke.py            # Tests for Qt UI dialog initialization
├── requirements.txt
└── README.md
```

---

## 🚀 Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run application
python3 main.py
```
