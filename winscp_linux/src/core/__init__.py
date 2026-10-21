"""Core modules for WinSCP-Linux."""
from .editor_manager import EditorInfo, EditorPreferencesManager, editor_preferences
from .session_store import SessionConfig, SessionStore
from .sftp_client import (
    RemoteFileInfo,
    SFTPActionWorker,
    SFTPBatchTransferWorker,
    SFTPClient,
    SFTPConnectWorker,
    SFTPDownloadWorker,
    SFTPListDirWorker,
    SFTPUploadWorker,
)
from .ssh_client import SSHShellClient
from .sync_engine import SyncEngine
from .transfer_queue import (
    TransferJob,
    TransferPriority,
    TransferQueueEngine,
    TransferStatus,
)

__all__ = [
    "EditorInfo",
    "EditorPreferencesManager",
    "editor_preferences",
    "SessionConfig",
    "SessionStore",
    "RemoteFileInfo",
    "SFTPActionWorker",
    "SFTPBatchTransferWorker",
    "SFTPClient",
    "SFTPConnectWorker",
    "SFTPDownloadWorker",
    "SFTPListDirWorker",
    "SFTPUploadWorker",
    "SSHShellClient",
    "SyncEngine",
    "TransferJob",
    "TransferPriority",
    "TransferQueueEngine",
    "TransferStatus",
]
