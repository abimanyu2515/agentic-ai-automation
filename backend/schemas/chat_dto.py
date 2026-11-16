"""Request and response models for the chat assistant routes (spec 0005)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from backend.models.chat import USER_MESSAGE_MAX


class ChatMessageCreate(BaseModel):
    thread_id: UUID | None = None
    # The pattern rejects blank messages; a regex error serializes cleanly
    # through the app's validation handler, a ValueError does not.
    content: str = Field(min_length=1, max_length=USER_MESSAGE_MAX, pattern=r"\S")
    # Untrusted (AC-4): unknown values are dropped by the service, never rejected.
    view_context: dict | None = None


class ChatHelpCreate(BaseModel):
    content: str = Field(min_length=1, max_length=USER_MESSAGE_MAX, pattern=r"\S")
    history: list[dict] = Field(default_factory=list, max_length=20)


class ChatThreadItem(BaseModel):
    id: UUID
    title: str
    last_activity_at: datetime


class ChatThreadPage(BaseModel):
    items: list[ChatThreadItem]
    next_cursor: str | None = None


class ChatMessageItem(BaseModel):
    id: UUID
    seq: int
    role: str
    content: str
    interrupted: bool
    action: dict | None = None
    created_at: datetime


class ChatMessagePage(BaseModel):
    items: list[ChatMessageItem]
    has_more: bool


class ChatActionConfirm(BaseModel):
    # AC-12: only confirm_text is read. Anything else the browser sends (for
    # example params) is ignored; confirm runs the params saved with the proposal.
    confirm_text: str | None = Field(default=None, max_length=20)
