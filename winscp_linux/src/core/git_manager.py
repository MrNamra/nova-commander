"""
Git Manager and Git-FTP Deployment Engine for WinSCP-Linux.
Provides:
- Git repository status detection (repo root, branch, commit, dirty status)
- Commit history extraction and commit-to-commit diff calculation
- Git repository cloning and branch switching
- Selective changed-files extraction (single commit, commit range, or working tree)
- Server-side conflict and drift detection (warns if remote file was modified directly on server)
- One-click deployment planning and execution without interfering with untracked client files (.env, uploads, etc.)
- Tracking of last deployed commit and deployment history
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from PyQt6.QtCore import QObject, QThread, pyqtSignal


@dataclass
class GitCommit:
    """Represents a single Git commit."""

    hash: str
    short_hash: str
    author: str
    date: str
    message: str

    def display_text(self) -> str:
        return f"[{self.short_hash}] {self.message} ({self.author}, {self.date[:10]})"


@dataclass
class GitChangedFile:
    """Represents a file changed in Git to be deployed or reviewed."""

    rel_path: str
    status: str  # "M" (Modified), "A" (Added), "D" (Deleted), "R" (Renamed), "??" (Untracked)
    old_path: Optional[str] = None
    local_path: str = ""
    remote_path: str = ""
    local_size: int = 0
    local_mtime: float = 0.0
    remote_exists: bool = False
    remote_size: int = 0
    remote_mtime: float = 0.0
    has_conflict: bool = False
    conflict_reason: str = ""
    selected: bool = True
    action: str = "UPLOAD"  # "UPLOAD", "DELETE_REMOTE", "SKIP"


@dataclass
class GitDeploymentRecord:
    """Persisted record of the last deployment for a project repository."""

    last_commit_hash: str = ""
    last_commit_short: str = ""
    last_commit_message: str = ""
    branch: str = ""
    deployed_at: str = ""
    remote_dir: str = ""
    total_files_deployed: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> GitDeploymentRecord:
        return cls(
            last_commit_hash=data.get("last_commit_hash", ""),
            last_commit_short=data.get("last_commit_short", ""),
            last_commit_message=data.get("last_commit_message", ""),
            branch=data.get("branch", ""),
            deployed_at=data.get("deployed_at", ""),
            remote_dir=data.get("remote_dir", ""),
            total_files_deployed=data.get("total_files_deployed", 0),
        )


class GitManager:
    """
    Manages Git operations for Git-FTP deployments.
    Communicates via Git CLI commands with safe quoting and error handling.
    """

    DEFAULT_EXCLUDES = {
        ".git",
        ".gitignore",
        ".gitattributes",
        ".gitmodules",
        ".env",
        ".env.local",
        ".env.production",
        ".DS_Store",
        "Thumbs.db",
        "node_modules",
        "vendor",
    }

    @staticmethod
    def is_git_installed() -> bool:
        """Returns True if git command is available in system PATH."""
        return shutil.which("git") is not None

    @staticmethod
    def run_git_cmd(args: List[str], cwd: Optional[str] = None, timeout: int = 30) -> Tuple[int, str, str]:
        """Runs a git command and returns (exit_code, stdout, stderr)."""
        cmd = ["git", "-c", "core.quotepath=false"] + args
        try:
            res = subprocess.run(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            return res.returncode, res.stdout.strip(), res.stderr.strip()
        except Exception as exc:
            return -1, "", str(exc)

    @classmethod
    def is_git_repo(cls, path: str | Path) -> bool:
        """Checks if a directory is inside a Git repository."""
        p = str(path)
        if not os.path.exists(p):
            return False
        code, out, _ = cls.run_git_cmd(["rev-parse", "--is-inside-work-tree"], cwd=p)
        return code == 0 and out.strip() == "true"

    @classmethod
    def get_repo_root(cls, path: str | Path) -> Optional[str]:
        """Returns the top-level directory of the Git repository."""
        p = str(path)
        if not os.path.exists(p):
            return None
        code, out, _ = cls.run_git_cmd(["rev-parse", "--show-toplevel"], cwd=p)
        if code == 0 and out.strip():
            return os.path.abspath(out.strip())
        return None

    @classmethod
    def get_current_branch(cls, repo_path: str) -> str:
        """Returns the current branch name or short commit if detached."""
        code, out, _ = cls.run_git_cmd(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
        if code == 0 and out.strip() and out.strip() != "HEAD":
            return out.strip()
        # Fallback to short hash if detached
        code, out, _ = cls.run_git_cmd(["rev-parse", "--short", "HEAD"], cwd=repo_path)
        return out.strip() if code == 0 else "unknown"

    @classmethod
    def list_branches(cls, repo_path: str) -> List[str]:
        """Returns list of local and remote branches."""
        code, out, _ = cls.run_git_cmd(["branch", "-a", "--format=%(refname:short)"], cwd=repo_path)
        if code != 0 or not out:
            return []
        branches: List[str] = []
        for line in out.splitlines():
            b = line.strip()
            if b and "HEAD" not in b and b not in branches:
                branches.append(b)
        return branches

    @classmethod
    def checkout_branch(cls, repo_path: str, branch_name: str) -> Tuple[bool, str]:
        """Switches to the specified Git branch."""
        # Handle remote branches like origin/main -> main
        clean_name = branch_name
        if clean_name.startswith("origin/"):
            local_candidate = clean_name.replace("origin/", "", 1)
            # Try checking out local tracking branch
            code, out, err = cls.run_git_cmd(["checkout", local_candidate], cwd=repo_path)
            if code == 0:
                return True, out
            # Try tracking checkout
            code, out, err = cls.run_git_cmd(["checkout", "-t", clean_name], cwd=repo_path)
            if code == 0:
                return True, out

        code, out, err = cls.run_git_cmd(["checkout", clean_name], cwd=repo_path)
        if code == 0:
            return True, out
        return False, err or out

    @classmethod
    def clone_repo(cls, repo_url: str, target_dir: str, branch: Optional[str] = None) -> Tuple[bool, str]:
        """Clones a remote repository into target_dir."""
        os.makedirs(target_dir, exist_ok=True)
        args = ["clone", repo_url, target_dir]
        if branch and branch.strip():
            args = ["clone", "--branch", branch.strip(), repo_url, target_dir]
        code, out, err = cls.run_git_cmd(args, timeout=120)
        if code == 0:
            return True, f"Cloned {repo_url} into {target_dir}"
        return False, err or out

    @classmethod
    def get_recent_commits(cls, repo_path: str, count: int = 50) -> List[GitCommit]:
        """Returns list of recent commits on current branch."""
        fmt = "%H|%h|%an|%ad|%s"
        code, out, _ = cls.run_git_cmd(["log", f"-n{count}", f"--format={fmt}", "--date=iso"], cwd=repo_path)
        if code != 0 or not out:
            return []
        commits: List[GitCommit] = []
        for line in out.splitlines():
            parts = line.strip().split("|", 4)
            if len(parts) == 5:
                commits.append(
                    GitCommit(
                        hash=parts[0],
                        short_hash=parts[1],
                        author=parts[2],
                        date=parts[3],
                        message=parts[4],
                    )
                )
        return commits

    @classmethod
    def get_commit_by_hash(cls, repo_path: str, commit_hash: str) -> Optional[GitCommit]:
        """Returns a single GitCommit for a specific hash."""
        fmt = "%H|%h|%an|%ad|%s"
        code, out, _ = cls.run_git_cmd(["log", "-1", f"--format={fmt}", "--date=iso", commit_hash], cwd=repo_path)
        if code == 0 and out.strip():
            parts = out.strip().split("|", 4)
            if len(parts) == 5:
                return GitCommit(
                    hash=parts[0],
                    short_hash=parts[1],
                    author=parts[2],
                    date=parts[3],
                    message=parts[4],
                )
        return None

    @classmethod
    def get_changes_in_commit(cls, repo_path: str, commit_hash: str) -> List[GitChangedFile]:
        """
        Gets list of files changed in a single specific commit.
        Uses --root flag so initial/root commits work seamlessly.
        """
        code, out, _ = cls.run_git_cmd(
            ["diff-tree", "--no-commit-id", "--name-status", "-r", "--root", commit_hash],
            cwd=repo_path,
        )
        if code != 0 or not out:
            return []
        return cls._parse_diff_status(repo_path, out)

    @classmethod
    def get_changes_between(cls, repo_path: str, from_ref: str, to_ref: str = "HEAD") -> List[GitChangedFile]:
        """
        Gets list of files changed between two commit hashes/refs.
        Ideal for "Changes since last deployment".
        """
        code, out, _ = cls.run_git_cmd(["diff", "--name-status", from_ref, to_ref], cwd=repo_path)
        if code != 0 or not out:
            return []
        return cls._parse_diff_status(repo_path, out)

    @classmethod
    def get_working_tree_changes(cls, repo_path: str) -> List[GitChangedFile]:
        """Gets unstaged, staged, and untracked changes in the working tree."""
        code, out, _ = cls.run_git_cmd(["status", "--porcelain=v1"], cwd=repo_path)
        if code != 0 or not out:
            return []

        changed: List[GitChangedFile] = []
        for line in out.splitlines():
            line = line.strip()
            if not line or len(line) < 3:
                continue
            st = line[:2].strip()
            path_part = line[3:].strip()

            # Handle renames e.g. "R  old -> new"
            if " -> " in path_part:
                old_p, new_p = path_part.split(" -> ", 1)
                rel_path = new_p.strip()
                old_path = old_p.strip()
            else:
                rel_path = path_part
                old_path = None

            if cls._should_exclude(rel_path):
                continue

            full_loc = os.path.join(repo_path, rel_path.replace("/", os.sep))
            size = 0
            mtime = 0.0
            if os.path.exists(full_loc) and not os.path.isdir(full_loc):
                try:
                    s = os.stat(full_loc)
                    size = s.st_size
                    mtime = s.st_mtime
                except Exception:
                    pass

            action = "DELETE_REMOTE" if "D" in st else "UPLOAD"
            changed.append(
                GitChangedFile(
                    rel_path=rel_path,
                    status=st,
                    old_path=old_path,
                    local_path=full_loc,
                    local_size=size,
                    local_mtime=mtime,
                    action=action,
                    selected=True,
                )
            )
        return changed

    @classmethod
    def _parse_diff_status(cls, repo_path: str, raw_output: str) -> List[GitChangedFile]:
        """Parses output from git diff-tree / git diff --name-status."""
        files: List[GitChangedFile] = []
        for line in raw_output.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if not parts:
                continue

            st = parts[0].strip()
            if len(parts) >= 3:
                # Renamed or copied: R100  old_path  new_path
                old_path = parts[1].strip()
                rel_path = parts[2].strip()
            elif len(parts) == 2:
                old_path = None
                rel_path = parts[1].strip()
            else:
                continue

            if cls._should_exclude(rel_path):
                continue

            full_loc = os.path.join(repo_path, rel_path.replace("/", os.sep))
            size = 0
            mtime = 0.0
            if os.path.exists(full_loc) and not os.path.isdir(full_loc):
                try:
                    s = os.stat(full_loc)
                    size = s.st_size
                    mtime = s.st_mtime
                except Exception:
                    pass

            status_char = st[0] if st else "M"
            action = "DELETE_REMOTE" if status_char == "D" else "UPLOAD"
            files.append(
                GitChangedFile(
                    rel_path=rel_path,
                    status=status_char,
                    old_path=old_path,
                    local_path=full_loc,
                    local_size=size,
                    local_mtime=mtime,
                    action=action,
                    selected=True,
                )
            )
        return files

    @classmethod
    def _should_exclude(cls, rel_path: str) -> bool:
        """Filters out .git internal files and sensitive/excluded paths."""
        norm = rel_path.replace(os.sep, "/").strip("/")
        parts = norm.split("/")
        for p in parts:
            if p == ".git":
                return True
            if p in cls.DEFAULT_EXCLUDES:
                return True
        return False

    @classmethod
    def get_deployment_history(cls, repo_path: str) -> Optional[GitDeploymentRecord]:
        """Loads last deployment record from repository."""
        meta_file = Path(repo_path) / ".git" / "nova_deployment.json"
        if meta_file.exists():
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                return GitDeploymentRecord.from_dict(data)
            except Exception:
                return None
        return None

    @classmethod
    def save_deployment_history(cls, repo_path: str, record: GitDeploymentRecord) -> None:
        """Persists the deployment record into .git/nova_deployment.json."""
        git_dir = Path(repo_path) / ".git"
        if git_dir.exists():
            meta_file = git_dir / "nova_deployment.json"
            try:
                meta_file.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")
            except Exception:
                pass

    @classmethod
    def get_file_content_at_commit(cls, repo_path: str, commit_hash: str, rel_path: str) -> Optional[str]:
        """Reads file content at a specific commit using git show."""
        spec = f"{commit_hash}:{rel_path}"
        code, out, _ = cls.run_git_cmd(["show", spec], cwd=repo_path)
        return out if code == 0 else None


class GitConflictScanWorker(QThread):
    """
    Background worker that connects changed Git files with remote server files.
    Checks remote existence, timestamp, and size to detect server-side drift/conflicts.
    """

    progress = pyqtSignal(str, int, int)  # file_name, current_idx, total
    finished_scan = pyqtSignal(list, int)  # updated_files, conflict_count
    failed = pyqtSignal(str)

    def __init__(
        self,
        sftp_client,
        changed_files: List[GitChangedFile],
        remote_base_dir: str,
        commit_timestamp: Optional[float] = None,
    ):
        super().__init__()
        self.sftp_client = sftp_client
        self.changed_files = changed_files
        self.remote_base_dir = remote_base_dir.rstrip("/")
        self.commit_timestamp = commit_timestamp or 0.0

    def run(self):
        try:
            total = len(self.changed_files)
            conflict_count = 0

            for idx, f in enumerate(self.changed_files):
                self.progress.emit(f.rel_path, idx + 1, total)
                remote_full = f"{self.remote_base_dir}/{f.rel_path.lstrip('/')}"
                f.remote_path = remote_full

                if not self.sftp_client or not self.sftp_client.is_connected:
                    continue

                try:
                    attr = self.sftp_client.stat(remote_full)
                    f.remote_exists = True
                    f.remote_size = int(attr.st_size or 0)
                    f.remote_mtime = float(attr.st_mtime or 0.0)

                    # CONFLICT / DRIFT DETECTION:
                    # If file exists on remote server, check if server file was modified
                    # AFTER the local file or AFTER the commit date
                    if f.status != "D":
                        time_threshold = max(f.local_mtime, self.commit_timestamp)
                        # If server file was modified more than 3 seconds AFTER local/commit:
                        if f.remote_mtime > (time_threshold + 3.0):
                            f.has_conflict = True
                            f.conflict_reason = (
                                f"Remote server file was modified directly on server after commit! "
                                f"(Server: {datetime.fromtimestamp(f.remote_mtime).strftime('%Y-%m-%d %H:%M:%S')})"
                            )
                            conflict_count += 1
                        elif f.local_size > 0 and f.remote_size != f.local_size and f.remote_mtime >= (time_threshold - 3.0):
                            # Size mismatch with recent remote modification
                            f.has_conflict = True
                            f.conflict_reason = f"Size differs (Local: {f.local_size} B, Remote: {f.remote_size} B)"
                            conflict_count += 1

                except Exception:
                    # Remote file does not exist
                    f.remote_exists = False
                    f.has_conflict = False

            self.finished_scan.emit(self.changed_files, conflict_count)
        except Exception as exc:
            self.failed.emit(str(exc))


class GitDeployExecuteWorker(QThread):
    """
    Background worker that uploads selected changed files to the remote server.
    Safely creates remote directories and handles conflict backup.
    """

    file_progress = pyqtSignal(str, int, int)  # file_name, current_idx, total
    step_message = pyqtSignal(str)
    finished_deploy = pyqtSignal(int, int, list)  # success_count, fail_count, errors
    failed = pyqtSignal(str)

    def __init__(
        self,
        sftp_client,
        files_to_deploy: List[GitChangedFile],
        backup_conflicts: bool = True,
    ):
        super().__init__()
        self.sftp_client = sftp_client
        self.files_to_deploy = [f for f in files_to_deploy if f.selected]
        self.backup_conflicts = backup_conflicts

    def run(self):
        total = len(self.files_to_deploy)
        success_count = 0
        fail_count = 0
        errors: List[str] = []

        try:
            for idx, f in enumerate(self.files_to_deploy):
                self.file_progress.emit(f.rel_path, idx + 1, total)

                if f.action == "SKIP":
                    continue

                try:
                    if f.action == "DELETE_REMOTE":
                        self.step_message.emit(f"Deleting remote {f.rel_path}...")
                        try:
                            self.sftp_client.delete_file(f.remote_path)
                            success_count += 1
                        except Exception as e:
                            # If file already didn't exist, ignore
                            success_count += 1

                    elif f.action == "UPLOAD":
                        # If conflict backup is enabled and remote file has conflict:
                        if self.backup_conflicts and f.has_conflict and f.remote_exists:
                            bak_path = f"{f.remote_path}.bak"
                            self.step_message.emit(f"Backing up server file to {os.path.basename(bak_path)}...")
                            try:
                                # Overwrite previous .bak if present
                                try:
                                    self.sftp_client.delete_file(bak_path)
                                except Exception:
                                    pass
                                self.sftp_client.rename(f.remote_path, bak_path)
                            except Exception:
                                pass

                        # Ensure remote parent directory exists
                        parent_remote = os.path.dirname(f.remote_path)
                        if parent_remote and parent_remote != "/":
                            self._ensure_remote_dir_recursive(parent_remote)

                        self.step_message.emit(f"Uploading {f.rel_path}...")
                        self.sftp_client.upload_file(f.local_path, f.remote_path)
                        success_count += 1

                except Exception as exc:
                    fail_count += 1
                    errors.append(f"{f.rel_path}: {exc}")

            self.finished_deploy.emit(success_count, fail_count, errors)
        except Exception as exc:
            self.failed.emit(str(exc))

    def _ensure_remote_dir_recursive(self, remote_dir: str):
        """Recursively creates remote directories as needed."""
        if hasattr(self.sftp_client, "ensure_remote_dir"):
            self.sftp_client.ensure_remote_dir(remote_dir)
        else:
            clean = remote_dir.strip("/")
            parts = clean.split("/")
            curr = ""
            for p in parts:
                curr += f"/{p}"
                try:
                    self.sftp_client.mkdir(curr)
                except Exception:
                    pass
