"""PostgreSQL chat store (spec 0005).

Each call uses its own short lived session. Any failure rolls the session
back and closes it before the error is raised, so a failed transaction is
never reused.
"""

import logging
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, update

from backend.database.db import SessionLocal
from backend.database.db_models import ChatMessageDB, ChatThreadDB
from backend.models.chat import ChatMessage, ChatThread
from backend.repository.chat_repository import (
    MESSAGE_PAGE_DEFAULT,
    MESSAGE_PAGE_MAX,
    THREAD_PAGE_DEFAULT,
    THREAD_PAGE_MAX,
    ACTIVE_ACTION_STATUSES,
    ChatRepository,
    decode_thread_cursor,
    encode_thread_cursor,
    merge_action,
)

logger = logging.getLogger(__name__)

_MESSAGE_FIELDS = (
    "thread_id", "seq", "role", "content", "view_context", "tool_name", "tool_call_id",
    "tool_calls", "tool_args", "tool_result", "interrupted", "action", "created_at",
)


def _thread(row: ChatThreadDB) -> ChatThread:
    return ChatThread(
        id=row.id,
        project_id=row.project_id,
        user_id=row.user_id,
        title=row.title,
        created_at=row.created_at,
        last_activity_at=row.last_activity_at,
    )


def _message(row: ChatMessageDB) -> ChatMessage:
    return ChatMessage(id=row.id, **{field: getattr(row, field) for field in _MESSAGE_FIELDS})


def _visible_filter():
    """Not a tool row, and not a hidden assistant row that holds tool_calls."""
    return and_(
        ChatMessageDB.role != "tool",
        or_(ChatMessageDB.role != "assistant", ChatMessageDB.tool_calls.is_(None)),
    )


