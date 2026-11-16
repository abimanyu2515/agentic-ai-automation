"""
Chat thread and message persistence (spec 0005).

`ChatRepository` is the interface. `JsonChatRepository` keeps everything in
`backend/database/chat_store.json` under one process lock and writes through
a temp file plus an atomic rename. `PostgresChatRepository`
(postgres_chat_repository.py) is the SQL implementation. Both are picked by
the same provider setting as `get_project_repository()`.

`compare_and_set_action_status` is the only way an action's status changes.
"""

import base64
import json
import os
import tempfile
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from uuid import UUID

from backend.models.chat import ChatMessage, ChatThread, utc_now

CHAT_STORE_PATH = Path(__file__).parent.parent / "database" / "chat_store.json"

MESSAGE_PAGE_DEFAULT = 50
MESSAGE_PAGE_MAX = 100
THREAD_PAGE_DEFAULT = 20
THREAD_PAGE_MAX = 50


class InvalidCursorError(ValueError):
    pass


def encode_thread_cursor(thread: ChatThread) -> str:
    raw = f"{thread.last_activity_at.isoformat()}|{thread.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_thread_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        stamp, thread_id = raw.split("|", 1)
        return datetime.fromisoformat(stamp), UUID(thread_id)
    except Exception as exc:
        raise InvalidCursorError("Invalid thread cursor") from exc


def _after_cursor(thread: ChatThread, cursor: tuple[datetime, UUID] | None) -> bool:
    """Order is last_activity_at DESC, id DESC; keep rows strictly after the cursor."""
    if cursor is None:
        return True
    stamp, thread_id = cursor
    return (thread.last_activity_at, str(thread.id)) < (stamp, str(thread_id))


def merge_action(action: dict, new_status: str, patch: dict | None) -> dict:
    merged = {**action, **(patch or {})}
    merged["status"] = new_status
    merged["updated_at"] = utc_now().isoformat()
    return merged


class ChatRepository(ABC):
    @abstractmethod
    def create_thread(self, project_id: UUID, user_id: UUID, title: str) -> ChatThread:
        pass

    @abstractmethod
    def get_thread(self, thread_id: UUID) -> ChatThread | None:
        pass

    @abstractmethod
    def list_threads(
        self, project_id: UUID, user_id: UUID, cursor: str | None = None, limit: int = THREAD_PAGE_DEFAULT,
    ) -> tuple[list[ChatThread], str | None]:
        """Most recent activity first. Returns the page and the next cursor (None at the end)."""

    @abstractmethod
    def delete_thread(self, thread_id: UUID) -> bool:
        pass

    @abstractmethod
    def delete_threads_for_project(self, project_id: UUID) -> int:
        pass

    @abstractmethod
    def purge_idle(
        self,
        before: datetime,
        project_id: UUID | None = None,
        user_id: UUID | None = None,
        skip_thread_ids: set[UUID] | None = None,
    ) -> int:
        """Delete threads whose last activity is older than `before`, with their messages."""

    @abstractmethod
    def append_message(self, thread_id: UUID, role: str, content: str = "", **fields) -> ChatMessage:
        """Assign the next seq and bump the thread's last_activity_at in the same write."""

    @abstractmethod
    def delete_messages_after(self, thread_id: UUID, seq: int) -> int:
        """Delete every row after seq, including hidden tool history."""

    @abstractmethod
    def list_messages(
        self,
        thread_id: UUID,
        before_seq: int | None = None,
        limit: int = MESSAGE_PAGE_DEFAULT,
        include_tool: bool = False,
    ) -> tuple[list[ChatMessage], bool]:
        """Visible rows (unless include_tool) below before_seq, the newest `limit` of them,
        returned in seq order, plus whether older rows remain."""

    @abstractmethod
    def recent_messages_for_llm(self, thread_id: UUID, max_messages: int) -> list[ChatMessage]:
        """The newest `max_messages` rows of every role, in seq order."""

    @abstractmethod
    def get_message(self, message_id: UUID) -> ChatMessage | None:
        pass

    @abstractmethod
    def update_message_content(self, message_id: UUID, content: str, interrupted: bool = False) -> bool:
        pass

    @abstractmethod
    def compare_and_set_action_status(
        self, message_id: UUID, expected: set[str], new_status: str, patch: dict | None = None,
    ) -> dict | None:
        """Atomically move the action to new_status if its status is in `expected`.
        Returns the updated action, or None when the status did not match."""

    @abstractmethod
    def expire_proposed_actions(self, thread_id: UUID) -> int:
        pass

    @abstractmethod
    def set_message_action(self, message_id: UUID, action: dict) -> bool:
        """Attach a new `proposed` action to a message that has none. Status
        changes after that go through compare_and_set_action_status only."""

    @abstractmethod
    def list_active_actions(self) -> list[tuple[ChatMessage, UUID]]:
        """Every message whose action is `queued` or `running`, with its thread's
        project ID (startup recovery)."""

    @abstractmethod
    def mark_empty_replies_interrupted(self) -> int:
        """Startup recovery: visible assistant rows left empty by a turn the
        restart cut off are marked interrupted."""


