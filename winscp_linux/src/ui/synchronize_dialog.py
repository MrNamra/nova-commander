"""
WinSCP-style Directory Synchronization Dialog and Checklist for WinSCP-Linux.
Matches WinSCP Synchronize (Synchronize.dfm) & Checklist (SynchronizeChecklist.dfm).
Supports:
- Local -> Remote (Upload new & updated)
- Remote -> Local (Download new & updated)
- Bi-directional Synchronization (Both directions)
- Criteria: Timestamp and File Size
- Mirror directory (delete obsolete destination files)
- Interactive difference checklist preview before executing transfers
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.sftp_client import RemoteFileInfo, SFTPClient


@dataclass
class SyncActionItem:
    """Represents a single planned synchronization transfer or deletion."""

    action: str  # "UPLOAD", "DOWNLOAD", "DELETE_REMOTE", "DELETE_LOCAL"
    rel_path: str
    source_path: str
    dest_path: str
    size: int
    mtime_source: float
    mtime_dest: float
    is_dir: bool = False
    selected: bool = True


class SynchronizeChecklistDialog(QDialog):
    """
    Checklist preview dialog showing planned sync actions before starting transfers.
    Allows user to inspect and individually check/uncheck actions.
    """

    def __init__(self, action_items: List[SyncActionItem], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.action_items = action_items
        self.setWindowTitle("Synchronization Checklist - Confirm Actions")
        self.resize(850, 480)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        lbl_desc = QLabel(
            f"The following <b>{len(self.action_items)} action(s)</b> are planned based on your comparison criteria. "
            "Review and uncheck any items you do not wish to synchronize:"
        )
        layout.addWidget(lbl_desc)

        self.table = QTableWidget(len(self.action_items), 5)
        self.table.setHorizontalHeaderLabels(["Action", "Relative Path", "Size", "Source Timestamp", "Dest Timestamp"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

        for row, item in enumerate(self.action_items):
            # Column 0: Action with Checkbox
            action_label = {
                "UPLOAD": "⬆ Upload (to remote)",
                "DOWNLOAD": "⬇ Download (to local)",
                "DELETE_REMOTE": "🗑 Delete remote",
                "DELETE_LOCAL": "🗑 Delete local",
            }.get(item.action, item.action)

            chk_item = QTableWidgetItem(action_label)
            chk_item.setFlags(chk_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            chk_item.setCheckState(Qt.CheckState.Checked if item.selected else Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, chk_item)

            # Column 1: Path
            self.table.setItem(row, 1, QTableWidgetItem(item.rel_path))

            # Column 2: Size
            size_str = f"{item.size:,} B" if not item.is_dir else "<DIR>"
            self.table.setItem(row, 2, QTableWidgetItem(size_str))

            # Column 3: Source Time
            src_dt = datetime.fromtimestamp(item.mtime_source).strftime("%Y-%m-%d %H:%M:%S") if item.mtime_source else "-"
            self.table.setItem(row, 3, QTableWidgetItem(src_dt))

            # Column 4: Dest Time
            dest_dt = datetime.fromtimestamp(item.mtime_dest).strftime("%Y-%m-%d %H:%M:%S") if item.mtime_dest else "-"
            self.table.setItem(row, 4, QTableWidgetItem(dest_dt))

        layout.addWidget(self.table)

        # Quick Select Buttons
        btn_layout = QHBoxLayout()
        btn_all = QPushButton("Select All")
        btn_none = QPushButton("Deselect All")
        btn_all.clicked.connect(self._select_all)
        btn_none.clicked.connect(self._deselect_all)
        btn_layout.addWidget(btn_all)
        btn_layout.addWidget(btn_none)
        btn_layout.addStretch()

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("Start Synchronization")
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        btn_layout.addWidget(btn_box)

        layout.addLayout(btn_layout)

    def _select_all(self):
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item:
                item.setCheckState(Qt.CheckState.Checked)

    def _deselect_all(self):
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item:
                item.setCheckState(Qt.CheckState.Unchecked)

    def _on_accept(self):
        # Update selected flags in items
        for r, it in enumerate(self.action_items):
            tbl_item = self.table.item(r, 0)
            it.selected = bool(tbl_item and tbl_item.checkState() == Qt.CheckState.Checked)
        self.accept()


class SyncScanWorker(QThread):
    """Background worker to scan local & remote directories and build comparison plan."""

    progress = pyqtSignal(str)
    finished_plan = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(
        self,
        sftp_client: SFTPClient,
        local_dir: str,
        remote_dir: str,
        direction: str,  # "LOCAL_TO_REMOTE", "REMOTE_TO_LOCAL", "BOTH"
        criteria: str,  # "TIME_SIZE" or "SIZE_ONLY"
        delete_obsolete: bool = False,
    ):
        super().__init__()
        self.sftp_client = sftp_client
        self.local_dir = os.path.abspath(local_dir)
        self.remote_dir = remote_dir.rstrip("/") or "/"
        self.direction = direction
        self.criteria = criteria
        self.delete_obsolete = delete_obsolete

    def run(self):
        try:
            self.progress.emit("Scanning local and remote directories...")
            actions: List[SyncActionItem] = []

            # 1. Scan Local Files
            local_files: dict[str, tuple[int, float, bool]] = {}  # rel_path -> (size, mtime, is_dir)
            for root, dirs, files in os.walk(self.local_dir):
                for d in dirs:
                    full = os.path.join(root, d)
                    rel = os.path.relpath(full, self.local_dir).replace(os.sep, "/")
                    try:
                        st = os.stat(full)
                        local_files[rel] = (0, st.st_mtime, True)
                    except Exception:
                        pass
                for f in files:
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, self.local_dir).replace(os.sep, "/")
                    try:
                        st = os.stat(full)
                        local_files[rel] = (st.st_size, st.st_mtime, False)
                    except Exception:
                        pass

            # 2. Scan Remote Files recursively
            remote_files: dict[str, tuple[int, float, bool]] = {}
            self._scan_remote_dir(self.remote_dir, "", remote_files)

            all_rel_paths = sorted(set(local_files.keys()) | set(remote_files.keys()))

            for rel in all_rel_paths:
                in_local = rel in local_files
                in_remote = rel in remote_files

                loc_size, loc_mtime, loc_dir = local_files.get(rel, (0, 0.0, False))
                rem_size, rem_mtime, rem_dir = remote_files.get(rel, (0, 0.0, False))

                src_loc_path = os.path.join(self.local_dir, rel.replace("/", os.sep))
                src_rem_path = f"{self.remote_dir}/{rel}".replace("//", "/")

                if self.direction in ("LOCAL_TO_REMOTE", "BOTH"):
                    if in_local and not in_remote:
                        actions.append(
                            SyncActionItem(
                                action="UPLOAD",
                                rel_path=rel,
                                source_path=src_loc_path,
                                dest_path=src_rem_path,
                                size=loc_size,
                                mtime_source=loc_mtime,
                                mtime_dest=0.0,
                                is_dir=loc_dir,
                            )
                        )
                    elif in_local and in_remote and not loc_dir:
                        # Compare
                        needs_upload = False
                        if self.criteria == "TIME_SIZE":
                            if loc_mtime > (rem_mtime + 2.0) or loc_size != rem_size:
                                needs_upload = True
                        elif self.criteria == "SIZE_ONLY":
                            if loc_size != rem_size:
                                needs_upload = True

                        if needs_upload and (self.direction == "LOCAL_TO_REMOTE" or loc_mtime >= rem_mtime):
                            actions.append(
                                SyncActionItem(
                                    action="UPLOAD",
                                    rel_path=rel,
                                    source_path=src_loc_path,
                                    dest_path=src_rem_path,
                                    size=loc_size,
                                    mtime_source=loc_mtime,
                                    mtime_dest=rem_mtime,
                                    is_dir=False,
                                )
                            )

                if self.direction in ("REMOTE_TO_LOCAL", "BOTH"):
                    if in_remote and not in_local:
                        actions.append(
                            SyncActionItem(
                                action="DOWNLOAD",
                                rel_path=rel,
                                source_path=src_rem_path,
                                dest_path=src_loc_path,
                                size=rem_size,
                                mtime_source=rem_mtime,
                                mtime_dest=0.0,
                                is_dir=rem_dir,
                            )
                        )
                    elif in_local and in_remote and not rem_dir:
                        needs_download = False
                        if self.criteria == "TIME_SIZE":
                            if rem_mtime > (loc_mtime + 2.0) or rem_size != loc_size:
                                needs_download = True
                        elif self.criteria == "SIZE_ONLY":
                            if rem_size != loc_size:
                                needs_download = True

                        if needs_download and (self.direction == "REMOTE_TO_LOCAL" or rem_mtime > loc_mtime):
                            actions.append(
                                SyncActionItem(
                                    action="DOWNLOAD",
                                    rel_path=rel,
                                    source_path=src_rem_path,
                                    dest_path=src_loc_path,
                                    size=rem_size,
                                    mtime_source=rem_mtime,
                                    mtime_dest=loc_mtime,
                                    is_dir=False,
                                )
                            )

                # Mirror deletions
                if self.delete_obsolete:
                    if self.direction == "LOCAL_TO_REMOTE" and in_remote and not in_local:
                        actions.append(
                            SyncActionItem(
                                action="DELETE_REMOTE",
                                rel_path=rel,
                                source_path="",
                                dest_path=src_rem_path,
                                size=rem_size,
                                mtime_source=0.0,
                                mtime_dest=rem_mtime,
                                is_dir=rem_dir,
                            )
                        )
                    elif self.direction == "REMOTE_TO_LOCAL" and in_local and not in_remote:
                        actions.append(
                            SyncActionItem(
                                action="DELETE_LOCAL",
                                rel_path=rel,
                                source_path="",
                                dest_path=src_loc_path,
                                size=loc_size,
                                mtime_source=0.0,
                                mtime_dest=loc_mtime,
                                is_dir=loc_dir,
                            )
                        )

            self.finished_plan.emit(actions)
        except Exception as e:
            self.failed.emit(str(e))

    def _scan_remote_dir(self, remote_base: str, rel_prefix: str, out_dict: dict):
        try:
            items = self.sftp_client.listdir(remote_base)
            for it in items:
                if it.name in (".", ".."):
                    continue
                rel = f"{rel_prefix}/{it.name}".lstrip("/")
                out_dict[rel] = (it.size, it.modified_time, it.is_dir)
                if it.is_dir:
                    self._scan_remote_dir(it.path, rel, out_dict)
        except Exception:
            pass


class SyncExecuteWorker(QThread):
    """Background worker to execute the approved synchronization action plan."""

    item_progress = pyqtSignal(str, int, int)  # current_item, current_idx, total
    finished_sync = pyqtSignal(int, int)  # success_count, fail_count
    failed = pyqtSignal(str)

    def __init__(self, sftp_client: SFTPClient, actions: List[SyncActionItem]):
        super().__init__()
        self.sftp_client = sftp_client
        self.actions = [a for a in actions if a.selected]

    def run(self):
        total = len(self.actions)
        success = 0
        failed = 0

        for idx, act in enumerate(self.actions):
            self.item_progress.emit(f"[{idx+1}/{total}] {act.action}: {act.rel_path}", idx + 1, total)
            try:
                if act.action == "UPLOAD":
                    if act.is_dir:
                        try:
                            self.sftp_client.mkdir(act.dest_path)
                        except Exception:
                            pass
                    else:
                        parent_rem = os.path.dirname(act.dest_path)
                        try:
                            self.sftp_client.mkdir(parent_rem)
                        except Exception:
                            pass
                        self.sftp_client.upload_file(act.source_path, act.dest_path)

                elif act.action == "DOWNLOAD":
                    if act.is_dir:
                        os.makedirs(act.dest_path, exist_ok=True)
                    else:
                        os.makedirs(os.path.dirname(act.dest_path), exist_ok=True)
                        self.sftp_client.download_file(act.source_path, act.dest_path)

                elif act.action == "DELETE_REMOTE":
                    if act.is_dir:
                        self.sftp_client.delete_directory_recursive(act.dest_path)
                    else:
                        self.sftp_client.delete_file(act.dest_path)

                elif act.action == "DELETE_LOCAL":
                    if act.is_dir:
                        import shutil
                        shutil.rmtree(act.dest_path, ignore_errors=True)
                    else:
                        try:
                            os.remove(act.dest_path)
                        except Exception:
                            pass

                success += 1
            except Exception:
                failed += 1

        self.finished_sync.emit(success, failed)


class SynchronizeDialog(QDialog):
    """
    Main WinSCP-style Synchronize configuration dialog.
    Allows configuring sync direction, comparison mode, and starts scanning.
    """

    sync_completed = pyqtSignal()

    def __init__(
        self,
        sftp_client: SFTPClient,
        local_dir: str,
        remote_dir: str,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.sftp_client = sftp_client
        self.local_dir = local_dir
        self.remote_dir = remote_dir

        self.setWindowTitle("Synchronize Directories")
        self.resize(540, 360)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Paths
        group_paths = QGroupBox("Directories to Synchronize")
        form_paths = QFormLayout(group_paths)
        self.txt_local = QLineEdit(self.local_dir)
        self.txt_remote = QLineEdit(self.remote_dir)
        form_paths.addRow("Local Directory:", self.txt_local)
        form_paths.addRow("Remote Directory:", self.txt_remote)
        layout.addWidget(group_paths)

        # Direction Group
        group_dir = QGroupBox("Direction / Target")
        layout_dir = QVBoxLayout(group_dir)
        self.rb_local_to_remote = QRadioButton("Local -> Remote (Upload new & updated files)")
        self.rb_remote_to_local = QRadioButton("Remote -> Local (Download new & updated files)")
        self.rb_both = QRadioButton("Both directions (Synchronize bidirectional)")
        self.rb_local_to_remote.setChecked(True)

        layout_dir.addWidget(self.rb_local_to_remote)
        layout_dir.addWidget(self.rb_remote_to_local)
        layout_dir.addWidget(self.rb_both)
        layout.addWidget(group_dir)

        # Criteria & Options
        group_opts = QGroupBox("Comparison Criteria & Options")
        layout_opts = QVBoxLayout(group_opts)

        self.rb_crit_time_size = QRadioButton("Compare by Modification Time and Size (Recommended)")
        self.rb_crit_size_only = QRadioButton("Compare by File Size only")
        self.rb_crit_time_size.setChecked(True)
        layout_opts.addWidget(self.rb_crit_time_size)
        layout_opts.addWidget(self.rb_crit_size_only)

        self.chk_delete = QCheckBox("Mirror directory (delete obsolete destination files)")
        self.chk_preview = QCheckBox("Preview checklist before synchronization")
        self.chk_preview.setChecked(True)
        layout_opts.addWidget(self.chk_delete)
        layout_opts.addWidget(self.chk_preview)
        layout.addWidget(group_opts)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        # Buttons
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.btn_ok = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        self.btn_ok.setText("Synchronize...")
        btn_box.accepted.connect(self._start_scan)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _start_scan(self):
        if not self.sftp_client.is_connected:
            QMessageBox.warning(self, "Not Connected", "Must be connected to a remote server to synchronize.")
            return

        direction = (
            "LOCAL_TO_REMOTE"
            if self.rb_local_to_remote.isChecked()
            else ("REMOTE_TO_LOCAL" if self.rb_remote_to_local.isChecked() else "BOTH")
        )
        criteria = "TIME_SIZE" if self.rb_crit_time_size.isChecked() else "SIZE_ONLY"
        delete_obs = self.chk_delete.isChecked()

        self.btn_ok.setEnabled(False)
        self.progress_bar.show()

        self.scan_worker = SyncScanWorker(
            sftp_client=self.sftp_client,
            local_dir=self.txt_local.text(),
            remote_dir=self.txt_remote.text(),
            direction=direction,
            criteria=criteria,
            delete_obsolete=delete_obs,
        )
        self.scan_worker.finished_plan.connect(self._on_plan_ready)
        self.scan_worker.failed.connect(self._on_scan_failed)
        self.scan_worker.start()

    def _on_scan_failed(self, error: str):
        self.progress_bar.hide()
        self.btn_ok.setEnabled(True)
        QMessageBox.critical(self, "Sync Scan Failed", f"Could not analyze directories: {error}")

    def _on_plan_ready(self, actions: List[SyncActionItem]):
        self.progress_bar.hide()
        self.btn_ok.setEnabled(True)

        if not actions:
            QMessageBox.information(self, "Synchronize", "Directories are already synchronized! No actions needed.")
            self.accept()
            return

        if self.chk_preview.isChecked():
            chk_dlg = SynchronizeChecklistDialog(actions, self)
            if chk_dlg.exec() != QDialog.DialogCode.Accepted:
                return

        # Execute transfers
        selected_actions = [a for a in actions if a.selected]
        if not selected_actions:
            QMessageBox.information(self, "Synchronize", "No items selected to synchronize.")
            return

        self.progress_bar.setRange(0, len(selected_actions))
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.btn_ok.setEnabled(False)

        self.exec_worker = SyncExecuteWorker(self.sftp_client, selected_actions)
        self.exec_worker.item_progress.connect(lambda msg, cur, tot: self.progress_bar.setValue(cur))
        self.exec_worker.finished_sync.connect(self._on_sync_finished)
        self.exec_worker.failed.connect(self._on_sync_failed)
        self.exec_worker.start()

    def _on_sync_finished(self, success_count: int, fail_count: int):
        self.progress_bar.hide()
        self.btn_ok.setEnabled(True)
        msg = f"Synchronization completed!\n- Successfully processed: {success_count} item(s)"
        if fail_count > 0:
            msg += f"\n- Failed items: {fail_count}"
        QMessageBox.information(self, "Synchronization Finished", msg)
        self.sync_completed.emit()
        self.accept()

    def _on_sync_failed(self, error: str):
        self.progress_bar.hide()
        self.btn_ok.setEnabled(True)
        QMessageBox.critical(self, "Sync Error", f"Synchronization encountered an error: {error}")
