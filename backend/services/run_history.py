"""
Run history for the log analyser.

Summarizes earlier runs of one test case so the analyser can tell a flaky test
(passes and fails alternate) from a stable regression (a long pass streak, then
a new failure). Everything here is deterministic; the LLM only receives the
summary as context, and `flaky_score` is stored as computed.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, Field

from backend.models.execution_result import ExecutionResult, ExecutionStatus

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_LIMIT = 10
FLAKY_SCORE_THRESHOLD = 0.5

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")


class RunHistory(BaseModel):
    """Outcome summary of the earlier runs of one test case, oldest first."""

    runs: int = 0
    passes: int = 0
    fails: int = 0
    flips: int = 0
    same_error_count: int = 0
    outcomes: list[str] = Field(default_factory=list)
    flaky_score: float = 0.0

    def describe(self) -> str:
        if self.runs == 0:
            return "No earlier runs of this test case."
        return (
            f"Last {self.runs} earlier runs (oldest first): {' '.join(self.outcomes)}. "
            f"Passed {self.passes}, did not pass {self.fails}, outcome flips {self.flips}. "
            f"Earlier failures with the same error signature: {self.same_error_count}. "
            f"Computed flaky score: {self.flaky_score:.2f} (0 = stable, 1 = highly flaky)."
        )


def error_signature(message: str | None) -> str:
    """A stable hash of the first error line with volatile parts removed.

    UUIDs, numbers (timeouts, counts, line numbers) and quoted values are
    replaced, so the same failure from two runs gets the same signature.
    """
    text = _ANSI_ESCAPE.sub("", message or "").strip()
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not first:
        return ""
    first = _UUID.sub("<id>", first)
    first = _QUOTED.sub("<value>", first)
    first = _NUMBER.sub("<n>", first).lower()
    return hashlib.sha1(first.encode("utf-8")).hexdigest()[:16]


def _passed(result: ExecutionResult) -> bool:
    return getattr(result.status, "value", result.status) == ExecutionStatus.PASSED.value


def _sort_key(result: ExecutionResult) -> datetime:
    executed = result.executed_at
    if executed is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return executed if executed.tzinfo else executed.replace(tzinfo=timezone.utc)


def compute(history: list[ExecutionResult], current: ExecutionResult | None = None) -> RunHistory:
    """Build a `RunHistory` from earlier results (any order) and the current failure."""
    ordered = sorted(
        (r for r in history if getattr(r.status, "value", r.status) != ExecutionStatus.SKIPPED.value),
        key=_sort_key,
    )
    if not ordered:
        return RunHistory()
    outcomes = ["P" if _passed(r) else "F" for r in ordered]
    sequence = outcomes + (["F"] if current is not None and not _passed(current) else [])
    flips = sum(1 for a, b in zip(sequence, sequence[1:]) if a != b)
    passes = outcomes.count("P")
    fails = len(outcomes) - passes

    signature = error_signature(current.error_message) if current is not None else ""
    same_error = 0
    if signature:
        same_error = sum(1 for r in ordered if not _passed(r) and error_signature(r.error_message) == signature)

    flaky_score = 0.0
    if len(sequence) >= 2 and passes and (fails or (current is not None and not _passed(current))):
        flip_rate = flips / (len(sequence) - 1)
        all_fail = fails + (1 if current is not None and not _passed(current) else 0)
        balance = min(passes, all_fail) / max(passes, all_fail)
        flaky_score = round(min(1.0, 0.7 * flip_rate + 0.3 * balance), 2)

    return RunHistory(
        runs=len(ordered),
        passes=passes,
        fails=fails,
        flips=flips,
        same_error_count=same_error,
        outcomes=outcomes,
        flaky_score=flaky_score,
    )


def summarize(
    repo,
    project_id: UUID,
    result: ExecutionResult,
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> RunHistory:
    """Load earlier runs of `result.test_case_id` from the repository and summarize them.

    Deleted runs and the current run are excluded. Never raises: a repository
    error gives an empty history.
    """
    try:
        rows = repo.get_execution_results(project_id) or []
        earlier = []
        for row in rows:
            if row.test_case_id != result.test_case_id or row.id == result.id:
                continue
            try:
                if repo.is_execution_deleted(project_id, row.id):
                    continue
            except Exception:
                pass
            earlier.append(row)
        earlier = sorted(earlier, key=_sort_key)[-limit:]
    except Exception:
        logger.exception("Run history lookup failed: project_id=%s test_case_id=%s", project_id, result.test_case_id)
        return RunHistory()
    return compute(earlier, result)
