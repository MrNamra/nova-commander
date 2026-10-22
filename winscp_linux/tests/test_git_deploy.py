"""
Comprehensive tests for Git-FTP Deployment Engine, GitManager, conflict detection,
and Git deployment UI dialogs in Nova Commander.
"""

import os
import subprocess
import time
from pathlib import Path
import pytest
from PyQt6.QtWidgets import QApplication

from src.core.git_manager import (
    GitChangedFile,
    GitCommit,
    GitConflictScanWorker,
    GitDeployExecuteWorker,
    GitDeploymentRecord,
    GitManager,
)
from src.ui.git_deploy_dialog import (
    CloneRepoDialog,
    GitDeployDialog,
    GitDiffViewerDialog,
)

os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """Creates a temporary Git repository with sample commits, branches, and files."""
    repo = tmp_path / "sample_project"
    repo.mkdir()

    # git init
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Developer"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=repo, check=True)

    # Initial commit
    file1 = repo / "index.php"
    file1.write_text("<?php echo 'Version 1.0'; ?>")
    file_cfg = repo / "config.php"
    file_cfg.write_text("<?php return ['env' => 'prod']; ?>")

    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit: Add index.php and config.php"], cwd=repo, check=True)

    # Commit 2: Modify index.php and add new feature
    time.sleep(0.05)
    file1.write_text("<?php echo 'Version 1.1 Updated'; ?>")
    file2 = repo / "about.php"
    file2.write_text("<?php echo 'About page'; ?>")

    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "Feature: Update index and add about.php"], cwd=repo, check=True)

    # Create dev branch
    subprocess.run(["git", "branch", "staging"], cwd=repo, check=True)

    return repo


def test_git_manager_repo_detection_and_branches(temp_git_repo: Path):
    """Verifies Git repository discovery and branch listing."""
    assert GitManager.is_git_repo(temp_git_repo) is True
    assert GitManager.is_git_repo(temp_git_repo / "nonexistent") is False

    root = GitManager.get_repo_root(temp_git_repo)
    assert root == str(temp_git_repo.resolve())

    branch = GitManager.get_current_branch(str(temp_git_repo))
    assert branch == "main"

    branches = GitManager.list_branches(str(temp_git_repo))
    assert "main" in branches
    assert "staging" in branches


def test_git_manager_branch_checkout(temp_git_repo: Path):
    """Verifies switching branches via checkout_branch."""
    success, msg = GitManager.checkout_branch(str(temp_git_repo), "staging")
    assert success is True
    assert GitManager.get_current_branch(str(temp_git_repo)) == "staging"

    # Switch back to main
    success, msg = GitManager.checkout_branch(str(temp_git_repo), "main")
    assert success is True
    assert GitManager.get_current_branch(str(temp_git_repo)) == "main"


def test_git_manager_commit_history_and_commit_changes(temp_git_repo: Path):
    """Verifies extraction of commit history and selective commit diff."""
    commits = GitManager.get_recent_commits(str(temp_git_repo), count=10)
    assert len(commits) == 2
    assert commits[0].message == "Feature: Update index and add about.php"
    assert commits[1].message == "Initial commit: Add index.php and config.php"

    # Inspect changes in HEAD commit (Feature commit: modified index.php, added about.php)
    head_changes = GitManager.get_changes_in_commit(str(temp_git_repo), commits[0].hash)
    rel_paths = [f.rel_path for f in head_changes]
    assert "index.php" in rel_paths
    assert "about.php" in rel_paths
    assert "config.php" not in rel_paths  # config.php was not modified in commit 2!

    for f in head_changes:
        if f.rel_path == "about.php":
            assert f.status == "A"
        elif f.rel_path == "index.php":
            assert f.status == "M"


def test_git_manager_diff_between_commits_and_working_tree(temp_git_repo: Path):
    """Verifies diffing between two commits and working tree change detection."""
    commits = GitManager.get_recent_commits(str(temp_git_repo))
    c_initial = commits[1].hash
    c_feature = commits[0].hash

    # Diff between initial commit and feature commit
    changes = GitManager.get_changes_between(str(temp_git_repo), c_initial, c_feature)
    paths = {f.rel_path: f.status for f in changes}
    assert paths.get("index.php") == "M"
    assert paths.get("about.php") == "A"

    # Working tree uncommitted changes
    test_work = temp_git_repo / "contact.php"
    test_work.write_text("<?php echo 'Contact Us'; ?>")

    uncommitted = GitManager.get_working_tree_changes(str(temp_git_repo))
    uncommitted_paths = [f.rel_path for f in uncommitted]
    assert "contact.php" in uncommitted_paths


def test_git_manager_deployment_history_persistence(temp_git_repo: Path):
    """Verifies saving and retrieving deployment history metadata."""
    record = GitDeploymentRecord(
        last_commit_hash="11223344556677889900aabbccddeeff11223344",
        last_commit_short="1122334",
        last_commit_message="Deploy production release",
        branch="main",
        deployed_at="2026-10-22 12:00:00",
        remote_dir="/public_html",
        total_files_deployed=5,
    )
    GitManager.save_deployment_history(str(temp_git_repo), record)

    loaded = GitManager.get_deployment_history(str(temp_git_repo))
    assert loaded is not None
    assert loaded.last_commit_short == "1122334"
    assert loaded.remote_dir == "/public_html"
    assert loaded.total_files_deployed == 5


class MockSFTPAttr:
    def __init__(self, size: int, mtime: float):
        self.st_size = size
        self.st_mtime = mtime


