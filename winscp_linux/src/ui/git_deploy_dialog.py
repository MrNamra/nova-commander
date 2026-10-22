"""
Git-FTP Deployment and Smart Sync UI for WinSCP-Linux.
Provides:
- One-click Git deployment to shared webhosting without SSH shell access.
- Selective replacement of only the files changed in a selected commit, commit range, or working tree.
- Safe client isolation (preserves server-only files, .env, storage, uploads).
- Server drift / conflict detection with warning alerts before replacing files.
- Built-in Diff Viewer comparing Local Git vs Server Version.
- Git repository cloning and branch switching dialogs.
"""

from __future__ import annotations

import difflib
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QKeySequence
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.git_manager import (
    GitChangedFile,
    GitCommit,
    GitConflictScanWorker,
    GitDeployExecuteWorker,
    GitDeploymentRecord,
    GitManager,
)
from ..core.sftp_client import SFTPClient


class CloneRepoWorker(QThread):
    """Background worker for git clone operations."""

    finished_clone = pyqtSignal(bool, str)

    def __init__(self, repo_url: str, target_dir: str, branch: Optional[str] = None):
        super().__init__()
        self.repo_url = repo_url
        self.target_dir = target_dir
        self.branch = branch

    def run(self):
        success, msg = GitManager.clone_repo(self.repo_url, self.target_dir, self.branch)
        self.finished_clone.emit(success, msg)


