from typing import Any, Optional
from uuid import UUID
from pydantic import BaseModel, Field

class QueueItem(BaseModel):
    task_id: str
    requirement_id: Optional[UUID] = None
    scenario_id: Optional[UUID] = None
    testcase_id: UUID
    required_state: str
    playwright_script: Optional[str] = None
    dataset_row: Optional[int] = None
    priority: int = 1
    estimated_duration: float = 0.0
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    status: str = "queued"  # queued, running, passed, failed, blocked
    duration: float = 0.0
    error_message: Optional[str] = None



class BatchContext(BaseModel):
    batch_id: str
    project_id: UUID
    release_id: Optional[UUID] = None
    cycle_id: Optional[UUID] = None
    execution_strategy: str = "SequentialStrategy"
    current_state: str = "LOGIN"
    queue: list[QueueItem] = Field(default_factory=list)
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    login_count: int = 0
    session_reuse_count: int = 0
    current_dataset_row: Optional[int] = None
    current_retry_count: int = 0
    navigation_decision: Optional[str] = None
    artifacts: dict[str, Any] = Field(default_factory=lambda: {
        "screenshots": [],
        "video": None,
        "trace": None,
        "logs": [],
        "console": []
    })

