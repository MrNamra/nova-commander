"""
Tests for Transfer Queue System, TransferQueueEngine, and TransferQueueWidget.
Validates multi-file queueing, progress calculations, pause/resume,
cancellation, priority sorting/reordering, and UI interactions.
"""

from pathlib import Path
import time
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QProgressBar

from src.core.sftp_client import SFTPClient
from src.core.transfer_queue import (
    TransferJob,
    TransferPriority,
    TransferQueueEngine,
    TransferStatus,
)
from src.ui.main_window import MainWindow, SessionCommanderView
from src.ui.transfer_queue_widget import TransferQueueWidget


@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_transfer_job_attributes_and_calculations():
    """Verifies TransferJob percent, size_str, speed_str, and eta_str calculations."""
    job = TransferJob(
        source="/local/test_file.zip",
        dest="/remote/test_file.zip",
        is_dir=False,
        direction="upload",
        priority=TransferPriority.HIGH,
        order=1,
    )

    assert job.name == "test_file.zip"
    assert job.direction == "upload"
    assert job.priority == TransferPriority.HIGH
    assert job.status == TransferStatus.QUEUED
    assert job.percent == 0.0

    job.total_bytes = 100 * 1024 * 1024  # 100 MB
    job.transferred_bytes = 25 * 1024 * 1024  # 25 MB
    assert job.percent == 25.0
    assert "25.0 MB / 100.0 MB" in job.size_str

    job.status = TransferStatus.RUNNING
    job.speed_bps = 5 * 1024 * 1024  # 5 MB/s
    job.eta_seconds = 15.0
    assert job.speed_str == "5.0 MB/s"
    assert job.eta_str == "00:15"


def test_transfer_queue_enqueue_and_priority_reordering():
    """Verifies queueing multiple transfers and adjusting priority and order."""
    mock_sftp = MagicMock(spec=SFTPClient)
    engine = TransferQueueEngine(mock_sftp)
    engine.pause_all()
    try:
        transfers = [
            {"source": "/path/low.txt", "dest": "/dest/low.txt", "is_dir": False, "priority": TransferPriority.LOW},
            {"source": "/path/normal.txt", "dest": "/dest/normal.txt", "is_dir": False, "priority": TransferPriority.NORMAL},
            {"source": "/path/high.txt", "dest": "/dest/high.txt", "is_dir": False, "priority": TransferPriority.HIGH},
        ]

        jobs = engine.add_transfers(transfers)
        assert len(jobs) == 3
        assert engine.queued_job_count() == 3

        # Check priorities
        assert jobs[0].priority == TransferPriority.LOW
        assert jobs[1].priority == TransferPriority.NORMAL
        assert jobs[2].priority == TransferPriority.HIGH

        # Move low job up
        low_id = jobs[0].job_id
        engine.set_job_priority(low_id, TransferPriority.HIGH)
        assert engine.get_job(low_id).priority == TransferPriority.HIGH

        # Move normal job down
        norm_id = jobs[1].job_id
        engine.move_job_down(norm_id)
        assert engine.queued_job_count() == 3
    finally:
        engine.stop()
        engine.wait(500)


def test_transfer_queue_pause_resume_cancel():
    """Verifies individual and bulk pause, resume, and cancellation."""
    mock_sftp = MagicMock(spec=SFTPClient)
    engine = TransferQueueEngine(mock_sftp)
    engine.pause_all()
    try:
        j1 = engine.add_transfer("/src/a.txt", "/dst/a.txt")
        j2 = engine.add_transfer("/src/b.txt", "/dst/b.txt")
        j3 = engine.add_transfer("/src/c.txt", "/dst/c.txt")

        # Pause specific job
        engine.pause_job(j1.job_id)
        assert j1.status == TransferStatus.PAUSED

        # Resume specific job
        engine.resume_job(j1.job_id)
        assert j1.status == TransferStatus.QUEUED

        # Cancel specific job
        engine.cancel_job(j2.job_id)
        assert j2.status == TransferStatus.CANCELLED

        # Resume all
        engine.resume_all()
        assert engine._queue_paused.is_set()

        # Pause all
        engine.pause_all()
        assert not engine._queue_paused.is_set()

        # Cancel all
        engine.cancel_all()
        assert j1.status == TransferStatus.CANCELLED
        assert j3.status == TransferStatus.CANCELLED

        # Clear completed
        engine.clear_completed()
        assert len(engine.get_jobs()) == 0
    finally:
        engine.stop()
        engine.wait(500)