class GitDiffViewerDialog(QDialog):
    """
    Side-by-side / Unified Diff Viewer comparing Local Git file with Remote Server file.
    Shows exactly what differs between local code and production server.
    """

    def __init__(
        self,
        rel_path: str,
        local_content: str,
        remote_content: Optional[str],
        local_info: str = "Local Git Version",
        remote_info: str = "Production Server Version",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.rel_path = rel_path
        self.local_content = local_content
        self.remote_content = remote_content if remote_content is not None else "(File does not exist on server)"
        self.local_info = local_info
        self.remote_info = remote_info

        self.setWindowTitle(f"Diff Comparison - {os.path.basename(rel_path)}")
        self.resize(950, 600)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Header
        lbl_title = QLabel(f"<b>File:</b> <code>{self.rel_path}</code>")
        layout.addWidget(lbl_title)

        tab_widget = QTabWidget()
        self.tab_widget = tab_widget
        self.tabs = tab_widget

        # Tab 1: Unified Diff
        unified_widget = QWidget()
        unified_layout = QVBoxLayout(unified_widget)
        self.txt_unified = QTextEdit()
        self.txt_unified.setReadOnly(True)
        self.txt_unified.setFont(QFont("Monospace", 10))
        self._render_unified_diff()
        unified_layout.addWidget(self.txt_unified)
        tab_widget.addTab(unified_widget, "🔍 Unified Diff")

        # Tab 2: Side-by-Side View
        side_widget = QWidget()
        side_layout = QHBoxLayout(side_widget)

        # Left: Remote Server
        left_box = QGroupBox(f"🌐 Remote Server ({self.remote_info})")
        left_box_layout = QVBoxLayout(left_box)
        self.txt_remote = QTextEdit()
        self.txt_remote.setReadOnly(True)
        self.txt_remote.setFont(QFont("Monospace", 9))
        self.txt_remote.setPlainText(self.remote_content)
        left_box_layout.addWidget(self.txt_remote)
        side_layout.addWidget(left_box)

        # Right: Local Git
        right_box = QGroupBox(f"💻 Local Git ({self.local_info})")
        right_box_layout = QVBoxLayout(right_box)
        self.txt_local = QTextEdit()
        self.txt_local.setReadOnly(True)
        self.txt_local.setFont(QFont("Monospace", 9))
        self.txt_local.setPlainText(self.local_content)
        right_box_layout.addWidget(self.txt_local)
        side_layout.addWidget(right_box)

        tab_widget.addTab(side_widget, "↔ Side-by-Side View")
        layout.addWidget(tab_widget)

        # Close button
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.close)
        layout.addWidget(btn_box)

    def tabText(self, index: int) -> str:
        """Returns the tab title at the specified index."""
        return self.tab_widget.tabText(index)

    def _render_unified_diff(self):
        remote_lines = self.remote_content.splitlines(keepends=True)
        local_lines = self.local_content.splitlines(keepends=True)

        diff = list(
            difflib.unified_diff(
                remote_lines,
                local_lines,
                fromfile="server/" + self.rel_path,
                tofile="local/" + self.rel_path,
            )
        )

        if not diff:
            self.txt_unified.setHtml("<p style='color: #4ec9b0;'><b>✔ Files are identical. No differences found.</b></p>")
            return

        html_parts = ["<pre style='font-family: monospace; font-size: 11px;'>"]
        for line in diff:
            escaped = (
                line.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            if line.startswith("---") or line.startswith("+++"):
                html_parts.append(f"<span style='color: #dcdcaa; font-weight: bold;'>{escaped}</span>")
            elif line.startswith("@@"):
                html_parts.append(f"<span style='color: #569cd6; font-weight: bold;'>{escaped}</span>")
            elif line.startswith("+"):
                html_parts.append(f"<span style='color: #4ec9b0; background-color: #1e3d30;'>{escaped}</span>")
            elif line.startswith("-"):
                html_parts.append(f"<span style='color: #f14c4c; background-color: #3e2020;'>{escaped}</span>")
            else:
                html_parts.append(f"<span style='color: #cccccc;'>{escaped}</span>")
        html_parts.append("</pre>")
        self.txt_unified.setHtml("".join(html_parts))


class CloneRepoDialog(QDialog):
    """Dialog allowing user to clone a Git repository into a local path."""

    repo_cloned = pyqtSignal(str)  # Returns path of cloned directory

    def __init__(self, initial_dest_dir: Optional[str] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.initial_dest_dir = initial_dest_dir or str(Path.home())
        self.setWindowTitle("Clone Git Repository")
        self.resize(550, 240)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.txt_url = QLineEdit()
        self.txt_url.setPlaceholderText("https://github.com/username/repository.git")
        form.addRow("Repository URL:", self.txt_url)

        dest_row = QHBoxLayout()
        self.txt_dest = QLineEdit(self.initial_dest_dir)
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._browse_dest)
        dest_row.addWidget(self.txt_dest)
        dest_row.addWidget(btn_browse)
        form.addRow("Local Target Directory:", dest_row)

        self.txt_branch = QLineEdit()
        self.txt_branch.setPlaceholderText("Optional (defaults to main/master)")
        form.addRow("Branch (optional):", self.txt_branch)

        layout.addLayout(form)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #888888;")
        layout.addWidget(self.lbl_status)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btn_clone = QPushButton("🚀 Clone Repository")
        self.btn_clone.setStyleSheet("font-weight: bold; background-color: #238636; color: white;")
        self.btn_clone.clicked.connect(self._start_clone)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_clone)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

    def _browse_dest(self):
        chosen = QFileDialog.getExistingDirectory(self, "Select Target Directory", self.txt_dest.text())
        if chosen:
            self.txt_dest.setText(chosen)

    def _start_clone(self):
        url = self.txt_url.text().strip()
        dest = self.txt_dest.text().strip()
        branch = self.txt_branch.text().strip() or None

        if not url:
            QMessageBox.warning(self, "Missing URL", "Please enter a valid Git repository URL.")
            return

        if not dest:
            QMessageBox.warning(self, "Missing Directory", "Please select a target destination directory.")
            return

        self.btn_clone.setEnabled(False)
        self.progress_bar.show()
        self.lbl_status.setText("Cloning repository in progress... Please wait.")

        self.worker = CloneRepoWorker(url, dest, branch)
        self.worker.finished_clone.connect(self._on_clone_finished)
        self.worker.start()

    def _on_clone_finished(self, success: bool, msg: str):
        self.progress_bar.hide()
        self.btn_clone.setEnabled(True)
        if success:
            QMessageBox.information(self, "Clone Successful", f"Repository successfully cloned!\n{msg}")
            self.repo_cloned.emit(self.txt_dest.text().strip())
            self.accept()
        else:
            QMessageBox.critical(self, "Clone Failed", f"Failed to clone repository:\n{msg}")
            self.lbl_status.setText("Clone failed.")


