"""Chat assistant domain objects (spec 0005).

A thread belongs to one user on one project. Messages are numbered by `seq`
within the thread. `tool` rows and the hidden assistant rows that hold
`tool_calls` are kept for the LLM and never sent to the browser.
"""

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

ChatRole = Literal["user", "assistant", "tool"]

THREAD_TITLE_MAX = 80
USER_MESSAGE_MAX = 4000


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def thread_title(content: str) -> str:
    """AC-5: the first 80 characters of the first message, whitespace collapsed."""
    return " ".join(content.split())[:THREAD_TITLE_MAX] or "New chat"


class ChatViewContext(BaseModel):
    view: str
    item_type: str | None = None
    item_id: UUID | None = None


class ChatThread(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    user_id: UUID
    title: str = Field(max_length=THREAD_TITLE_MAX)
    created_at: datetime = Field(default_factory=utc_now)
    last_activity_at: datetime = Field(default_factory=utc_now)


class ChatMessage(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    thread_id: UUID
    seq: int = 0
    role: ChatRole
    content: str = ""
    view_context: dict | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict] | None = None
    tool_args: dict | None = None
    tool_result: str | None = None
    interrupted: bool = False
    action: dict | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def visible(self) -> bool:
        """Shown to the browser: user rows and the visible assistant row of a turn."""
        if self.role == "tool":
            return False
        return not (self.role == "assistant" and self.tool_calls)