def test_transfer_queue_execution_worker(tmp_path: Path):
    """Verifies background thread executes transfers and updates job status to completed."""
    mock_sftp = MagicMock(spec=SFTPClient)
    mock_sftp.is_connected = True

    # Dummy local file
    f1 = tmp_path / "queue_test1.txt"
    f1.write_text("Hello World Queue Transfer")

    engine = TransferQueueEngine(mock_sftp)
    engine.pause_all()
    try:
        job = engine.add_transfer(str(f1), "/remote/queue_test1.txt", is_dir=False, direction="upload")
        assert job.total_bytes == len(f1.read_bytes())

        # Simulate execution of 1 job
        engine._execute_job(job)
        assert mock_sftp.upload_file.called
        job.status = TransferStatus.COMPLETED

        assert job.status == TransferStatus.COMPLETED
        assert job.percent == 100.0
    finally:
        engine.stop()
        engine.wait(500)


def test_transfer_queue_widget_ui(tmp_path: Path):
    """Verifies TransferQueueWidget UI rendering, table rows, progress bars, and buttons."""
    mock_sftp = MagicMock(spec=SFTPClient)
    engine = TransferQueueEngine(mock_sftp)
    engine.pause_all()
    try:
        widget = TransferQueueWidget(engine)
        assert widget.table.columnCount() == 10

        # Add 2 jobs to engine
        j1 = engine.add_transfer(str(tmp_path / "item1.dat"), "/remote/item1.dat", direction="upload", priority=TransferPriority.HIGH)
        j2 = engine.add_transfer("/remote/item2.dat", str(tmp_path / "item2.dat"), direction="download", priority=TransferPriority.NORMAL)

        widget.refresh_queue()
        assert widget.table.rowCount() == 2

        # Check progress bar cell in row 0
        pbar = widget.table.cellWidget(0, 4)
        assert isinstance(pbar, QProgressBar)

        # Check action widget cell in row 0
        action_w = widget.table.cellWidget(0, 9)
        assert action_w is not None

        # Test pause toggle via engine
        engine.pause_job(j1.job_id)
        widget.refresh_queue()
        assert widget.table.item(0, 8).text() == TransferStatus.PAUSED
    finally:
        engine.stop()
        engine.wait(500)


def test_session_commander_view_queue_integration(qapp, tmp_path: Path):
    """Verifies that SessionCommanderView embeds TransferQueueWidget and toggles correctly."""
    view = SessionCommanderView(initial_local_dir=str(tmp_path))
    assert hasattr(view, "queue_engine")
    assert hasattr(view, "queue_widget")

    # Initially queue widget is hidden
    assert view.queue_widget.isHidden() is True

    # Show transfer queue
    view.show_transfer_queue()
    assert not view.queue_widget.isHidden()

    # Toggle transfer queue
    view.toggle_transfer_queue()
    assert view.queue_widget.isHidden() is True

    # Execute batch transfer automatically shows queue
    transfers = [
        {"source": str(tmp_path / "auto.txt"), "dest": "/remote/auto.txt", "is_dir": False, "direction": "upload"}
    ]
    view._execute_batch_transfer(transfers, refresh_pane="remote")
    assert not view.queue_widget.isHidden()
    assert len(view.queue_engine.get_jobs()) == 1

    view.cleanup()


def test_main_window_queue_toolbar_and_menu(qapp):
    """Verifies MainWindow has Queue toolbar button and Ctrl+Q shortcut wired."""
    win = MainWindow()
    assert hasattr(win, "tb_queue")
    assert hasattr(win, "toggle_transfer_queue")

    # Toggle queue via MainWindow
    view = win.active_session_view()
    assert view.queue_widget.isHidden() is True

    win.toggle_transfer_queue()
    assert not view.queue_widget.isHidden()

    win.toggle_transfer_queue()
    assert view.queue_widget.isHidden() is True

    win.close()