class GitDeployDialog(QDialog):
    """
    Main Git-FTP Deployment Dialog for WinSCP-Linux.
    Features:
    - Automatically discovers Git repository and branch
    - Change scope selector (By Commit ID, Since Last Deploy, Working Tree)
    - Conflict and server-drift detector with visual warnings
    - One-Click selective replacement of modified files only
    - Built-in Diff Viewer
    """

    deployment_completed = pyqtSignal()

    def __init__(
        self,
        sftp_client: SFTPClient,
        local_dir: str,
        remote_dir: str,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.sftp_client = sftp_client
        self.local_dir = os.path.abspath(local_dir)
        self.remote_dir = remote_dir.rstrip("/") or "/"
        self.repo_root = GitManager.get_repo_root(self.local_dir) or self.local_dir

        self.changed_files: List[GitChangedFile] = []
        self.recent_commits: List[GitCommit] = []
        self.last_record: Optional[GitDeploymentRecord] = None

        self.setWindowTitle("Nova Commander - Git FTP Deploy & Smart Sync")
        self.resize(1000, 680)

        self._setup_ui()
        self._init_git_data()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)

        # ================= 1. Top Repository Info Bar =================
        top_group = QGroupBox("Git Repository & Branch")
        top_layout = QVBoxLayout(top_group)

        row1 = QHBoxLayout()
        self.lbl_repo_path = QLabel(f"📁 <b>Repository:</b> {self.repo_root}")
        self.lbl_repo_path.setStyleSheet("color: #d4d4d4;")
        row1.addWidget(self.lbl_repo_path, 1)

        self.btn_change_repo = QPushButton("Change Repo...")
        self.btn_change_repo.clicked.connect(self._on_change_repo)
        row1.addWidget(self.btn_change_repo)

        self.btn_clone_repo = QPushButton("Clone Repo...")
        self.btn_clone_repo.clicked.connect(self._on_clone_repo)
        row1.addWidget(self.btn_clone_repo)
        top_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("<b>Branch:</b>"))
        self.cmb_branch = QComboBox()
        self.cmb_branch.setMinimumWidth(180)
        row2.addWidget(self.cmb_branch)

        self.btn_switch_branch = QPushButton("Switch Branch")
        self.btn_switch_branch.clicked.connect(self._on_switch_branch)
        row2.addWidget(self.btn_switch_branch)

        row2.addSpacing(20)
        self.lbl_last_deploy = QLabel("🕒 <b>Last Deployed:</b> Loading...")
        self.lbl_last_deploy.setStyleSheet("color: #569cd6; font-size: 11px;")
        row2.addWidget(self.lbl_last_deploy, 1)

        top_layout.addLayout(row2)
        main_layout.addWidget(top_group)

        # ================= 2. Deployment Scope Selection =================
        scope_group = QGroupBox("Deployment Scope (Select What Code to Upload)")
        scope_layout = QVBoxLayout(scope_group)

        self.rb_by_commit = QRadioButton("Specific Commit ID (Upload changes made in this commit)")
        self.rb_since_last = QRadioButton("Changes Since Last Deployment (Auto-diff from last deployed commit to HEAD)")
        self.rb_working_tree = QRadioButton("Uncommitted Working Changes (Upload currently modified & unstaged files)")
        self.rb_by_commit.setChecked(True)

        self.scope_btn_group = QButtonGroup(self)
        self.scope_btn_group.addButton(self.rb_by_commit)
        self.scope_btn_group.addButton(self.rb_since_last)
        self.scope_btn_group.addButton(self.rb_working_tree)
        self.scope_btn_group.buttonClicked.connect(self._on_scope_changed)

        scope_layout.addWidget(self.rb_by_commit)

        # Commit Selector Row
        self.commit_widget = QWidget()
        commit_row = QHBoxLayout(self.commit_widget)
        commit_row.setContentsMargins(20, 0, 0, 5)
        commit_row.addWidget(QLabel("Select Commit:"))
        self.cmb_commits = QComboBox()
        self.cmb_commits.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.cmb_commits.currentIndexChanged.connect(self._load_changes_for_current_scope)
        commit_row.addWidget(self.cmb_commits, 1)
        scope_layout.addWidget(self.commit_widget)

        scope_layout.addWidget(self.rb_since_last)
        scope_layout.addWidget(self.rb_working_tree)

        # Remote Path configuration row
        rem_row = QHBoxLayout()
        rem_row.addWidget(QLabel("<b>Remote Destination Dir:</b>"))
        self.txt_remote_dest = QLineEdit(self.remote_dir)
        self.txt_remote_dest.setPlaceholderText("/public_html or /var/www/html")
        self.txt_remote_dest.textChanged.connect(self._on_remote_path_changed)
        rem_row.addWidget(self.txt_remote_dest, 1)

        self.chk_backup_conflicts = QCheckBox("Backup conflicting files on server (.bak) before overwriting")
        self.chk_backup_conflicts.setChecked(True)
        self.chk_backup_conflicts.setToolTip("Creates filename.ext.bak on the remote server before replacing")
        rem_row.addWidget(self.chk_backup_conflicts)

        scope_layout.addLayout(rem_row)
        main_layout.addWidget(scope_group)

        # ================= 3. Conflict Banner (Dynamic Warning) =================
        self.banner_conflict = QFrame()
        self.banner_conflict.setStyleSheet(
            "QFrame { background-color: #3e2600; border: 1px solid #d29922; border-radius: 4px; padding: 6px; }"
        )
        banner_layout = QHBoxLayout(self.banner_conflict)
        self.lbl_conflict_banner = QLabel("⚠️ <b>Server Drift Warning:</b> Conflicting files detected!")
        self.lbl_conflict_banner.setStyleSheet("color: #e3b341;")
        banner_layout.addWidget(self.lbl_conflict_banner, 1)

        self.btn_skip_conflicts = QPushButton("Skip Conflicted Files")
        self.btn_skip_conflicts.clicked.connect(self._skip_all_conflicts)
        banner_layout.addWidget(self.btn_skip_conflicts)

        self.btn_overwrite_conflicts = QPushButton("Overwrite Server Files")
        self.btn_overwrite_conflicts.clicked.connect(self._select_all_conflicts)
        banner_layout.addWidget(self.btn_overwrite_conflicts)

        self.banner_conflict.hide()
        main_layout.addWidget(self.banner_conflict)

        # ================= 4. Changed Files Table =================
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Deploy?",
            "Status",
            "File Path",
            "Local Size",
            "Server State",
            "Action",
        ])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemDoubleClicked.connect(self._on_row_double_clicked)
        main_layout.addWidget(self.table)

        # Table Control Buttons
        tbl_btn_row = QHBoxLayout()
        btn_sel_all = QPushButton("Select All")
        btn_desel_all = QPushButton("Deselect All")
        btn_sel_safe = QPushButton("Select Non-Conflicting Only")
        btn_sel_all.clicked.connect(self._select_all)
        btn_desel_all.clicked.connect(self._deselect_all)
        btn_sel_safe.clicked.connect(self._select_safe_only)

        tbl_btn_row.addWidget(btn_sel_all)
        tbl_btn_row.addWidget(btn_desel_all)
        tbl_btn_row.addWidget(btn_sel_safe)

        self.btn_view_diff = QPushButton("🔍 Compare / View Diff with Server")
        self.btn_view_diff.clicked.connect(self._on_view_diff_clicked)
        tbl_btn_row.addWidget(self.btn_view_diff)

        tbl_btn_row.addStretch()
        self.lbl_summary = QLabel("0 files to deploy")
        self.lbl_summary.setStyleSheet("font-weight: bold;")
        tbl_btn_row.addWidget(self.lbl_summary)
        main_layout.addLayout(tbl_btn_row)

        # ================= 5. Progress and Deployment Action =================
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        main_layout.addWidget(self.progress_bar)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet("color: #888888;")
        main_layout.addWidget(self.lbl_status)

        bottom_row = QHBoxLayout()
        self.btn_deploy = QPushButton("🚀 Deploy Changed Files (1-Click)")
        self.btn_deploy.setStyleSheet("font-weight: bold; font-size: 13px; padding: 8px 24px; background-color: #238636; color: white;")
        self.btn_deploy.clicked.connect(self._start_deployment)
        bottom_row.addWidget(self.btn_deploy)

        bottom_row.addStretch()

        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.close)
        bottom_row.addWidget(self.btn_close)
        main_layout.addLayout(bottom_row)

    def _init_git_data(self):
        """Discovers repository, branches, commits, and previous deployment state."""
        if not GitManager.is_git_repo(self.repo_root):
            self.lbl_repo_path.setText(f"⚠️ <b>Not a Git Repository:</b> {self.repo_root}")
            QMessageBox.warning(
                self,
                "No Git Repository",
                f"The directory '{self.local_dir}' is not a Git repository.\n\n"
                "You can initialize a repository or click 'Clone Repo...' to clone one from GitHub/GitLab.",
            )
            self.btn_deploy.setEnabled(False)
            return

        # Branches
        curr_branch = GitManager.get_current_branch(self.repo_root)
        all_branches = GitManager.list_branches(self.repo_root)
        self.cmb_branch.blockSignals(True)
        self.cmb_branch.clear()
        if curr_branch not in all_branches:
            self.cmb_branch.addItem(curr_branch)
        for b in all_branches:
            self.cmb_branch.addItem(b)
        idx = self.cmb_branch.findText(curr_branch)
        if idx >= 0:
            self.cmb_branch.setCurrentIndex(idx)
        self.cmb_branch.blockSignals(False)

        # Last deployment history
        self.last_record = GitManager.get_deployment_history(self.repo_root)
        if self.last_record and self.last_record.last_commit_short:
            self.lbl_last_deploy.setText(
                f"🕒 <b>Last Deployed:</b> <code>[{self.last_record.last_commit_short}]</code> on {self.last_record.deployed_at} ({self.last_record.total_files_deployed} files)"
            )
            self.rb_since_last.setText(
                f"Changes Since Last Deployment (Diff from [{self.last_record.last_commit_short}] to HEAD)"
            )
            self.rb_since_last.setEnabled(True)
        else:
            self.lbl_last_deploy.setText("🕒 <b>Last Deployed:</b> Never deployed via Nova Commander")
            self.rb_since_last.setEnabled(False)

        # Commits
        self._load_commits()

    def _load_commits(self):
        """Loads commit history into dropdown."""
        self.recent_commits = GitManager.get_recent_commits(self.repo_root, count=50)
        self.cmb_commits.blockSignals(True)
        self.cmb_commits.clear()
        for c in self.recent_commits:
            self.cmb_commits.addItem(c.display_text(), c.hash)
        self.cmb_commits.blockSignals(False)

        self._load_changes_for_current_scope()

    def _on_scope_changed(self):
        self.commit_widget.setVisible(self.rb_by_commit.isChecked())
        self._load_changes_for_current_scope()

    def _on_remote_path_changed(self):
        self.remote_dir = self.txt_remote_dest.text().strip().rstrip("/")
        # Rescan remote files for conflict
        self._scan_remote_conflicts()

    def _load_changes_for_current_scope(self):
        """Calculates changed files based on selected mode."""
        if not GitManager.is_git_repo(self.repo_root):
            return

        if self.rb_by_commit.isChecked():
            commit_hash = self.cmb_commits.currentData()
            if not commit_hash:
                return
            self.changed_files = GitManager.get_changes_in_commit(self.repo_root, commit_hash)

        elif self.rb_since_last.isChecked():
            if self.last_record and self.last_record.last_commit_hash:
                self.changed_files = GitManager.get_changes_between(
                    self.repo_root, self.last_record.last_commit_hash, "HEAD"
                )
            else:
                self.changed_files = []

        elif self.rb_working_tree.isChecked():
            self.changed_files = GitManager.get_working_tree_changes(self.repo_root)

        # Immediately display discovered files so user has instant feedback
        self._populate_table(0)

        # Trigger conflict scan with remote server
        self._scan_remote_conflicts()

    def _scan_remote_conflicts(self):
        """Initiates background inspection of remote server files to detect drift."""
        if not self.changed_files:
            return

        if not self.sftp_client or not self.sftp_client.is_connected:
            self.lbl_status.setText("Remote server not connected. Connect in main window to enable conflict checks.")
            return

        self.lbl_status.setText("Checking remote server files for conflicts / drift...")
        self.scan_worker = GitConflictScanWorker(
            self.sftp_client,
            self.changed_files,
            self.remote_dir,
        )
        self.scan_worker.finished_scan.connect(self._on_conflict_scan_finished)
        self.scan_worker.start()

    def _on_conflict_scan_finished(self, updated_files: List[GitChangedFile], conflict_count: int):
        self.changed_files = updated_files
        self._populate_table(conflict_count)

    def _populate_table(self, conflict_count: int):
        self.table.setRowCount(len(self.changed_files))

        if conflict_count > 0:
            self.banner_conflict.show()
            self.lbl_conflict_banner.setText(
                f"⚠️ <b>Server Drift Warning:</b> {conflict_count} file(s) have been modified directly on the remote server! "
                "Local deployment will replace them."
            )
        else:
            self.banner_conflict.hide()

        for row, f in enumerate(self.changed_files):
            # Col 0: Checkbox
            chk = QTableWidgetItem()
            chk.setFlags(chk.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            chk.setCheckState(Qt.CheckState.Checked if f.selected else Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, chk)

            # Col 1: Status
            st_map = {
                "A": "✨ Added",
                "M": "✏ Modified",
                "D": "🗑 Deleted",
                "R": "📦 Renamed",
                "??": "❓ Untracked",
            }
            st_item = QTableWidgetItem(st_map.get(f.status, f.status))
            if f.status == "A":
                st_item.setForeground(QColor("#4ec9b0"))
            elif f.status == "M":
                st_item.setForeground(QColor("#569cd6"))
            elif f.status == "D":
                st_item.setForeground(QColor("#f14c4c"))
            self.table.setItem(row, 1, st_item)

            # Col 2: Relative Path
            path_item = QTableWidgetItem(f.rel_path)
            self.table.setItem(row, 2, path_item)

            # Col 3: Local Size
            size_str = f"{f.local_size:,} B" if f.local_size > 0 else "-"
            self.table.setItem(row, 3, QTableWidgetItem(size_str))

            # Col 4: Server State
            if f.has_conflict:
                state_item = QTableWidgetItem("⚠️ CONFLICT (Drift)")
                state_item.setForeground(QColor("#d29922"))
                state_item.setToolTip(f.conflict_reason)
                # Color whole row slightly
                for c in range(6):
                    if self.table.item(row, c):
                        self.table.item(row, c).setBackground(QColor("#3d2800"))
            elif f.remote_exists:
                state_item = QTableWidgetItem("🟢 Exists (Safe Update)")
                state_item.setForeground(QColor("#4ec9b0"))
            else:
                state_item = QTableWidgetItem("✨ New on Server")
                state_item.setForeground(QColor("#888888"))
            self.table.setItem(row, 4, state_item)

            # Col 5: Action Dropdown
            cmb_action = QComboBox()
            if f.status == "D":
                cmb_action.addItems(["Delete Remote", "Skip"])
                cmb_action.setCurrentText("Delete Remote" if f.action == "DELETE_REMOTE" else "Skip")
            else:
                cmb_action.addItems(["Upload", "Skip"])
                cmb_action.setCurrentText("Upload" if f.action == "UPLOAD" else "Skip")

            cmb_action.currentIndexChanged.connect(
                lambda idx, target_row=row, cmb=cmb_action: self._on_action_changed(target_row, cmb.currentText())
            )
            self.table.setCellWidget(row, 5, cmb_action)

        self._update_summary()

    def _on_action_changed(self, row: int, action_text: str):
        if row < len(self.changed_files):
            act_code = "UPLOAD" if action_text == "Upload" else ("DELETE_REMOTE" if action_text == "Delete Remote" else "SKIP")
            self.changed_files[row].action = act_code
            self._update_summary()

    def _update_summary(self):
        sel_count = 0
        conflicts_selected = 0
        for r, f in enumerate(self.changed_files):
            chk = self.table.item(r, 0)
            is_checked = chk and chk.checkState() == Qt.CheckState.Checked
            f.selected = is_checked
            if is_checked and f.action != "SKIP":
                sel_count += 1
                if f.has_conflict:
                    conflicts_selected += 1

        summary = f"{sel_count} of {len(self.changed_files)} file(s) selected for deployment"
        if conflicts_selected > 0:
            summary += f" ({conflicts_selected} with server drift)"
        self.lbl_summary.setText(summary)
        self.btn_deploy.setEnabled(sel_count > 0)

    def _select_all(self):
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it:
                it.setCheckState(Qt.CheckState.Checked)
        self._update_summary()

    def _deselect_all(self):
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it:
                it.setCheckState(Qt.CheckState.Unchecked)
        self._update_summary()

    def _select_safe_only(self):
        """Selects only files that do not have any server conflicts/drift."""
        for r, f in enumerate(self.changed_files):
            it = self.table.item(r, 0)
            if it:
                it.setCheckState(Qt.CheckState.Unchecked if f.has_conflict else Qt.CheckState.Checked)
        self._update_summary()

    def _skip_all_conflicts(self):
        """Sets action for all conflicting files to Skip."""
        for r, f in enumerate(self.changed_files):
            if f.has_conflict:
                cmb = self.table.cellWidget(r, 5)
                if isinstance(cmb, QComboBox):
                    cmb.setCurrentText("Skip")
        self._update_summary()

    def _select_all_conflicts(self):
        """Sets action for all conflicting files to Upload (Overwrite)."""
        for r, f in enumerate(self.changed_files):
            if f.has_conflict:
                cmb = self.table.cellWidget(r, 5)
                if isinstance(cmb, QComboBox):
                    cmb.setCurrentText("Upload")
                it = self.table.item(r, 0)
                if it:
                    it.setCheckState(Qt.CheckState.Checked)
        self._update_summary()

    def _on_row_double_clicked(self, item: QTableWidgetItem):
        self._on_view_diff_clicked()

    def _on_view_diff_clicked(self):
        """Opens Diff viewer between local file and remote server version."""
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "Select File", "Please select a file from the list to view diff.")
            return

        row = rows[0].row()
        if row >= len(self.changed_files):
            return

        f = self.changed_files[row]

        # Read local content
        local_content = ""
        if os.path.exists(f.local_path):
            try:
                local_content = Path(f.local_path).read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                local_content = f"(Could not read local file: {e})"
        else:
            local_content = "(Local file deleted)"

        # Read remote content
        remote_content = None
        if self.sftp_client and self.sftp_client.is_connected and f.remote_exists:
            try:
                remote_content = self.sftp_client.read_file_text(f.remote_path)
            except Exception as e:
                remote_content = f"(Could not read remote file: {e})"

        dlg = GitDiffViewerDialog(
            f.rel_path,
            local_content,
            remote_content,
            local_info="Local Git",
            remote_info="Remote Server",
            parent=self,
        )
        dlg.exec()

    def _on_change_repo(self):
        chosen = QFileDialog.getExistingDirectory(self, "Select Git Repository Directory", self.repo_root)
        if chosen:
            root = GitManager.get_repo_root(chosen) or chosen
            self.repo_root = root
            self.lbl_repo_path.setText(f"📁 <b>Repository:</b> {self.repo_root}")
            self._init_git_data()

    def _on_clone_repo(self):
        dlg = CloneRepoDialog(initial_dest_dir=self.repo_root, parent=self)
        dlg.repo_cloned.connect(self._on_repo_cloned)
        dlg.exec()

    def _on_repo_cloned(self, new_dir: str):
        self.repo_root = new_dir
        self.lbl_repo_path.setText(f"📁 <b>Repository:</b> {self.repo_root}")
        self._init_git_data()

    def _on_switch_branch(self):
        new_branch = self.cmb_branch.currentText().strip()
        if not new_branch:
            return

        success, msg = GitManager.checkout_branch(self.repo_root, new_branch)
        if success:
            QMessageBox.information(self, "Branch Switched", f"Successfully switched to branch '{new_branch}'.")
            self._init_git_data()
        else:
            QMessageBox.critical(self, "Checkout Failed", f"Could not switch to branch '{new_branch}':\n{msg}")

    def _start_deployment(self):
        """Executes the Git-FTP deployment with pre-flight safety warnings."""
        if not self.sftp_client or not self.sftp_client.is_connected:
            QMessageBox.critical(self, "Not Connected", "You must be connected to an SFTP/FTP server to deploy files.")
            return

        self._update_summary()
        files_to_deploy = [f for f in self.changed_files if f.selected and f.action != "SKIP"]
        if not files_to_deploy:
            QMessageBox.warning(self, "No Files Selected", "Please select at least one file to deploy.")
            return

        # Pre-flight Conflict Warning
        conflicted = [f for f in files_to_deploy if f.has_conflict and f.action == "UPLOAD"]
        if conflicted:
            msg = (
                f"⚠️ <b>Warning: {len(conflicted)} file(s) have been modified directly on the server!</b><br><br>"
                "Deploying will replace these production files with your local Git version.<br><br>"
                "<b>Conflicting files:</b><ul>"
            )
            for c in conflicted[:6]:
                msg += f"<li><code>{c.rel_path}</code></li>"
            if len(conflicted) > 6:
                msg += f"<li>...and {len(conflicted) - 6} more</li>"
            msg += "</ul><br>Do you want to proceed with deployment?"

            reply = QMessageBox.warning(
                self,
                "Confirm Server File Replacement",
                msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        # Disable buttons and start background deployment worker
        self.btn_deploy.setEnabled(False)
        self.btn_close.setEnabled(False)
        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.lbl_status.setText(f"Deploying {len(files_to_deploy)} file(s)...")

        self.deploy_worker = GitDeployExecuteWorker(
            self.sftp_client,
            files_to_deploy,
            backup_conflicts=self.chk_backup_conflicts.isChecked(),
        )
        self.deploy_worker.file_progress.connect(self._on_deploy_progress)
        self.deploy_worker.step_message.connect(self.lbl_status.setText)
        self.deploy_worker.finished_deploy.connect(self._on_deploy_finished)
        self.deploy_worker.failed.connect(self._on_deploy_failed)
        self.deploy_worker.start()

    def _on_deploy_progress(self, rel_path: str, current_idx: int, total: int):
        pct = int((current_idx / total) * 100) if total > 0 else 0
        self.progress_bar.setValue(pct)
        self.lbl_status.setText(f"[{current_idx}/{total}] Deploying: {rel_path}")

    def _on_deploy_finished(self, success_count: int, fail_count: int, errors: List[str]):
        self.progress_bar.setValue(100)
        self.btn_deploy.setEnabled(True)
        self.btn_close.setEnabled(True)

        # Record deployment history
        curr_commit = self.recent_commits[0] if self.recent_commits else GitCommit("", "", "", "", "")
        record = GitDeploymentRecord(
            last_commit_hash=curr_commit.hash,
            last_commit_short=curr_commit.short_hash,
            last_commit_message=curr_commit.message,
            branch=self.cmb_branch.currentText(),
            deployed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            remote_dir=self.remote_dir,
            total_files_deployed=success_count,
        )
        GitManager.save_deployment_history(self.repo_root, record)
        self.last_record = record

        if fail_count == 0:
            QMessageBox.information(
                self,
                "Deployment Successful",
                f"✔ Successfully deployed <b>{success_count} file(s)</b> to remote server:<br>"
                f"<code>{self.remote_dir}</code><br><br>"
                f"Deployment marker updated to commit <code>[{curr_commit.short_hash}]</code>.",
            )
            self.lbl_status.setText(f"✔ Completed: {success_count} file(s) deployed successfully.")
            self.deployment_completed.emit()
        else:
            err_summary = "<br>".join(errors[:5])
            if len(errors) > 5:
                err_summary += f"<br>...and {len(errors) - 5} more errors."
            QMessageBox.warning(
                self,
                "Deployment Completed with Errors",
                f"Deployed: {success_count} file(s)<br>"
                f"Failed: {fail_count} file(s)<br><br>"
                f"<b>Errors:</b><br>{err_summary}",
            )
            self.lbl_status.setText(f"Completed with errors ({success_count} ok, {fail_count} failed).")

    def _on_deploy_failed(self, err_msg: str):
        self.progress_bar.hide()
        self.btn_deploy.setEnabled(True)
        self.btn_close.setEnabled(True)
        QMessageBox.critical(self, "Deployment Failed", f"Deployment encountered a critical error:\n{err_msg}")
        self.lbl_status.setText("Deployment failed.")

    def closeEvent(self, event):
        if hasattr(self, "scan_worker") and self.scan_worker and self.scan_worker.isRunning():
            self.scan_worker.quit()
            self.scan_worker.wait(500)
        if hasattr(self, "deploy_worker") and self.deploy_worker and self.deploy_worker.isRunning():
            self.deploy_worker.quit()
            self.deploy_worker.wait(500)
        super().closeEvent(event)