class MockSFTPClient:
    """Mock SFTP client simulating a remote shared webhost."""

    def __init__(self):
        self.is_connected = True
        self._sftp = self
        self.remote_fs = {}  # path -> (size, mtime, content_bytes)
        self.uploaded_files = []
        self.deleted_files = []
        self.renamed_files = []

    def stat(self, remote_path: str):
        if remote_path in self.remote_fs:
            size, mtime, _ = self.remote_fs[remote_path]
            return MockSFTPAttr(size, mtime)
        raise FileNotFoundError(f"No such file: {remote_path}")

    def upload_file(self, local_path: str, remote_path: str):
        content = Path(local_path).read_bytes()
        self.remote_fs[remote_path] = (len(content), time.time(), content)
        self.uploaded_files.append((local_path, remote_path))

    def delete_file(self, remote_path: str):
        if remote_path in self.remote_fs:
            del self.remote_fs[remote_path]
        self.deleted_files.append(remote_path)

    def rename(self, old_p: str, new_p: str):
        if old_p in self.remote_fs:
            self.remote_fs[new_p] = self.remote_fs.pop(old_p)
        self.renamed_files.append((old_p, new_p))

    def mkdir(self, remote_dir: str):
        pass


def test_conflict_scan_worker_detects_server_drift(temp_git_repo: Path):
    """Verifies that conflict scan warns when a remote file was modified directly on the server."""
    mock_sftp = MockSFTPClient()

    local_index = temp_git_repo / "index.php"
    local_stat = local_index.stat()

    # Scenario: index.php exists on remote server and its timestamp is NEWER than local file!
    # (Someone directly edited index.php on cPanel/shared host!)
    future_server_mtime = local_stat.st_mtime + 500.0
    mock_sftp.remote_fs["/public_html/index.php"] = (300, future_server_mtime, b"Directly edited on server!")

    files = [
        GitChangedFile(
            rel_path="index.php",
            status="M",
            local_path=str(local_index),
            local_size=local_stat.st_size,
            local_mtime=local_stat.st_mtime,
        ),
        GitChangedFile(
            rel_path="about.php",
            status="A",
            local_path=str(temp_git_repo / "about.php"),
            local_size=20,
            local_mtime=time.time(),
        ),
    ]

    worker = GitConflictScanWorker(mock_sftp, files, "/public_html")
    worker.run()

    # index.php should be flagged with CONFLICT (drift)
    assert files[0].has_conflict is True
    assert "Remote server file was modified directly on server" in files[0].conflict_reason

    # about.php does not exist on remote server -> no conflict
    assert files[1].has_conflict is False
    assert files[1].remote_exists is False


def test_git_deploy_execute_worker(temp_git_repo: Path):
    """Verifies selective upload and conflict backup execution."""
    mock_sftp = MockSFTPClient()
    mock_sftp.remote_fs["/public_html/index.php"] = (100, time.time() + 100, b"Old server version")

    local_index = temp_git_repo / "index.php"
    local_about = temp_git_repo / "about.php"

    files_to_deploy = [
        GitChangedFile(
            rel_path="index.php",
            status="M",
            local_path=str(local_index),
            remote_path="/public_html/index.php",
            has_conflict=True,
            remote_exists=True,
            action="UPLOAD",
            selected=True,
        ),
        GitChangedFile(
            rel_path="about.php",
            status="A",
            local_path=str(local_about),
            remote_path="/public_html/about.php",
            has_conflict=False,
            remote_exists=False,
            action="UPLOAD",
            selected=True,
        ),
    ]

    worker = GitDeployExecuteWorker(mock_sftp, files_to_deploy, backup_conflicts=True)
    worker.run()

    # Conflicted index.php should have been renamed to .bak
    assert "/public_html/index.php.bak" in mock_sftp.remote_fs

    # Both files must be uploaded to server
    assert "/public_html/index.php" in mock_sftp.remote_fs
    assert "/public_html/about.php" in mock_sftp.remote_fs
    assert len(mock_sftp.uploaded_files) == 2


def test_git_diff_viewer_dialog_ui(qapp):
    """Verifies unified diff computation and dialog rendering."""
    local_code = "<?php\necho 'Hello from local Git';\n"
    remote_code = "<?php\necho 'Hello from production server';\n"

    dlg = GitDiffViewerDialog("test.php", local_code, remote_code)
    assert dlg.windowTitle().startswith("Diff Comparison")
    assert "Unified Diff" in dlg.tabText(0) or dlg.txt_unified.toPlainText() is not None
    dlg.close()


def test_git_deploy_dialog_instantiation(qapp, temp_git_repo: Path):
    """Verifies GitDeployDialog initializes cleanly with repository info and commit table."""
    mock_sftp = MockSFTPClient()

    dlg = GitDeployDialog(
        sftp_client=mock_sftp,
        local_dir=str(temp_git_repo),
        remote_dir="/public_html",
    )
    assert dlg.windowTitle().startswith("Nova Commander - Git FTP Deploy")
    assert dlg.cmb_branch.count() >= 2
    assert dlg.cmb_commits.count() == 2
    assert dlg.table.rowCount() >= 1
    dlg.close()


def test_clone_repo_dialog_ui(qapp, tmp_path: Path):
    """Verifies CloneRepoDialog inputs and controls."""
    dlg = CloneRepoDialog(initial_dest_dir=str(tmp_path))
    assert dlg.windowTitle() == "Clone Git Repository"
    assert dlg.txt_dest.text() == str(tmp_path)
    dlg.close()
