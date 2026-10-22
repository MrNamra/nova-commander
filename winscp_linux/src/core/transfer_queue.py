"""
Transfer Queue Management System for WinSCP-Linux.
Provides multi-file upload/download queueing, per-item progress tracking,
pause/resume, cancellation, dynamic priority scheduling, and speed/ETA calculation.
"""

import concurrent.futures
from enum import IntEnum
import os
from pathlib import Path
import queue
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

        self.current_file: str = ""
        self.current_file_idx: int = 0
        self.total_files: int = 0

        self._pause_event = threading.Event()
        self._pause_event.set()  # set = running (not paused)
        self._cancel_event = threading.Event()

        self._start_time: float = 0.0
        self._last_calc_time: float = 0.0
        self._last_calc_bytes: int = 0

    @property
    def display_name(self) -> str:
        """User-friendly name showing active relative file path when transferring directory."""
        if not self.is_dir:
            return self.name

        if self.status == TransferStatus.RUNNING:
            if self.current_file:
                if self.total_files > 0 and self.current_file_idx > 0:
                    return f"{self.name} ➔ {self.current_file} [{self.current_file_idx}/{self.total_files}]"
                return f"{self.name} ➔ {self.current_file}"
            return self.name

        if self.status == TransferStatus.COMPLETED:
            if self.total_files > 0:
                return f"{self.name} ✔ ({self.total_files} files)"
            return self.name

        if self.status == TransferStatus.CANCELLED:
            if self.current_file:
                return f"{self.name} ⏸ (Stopped at: {self.current_file})"
            return self.name

        if self.status == TransferStatus.FAILED:
            if self.current_file:
                return f"{self.name} ❌ (Failed at: {self.current_file})"
            return self.name

        if self.total_files > 0:
            return f"{self.name} ({self.total_files} files)"

        return self.name

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
    max_concurrent_changed = pyqtSignal(int)

    def __init__(self, sftp_client: SFTPClient, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.sftp_client = sftp_client
        self._lock = threading.RLock()
        self._jobs: List[TransferJob] = []
        self._next_order: int = 1
        self._running: bool = True
        self._queue_paused = threading.Event()
        self._queue_paused.set()  # set = running (not paused)
        from PyQt6.QtCore import QSettings
        saved_concurrency = int(QSettings("NovaCommander", "WinSCP-Linux").value("transfers/max_concurrent", 2))
        self._max_concurrent: int = max(1, min(10, saved_concurrency))
        self._active_jobs: Dict[str, TransferJob] = {}
        self._current_job: Optional[TransferJob] = None

    @property
    def max_concurrent(self) -> int:
        with self._lock:
            return self._max_concurrent

    def set_max_concurrent(self, val: int):
        val = max(1, min(10, int(val)))
        with self._lock:
            if self._max_concurrent == val:
                return
            self._max_concurrent = val
        self.max_concurrent_changed.emit(val)

    def stop(self):
        """Stops the queue execution thread cleanly."""
        self._running = False
        self._queue_paused.set()
        with self._lock:
            for job in list(self._active_jobs.values()):
                job._cancel_event.set()
                job._pause_event.set()
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
                if job.status in (TransferStatus.RUNNING, TransferStatus.QUEUED):
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
        """Main thread loop managing and executing transfer jobs with concurrency."""
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=10, thread_name_prefix="TransferWorker")
        active_futures: Dict[concurrent.futures.Future, TransferJob] = {}

        try:
            while self._running:
                # Wait if entire queue is paused
                self._queue_paused.wait(timeout=0.1)
                if not self._running:
                    break

                # Clean up completed futures
                done_futures = [f for f in list(active_futures.keys()) if f.done()]
                for f in done_futures:
                    job = active_futures.pop(f, None)
                    if job:
                        with self._lock:
                            self._active_jobs.pop(job.job_id, None)
                            if self._current_job and self._current_job.job_id == job.job_id:
                                self._current_job = next(iter(self._active_jobs.values()), None)
                    try:
                        f.result()
                    except Exception:
                        pass
                    self.queue_updated.emit()
                    self._emit_queue_overall_progress()

                # Start new jobs up to max_concurrent
                with self._lock:
                    max_c = self._max_concurrent
                    active_count = len(self._active_jobs)
                    available_slots = max(0, max_c - active_count)

                    if available_slots > 0 and self._queue_paused.is_set():
                        queued = [j for j in self._jobs if j.status == TransferStatus.QUEUED]
                        if queued:
                            queued.sort(key=lambda j: (-int(j.priority), j.order))
                            jobs_to_start = queued[:available_slots]
                            for j in jobs_to_start:
                                j.status = TransferStatus.RUNNING
                                self._active_jobs[j.job_id] = j
                                self._current_job = j
                                self.job_status_changed.emit(j.job_id, j.status)
                                self.job_updated.emit(j)
                                fut = executor.submit(self._worker_execute, j)
                                active_futures[fut] = j
                            self.queue_updated.emit()

                # Check if queue has finished everything
                with self._lock:
                    has_active = len(self._active_jobs) > 0
                    has_queued = any(j.status == TransferStatus.QUEUED for j in self._jobs)
                    total_jobs = len(self._jobs)

                if not has_active and not has_queued:
                    if total_jobs > 0:
                        self.queue_finished.emit()
                    time.sleep(0.1)
                else:
                    time.sleep(0.05)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _worker_execute(self, job: TransferJob):
        worker_client = None
        try:
            if hasattr(self.sftp_client, "get_worker_client"):
                worker_client = self.sftp_client.get_worker_client()
            else:
                worker_client = self.sftp_client

            self._execute_job(job, client=worker_client)

            with self._lock:
                if job._cancel_event.is_set():
                    job.status = TransferStatus.CANCELLED
                elif not job._pause_event.is_set() or not self._queue_paused.is_set():
                    job.status = TransferStatus.PAUSED
                elif job.status == TransferStatus.RUNNING:
                    job.status = TransferStatus.COMPLETED
                    job.transferred_bytes = job.total_bytes
                self.job_status_changed.emit(job.job_id, job.status)
                self.job_updated.emit(job)
        except _TransferCancelledException:
            job.status = TransferStatus.CANCELLED
            self.job_status_changed.emit(job.job_id, job.status)
            self.job_updated.emit(job)
        except Exception as exc:
            if job.status != TransferStatus.CANCELLED:
                job.status = TransferStatus.FAILED
                job.error_msg = str(exc)
                self.job_status_changed.emit(job.job_id, job.status)
                self.job_updated.emit(job)
                self.error_occurred.emit(job.job_id, str(exc))
        finally:
            if worker_client and worker_client is not self.sftp_client:
                try:
                    if hasattr(worker_client, "close_worker"):
                        worker_client.close_worker()
                    else:
                        worker_client.disconnect()
                except Exception:
                    pass

    def _execute_job(self, job: TransferJob, client: Optional[SFTPClient] = None):
        """Performs actual SFTP transfer with cancellation & pause checks."""
        active_client = client or self.sftp_client
        job._start_time = time.time()
        job._last_calc_time = job._start_time
        job._last_calc_bytes = 0

        # Check cancellation or individual pause before initiating transfer
        if job._cancel_event.is_set():
            raise _TransferCancelledException()
        while not job._pause_event.is_set():
            time.sleep(0.05)
            if job._cancel_event.is_set():
                raise _TransferCancelledException()

        # Resolve remote size if downloading
        if job.direction == "download" and job.total_bytes <= 0:
            job.total_bytes = self._estimate_remote_size(job.source, job.is_dir)

        def _progress_callback(transferred: int, total: int):
            if job._cancel_event.is_set():
                raise _TransferCancelledException()

            # Handle pause
            while not job._pause_event.is_set():
                time.sleep(0.05)
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
                self._upload_dir_with_hooks(job, _progress_callback, client=active_client)
            else:
                active_client.upload_file(job.source, job.dest, callback=_progress_callback)
        else:  # download
            if job.is_dir:
                self._download_dir_with_hooks(job, _progress_callback, client=active_client)
            else:
                active_client.download_file(job.source, job.dest, callback=_progress_callback)

        if job.total_bytes > 0:
            job.transferred_bytes = job.total_bytes

    def _upload_dir_with_hooks(
        self,
        job: TransferJob,
        callback: Callable[[int, int], None],
        client: Optional[SFTPClient] = None,
    ):
        """Uploads directory files while maintaining aggregate progress and displaying active file path."""
        active_client = client or self.sftp_client
        local_path = Path(job.source).resolve()

        # Pre-count total files and collect file entries
        file_entries: List[Tuple[str, str, str, int]] = []
        total_size = 0

        # Ensure destination root directory exists
        if hasattr(active_client, "ensure_remote_dir"):
            active_client.ensure_remote_dir(job.dest)
        else:
            try:
                active_client.mkdir(job.dest)
            except Exception:
                pass

        for root, dirs, files in os.walk(str(local_path)):
            if job._cancel_event.is_set():
                raise _TransferCancelledException()

            rel_path = os.path.relpath(root, str(local_path))
            curr_remote_dir = job.dest if rel_path == "." else f"{job.dest.rstrip('/')}/{rel_path.replace(os.sep, '/')}"

            if hasattr(active_client, "ensure_remote_dir"):
                active_client.ensure_remote_dir(curr_remote_dir)

            for d in dirs:
                target_sub = f"{curr_remote_dir.rstrip('/')}/{d}"
                if hasattr(active_client, "ensure_remote_dir"):
                    active_client.ensure_remote_dir(target_sub)
                else:
                    try:
                        active_client.mkdir(target_sub)
                    except Exception:
                        pass

            for f in files:
                src_file = os.path.join(root, f)
                dst_file = f"{curr_remote_dir.rstrip('/')}/{f}"
                rel_file = f if rel_path == "." else f"{rel_path.replace(os.sep, '/')}/{f}"
                try:
                    f_size = os.path.getsize(src_file)
                except Exception:
                    f_size = 0
                file_entries.append((src_file, dst_file, rel_file, f_size))
                total_size += f_size

        job.total_files = len(file_entries)
        if total_size > 0 or job.total_bytes <= 0:
            job.total_bytes = total_size

        if not file_entries:
            return

        max_c = self.max_concurrent
        if max_c <= 1 or len(file_entries) <= 1:
            # Sequential upload
            accumulated_bytes = 0
            for current_idx, (src_file, dst_file, rel_file, f_size) in enumerate(file_entries, 1):
                if job._cancel_event.is_set():
                    raise _TransferCancelledException()

                job.current_file_idx = current_idx
                job.current_file = rel_file
                self.job_updated.emit(job)

                def _file_cb(cur: int, tot: int):
                    callback(accumulated_bytes + cur, total_size)

                try:
                    active_client.upload_file(src_file, dst_file, callback=_file_cb)
                except Exception:
                    time.sleep(0.05)
                    active_client.upload_file(src_file, dst_file, callback=_file_cb)

                accumulated_bytes += f_size
                callback(accumulated_bytes, total_size)
        else:
            # Concurrent upload using worker clients up to max_concurrent
            num_workers = min(max_c, len(file_entries))
            file_queue = queue.Queue()
            for item in file_entries:
                file_queue.put(item)

            accum_lock = threading.Lock()
            accumulated_bytes = 0
            completed_files = 0
            worker_error = []

            worker_clients: List[SFTPClient] = [active_client]
            for _ in range(num_workers - 1):
                if hasattr(active_client, "get_worker_client"):
                    w = active_client.get_worker_client()
                else:
                    w = active_client
                worker_clients.append(w)

            def _thread_worker(w_client: SFTPClient):
                nonlocal accumulated_bytes, completed_files
                while not file_queue.empty() and not job._cancel_event.is_set() and not worker_error:
                    try:
                        src_file, dst_file, rel_file, f_size = file_queue.get_nowait()
                    except queue.Empty:
                        break

                    with accum_lock:
                        completed_files += 1
                        job.current_file_idx = completed_files
                        job.current_file = rel_file
                        self.job_updated.emit(job)

                    file_transferred = 0
                    def _file_cb(cur: int, tot: int):
                        nonlocal file_transferred
                        delta = cur - file_transferred
                        file_transferred = cur
                        with accum_lock:
                            accumulated_bytes += delta
                            callback(accumulated_bytes, total_size)

                    try:
                        w_client.upload_file(src_file, dst_file, callback=_file_cb)
                    except Exception as err:
                        if not job._cancel_event.is_set():
                            time.sleep(0.05)
                            try:
                                w_client.upload_file(src_file, dst_file, callback=_file_cb)
                            except Exception as retry_err:
                                worker_error.append(retry_err)
                                file_queue.task_done()
                                break

                    with accum_lock:
                        rem = f_size - file_transferred
                        if rem > 0:
                            accumulated_bytes += rem
                        callback(accumulated_bytes, total_size)

                    file_queue.task_done()

            threads: List[threading.Thread] = []
            for w in worker_clients:
                t = threading.Thread(target=_thread_worker, args=(w,), daemon=True)
                t.start()
                threads.append(t)

            for t in threads:
                t.join()

            for w in worker_clients:
                if w is not active_client and hasattr(w, "close_worker"):
                    try:
                        w.close_worker()
                    except Exception:
                        pass

            if job._cancel_event.is_set():
                raise _TransferCancelledException()
            if worker_error:
                raise worker_error[0]

    def _download_dir_with_hooks(
        self,
        job: TransferJob,
        callback: Callable[[int, int], None],
        client: Optional[SFTPClient] = None,
    ):
        """Downloads directory files while maintaining aggregate progress and displaying active file path."""
        active_client = client or self.sftp_client
        os.makedirs(job.dest, exist_ok=True)
        total_size = job.total_bytes

        file_entries: List[Tuple[str, str, str, int]] = []

        def _scan_remote(rem_dir: str, loc_dir: str, rel_prefix: str = ""):
            os.makedirs(loc_dir, exist_ok=True)
            for item in active_client.listdir(rem_dir):
                if job._cancel_event.is_set():
                    raise _TransferCancelledException()
                if item.name in (".", ".."):
                    continue
                sub_rem = f"{rem_dir.rstrip('/')}/{item.name}"
                sub_loc = os.path.join(loc_dir, item.name)
                item_rel = f"{rel_prefix}/{item.name}" if rel_prefix else item.name

                if item.is_dir:
                    _scan_remote(sub_rem, sub_loc, item_rel)
                else:
                    item_sz = getattr(item, "size", getattr(item, "st_size", 0))
                    file_entries.append((sub_rem, sub_loc, item_rel, item_sz))

        _scan_remote(job.source, job.dest)
        job.total_files = len(file_entries)
        calc_total = sum(f[3] for f in file_entries)
        if calc_total > 0 or total_size <= 0:
            total_size = calc_total
            job.total_bytes = calc_total

        if not file_entries:
            return

        max_c = self.max_concurrent
        if max_c <= 1 or len(file_entries) <= 1:
            accumulated_bytes = 0
            for current_idx, (sub_rem, sub_loc, item_rel, item_sz) in enumerate(file_entries, 1):
                if job._cancel_event.is_set():
                    raise _TransferCancelledException()

                job.current_file_idx = current_idx
                job.current_file = item_rel
                self.job_updated.emit(job)

                def _file_cb(cur: int, tot: int):
                    callback(accumulated_bytes + cur, total_size or (accumulated_bytes + tot))

                active_client.download_file(sub_rem, sub_loc, callback=_file_cb)
                accumulated_bytes += item_sz
                callback(accumulated_bytes, total_size or accumulated_bytes)
        else:
            num_workers = min(max_c, len(file_entries))
            file_queue = queue.Queue()
            for item in file_entries:
                file_queue.put(item)

            accum_lock = threading.Lock()
            accumulated_bytes = 0
            completed_files = 0
            worker_error = []

            worker_clients: List[SFTPClient] = [active_client]
            for _ in range(num_workers - 1):
                if hasattr(active_client, "get_worker_client"):
                    w = active_client.get_worker_client()
                else:
                    w = active_client
                worker_clients.append(w)

            def _thread_worker(w_client: SFTPClient):
                nonlocal accumulated_bytes, completed_files
                while not file_queue.empty() and not job._cancel_event.is_set() and not worker_error:
                    try:
                        sub_rem, sub_loc, item_rel, item_sz = file_queue.get_nowait()
                    except queue.Empty:
                        break

                    with accum_lock:
                        completed_files += 1
                        job.current_file_idx = completed_files
                        job.current_file = item_rel
                        self.job_updated.emit(job)

                    file_transferred = 0
                    def _file_cb(cur: int, tot: int):
                        nonlocal file_transferred
                        delta = cur - file_transferred
                        file_transferred = cur
                        with accum_lock:
                            accumulated_bytes += delta
                            callback(accumulated_bytes, total_size or accumulated_bytes)

                    try:
                        w_client.download_file(sub_rem, sub_loc, callback=_file_cb)
                    except Exception as err:
                        if not job._cancel_event.is_set():
                            time.sleep(0.05)
                            try:
                                w_client.download_file(sub_rem, sub_loc, callback=_file_cb)
                            except Exception as retry_err:
                                worker_error.append(retry_err)
                                file_queue.task_done()
                                break

                    with accum_lock:
                        rem = item_sz - file_transferred
                        if rem > 0:
                            accumulated_bytes += rem
                        callback(accumulated_bytes, total_size or accumulated_bytes)

                    file_queue.task_done()

            threads: List[threading.Thread] = []
            for w in worker_clients:
                t = threading.Thread(target=_thread_worker, args=(w,), daemon=True)
                t.start()
                threads.append(t)

            for t in threads:
                t.join()

            for w in worker_clients:
                if w is not active_client and hasattr(w, "close_worker"):
                    try:
                        w.close_worker()
                    except Exception:
                        pass

            if job._cancel_event.is_set():
                raise _TransferCancelledException()
            if worker_error:
                raise worker_error[0]

    def _emit_queue_overall_progress(self):
        """Calculates and emits overall progress across all jobs in queue."""
        with self._lock:
            total_all = sum(j.total_bytes for j in self._jobs)
            total_tx = sum(j.transferred_bytes for j in self._jobs)
            if hasattr(self, "_active_jobs") and self._active_jobs:
                speed = sum(j.speed_bps for j in self._active_jobs.values())
            else:
                speed = self._current_job.speed_bps if self._current_job else 0.0
            pct = (total_tx / total_all * 100.0) if total_all > 0 else 0.0
            eta = ((total_all - total_tx) / speed) if speed > 0 and total_all > total_tx else 0.0

        self.queue_progress.emit(total_tx, total_all, min(100.0, max(0.0, pct)), speed, eta)

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