def test_transfer_job_display_name():
    """Verifies display_name reflects active sub-file path and indices during directory transfers."""
    file_job = TransferJob(
        source="/local/test.txt",
        dest="/remote/test.txt",
        is_dir=False,
    )
    assert file_job.display_name == "test.txt"

    dir_job = TransferJob(
        source="/local/project/.git",
        dest="/remote/project/.git",
        is_dir=True,
    )
    assert dir_job.display_name == ".git"

    # Queued with known file count
    dir_job.total_files = 322
    assert dir_job.display_name == ".git (322 files)"

    # Running with active sub-file and index
    dir_job.status = TransferStatus.RUNNING
    dir_job.current_file = "objects/4a/abcdef1234"
    dir_job.current_file_idx = 45
    assert dir_job.display_name == ".git ➔ objects/4a/abcdef1234 [45/322]"

    # Completed
    dir_job.status = TransferStatus.COMPLETED
    assert dir_job.display_name == ".git ✔ (322 files)"

    # Cancelled
    dir_job.status = TransferStatus.CANCELLED
    assert dir_job.display_name == ".git ⏸ (Stopped at: objects/4a/abcdef1234)"

    # Failed
    dir_job.status = TransferStatus.FAILED
    assert dir_job.display_name == ".git ❌ (Failed at: objects/4a/abcdef1234)"


def test_transfer_queue_widget_directory_display_and_job_updated(qapp, tmp_path: Path):
    """Verifies TransferQueueWidget updates Column 2 with sub-file path on job_updated signal."""
    mock_sftp = MagicMock(spec=SFTPClient)
    engine = TransferQueueEngine(mock_sftp)
    engine.pause_all()
    try:
        widget = TransferQueueWidget(engine)
        job = engine.add_transfer(
            str(tmp_path / ".git"),
            "/remote/.git",
            is_dir=True,
            direction="upload",
        )
        widget.refresh_queue()

        # Initially shows folder name
        it_name = widget.table.item(0, 2)
        assert it_name is not None
        assert it_name.text() == ".git"

        # Simulate job running and sub-file update
        job.status = TransferStatus.RUNNING
        job.total_files = 150
        job.current_file = "hooks/pre-commit"
        job.current_file_idx = 10
        engine.job_updated.emit(job)

        # Verify Column 2 updated dynamically
        it_name = widget.table.item(0, 2)
        assert it_name.text() == ".git ➔ hooks/pre-commit [10/150]"
        assert "hooks/pre-commit" in it_name.toolTip()
    finally:
        engine.stop()
        engine.wait(500)


def test_concurrent_transfers_configuration_and_widget_sync(qapp):
    """Verifies that max_concurrent transfers can be configured and syncs between engine and widget."""
    mock_sftp = MagicMock(spec=SFTPClient)
    engine = TransferQueueEngine(mock_sftp)
    engine.pause_all()
    try:
        assert engine.max_concurrent >= 1
        engine.set_max_concurrent(4)
        assert engine.max_concurrent == 4

        widget = TransferQueueWidget(engine)
        assert hasattr(widget, "spin_parallel")
        assert widget.spin_parallel.value() == 4

        # Changing widget spinbox updates engine
        widget.spin_parallel.setValue(3)
        assert engine.max_concurrent == 3

        # Changing engine updates widget
        engine.set_max_concurrent(5)
        assert widget.spin_parallel.value() == 5
    finally:
        engine.stop()
        engine.wait(500)


def test_concurrent_folder_upload(tmp_path: Path):
    """Verifies concurrent directory upload handles multiple files with hooks."""
    mock_sftp = MagicMock(spec=SFTPClient)
    mock_sftp.is_connected = True
    # Return mock_sftp for get_worker_client
    mock_sftp.get_worker_client.return_value = mock_sftp

    # Create dummy folder with 4 files
    test_dir = tmp_path / "parallel_folder"
    test_dir.mkdir()
    for i in range(4):
        (test_dir / f"file_{i}.txt").write_text(f"content {i}" * 10)

    engine = TransferQueueEngine(mock_sftp)
    engine.pause_all()
    try:
        engine.set_max_concurrent(3)
        assert engine.max_concurrent == 3

        job = engine.add_transfer(str(test_dir), "/remote/parallel_folder", is_dir=True, direction="upload")
        assert job.is_dir is True

        progress_calls = []
        def _cb(tx, tot):
            progress_calls.append((tx, tot))

        engine._upload_dir_with_hooks(job, _cb, client=mock_sftp)

        assert mock_sftp.upload_file.call_count >= 4
        assert job.total_files == 4
        assert len(progress_calls) >= 4
    finally:
        engine.stop()
        engine.wait(500)


