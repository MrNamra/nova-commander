"""
Transfer Queue Management System for WinSCP-Linux.
Provides multi-file upload/download queueing, per-item progress tracking,
pause/resume, cancellation, dynamic priority scheduling, and speed/ETA calculation.
"""

from enum import IntEnum
import os
from pathlib import Path
import stat
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple
import uuid

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from .sftp_client import SFTPClient


class TransferPriority(IntEnum):
    LOW = 1
    NORMAL = 2
    HIGH = 3


class TransferStatus:
    QUEUED = "Queued"
    RUNNING = "Transferring"
    PAUSED = "Paused"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"
    FAILED = "Failed"


class _TransferCancelledException(Exception):
    """Internal exception raised when a transfer job is cancelled."""
    pass


class TransferJob:
    """Represents a single queued or active file/directory transfer job."""

    def __init__(
        self,
        source: str,
        dest: str,
        is_dir: bool = False,
        direction: str = "upload",  # "upload" or "download"
        priority: TransferPriority = TransferPriority.NORMAL,
        order: int = 0,
    ):
        self.job_id: str = uuid.uuid4().hex[:8]
        self.source: str = source
        self.dest: str = dest
        self.name: str = Path(source).name or source
        self.is_dir: bool = is_dir
        self.direction: str = direction.lower()
        self.priority: TransferPriority = priority
        self.order: int = order
        self.status: str = TransferStatus.QUEUED

        self.total_bytes: int = 0
        self.transferred_bytes: int = 0
        self.speed_bps: float = 0.0
        self.eta_seconds: float = 0.0
        self.error_msg: Optional[str] = None

        self._pause_event = threading.Event()
        self._pause_event.set()  # set = running (not paused)
        self._cancel_event = threading.Event()

        self._start_time: float = 0.0
        self._last_calc_time: float = 0.0
        self._last_calc_bytes: int = 0

    @property
    def percent(self) -> float:
        if self.total_bytes <= 0:
            return 100.0 if self.status == TransferStatus.COMPLETED else 0.0
        pct = (self.transferred_bytes / self.total_bytes) * 100.0
        return min(100.0, max(0.0, round(pct, 1)))

    @property
    def speed_str(self) -> str:
        if self.status != TransferStatus.RUNNING or self.speed_bps <= 0:
            return "--"
        return self._format_bytes_speed(self.speed_bps)

    @property
    def eta_str(self) -> str:
        if self.status != TransferStatus.RUNNING or self.eta_seconds <= 0 or self.eta_seconds > 86400:
            return "--"
        m, s = divmod(int(self.eta_seconds), 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    @property
    def size_str(self) -> str:
        if self.total_bytes <= 0:
            return self._format_bytes(self.transferred_bytes) if self.transferred_bytes > 0 else "--"
        return f"{self._format_bytes(self.transferred_bytes)} / {self._format_bytes(self.total_bytes)}"

    @staticmethod
    def _format_bytes(num_bytes: int) -> str:
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if num_bytes < 1024.0:
                return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{num_bytes} B"
            num_bytes /= 1024.0
        return f"{num_bytes:.1f} PB"

    @staticmethod
    def _format_bytes_speed(bps: float) -> str:
        for unit in ["B/s", "KB/s", "MB/s", "GB/s"]:
            if bps < 1024.0:
                return f"{bps:.1f} {unit}"
            bps /= 1024.0
        return f"{bps:.1f} TB/s"


class TransferQueueEngine(QThread):
    """
    Background worker thread managing and executing transfers from the queue.
    Supports pause/resume, individual and bulk cancellation, dynamic re-ordering,
    and priority scheduling.
    """

    job_added = pyqtSignal(object)  # TransferJob
    job_updated = pyqtSignal(object)  # TransferJob
    job_progress = pyqtSignal(str, int, int, float, float)  # (job_id, transferred, total, speed, eta)
    job_status_changed = pyqtSignal(str, str)  # (job_id, status)
    queue_updated = pyqtSignal()
    queue_progress = pyqtSignal(int, int, float, float, float)  # (total_tx, total_all, pct, speed, eta)
    queue_finished = pyqtSignal()
    error_occurred = pyqtSignal(str, str)  # (job_id, error_message)

    def __init__(self, sftp_client: SFTPClient, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.sftp_client = sftp_client
        self._lock = threading.RLock()
        self._jobs: List[TransferJob] = []
        self._next_order: int = 1
        self._running: bool = True
        self._queue_paused = threading.Event()
        self._queue_paused.set()  # set = running (not paused)
        self._current_job: Optional[TransferJob] = None

    def stop(self):
        """Stops the queue execution thread cleanly."""
        self._running = False
        self._queue_paused.set()
        if self._current_job:
            self._current_job._cancel_event.set()
            self._current_job._pause_event.set()

    # =========================================================================
    # Queue Modification Methods
    # =========================================================================

    def add_transfer(
        self,
        source: str,
        dest: str,
        is_dir: bool = False,
        direction: str = "upload",
        priority: TransferPriority = TransferPriority.NORMAL,
    ) -> TransferJob:
        """Enqueues a single file or directory transfer."""
        with self._lock:
            job = TransferJob(
                source=source,
                dest=dest,
                is_dir=is_dir,
                direction=direction,
                priority=priority,
                order=self._next_order,
            )
            self._next_order += 1

            # Estimate total bytes if local upload
            if direction == "upload":
                job.total_bytes = self._estimate_local_size(source, is_dir)

            self._jobs.append(job)

        self.job_added.emit(job)
        self.queue_updated.emit()
        self._ensure_running()
        return job

    def add_transfers(
        self,
        transfers: List[dict],
        priority: TransferPriority = TransferPriority.NORMAL,
    ) -> List[TransferJob]:
        """Enqueues a batch of file/directory transfers."""
        created_jobs: List[TransferJob] = []
        with self._lock:
            for item in transfers:
                source = item["source"]
                dest = item["dest"]
                is_dir = item.get("is_dir", False)
                direction = item.get("direction", "upload")
                item_pri = item.get("priority", priority)

                job = TransferJob(
                    source=source,
                    dest=dest,
                    is_dir=is_dir,
                    direction=direction,
                    priority=item_pri,
                    order=self._next_order,
                )
                self._next_order += 1

                if direction == "upload":
                    job.total_bytes = self._estimate_local_size(source, is_dir)

                self._jobs.append(job)
                created_jobs.append(job)

        for job in created_jobs:
            self.job_added.emit(job)

        self.queue_updated.emit()
        self._ensure_running()
        return created_jobs

    def pause_job(self, job_id: str):
        """Pauses a specific job."""
        with self._lock:
            for job in self._jobs:
                if job.job_id == job_id:
                    if job.status in (TransferStatus.QUEUED, TransferStatus.RUNNING):
                        job.status = TransferStatus.PAUSED
                        job._pause_event.clear()
                        self.job_status_changed.emit(job.job_id, job.status)
                        self.job_updated.emit(job)
                        self.queue_updated.emit()
                    break

    def resume_job(self, job_id: str):
        """Resumes a paused job."""
        with self._lock:
            for job in self._jobs:
                if job.job_id == job_id:
                    if job.status == TransferStatus.PAUSED:
                        # If it was the currently transferring job, set status back to running
                        if self._current_job and self._current_job.job_id == job_id:
                            job.status = TransferStatus.RUNNING
                        else:
                            job.status = TransferStatus.QUEUED
                        job._pause_event.set()
                        self.job_status_changed.emit(job.job_id, job.status)
                        self.job_updated.emit(job)
                        self.queue_updated.emit()
                    break
        self._ensure_running()

    def cancel_job(self, job_id: str):
        """Cancels a specific job."""
        with self._lock:
            for job in self._jobs:
                if job.job_id == job_id:
                    if job.status in (TransferStatus.QUEUED, TransferStatus.PAUSED):
                        job.status = TransferStatus.CANCELLED
                        job._cancel_event.set()
                        job._pause_event.set()
                        self.job_status_changed.emit(job.job_id, job.status)
                        self.job_updated.emit(job)
                        self.queue_updated.emit()
                    elif job.status == TransferStatus.RUNNING:
                        job.status = TransferStatus.CANCELLED
                        job._cancel_event.set()
                        job._pause_event.set()
                        self.job_status_changed.emit(job.job_id, job.status)
                        self.job_updated.emit(job)
                        self.queue_updated.emit()
                    break

    def set_job_priority(self, job_id: str, priority: TransferPriority):
        """Changes the priority of a queued job."""
        with self._lock:
            for job in self._jobs:
                if job.job_id == job_id:
                    job.priority = priority
                    self.job_updated.emit(job)
                    self.queue_updated.emit()
                    break

    def move_job_up(self, job_id: str):
        """Moves a queued job earlier in execution order."""
        with self._lock:
            queued = [j for j in self._jobs if j.status in (TransferStatus.QUEUED, TransferStatus.PAUSED)]
            idx = next((i for i, j in enumerate(queued) if j.job_id == job_id), None)
            if idx is not None and idx > 0:
                prev_job = queued[idx - 1]
                cur_job = queued[idx]
                # Swap orders
                cur_job.order, prev_job.order = prev_job.order, cur_job.order
                # If priority differed, match or promote
                if cur_job.priority < prev_job.priority:
                    cur_job.priority = prev_job.priority
                self.queue_updated.emit()

    def move_job_down(self, job_id: str):
        """Moves a queued job later in execution order."""
        with self._lock:
            queued = [j for j in self._jobs if j.status in (TransferStatus.QUEUED, TransferStatus.PAUSED)]
            idx = next((i for i, j in enumerate(queued) if j.job_id == job_id), None)
            if idx is not None and idx < len(queued) - 1:
                next_job = queued[idx + 1]
                cur_job = queued[idx]
                # Swap orders
                cur_job.order, next_job.order = next_job.order, cur_job.order
                self.queue_updated.emit()

    def pause_all(self):
        """Pauses the entire queue."""
        self._queue_paused.clear()
        with self._lock:
            for job in self._jobs:
                if job.status == TransferStatus.RUNNING:
                    job.status = TransferStatus.PAUSED
                    job._pause_event.clear()
                    self.job_status_changed.emit(job.job_id, job.status)
                    self.job_updated.emit(job)
        self.queue_updated.emit()

    def resume_all(self):
        """Resumes the entire queue."""
        self._queue_paused.set()
        with self._lock:
            for job in self._jobs:
                if job.status == TransferStatus.PAUSED:
                    job.status = TransferStatus.QUEUED
                    job._pause_event.set()
                    self.job_status_changed.emit(job.job_id, job.status)
                    self.job_updated.emit(job)
        self.queue_updated.emit()
        self._ensure_running()

    def cancel_all(self):
        """Cancels all queued and active jobs."""
        with self._lock:
            for job in self._jobs:
                if job.status in (TransferStatus.QUEUED, TransferStatus.PAUSED, TransferStatus.RUNNING):
                    job.status = TransferStatus.CANCELLED
                    job._cancel_event.set()
                    job._pause_event.set()
                    self.job_status_changed.emit(job.job_id, job.status)
                    self.job_updated.emit(job)
        self.queue_updated.emit()

    def clear_completed(self):
        """Removes completed, cancelled, and failed jobs from list."""
        with self._lock:
            self._jobs = [
                j for j in self._jobs
                if j.status in (TransferStatus.QUEUED, TransferStatus.RUNNING, TransferStatus.PAUSED)
            ]
        self.queue_updated.emit()

    def get_jobs(self) -> List[TransferJob]:
        with self._lock:
            return list(self._jobs)

    def get_job(self, job_id: str) -> Optional[TransferJob]:
        with self._lock:
            for j in self._jobs:
                if j.job_id == job_id:
                    return j
            return None

    def active_job_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs if j.status == TransferStatus.RUNNING)

    def queued_job_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs if j.status in (TransferStatus.QUEUED, TransferStatus.PAUSED))

    # =========================================================================
    # Execution Loop
    # =========================================================================

    def _ensure_running(self):
        from PyQt6.QtCore import QCoreApplication
        if QCoreApplication.instance() is None:
            return
        if not self.isRunning() and self._running:
            self.start()

    def run(self):
        """Main thread loop picking and executing transfer jobs."""
        while self._running:
            # Wait if entire queue is paused
            self._queue_paused.wait(timeout=0.2)
            if not self._running:
                break

            # Find next eligible job: highest priority, then earliest order
            next_job: Optional[TransferJob] = None
            with self._lock:
                queued = [j for j in self._jobs if j.status == TransferStatus.QUEUED]
                if queued:
                    # Sort by (-priority, order)
                    queued.sort(key=lambda j: (-int(j.priority), j.order))
                    next_job = queued[0]
                    next_job.status = TransferStatus.RUNNING
                    self._current_job = next_job

            if not next_job:
                # No more queued items
                self._current_job = None
                self.queue_finished.emit()
                for _ in range(10):
                    if not self._running or self.queued_job_count() > 0:
                        break
                    time.sleep(0.05)
                continue

            self.job_status_changed.emit(next_job.job_id, next_job.status)
            self.job_updated.emit(next_job)
            self.queue_updated.emit()

            # Execute the transfer
            try:
                self._execute_job(next_job)
                if next_job.status == TransferStatus.RUNNING:
                    next_job.status = TransferStatus.COMPLETED
                    next_job.transferred_bytes = next_job.total_bytes
                    self.job_status_changed.emit(next_job.job_id, next_job.status)
                    self.job_updated.emit(next_job)
            except _TransferCancelledException:
                next_job.status = TransferStatus.CANCELLED
                self.job_status_changed.emit(next_job.job_id, next_job.status)
                self.job_updated.emit(next_job)
            except Exception as exc:
                if next_job.status != TransferStatus.CANCELLED:
                    next_job.status = TransferStatus.FAILED
                    next_job.error_msg = str(exc)
                    self.job_status_changed.emit(next_job.job_id, next_job.status)
                    self.job_updated.emit(next_job)
                    self.error_occurred.emit(next_job.job_id, str(exc))

            self._current_job = None
            self.queue_updated.emit()

    def _execute_job(self, job: TransferJob):
        """Performs actual SFTP transfer with cancellation & pause checks."""
        job._start_time = time.time()
        job._last_calc_time = job._start_time
        job._last_calc_bytes = 0

        # Resolve remote size if downloading
        if job.direction == "download" and job.total_bytes <= 0:
            job.total_bytes = self._estimate_remote_size(job.source, job.is_dir)

        def _progress_callback(transferred: int, total: int):
            if job._cancel_event.is_set():
                raise _TransferCancelledException()

            # Handle pause
            while not job._pause_event.is_set() or not self._queue_paused.is_set():
                time.sleep(0.1)
                if job._cancel_event.is_set():
                    raise _TransferCancelledException()

            now = time.time()
            dt = now - job._last_calc_time
            is_done = (total > 0 and transferred >= total)
            if dt >= 0.15 or is_done:
                delta_bytes = transferred - job._last_calc_bytes
                if dt > 0:
                    job.speed_bps = max(0.0, delta_bytes / dt)
                    remaining = (total - transferred) if total > transferred else 0
                    job.eta_seconds = (remaining / job.speed_bps) if job.speed_bps > 0 else 0.0

                job._last_calc_time = now
                job._last_calc_bytes = transferred

                job.transferred_bytes = transferred
                if total > 0:
                    job.total_bytes = total

                self.job_progress.emit(
                    job.job_id,
                    job.transferred_bytes,
                    job.total_bytes,
                    job.speed_bps,
                    job.eta_seconds,
                )
                self._emit_queue_overall_progress()

        if job.direction == "upload":
            if job.is_dir or os.path.isdir(job.source):
                self._upload_dir_with_hooks(job, _progress_callback)
            else:
                self.sftp_client.upload_file(job.source, job.dest, callback=_progress_callback)
        else:  # download
            if job.is_dir:
                self._download_dir_with_hooks(job, _progress_callback)
            else:
                self.sftp_client.download_file(job.source, job.dest, callback=_progress_callback)

        if job.total_bytes > 0:
            job.transferred_bytes = job.total_bytes

    def _upload_dir_with_hooks(self, job: TransferJob, callback: Callable[[int, int], None]):
        """Uploads directory files while maintaining aggregate progress."""
        local_path = Path(job.source).resolve()
        total_size = job.total_bytes or self._estimate_local_size(job.source, True)
        job.total_bytes = total_size
        accumulated_bytes = 0

        # Ensure destination root directory exists
        if hasattr(self.sftp_client, "ensure_remote_dir"):
            self.sftp_client.ensure_remote_dir(job.dest)
        else:
            try:
                self.sftp_client.mkdir(job.dest)
            except Exception:
                pass

        for root, dirs, files in os.walk(str(local_path)):
            if job._cancel_event.is_set():
                raise _TransferCancelledException()

            rel_path = os.path.relpath(root, str(local_path))
            curr_remote_dir = job.dest if rel_path == "." else f"{job.dest.rstrip('/')}/{rel_path.replace(os.sep, '/')}"

            if hasattr(self.sftp_client, "ensure_remote_dir"):
                self.sftp_client.ensure_remote_dir(curr_remote_dir)

            for d in dirs:
                target_sub = f"{curr_remote_dir.rstrip('/')}/{d}"
                if hasattr(self.sftp_client, "ensure_remote_dir"):
                    self.sftp_client.ensure_remote_dir(target_sub)
                else:
                    try:
                        self.sftp_client.mkdir(target_sub)
                    except Exception:
                        pass

            for f in files:
                if job._cancel_event.is_set():
                    raise _TransferCancelledException()

                src_file = os.path.join(root, f)
                dst_file = f"{curr_remote_dir.rstrip('/')}/{f}"
                try:
                    f_size = os.path.getsize(src_file)
                except Exception:
                    continue

                def _file_cb(cur: int, tot: int):
                    callback(accumulated_bytes + cur, total_size)

                try:
                    self.sftp_client.upload_file(src_file, dst_file, callback=_file_cb)
                except Exception:
                    time.sleep(0.05)
                    self.sftp_client.upload_file(src_file, dst_file, callback=_file_cb)

                accumulated_bytes += f_size
                callback(accumulated_bytes, total_size)

    def _download_dir_with_hooks(self, job: TransferJob, callback: Callable[[int, int], None]):
        """Downloads directory files while maintaining aggregate progress."""
        os.makedirs(job.dest, exist_ok=True)
        total_size = job.total_bytes
        accumulated_bytes = 0

        # Recursively download items
        def _recurse_dl(rem_dir: str, loc_dir: str):
            nonlocal accumulated_bytes
            os.makedirs(loc_dir, exist_ok=True)
            for item in self.sftp_client.listdir(rem_dir):
                if job._cancel_event.is_set():
                    raise _TransferCancelledException()
                if item.name in (".", ".."):
                    continue
                sub_rem = f"{rem_dir.rstrip('/')}/{item.name}"
                sub_loc = os.path.join(loc_dir, item.name)

                if item.is_dir:
                    _recurse_dl(sub_rem, sub_loc)
                else:
                    def _file_cb(cur: int, tot: int):
                        callback(accumulated_bytes + cur, total_size or (accumulated_bytes + tot))

                    self.sftp_client.download_file(sub_rem, sub_loc, callback=_file_cb)
                    accumulated_bytes += item.st_size
                    callback(accumulated_bytes, total_size or accumulated_bytes)

        _recurse_dl(job.source, job.dest)

    def _emit_queue_overall_progress(self):
        """Calculates and emits overall progress across all jobs in queue."""
        with self._lock:
            total_all = sum(j.total_bytes for j in self._jobs)
            total_tx = sum(j.transferred_bytes for j in self._jobs)
            speed = self._current_job.speed_bps if self._current_job else 0.0
            pct = (total_tx / total_all * 100.0) if total_all > 0 else 0.0
            eta = ((total_all - total_tx) / speed) if speed > 0 and total_all > total_tx else 0.0

        self.queue_progress.emit(total_tx, total_all, min(100.0, max(0.0, pct)), speed, eta)

    # =========================================================================
    # Helpers
    # =========================================================================

    def _estimate_local_size(self, path: str, is_dir: bool) -> int:
        try:
            if not is_dir and os.path.isfile(path):
                return os.path.getsize(path)
            total = 0
            for root, _, files in os.walk(path):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except Exception:
                        pass
            return total
        except Exception:
            return 0

    def _estimate_remote_size(self, path: str, is_dir: bool) -> int:
        if not self.sftp_client.is_connected:
            return 0
        try:
            if not is_dir:
                return self.sftp_client.stat(path).st_size

            total = 0
            def _walk(p: str):
                nonlocal total
                for item in self.sftp_client.listdir(p):
                    if item.name in (".", ".."):
                        continue
                    if item.is_dir:
                        _walk(item.path)
                    else:
                        total += item.size

            _walk(path)
            return total
        except Exception:
            return 0
