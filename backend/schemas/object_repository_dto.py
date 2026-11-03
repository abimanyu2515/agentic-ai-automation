from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class LocatorInput(BaseModel):
    strategy: str
    value: str
    options: dict = Field(default_factory=dict)


class PageCreate(BaseModel):
    name: str
    url_pattern: str


class PageUpdate(BaseModel):
    name: str | None = None
    url_pattern: str | None = None


class ElementCreate(BaseModel):
    name: str
    element_type: str
    locators: list[LocatorInput]
    description: str | None = None


class ElementUpdate(BaseModel):
    name: str | None = None
    element_type: str | None = None
    description: str | None = None
    locators: list[LocatorInput] | None = None


class HealthCounts(BaseModel):
    healthy: int = 0
    needs_review: int = 0
    broken: int = 0
    missing: int = 0


class PageResponse(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    url_pattern: str
    last_scanned_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    counts: HealthCounts | None = None


class PageListResponse(BaseModel):
    pages: list[PageResponse]
    total: int


class PageUpdateResponse(BaseModel):
    page: PageResponse
    scripts_rewritten: int


class PageDeleteResponse(BaseModel):
    elements_deleted: int
    referencing_test_cases: int


class LocatorResponse(BaseModel):
    id: UUID
    position: int
    strategy: str
    value: str
    options: dict
    success_count: int
    failure_count: int
    last_succeeded_at: datetime | None = None


class ElementResponse(BaseModel):
    id: UUID
    page_id: UUID
    key: str
    name: str
    element_type: str
    description: str | None = None
    health: str
    manually_edited: bool
    last_used_at: datetime | None = None
    last_execution_id: UUID | None = None
    last_locator_index: int | None = None
    failure_count: int
    consecutive_failed_runs: int
    created_at: datetime
    updated_at: datetime
    locators: list[LocatorResponse]


class ElementListResponse(BaseModel):
    elements: list[ElementResponse]
    total: int


class ElementUpdateResponse(BaseModel):
    element: ElementResponse
    scripts_rewritten: int


class ElementDeleteResponse(BaseModel):
    referencing_test_cases: int


class ScanStartResponse(BaseModel):
    scan_id: str


class ScanStatusResponse(BaseModel):
    scan_id: str
    status: str
    pages_scanned: int = 0
    added: int = 0
    updated: int = 0
    marked_missing: int = 0
    error: str | None = None