class PostgresChatRepository(ChatRepository):
    @contextmanager
    def _session(self):
        session = SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ---------------------------------------------------------------- threads

    def create_thread(self, project_id: UUID, user_id: UUID, title: str) -> ChatThread:
        thread = ChatThread(project_id=project_id, user_id=user_id, title=title)
        with self._session() as session:
            session.add(ChatThreadDB(**thread.model_dump()))
        return thread

    def get_thread(self, thread_id: UUID) -> ChatThread | None:
        with self._session() as session:
            row = session.get(ChatThreadDB, thread_id)
            return _thread(row) if row else None

    def list_threads(self, project_id, user_id, cursor=None, limit=THREAD_PAGE_DEFAULT):
        limit = max(1, min(limit, THREAD_PAGE_MAX))
        query = select(ChatThreadDB).where(
            ChatThreadDB.project_id == project_id,
            ChatThreadDB.user_id == user_id,
        )
        if cursor:
            stamp, thread_id = decode_thread_cursor(cursor)
            query = query.where(or_(
                ChatThreadDB.last_activity_at < stamp,
                and_(ChatThreadDB.last_activity_at == stamp, ChatThreadDB.id < thread_id),
            ))
        query = query.order_by(ChatThreadDB.last_activity_at.desc(), ChatThreadDB.id.desc()).limit(limit + 1)
        with self._session() as session:
            rows = [_thread(row) for row in session.scalars(query).all()]
        page = rows[:limit]
        return page, (encode_thread_cursor(page[-1]) if len(rows) > limit else None)

    def delete_thread(self, thread_id: UUID) -> bool:
        with self._session() as session:
            session.execute(delete(ChatMessageDB).where(ChatMessageDB.thread_id == thread_id))
            result = session.execute(delete(ChatThreadDB).where(ChatThreadDB.id == thread_id))
            return result.rowcount > 0

    def _delete_threads(self, session, condition) -> int:
        ids = list(session.scalars(select(ChatThreadDB.id).where(condition)).all())
        if not ids:
            return 0
        session.execute(delete(ChatMessageDB).where(ChatMessageDB.thread_id.in_(ids)))
        session.execute(delete(ChatThreadDB).where(ChatThreadDB.id.in_(ids)))
        return len(ids)

    def delete_threads_for_project(self, project_id: UUID) -> int:
        with self._session() as session:
            return self._delete_threads(session, ChatThreadDB.project_id == project_id)

    def purge_idle(self, before: datetime, project_id=None, user_id=None, skip_thread_ids=None) -> int:
        conditions = [ChatThreadDB.last_activity_at < before]
        if project_id is not None:
            conditions.append(ChatThreadDB.project_id == project_id)
        if user_id is not None:
            conditions.append(ChatThreadDB.user_id == user_id)
        if skip_thread_ids:
            conditions.append(ChatThreadDB.id.not_in(list(skip_thread_ids)))
        with self._session() as session:
            return self._delete_threads(session, and_(*conditions))

    # --------------------------------------------------------------- messages

    def append_message(self, thread_id: UUID, role: str, content: str = "", **fields) -> ChatMessage:
        with self._session() as session:
            # Lock the thread row so two writers never take the same seq.
            thread = session.scalars(
                select(ChatThreadDB).where(ChatThreadDB.id == thread_id).with_for_update()
            ).first()
            if thread is None:
                raise KeyError(f"Chat thread {thread_id} not found")
            last_seq = session.scalar(
                select(func.max(ChatMessageDB.seq)).where(ChatMessageDB.thread_id == thread_id)
            ) or 0
            message = ChatMessage(thread_id=thread_id, seq=last_seq + 1, role=role, content=content, **fields)
            session.add(ChatMessageDB(id=message.id, **{f: getattr(message, f) for f in _MESSAGE_FIELDS}))
            thread.last_activity_at = message.created_at
        return message

    def delete_messages_after(self, thread_id: UUID, seq: int) -> int:
        with self._session() as session:
            result = session.execute(
                delete(ChatMessageDB).where(
                    ChatMessageDB.thread_id == thread_id,
                    ChatMessageDB.seq > seq,
                )
            )
            return result.rowcount

    def list_messages(self, thread_id, before_seq=None, limit=MESSAGE_PAGE_DEFAULT, include_tool=False):
        limit = max(1, min(limit, MESSAGE_PAGE_MAX))
        query = select(ChatMessageDB).where(ChatMessageDB.thread_id == thread_id)
        if not include_tool:
            query = query.where(_visible_filter())
        if before_seq is not None:
            query = query.where(ChatMessageDB.seq < before_seq)
        query = query.order_by(ChatMessageDB.seq.desc()).limit(limit + 1)
        with self._session() as session:
            rows = [_message(row) for row in session.scalars(query).all()]
        page = rows[:limit]
        page.reverse()
        return page, len(rows) > limit

    def recent_messages_for_llm(self, thread_id: UUID, max_messages: int) -> list[ChatMessage]:
        if max_messages <= 0:
            return []
        query = (
            select(ChatMessageDB)
            .where(ChatMessageDB.thread_id == thread_id)
            .order_by(ChatMessageDB.seq.desc())
            .limit(max_messages)
        )
        with self._session() as session:
            rows = [_message(row) for row in session.scalars(query).all()]
        rows.reverse()
        return rows

    def get_message(self, message_id: UUID) -> ChatMessage | None:
        with self._session() as session:
            row = session.get(ChatMessageDB, message_id)
            return _message(row) if row else None

    def update_message_content(self, message_id: UUID, content: str, interrupted: bool = False) -> bool:
        with self._session() as session:
            result = session.execute(
                update(ChatMessageDB)
                .where(ChatMessageDB.id == message_id)
                .values(content=content, interrupted=interrupted)
            )
            return result.rowcount > 0

    def compare_and_set_action_status(self, message_id, expected, new_status, patch=None):
        with self._session() as session:
            # The row lock makes read, check, and write one atomic step, so at
            # most one writer moves an action out of a given status.
            row = session.scalars(
                select(ChatMessageDB).where(ChatMessageDB.id == message_id).with_for_update()
            ).first()
            if row is None or not row.action or row.action.get("status") not in expected:
                return None
            merged = merge_action(row.action, new_status, patch)
            session.execute(
                update(ChatMessageDB).where(ChatMessageDB.id == message_id).values(action=merged)
            )
            return merged

    def expire_proposed_actions(self, thread_id: UUID) -> int:
        with self._session() as session:
            rows = session.scalars(
                select(ChatMessageDB)
                .where(ChatMessageDB.thread_id == thread_id, ChatMessageDB.action.is_not(None))
                .with_for_update()
            ).all()
            count = 0
            for row in rows:
                if row.action and row.action.get("status") == "proposed":
                    row.action = merge_action(row.action, "expired", None)
                    count += 1
            return count

    def set_message_action(self, message_id: UUID, action: dict) -> bool:
        with self._session() as session:
            row = session.scalars(
                select(ChatMessageDB).where(ChatMessageDB.id == message_id).with_for_update()
            ).first()
            if row is None or row.action:
                return False
            row.action = action
            return True

    def list_active_actions(self) -> list[tuple[ChatMessage, UUID]]:
        query = (
            select(ChatMessageDB, ChatThreadDB.project_id)
            .join(ChatThreadDB, ChatThreadDB.id == ChatMessageDB.thread_id)
            .where(ChatMessageDB.action["status"].as_string().in_(ACTIVE_ACTION_STATUSES))
        )
        with self._session() as session:
            return [(_message(row), project_id) for row, project_id in session.execute(query).all()]

    def mark_empty_replies_interrupted(self) -> int:
        with self._session() as session:
            result = session.execute(
                update(ChatMessageDB)
                .where(
                    ChatMessageDB.role == "assistant",
                    ChatMessageDB.tool_calls.is_(None),
                    ChatMessageDB.action.is_(None),
                    ChatMessageDB.content == "",
                    ChatMessageDB.interrupted.is_(False),
                )
                .values(interrupted=True)
            )
            return result.rowcount
