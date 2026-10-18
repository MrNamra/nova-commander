# User Interface and Embedded Terminal Specifications

## 1. Login & Session Manager
*   **Layout:** Left sidebar showing a tree view of saved sessions (grouped by folders). Right panel with input fields: Hostname, Port, Username, Password (hidden).
*   **Actions:** Login, Save, Delete, **"Export to Cloud"**, **"Import from Cloud"**.

## 2. Main Interface (Commander View)
*   **Dual-Pane System:** 
    *   Left Pane (Local): A `QTreeView` and `QFileSystemModel` showing the local Linux filesystem.
    *   Right Pane (Remote): A `QTreeView` populated via the `paramiko` SFTP directory listing.
*   **Toolbar:** standard WinSCP buttons (Upload, Download, Properties, Edit, Delete, **Open Terminal**).
*   **Drag & Drop:** Must support dragging files between the local and remote panes to trigger transfers.

## 3. Embedded SSH Terminal
*   **Trigger:** Clicking the "Open Terminal" button on an active remote session.
*   **Implementation:** 
    *   Do NOT launch external apps (like xterm or gnome-terminal).
    *   Embed a terminal directly into the UI (e.g., as a bottom dock widget or a new tab).
    *   The terminal must share the same connection credentials or multiplex the existing SSH channel established by the session.