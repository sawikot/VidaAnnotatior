"""Prediction requests waiting for the trainer.

Asking a model about a patch must feel immediate, so these do not go through the database like
training runs: the request waits here (in memory) until the trainer, which keeps one connection open
asking for work, takes it and sends the answer back. If the app restarts, whoever was waiting simply
asks again.
"""
from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from dataclasses import dataclass, field

# How long a job may sit unclaimed, or unanswered once claimed, before the asker is told it failed.
# The first request to a model includes loading it, which takes a while.
ANSWER_TIMEOUT_S = 180


@dataclass
class Job:
    id: int
    payload: dict  # what the trainer is sent
    done: threading.Event = field(default_factory=threading.Event)
    results: list | None = None  # one list of detections per image
    info: dict | None = None  # what the model said about itself when it loaded (its classes)
    error: str | None = None


_ids = itertools.count(1)
_waiting: deque[Job] = deque()
_claimed: dict[int, Job] = {}
_arrived = threading.Condition()


def submit(payload: dict) -> Job:
    job = Job(id=next(_ids), payload=payload)
    job.payload["id"] = job.id
    with _arrived:
        _waiting.append(job)
        _arrived.notify()
    return job


def claim(wait_s: float) -> Job | None:
    """The next job, waiting up to ``wait_s`` for one to arrive (the trainer's open question)."""
    deadline = time.monotonic() + wait_s
    with _arrived:
        while not _waiting:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            _arrived.wait(remaining)
        job = _waiting.popleft()
        _claimed[job.id] = job
        return job


def finish(job_id: int, results: list | None, error: str | None, info: dict | None = None) -> bool:
    job = _claimed.pop(job_id, None)
    if job is None:
        return False  # nobody is waiting for it any more
    job.results, job.error, job.info = results, error, info
    job.done.set()
    return True


def wait(job: Job, timeout_s: float = ANSWER_TIMEOUT_S) -> list:
    """The job's results. Raises TimeoutError, or RuntimeError with the trainer's own words."""
    if not job.done.wait(timeout_s):
        with _arrived:
            if job in _waiting:
                _waiting.remove(job)
        _claimed.pop(job.id, None)
        raise TimeoutError
    if job.error:
        raise RuntimeError(job.error)
    return job.results or []
