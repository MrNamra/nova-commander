"""
Transfer Queue Widget for WinSCP-Linux.
Displays queued and active file transfers at the bottom of the window with:
- Scrollable list of files/folders
- Individual & overall percentage progress bars
- Real-time transfer speed & ETA
- Pause / Resume individual or all transfers
- Cancel individual or all transfers
- Priority adjustments (High, Normal, Low, Move Up, Move Down)
"""

from typing import Dict, Optional
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.transfer_queue import (
    TransferJob,
    TransferPriority,
    TransferQueueEngine,
    TransferStatus,
)


class TransferQueueWidget(QWidget):
    """
    Bottom dockable / collapsible transfer queue widget.
    Features real-time progress, speed, ETA, pause/resume, cancel, and priority reordering.
    """

    close_requested = pyqtSignal()
    transfers_cleared = pyqtSignal()

    def __init__(self, queue_engine: TransferQueueEngine, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.queue_engine = queue_engine
        self._row_map: Dict[str, int] = {}  # job_id -> row index

        self._setup_ui()
        self._wire_engine_signals()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ================= Top Header & Overall Progress Bar =================
        header = QHBoxLayout()
        header.setSpacing(6)

        self.lbl_title = QLabel("📥 Transfer Queue (0 items)")
        self.lbl_title.setStyleSheet("font-weight: bold; font-size: 12px;")
        header.addWidget(self.lbl_title)

        # Overall progress bar
        self.overall_progress = QProgressBar()
        self.overall_progress.setFixedHeight(18)
        self.overall_progress.setRange(0, 100)
        self.overall_progress.setValue(0)
        self.overall_progress.setTextVisible(True)
        self.overall_progress.setFormat("%p% (%v / %m)")
        self.overall_progress.setStyleSheet(
            "QProgressBar { border: 1px solid #7a7a7a; border-radius: 3px; text-align: center; font-size: 11px; font-weight: bold; }"
            "QProgressBar::chunk { background-color: #0078d7; }"
        )
        header.addWidget(self.overall_progress, 1)

        # Speed & ETA
        self.lbl_speed_eta = QLabel("⚡ --  ⏱ --")
        self.lbl_speed_eta.setStyleSheet("font-size: 11px; font-weight: 500;")
        header.addWidget(self.lbl_speed_eta)

        # Queue Toolbar Controls
        self.btn_pause_all = QPushButton("⏸ Pause All")
        self.btn_pause_all.setToolTip("Pause all active and queued transfers")
        self.btn_pause_all.setFixedHeight(26)
        self.btn_pause_all.clicked.connect(self._toggle_pause_all)

        self.btn_move_up = QPushButton("⬆ Up")
        self.btn_move_up.setToolTip("Move selected transfer up (increase priority)")
        self.btn_move_up.setFixedSize(54, 26)
        self.btn_move_up.clicked.connect(self._move_selected_up)

        self.btn_move_down = QPushButton("⬇ Down")
        self.btn_move_down.setToolTip("Move selected transfer down (decrease priority)")
        self.btn_move_down.setFixedSize(54, 26)
        self.btn_move_down.clicked.connect(self._move_selected_down)

        self.btn_priority = QPushButton("⚡ Priority ▾")
        self.btn_priority.setToolTip("Set priority for selected transfer")
        self.btn_priority.setFixedHeight(26)
        pri_menu = QMenu(self)
        pri_menu.addAction("⚡ High Priority (Transfer First)").triggered.connect(lambda: self._set_selected_priority(TransferPriority.HIGH))
        pri_menu.addAction(" Normal Priority").triggered.connect(lambda: self._set_selected_priority(TransferPriority.NORMAL))
        pri_menu.addAction("⬇ Low Priority").triggered.connect(lambda: self._set_selected_priority(TransferPriority.LOW))
        self.btn_priority.setMenu(pri_menu)

        self.btn_clear = QPushButton("🗑 Clear Finished")
        self.btn_clear.setToolTip("Remove completed, cancelled, and failed items from queue")
        self.btn_clear.setFixedHeight(26)
        self.btn_clear.clicked.connect(self.queue_engine.clear_completed)

        self.btn_cancel_all = QPushButton("❌ Cancel All")
        self.btn_cancel_all.setToolTip("Cancel all active and queued transfers")
        self.btn_cancel_all.setFixedHeight(26)
        self.btn_cancel_all.clicked.connect(self.queue_engine.cancel_all)

        self.btn_close = QPushButton("✖")
        self.btn_close.setToolTip("Hide Transfer Queue panel")
        self.btn_close.setFixedSize(26, 26)
        self.btn_close.setStyleSheet("QPushButton { font-weight: bold; }")
        self.btn_close.clicked.connect(self.close_requested.emit)

        header.addWidget(self.btn_pause_all)
        header.addWidget(self.btn_move_up)
        header.addWidget(self.btn_move_down)
        header.addWidget(self.btn_priority)
        header.addWidget(self.btn_clear)
        header.addWidget(self.btn_cancel_all)
        header.addWidget(self.btn_close)

        layout.addLayout(header)

        # ================= Scrollable Queue Table Widget =================
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels([
            "#",
            "Type",
            "File Name",
            "Size",
            "Progress",
            "Speed",
            "ETA",
            "Priority",
            "Status",
            "Actions",
        ])

        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        header_view.resizeSection(4, 160)
        header_view.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(9, QHeaderView.ResizeMode.ResizeToContents)

        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.setAlternatingRowColors(True)

        layout.addWidget(self.table, 1)

    def _wire_engine_signals(self):
        self.queue_engine.queue_updated.connect(self.refresh_queue)
        self.queue_engine.job_progress.connect(self._on_job_progress)
        self.queue_engine.job_status_changed.connect(self._on_job_status_changed)
        self.queue_engine.queue_progress.connect(self._on_queue_progress)

    # =========================================================================
    # Rendering & Refreshing Queue
    # =========================================================================

    def refresh_queue(self):
        """Full refresh of table rows to reflect current queue state."""
        jobs = self.queue_engine.get_jobs()

        active_count = sum(1 for j in jobs if j.status == TransferStatus.RUNNING)
        queued_count = sum(1 for j in jobs if j.status == TransferStatus.QUEUED)
        self.lbl_title.setText(f"📥 Transfer Queue ({len(jobs)} items: {active_count} active, {queued_count} queued)")

        # Update Pause All button text
        all_paused = not self.queue_engine._queue_paused.is_set()
        self.btn_pause_all.setText("▶ Resume All" if all_paused else "⏸ Pause All")

        self.table.setRowCount(0)
        self._row_map.clear()

        for row, job in enumerate(jobs):
            self.table.insertRow(row)
            self._row_map[job.job_id] = row

            # Col 0: #
            it_num = QTableWidgetItem(f"{row + 1}")
            it_num.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, it_num)

            # Col 1: Type
            type_icon = "📁" if job.is_dir else "📄"
            dir_str = "⬆ Upload" if job.direction == "upload" else "⬇ Download"
            it_type = QTableWidgetItem(f"{type_icon} {dir_str}")
            it_type.setData(Qt.ItemDataRole.UserRole, job.job_id)
            self.table.setItem(row, 1, it_type)

            # Col 2: Name & Source/Dest tooltip
            it_name = QTableWidgetItem(job.name)
            it_name.setFont(QFont("Sans", weight=QFont.Weight.Bold))
            it_name.setToolTip(f"Source: {job.source}\nDestination: {job.dest}")
            self.table.setItem(row, 2, it_name)

            # Col 3: Size
            it_size = QTableWidgetItem(job.size_str)
            it_size.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 3, it_size)

            # Col 4: Progress Bar Widget
            pbar = QProgressBar()
            pbar.setFixedHeight(18)
            pbar.setRange(0, 100)
            pbar.setValue(int(job.percent))
            pbar.setTextVisible(True)
            pbar.setFormat(f"{job.percent:.1f}%")
            pbar.setStyleSheet(
                "QProgressBar { border: 1px solid #7a7a7a; border-radius: 2px; text-align: center; font-size: 10px; }"
                "QProgressBar::chunk { background-color: #2da44e; }"
            )
            self.table.setCellWidget(row, 4, pbar)

            # Col 5: Speed
            it_speed = QTableWidgetItem(job.speed_str)
            it_speed.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 5, it_speed)

            # Col 6: ETA
            it_eta = QTableWidgetItem(job.eta_str)
            it_eta.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 6, it_eta)

            # Col 7: Priority
            pri_name = job.priority.name.capitalize()
            pri_icon = "⚡ " if job.priority == TransferPriority.HIGH else ("⬇ " if job.priority == TransferPriority.LOW else "  ")
            it_pri = QTableWidgetItem(f"{pri_icon}{pri_name}")
            it_pri.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if job.priority == TransferPriority.HIGH:
                it_pri.setForeground(QColor("#f0883e"))
            self.table.setItem(row, 7, it_pri)

            # Col 8: Status Badge
            it_status = QTableWidgetItem(job.status)
            it_status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            it_status.setFont(QFont("Sans", weight=QFont.Weight.Bold))
            if job.status == TransferStatus.RUNNING:
                it_status.setForeground(QColor("#58a6ff"))
            elif job.status == TransferStatus.COMPLETED:
                it_status.setForeground(QColor("#3fb950"))
            elif job.status == TransferStatus.PAUSED:
                it_status.setForeground(QColor("#d29922"))
            elif job.status in (TransferStatus.CANCELLED, TransferStatus.FAILED):
                it_status.setForeground(QColor("#f85149"))
            self.table.setItem(row, 8, it_status)

            # Col 9: Action Buttons (Pause/Resume + Cancel)
            action_widget = QWidget()
            act_layout = QHBoxLayout(action_widget)
            act_layout.setContentsMargins(2, 1, 2, 1)
            act_layout.setSpacing(4)

            btn_pause = QPushButton("▶" if job.status == TransferStatus.PAUSED else "⏸")
            btn_pause.setToolTip("Resume" if job.status == TransferStatus.PAUSED else "Pause")
            btn_pause.setFixedSize(24, 22)
            btn_pause.setEnabled(job.status in (TransferStatus.RUNNING, TransferStatus.PAUSED, TransferStatus.QUEUED))
            btn_pause.clicked.connect(lambda _, jid=job.job_id, s=job.status: self._toggle_job_pause(jid, s))

            btn_cancel = QPushButton("❌")
            btn_cancel.setToolTip("Cancel this transfer")
            btn_cancel.setFixedSize(24, 22)
            btn_cancel.setEnabled(job.status in (TransferStatus.RUNNING, TransferStatus.PAUSED, TransferStatus.QUEUED))
            btn_cancel.clicked.connect(lambda _, jid=job.job_id: self.queue_engine.cancel_job(jid))

            act_layout.addWidget(btn_pause)
            act_layout.addWidget(btn_cancel)
            self.table.setCellWidget(row, 9, action_widget)

    def _on_job_progress(self, job_id: str, transferred: int, total: int, speed: float, eta: float):
        """High-frequency granular progress update for a single job."""
        row = self._row_map.get(job_id)
        if row is None or row >= self.table.rowCount():
            return

        job = self.queue_engine.get_job(job_id)
        if not job:
            return

        # Update Size
        it_size = self.table.item(row, 3)
        if it_size:
            it_size.setText(job.size_str)

        # Update Progress Bar
        pbar = self.table.cellWidget(row, 4)
        if isinstance(pbar, QProgressBar):
            pbar.setValue(int(job.percent))
            pbar.setFormat(f"{job.percent:.1f}%")

        # Update Speed
        it_speed = self.table.item(row, 5)
        if it_speed:
            it_speed.setText(job.speed_str)

        # Update ETA
        it_eta = self.table.item(row, 6)
        if it_eta:
            it_eta.setText(job.eta_str)

    def _on_job_status_changed(self, job_id: str, status: str):
        """Updates status cell and action buttons when a job's status transitions."""
        row = self._row_map.get(job_id)
        if row is None or row >= self.table.rowCount():
            return

        it_status = self.table.item(row, 8)
        if it_status:
            it_status.setText(status)
            if status == TransferStatus.RUNNING:
                it_status.setForeground(QColor("#58a6ff"))
            elif status == TransferStatus.COMPLETED:
                it_status.setForeground(QColor("#3fb950"))
            elif status == TransferStatus.PAUSED:
                it_status.setForeground(QColor("#d29922"))
            elif status in (TransferStatus.CANCELLED, TransferStatus.FAILED):
                it_status.setForeground(QColor("#f85149"))

        # Update action buttons state
        action_widget = self.table.cellWidget(row, 9)
        if action_widget:
            btns = action_widget.findChildren(QPushButton)
            if len(btns) >= 2:
                btn_pause, btn_cancel = btns[0], btns[1]
                btn_pause.setText("▶" if status == TransferStatus.PAUSED else "⏸")
                btn_pause.setToolTip("Resume" if status == TransferStatus.PAUSED else "Pause")
                btn_pause.setEnabled(status in (TransferStatus.RUNNING, TransferStatus.PAUSED, TransferStatus.QUEUED))
                btn_cancel.setEnabled(status in (TransferStatus.RUNNING, TransferStatus.PAUSED, TransferStatus.QUEUED))

    def _on_queue_progress(self, total_tx: int, total_all: int, pct: float, speed: float, eta: float):
        """Updates overall progress bar and speed/ETA in header."""
        self.overall_progress.setValue(int(pct))
        overall_str = f"{TransferJob._format_bytes(total_tx)} / {TransferJob._format_bytes(total_all)}"
        self.overall_progress.setFormat(f"{pct:.1f}% [{overall_str}]")

        speed_str = TransferJob._format_bytes_speed(speed) if speed > 0 else "--"
        if eta > 0 and eta < 86400:
            m, s = divmod(int(eta), 60)
            h, m = divmod(m, 60)
            eta_str = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
        else:
            eta_str = "--"

        self.lbl_speed_eta.setText(f"⚡ {speed_str}  ⏱ ETA: {eta_str}")

    # =========================================================================
    # User Interactions & Context Menus
    # =========================================================================

    def _toggle_job_pause(self, job_id: str, current_status: str):
        if current_status == TransferStatus.PAUSED:
            self.queue_engine.resume_job(job_id)
        else:
            self.queue_engine.pause_job(job_id)

    def _toggle_pause_all(self):
        if not self.queue_engine._queue_paused.is_set():
            self.queue_engine.resume_all()
        else:
            self.queue_engine.pause_all()

    def _get_selected_job_id(self) -> Optional[str]:
        items = self.table.selectedItems()
        if not items:
            return None
        row = items[0].row()
        item_type = self.table.item(row, 1)
        if item_type:
            return item_type.data(Qt.ItemDataRole.UserRole)
        return None

    def _move_selected_up(self):
        jid = self._get_selected_job_id()
        if jid:
            self.queue_engine.move_job_up(jid)

    def _move_selected_down(self):
        jid = self._get_selected_job_id()
        if jid:
            self.queue_engine.move_job_down(jid)

    def _set_selected_priority(self, pri: TransferPriority):
        jid = self._get_selected_job_id()
        if jid:
            self.queue_engine.set_job_priority(jid, pri)

    def _show_context_menu(self, pos):
        item = self.table.itemAt(pos)
        if not item:
            return
        row = item.row()
        it_type = self.table.item(row, 1)
        if not it_type:
            return
        job_id = it_type.data(Qt.ItemDataRole.UserRole)
        job = self.queue_engine.get_job(job_id)
        if not job:
            return

        menu = QMenu(self)

        if job.status == TransferStatus.PAUSED:
            act_pause = menu.addAction("▶ Resume Transfer")
            act_pause.triggered.connect(lambda: self.queue_engine.resume_job(job_id))
        elif job.status in (TransferStatus.RUNNING, TransferStatus.QUEUED):
            act_pause = menu.addAction("⏸ Pause Transfer")
            act_pause.triggered.connect(lambda: self.queue_engine.pause_job(job_id))

        if job.status in (TransferStatus.RUNNING, TransferStatus.PAUSED, TransferStatus.QUEUED):
            act_cancel = menu.addAction("❌ Cancel Transfer")
            act_cancel.triggered.connect(lambda: self.queue_engine.cancel_job(job_id))

        menu.addSeparator()

        act_up = menu.addAction("⬆ Move Up in Queue")
        act_up.triggered.connect(lambda: self.queue_engine.move_job_up(job_id))

        act_down = menu.addAction("⬇ Move Down in Queue")
        act_down.triggered.connect(lambda: self.queue_engine.move_job_down(job_id))

        pri_menu = menu.addMenu("⚡ Set Priority")
        act_pri_high = pri_menu.addAction("⚡ High Priority (Transfer First)")
        act_pri_high.triggered.connect(lambda: self.queue_engine.set_job_priority(job_id, TransferPriority.HIGH))

        act_pri_norm = pri_menu.addAction(" Normal Priority")
        act_pri_norm.triggered.connect(lambda: self.queue_engine.set_job_priority(job_id, TransferPriority.NORMAL))

        act_pri_low = pri_menu.addAction("⬇ Low Priority")
        act_pri_low.triggered.connect(lambda: self.queue_engine.set_job_priority(job_id, TransferPriority.LOW))

        menu.addSeparator()
        act_clear = menu.addAction("🗑 Clear Finished Items")
        act_clear.triggered.connect(self.queue_engine.clear_completed)

        menu.exec(self.table.mapToGlobal(pos))
