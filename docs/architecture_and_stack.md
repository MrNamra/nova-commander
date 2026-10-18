# Project Architecture and Technology Stack

## 1. Project Objective
Develop a native Linux SFTP/SSH client that perfectly mimics the UI and functionality of WinSCP. The application must include an embedded Linux terminal and a custom feature to export/import encrypted session configurations (`.ini`) to a proprietary cloud service with a 7-day retention policy.

## 2. Technology Stack
The agent MUST strictly adhere to the following technology stack to ensure native Linux compatibility and performance:

*   **Programming Language:** Python 3.10+
*   **GUI Framework:** PyQt6 (Provides native Linux look-and-feel and complex widgets).
*   **SSH/SFTP Protocol:** `paramiko` (Standard Python library for SSH2 protocol).
*   **Terminal Emulator:** `QTermWidget` (PyQt binding for native terminal embedding) OR `pyte` if `QTermWidget` is unavailable for the target environment.
*   **File Watcher (Auto-save):** `watchdog` (Hooks into Linux `inotify`).
*   **Encryption:** `cryptography` package (specifically `Fernet` or AES-GCM primitives).

## 3. Application Structure
The application should follow an MVC (Model-View-Controller) architecture:
*   **Models:** `SessionManager` (handles saving/loading credentials), `CloudSync` (API interactions), `Crypto` (AES encryption).
*   **Views:** `LoginDialog`, `CommanderMainWindow` (Dual-pane), `TerminalPanel`.
*   **Controllers:** `SFTPWorker` (Background threads for file transfers to prevent GUI freezing), `FileWatcher` (Background thread for remote edit syncing).