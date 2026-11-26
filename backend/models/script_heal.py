"""
ScriptHeal model (spec 0011, AC-18 to AC-21).

One proposed or applied repair of a test case's stored Playwright script,
made by the self healing agent from the log analyser's RCA. It lives in its
own store (`backend/repository/script_heal_store.py`), never inside the test
case record, so a test case save can never drop it.

`old_script` and `new_script` hold object repository keys only, the same as
`TestCase.playwright_script`. A rendered script is never stored here.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

SCRIPT_HEAL_STATUSES = ("applied", "suggested", "accepted", "rejected", "reverted")
SCRIPT_HEAL_DECIDED_KEEP = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ScriptHeal(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    test_case_id: UUID
    project_id: UUID
    execution_id: UUID | None = None
    status: str
    old_script: str
    new_script: str
    rca_category: str
    root_cause_summary: str = ""
    failing_step: str | None = None
    reason: str = ""
    created_at: datetime = Field(default_factory=_now)
    decided_by: UUID | None = None
    decided_at: datetime | None = None


def trim_script_heals(heals: list[ScriptHeal]) -> list[ScriptHeal]:
    """Retention for one test case (AC-16): keep every `suggested` heal, the latest
    `applied` or `accepted` one (the revert source), and the last 10 decided ones."""
    ordered = sorted(heals, key=lambda heal: heal.created_at)
    keep: set[UUID] = {heal.id for heal in ordered if heal.status == "suggested"}
    live = [heal for heal in ordered if heal.status in ("applied", "accepted")]
    if live:
        keep.add(live[-1].id)
    decided = [heal for heal in ordered if heal.id not in keep]
    keep.update(heal.id for heal in decided[-SCRIPT_HEAL_DECIDED_KEEP:])
    return [heal for heal in ordered if heal.id in keep]
