# SFTP Transfer and Remote Editing Logic

## 1. Remote Execution & Transfers
*   **Threading:** ALL network operations (connect, upload, download, list dir) MUST be executed in `QThread` workers. The main GUI thread must never block.
*   **Progress Tracking:** Large transfers must emit signals to update a `QProgressBar` in the UI.

## 2. The "Remote Edit" Workflow
When a user double-clicks a file in the remote pane, the application must:
1.  Download the file to `/tmp/linux-scp-cache/<session_id>/<filename>`.
2.  Launch the user's default text editor using `subprocess.Popen(['xdg-open', filepath])`.
3.  Instantiate a `watchdog.observers.Observer` targeting that specific file.
4.  When `watchdog` detects an `on_modified` event, trigger an SFTP `put` operation to upload the cached file back to the original remote path.
5.  Provide visual feedback in the UI (e.g., a status bar message: "File uploaded successfully").