# AI Agent Execution Plan

**Instructions for the AI Agent:** Read files 01 through 04 thoroughly. Build the application iteratively following these phases. Do not move to the next phase until the current one compiles and functions correctly.

*   **Phase 1: Scaffolding & Session Manager.** Setup the PyQt6 window, the local SQLite database or local JSON file to store sessions, and the Login UI.
*   **Phase 2: Local File Manager.** Implement the left pane using `QFileSystemModel`. Ensure navigation works.
*   **Phase 3: SFTP Connectivity.** Implement `paramiko`. Connect to a server and populate the right pane. Implement basic double-click to download.
*   **Phase 4: Remote Editing.** Implement the download-to-temp, `xdg-open`, and `watchdog` auto-upload logic.
*   **Phase 5: Terminal Embedding.** Integrate the SSH shell into the UI dock.
*   **Phase 6: Cloud Encryption.** Implement the Crypto model. Test encrypting and decrypting a dummy `.ini` string.
*   **Phase 7: Cloud API.** Implement the HTTP requests using `requests` or `aiohttp` to upload and download the encrypted blobs.