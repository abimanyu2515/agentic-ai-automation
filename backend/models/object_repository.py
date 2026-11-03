"""
Object repository models (spec 0004).

A project's object repository is a list of pages, each holding UI elements,
each with one to five ordered locators. Scripts reference an element by its
key, `<page.name>.<element.name>`, and the backend injects the current
locators before every read and run.

The whole repository of one project is loaded and saved as one
`ProjectObjectRepository` aggregate, so the JSON store and PostgreSQL share
the same business rules in `ObjectRepositoryService`.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

LOCATOR_STRATEGIES = ("role", "label", "placeholder", "text", "test_id", "css", "xpath")
ELEMENT_TYPES = ("button", "link", "input", "select", "checkbox", "radio", "textarea", "nav")
HEALTH_STATES = ("healthy", "needs_review", "broken", "missing")
MAX_LOCATORS = 5
APPLIED_EXECUTION_HISTORY = 20


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ObjectLocator(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    position: int = Field(ge=0, le=MAX_LOCATORS - 1)
    strategy: str
    value: str
    options: dict = Field(default_factory=dict)
    success_count: int = 0
    failure_count: int = 0
    last_succeeded_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    def signature(self) -> tuple:
        """Identity used to keep counters when a locator list is replaced."""
        return (self.strategy, self.value, tuple(sorted(self.options.items())))


class ObjectElement(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    page_id: UUID
    name: str
    element_type: str
    description: str | None = None
    fingerprint: str | None = None
    health: str = "healthy"
    manually_edited: bool = False
    last_used_at: datetime | None = None
    last_execution_id: UUID | None = None
    last_locator_index: int | None = None
    failure_count: int = 0
    consecutive_failed_runs: int = 0
    applied_execution_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    is_deleted: bool = False
    deleted_at: datetime | None = None
    deleted_by: UUID | None = None
    locators: list[ObjectLocator] = Field(default_factory=list)


class ObjectPage(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    name: str
    url_pattern: str
    last_scanned_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    is_deleted: bool = False
    deleted_at: datetime | None = None
    deleted_by: UUID | None = None
    elements: list[ObjectElement] = Field(default_factory=list)

    def active_elements(self) -> list[ObjectElement]:
        return [element for element in self.elements if not element.is_deleted]


class ProjectObjectRepository(BaseModel):
    project_id: UUID
    pages: list[ObjectPage] = Field(default_factory=list)

    def active_pages(self) -> list[ObjectPage]:
        return [page for page in self.pages if not page.is_deleted]

    def find_page(self, page_id: UUID) -> ObjectPage | None:
        return next((page for page in self.pages if page.id == page_id), None)

    def find_element(self, element_id: UUID) -> tuple[ObjectPage, ObjectElement] | None:
        for page in self.pages:
            for element in page.elements:
                if element.id == element_id:
                    return page, element
        return None

    def element_by_key(self, key: str) -> ObjectElement | None:
        """The non deleted element for `<page>.<element>`, or None."""
        page_name, _, element_name = key.partition(".")
        for page in self.active_pages():
            if page.name != page_name:
                continue
            for element in page.active_elements():
                if element.name == element_name:
                    return element
        return None

    def active_keys(self) -> set[str]:
        return {
            f"{page.name}.{element.name}"
            for page in self.active_pages()
            for element in page.active_elements()
        }
