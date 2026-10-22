"""Core modules for WinSCP-Linux."""
from .editor_manager import EditorInfo, EditorPreferencesManager, editor_preferences
from .session_store import SessionConfig, SessionStore
from .sftp_client import (
    RemoteFileInfo,
    RemoteStat,
    SFTPActionWorker,
    SFTPBatchTransferWorker,
    SFTPClient,
    SFTPConnectWorker,
    SFTPDownloadWorker,
    SFTPListDirWorker,
    SFTPUploadWorker,
)
from .git_manager import (
    GitChangedFile,
    GitCommit,
    GitDeploymentRecord,
    GitManager,
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
    "GitChangedFile",
    "GitCommit",
    "GitDeploymentRecord",
    "GitManager",
    "EditorInfo",
    "EditorPreferencesManager",
    "editor_preferences",
    "SessionConfig",
    "SessionStore",
    "RemoteFileInfo",
    "RemoteStat",
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
