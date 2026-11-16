"""Background jobs for Playwright script generation.

Live-verified generation runs every step in a real browser with several LLM
calls per step, and rate-limited models add cooldowns, so one script can take
longer than any fixed HTTP timeout. The API starts a job here and returns at
once; the page polls the job until it completes or fails.

Jobs live in memory in this process. A backend restart loses running jobs, and
polling then reports the job as not found.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from uuid import UUID, uuid4

from backend.models.test_case import TestCase

logger = logging.getLogger(__name__)

RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"

# Finished jobs stay readable this long, so a slow poll still sees the result.
FINISHED_JOB_TTL_SECONDS = 3600

Generate = Callable[[UUID, UUID, str], TestCase]


@dataclass
class ScriptGenerationJob:
    id: UUID
    project_id: UUID
    test_case_id: UUID
    framework: str
    status: str = RUNNING
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    test_case: Optional[TestCase] = None
    error: str = ""
    # The HTTP status the synchronous route would have returned for this error.
    error_status: Optional[int] = None


def _error_details(exc: Exception) -> tuple[str, int]:
    """Same messages and status codes as the synchronous generate-script route."""
    if isinstance(exc, ValueError):
        message = str(exc)
        return message, 404 if "not found" in message.lower() else 400
    return f"Script generation failed: {exc}", 500


class ScriptGenerationJobs:
    """Starts script generation in a background thread and tracks its result."""

    def __init__(self, generate: Generate, finished_ttl_seconds: float = FINISHED_JOB_TTL_SECONDS):
        self._generate = generate
        self._finished_ttl_seconds = finished_ttl_seconds
        self._jobs: dict[UUID, ScriptGenerationJob] = {}
        self._lock = threading.Lock()

    def start(self, project_id: UUID, test_case_id: UUID, framework: str) -> ScriptGenerationJob:
        """Start a job, or return the one already running for this test case.

        Reusing the running job keeps a double click or a page reload from
        recording the same test case twice at once.
        """
        with self._lock:
            self._prune()
            for job in self._jobs.values():
                if (
                    job.status == RUNNING
                    and job.project_id == project_id
                    and job.test_case_id == test_case_id
                ):
                    return job
            job = ScriptGenerationJob(
                id=uuid4(),
                project_id=project_id,
                test_case_id=test_case_id,
                framework=framework,
            )
            self._jobs[job.id] = job
        threading.Thread(
            target=self._run, args=(job,), name=f"script-generation-{job.id}", daemon=True
        ).start()
        return job

    def get(self, project_id: UUID, job_id: UUID) -> Optional[ScriptGenerationJob]:
        """The job, or None when it is unknown or belongs to another project."""
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None or job.project_id != project_id:
            return None
        return job

    def _run(self, job: ScriptGenerationJob) -> None:
        logger.info(
            "Script generation job started: job_id=%s test_case_id=%s", job.id, job.test_case_id
        )
        try:
            test_case = self._generate(job.project_id, job.test_case_id, job.framework)
        except Exception as exc:
            error, error_status = _error_details(exc)
            if error_status == 500:
                logger.exception("Script generation job failed: job_id=%s", job.id)
            else:
                logger.info("Script generation job rejected: job_id=%s error=%s", job.id, error)
            with self._lock:
                job.error, job.error_status = error, error_status
                job.status, job.finished_at = FAILED, time.time()
            return
        with self._lock:
            job.test_case = test_case
            job.status, job.finished_at = COMPLETED, time.time()
        logger.info(
            "Script generation job completed: job_id=%s seconds=%.0f",
            job.id,
            job.finished_at - job.started_at,
        )

    def _prune(self) -> None:
        """Drop finished jobs past their TTL. Call with the lock held."""
        cutoff = time.time() - self._finished_ttl_seconds
        for job_id in [
            job_id
            for job_id, job in self._jobs.items()
            if job.finished_at is not None and job.finished_at < cutoff
        ]:
            del self._jobs[job_id]
