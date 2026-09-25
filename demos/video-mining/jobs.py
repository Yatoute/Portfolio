"""
In-memory job queue with a single background worker — processing depth is
capped at 1 so that at most one WhisperX + OpenCLIP run is ever in memory
at once, which is what keeps this demo viable on a small CPU-only machine.
Extra uploads wait in the queue rather than spawning concurrent workers.
"""

from __future__ import annotations

import logging
import queue
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pipeline.orchestrator import process_video

logger = logging.getLogger(__name__)

MAX_QUEUE_DEPTH = 2  # 1 processing + 1 waiting; further uploads are rejected (503)
JOB_RETENTION_SECONDS = 30 * 60


@dataclass
class Job:
    id: str
    video_path: str
    work_dir: str
    status: str = "queued"  # queued | processing | done | error
    stage: str = ""
    progress: float = 0.0
    created_at: float = field(default_factory=time.time)
    error: Optional[str] = None
    result: Optional[dict] = None


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._run_worker, daemon=True, name="video-mining-worker")
        self._worker.start()
        self._reaper = threading.Thread(target=self._run_reaper, daemon=True, name="video-mining-reaper")
        self._reaper.start()

    def queue_depth(self) -> int:
        return self._queue.qsize()

    def submit(self, video_path: str, work_dir: str) -> Job:
        if self._queue.qsize() >= MAX_QUEUE_DEPTH:
            raise RuntimeError("Server busy: too many videos already queued, try again shortly.")

        job = Job(id=uuid.uuid4().hex[:12], video_path=video_path, work_dir=work_dir)
        with self._lock:
            self._jobs[job.id] = job
        self._queue.put(job.id)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def _run_worker(self) -> None:
        while True:
            job_id = self._queue.get()
            job = self.get(job_id)
            if job is None:
                continue
            self._process(job)

    def _process(self, job: Job) -> None:
        job.status = "processing"
        keyframes_dir = str(Path(job.work_dir) / "keyframes")

        def on_progress(stage: str, fraction: float) -> None:
            job.stage = stage
            job.progress = fraction

        try:
            result = process_video(job.video_path, keyframes_dir, on_progress=on_progress)
            job.result = {
                "metadata": result.metadata,
                "language": result.language,
                "scenes": result.scenes,
            }
            job.status = "done"
            job.progress = 1.0
        except Exception as exc:  # noqa: BLE001 — surfaced to the client, not swallowed
            logger.exception("Job %s failed", job.id)
            job.status = "error"
            job.error = str(exc)

    def _run_reaper(self) -> None:
        """Deletes job working directories older than JOB_RETENTION_SECONDS."""
        while True:
            time.sleep(5 * 60)
            now = time.time()
            with self._lock:
                stale = [j for j in self._jobs.values() if now - j.created_at > JOB_RETENTION_SECONDS]
            for job in stale:
                shutil.rmtree(job.work_dir, ignore_errors=True)
                with self._lock:
                    self._jobs.pop(job.id, None)
                logger.info("Reaped stale job %s", job.id)


manager = JobManager()
