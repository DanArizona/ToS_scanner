# scan_job_queue.py

from __future__ import annotations

from dataclasses import dataclass, field
from queue import Empty, PriorityQueue
from threading import Lock
from typing import Optional

from scan_jobs import JobKind, JobRequest


# Lower number means higher priority.
JOB_PRIORITIES: dict[JobKind, int] = {
    JobKind.STOP: 0,
    JobKind.PAUSE: 1,

    JobKind.START: 2,
    JobKind.RESUME: 3,

    JobKind.REPLACE_WL_SYMBOLS: 10,
    JobKind.ADD_WL_SYMBOLS: 11,

    JobKind.EXPORT_WL: 20,
    JobKind.EXPORT_TS: 30,
    JobKind.EXPORT_TM: 40,
}


@dataclass(order=True)
class PrioritizedJob:
    priority: int
    sequence: int
    request: JobRequest = field(compare=False)


class ScanJobQueue:
    """
    Thread-safe priority queue for scan jobs.

    Multiple producers may submit jobs:
        - scheduler
        - GUI
        - file command ingress
        - future HTTP/socket ingress

    A single dispatcher should consume jobs and execute ToS actions serially.
    """

    def __init__(self) -> None:
        self._queue: PriorityQueue[PrioritizedJob] = PriorityQueue()
        self._lock = Lock()
        self._sequence = 0

    def submit(self, request: JobRequest) -> None:
        priority = JOB_PRIORITIES.get(request.kind, 100)

        with self._lock:
            sequence = self._sequence
            self._sequence += 1

        self._queue.put(
            PrioritizedJob(
                priority=priority,
                sequence=sequence,
                request=request,
            )
        )

    def get_next(self, timeout: float | None = None) -> Optional[JobRequest]:
        """
        Return the next JobRequest, or None if no job arrives before timeout.
        """

        try:
            item = self._queue.get(timeout=timeout)
        except Empty:
            return None

        return item.request

    def task_done(self) -> None:
        self._queue.task_done()

    def empty(self) -> bool:
        return self._queue.empty()

    def qsize(self) -> int:
        return self._queue.qsize()
    