def _is_empty_reply(row: dict) -> bool:
    return (
        row.get("role") == "assistant"
        and not row.get("tool_calls")
        and not row.get("action")
        and not row.get("content")
        and not row.get("interrupted")
    )


ACTIVE_ACTION_STATUSES = ("queued", "running")


class JsonChatRepository(ChatRepository):
    """The default store. One file, one lock, atomic replace on every write."""

    _lock = threading.RLock()

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else CHAT_STORE_PATH

    # ------------------------------------------------------------ file access

    def _read(self) -> dict:
        if not self.path.exists():
            return {"threads": {}, "messages": {}}
        with open(self.path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        data.setdefault("threads", {})
        data.setdefault("messages", {})
        return data

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".chat_store.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
            os.replace(temp_path, self.path)
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    @staticmethod
    def _thread(raw: dict) -> ChatThread:
        return ChatThread.model_validate(raw)

    @staticmethod
    def _message(raw: dict) -> ChatMessage:
        return ChatMessage.model_validate(raw)

    def _find_message(self, data: dict, message_id: UUID) -> tuple[str, int] | None:
        key = str(message_id)
        for thread_id, rows in data["messages"].items():
            for index, row in enumerate(rows):
                if row["id"] == key:
                    return thread_id, index
        return None

    # ---------------------------------------------------------------- threads

    def create_thread(self, project_id: UUID, user_id: UUID, title: str) -> ChatThread:
        thread = ChatThread(project_id=project_id, user_id=user_id, title=title)
        with self._lock:
            data = self._read()
            data["threads"][str(thread.id)] = thread.model_dump(mode="json")
            data["messages"][str(thread.id)] = []
            self._write(data)
        return thread

    def get_thread(self, thread_id: UUID) -> ChatThread | None:
        with self._lock:
            raw = self._read()["threads"].get(str(thread_id))
        return self._thread(raw) if raw else None

    def list_threads(self, project_id, user_id, cursor=None, limit=THREAD_PAGE_DEFAULT):
        limit = max(1, min(limit, THREAD_PAGE_MAX))
        position = decode_thread_cursor(cursor) if cursor else None
        with self._lock:
            threads = [self._thread(raw) for raw in self._read()["threads"].values()]
        threads = [
            t for t in threads
            if t.project_id == project_id and t.user_id == user_id and _after_cursor(t, position)
        ]
        threads.sort(key=lambda t: (t.last_activity_at, str(t.id)), reverse=True)
        page = threads[:limit]
        next_cursor = encode_thread_cursor(page[-1]) if len(threads) > limit else None
        return page, next_cursor

    def delete_thread(self, thread_id: UUID) -> bool:
        with self._lock:
            data = self._read()
            if data["threads"].pop(str(thread_id), None) is None:
                return False
            data["messages"].pop(str(thread_id), None)
            self._write(data)
        return True

    def _delete_where(self, keep) -> int:
        with self._lock:
            data = self._read()
            doomed = [tid for tid, raw in data["threads"].items() if not keep(self._thread(raw))]
            for thread_id in doomed:
                data["threads"].pop(thread_id, None)
                data["messages"].pop(thread_id, None)
            if doomed:
                self._write(data)
        return len(doomed)

    def delete_threads_for_project(self, project_id: UUID) -> int:
        return self._delete_where(lambda t: t.project_id != project_id)

    def purge_idle(self, before, project_id=None, user_id=None, skip_thread_ids=None):
        skip = skip_thread_ids or set()

        def keep(thread: ChatThread) -> bool:
            if thread.id in skip or thread.last_activity_at >= before:
                return True
            if project_id is not None and thread.project_id != project_id:
                return True
            if user_id is not None and thread.user_id != user_id:
                return True
            return False

        return self._delete_where(keep)

    # --------------------------------------------------------------- messages

    def append_message(self, thread_id: UUID, role: str, content: str = "", **fields) -> ChatMessage:
        with self._lock:
            data = self._read()
            thread = data["threads"].get(str(thread_id))
            if thread is None:
                raise KeyError(f"Chat thread {thread_id} not found")
            rows = data["messages"].setdefault(str(thread_id), [])
            next_seq = max((row["seq"] for row in rows), default=0) + 1
            message = ChatMessage(thread_id=thread_id, seq=next_seq, role=role, content=content, **fields)
            rows.append(message.model_dump(mode="json"))
            thread["last_activity_at"] = message.created_at.isoformat()
            self._write(data)
        return message

    def delete_messages_after(self, thread_id: UUID, seq: int) -> int:
        with self._lock:
            data = self._read()
            rows = data["messages"].get(str(thread_id), [])
            kept = [row for row in rows if row["seq"] <= seq]
            deleted = len(rows) - len(kept)
            if deleted:
                data["messages"][str(thread_id)] = kept
                self._write(data)
            return deleted

    def _rows(self, thread_id: UUID) -> list[ChatMessage]:
        with self._lock:
            rows = self._read()["messages"].get(str(thread_id), [])
        return sorted((self._message(row) for row in rows), key=lambda m: m.seq)

    def list_messages(self, thread_id, before_seq=None, limit=MESSAGE_PAGE_DEFAULT, include_tool=False):
        limit = max(1, min(limit, MESSAGE_PAGE_MAX))
        rows = [m for m in self._rows(thread_id) if include_tool or m.visible]
        if before_seq is not None:
            rows = [m for m in rows if m.seq < before_seq]
        return rows[-limit:], len(rows) > limit

    def recent_messages_for_llm(self, thread_id: UUID, max_messages: int) -> list[ChatMessage]:
        return self._rows(thread_id)[-max_messages:] if max_messages > 0 else []

    def get_message(self, message_id: UUID) -> ChatMessage | None:
        with self._lock:
            data = self._read()
            found = self._find_message(data, message_id)
            if found is None:
                return None
            thread_id, index = found
            return self._message(data["messages"][thread_id][index])

    def update_message_content(self, message_id: UUID, content: str, interrupted: bool = False) -> bool:
        with self._lock:
            data = self._read()
            found = self._find_message(data, message_id)
            if found is None:
                return False
            thread_id, index = found
            row = data["messages"][thread_id][index]
            row["content"] = content
            row["interrupted"] = interrupted
            self._write(data)
        return True

    def compare_and_set_action_status(self, message_id, expected, new_status, patch=None):
        with self._lock:
            data = self._read()
            found = self._find_message(data, message_id)
            if found is None:
                return None
            thread_id, index = found
            row = data["messages"][thread_id][index]
            action = row.get("action")
            if not action or action.get("status") not in expected:
                return None
            row["action"] = merge_action(action, new_status, patch)
            self._write(data)
            return row["action"]

    def expire_proposed_actions(self, thread_id: UUID) -> int:
        with self._lock:
            data = self._read()
            count = 0
            for row in data["messages"].get(str(thread_id), []):
                action = row.get("action")
                if action and action.get("status") == "proposed":
                    row["action"] = merge_action(action, "expired", None)
                    count += 1
            if count:
                self._write(data)
        return count

    def set_message_action(self, message_id: UUID, action: dict) -> bool:
        with self._lock:
            data = self._read()
            found = self._find_message(data, message_id)
            if found is None:
                return False
            thread_id, index = found
            row = data["messages"][thread_id][index]
            if row.get("action"):
                return False
            row["action"] = action
            self._write(data)
        return True

    def list_active_actions(self) -> list[tuple[ChatMessage, UUID]]:
        with self._lock:
            data = self._read()
        found = []
        for thread_id, rows in data["messages"].items():
            thread = data["threads"].get(thread_id)
            if thread is None:
                continue
            for row in rows:
                action = row.get("action")
                if action and action.get("status") in ACTIVE_ACTION_STATUSES:
                    found.append((self._message(row), UUID(thread["project_id"])))
        return found

    def mark_empty_replies_interrupted(self) -> int:
        with self._lock:
            data = self._read()
            count = 0
            for rows in data["messages"].values():
                for row in rows:
                    if _is_empty_reply(row):
                        row["interrupted"] = True
                        count += 1
            if count:
                self._write(data)
        return count


_chat_repository: ChatRepository | None = None
_chat_repository_lock = threading.Lock()


def get_chat_repository() -> ChatRepository:
    """The chat store for the active repository provider (json or postgres)."""
    global _chat_repository
    with _chat_repository_lock:
        if _chat_repository is None:
            from backend.config.settings import get_settings

            if get_settings().repository.provider == "postgres":
                from backend.repository.postgres_chat_repository import PostgresChatRepository

                _chat_repository = PostgresChatRepository()
            else:
                _chat_repository = JsonChatRepository()
        return _chat_repository


def set_chat_repository(repository: ChatRepository | None) -> None:
    """Swap the active chat store. Tests use this; None goes back to the provider default."""
    global _chat_repository
    with _chat_repository_lock:
        _chat_repository = repository
