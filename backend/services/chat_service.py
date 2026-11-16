"""
Chat assistant conversation loop (spec 0005).

A service level loop, not a WorkflowState agent. `prepare_turn` checks the
thread, saves the user message, and returns a PreparedTurn; it raises
ChatError subclasses the route maps to HTTP errors, so those happen before
any stream starts. `stream_turn` is a sync generator of SSE frames: it
builds the prompt, streams the LLM reply as `token` events, runs the read
tools the model calls (saving a hidden assistant `tool_calls` row and one
`tool` row per call, within CHAT_MAX_TOOL_STEPS), and saves the visible
assistant row at each tool boundary, at the end, or as `interrupted` when it
is cut short.

Actions (spec 0005, action catalog): the model may call one `propose_<type>`
tool per turn, offered only for the action types the user holds permission
for. A valid proposal is saved on the visible assistant row as a `proposed`
action, sent as an `action` event, and ends the turn. Nothing runs until
`confirm_action`, which rechecks ownership, permission, the typed DELETE, and
the targets, then moves the status with compare_and_set_action_status.

Every dependency comes through the constructor so tests inject an LLM stub
and an in memory store.
"""

import json
import logging
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import AsyncIterator, Callable, Iterator
from uuid import UUID

from backend.models.chat import ChatMessage, ChatThread, ChatViewContext, thread_title, utc_now
from backend.repository.chat_repository import InvalidCursorError
from backend.services.chat_actions import (
    ACTION_CATALOG,
    ACTIVE,
    CHANGED_MESSAGE,
    ONE_ACTION_RESULT,
    PROPOSED_RESULT,
    ActionInvalid,
    action_type_for_tool,
    propose_tool_definitions,
)

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).parent.parent / "prompts" / "agents" / "chat_assistant"

# View context vocabulary (spec 0005): the allowed (view, item_type) pairs.
# A view mapped to an empty set carries no item. Any other pair is dropped.
VIEW_ITEM_TYPES: dict[str, frozenset[str]] = {
    "dashboard": frozenset(),
    "project": frozenset(),
    "settings": frozenset(),
    "langsmith": frozenset(),
    "jira-traceability": frozenset(),
    "reports": frozenset(),
    "requirements": frozenset(),
    "scenarios": frozenset({"scenario", "requirement"}),
    "scenario-approval": frozenset({"scenario", "requirement"}),
    "testcases": frozenset({"test_case", "requirement"}),
    "testcase-approval": frozenset({"test_case", "requirement"}),
    "testdatagent": frozenset({"test_case"}),
    "playwright": frozenset({"test_case"}),
    "execution-workspace": frozenset({"execution", "test_case"}),
    "executions": frozenset({"execution"}),
    "object-repository": frozenset({"object_repository_page"}),
}

BUSY_MESSAGE = "The assistant is busy, try again in a moment"
CAP_INSTRUCTION = (
    "No more lookups are available in this turn. Answer now with what you have, "
    "and do not ask to call more tools."
)
CAP_NOTE = "\n\n_This answer may be incomplete: the lookup limit for one message was reached._"
CAP_RESULT = '{"error": "tool call limit reached for this turn"}'


class ChatError(Exception):
    status_code = 400


class ChatNotFound(ChatError):
    status_code = 404


class ChatInvalid(ChatError):
    status_code = 422


class ChatForbidden(ChatError):
    status_code = 403


class ChatConflict(ChatError):
    status_code = 409


class ChatRateLimited(ChatError):
    status_code = 429


NO_PERMISSION_MESSAGE = "You no longer have permission"
DELETE_CONFIRM_TEXT = "DELETE"
CONFLICT_MESSAGES = {
    "expired": "Expired, ask again",
    "cancelled": "This action was cancelled",
}


def allowed_action_types(can: Callable[[str, str], bool]) -> frozenset[str]:
    """AC-11: the action types whose (module, action) permission the user holds."""
    return frozenset(
        action_type for action_type, spec in ACTION_CATALOG.items() if can(spec.module, spec.permission_action)
    )


@dataclass
class PreparedTurn:
    project: object
    thread: ChatThread
    user_message: ChatMessage
    view_context: ChatViewContext | None
    created: bool
    allowed_actions: frozenset[str] = frozenset()
    locked: bool = True


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _load_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        logger.error("Chat prompt file missing: %s", path)
        return ""


def load_guide(max_chars: int, path: Path | None = None) -> str:
    """The app guide with HTML comments stripped, cut to CHAT_GUIDE_CHARS."""
    text = re.sub(r"<!--.*?-->", "", _load_text(path or PROMPT_DIR / "app_guide.md"), flags=re.DOTALL).strip()
    if len(text) > max_chars:
        logger.error("Chat app guide is %s characters, over CHAT_GUIDE_CHARS=%s; it was cut", len(text), max_chars)
        text = text[:max_chars]
    return text


def _view_line(view_context: dict | None) -> str:
    if not view_context or not view_context.get("view"):
        return ""
    parts = [view_context["view"], view_context.get("item_type") or "", view_context.get("item_id") or ""]
    return "[viewing: " + " ".join(p for p in parts if p) + "]"


class ChatService:
    def __init__(
        self,
        llm_service=None,
        chat_repository=None,
        project_repository=None,
        settings=None,
        object_repository_store=None,
        toolbox=None,
        action_executor=None,
        system_prompt: str | None = None,
        guide: str | None = None,
        clock: Callable[[], datetime] = utc_now,
    ):
        self._llm_service = llm_service
        self._chat_repository = chat_repository
        self._project_repository = project_repository
        self._settings = settings
        self._object_repository_store = object_repository_store
        self._toolbox = toolbox
        self._action_executor = action_executor
        self._system_prompt = system_prompt
        self._guide = guide
        self._clock = clock
        self._state_lock = threading.Lock()
        self._active_threads: set[UUID] = set()
        self._rate_events: dict[UUID, deque[float]] = defaultdict(deque)
        self._stream_semaphore = None

    # ------------------------------------------------------------ dependencies

    @property
    def settings(self):
        if self._settings is not None:
            return self._settings
        from backend.config.settings import get_settings

        return get_settings()

    @property
    def llm(self):
        if self._llm_service is None:
            from backend.services.llm import LLMService

            self._llm_service = LLMService(role="chat")
        return self._llm_service

    @property
    def chats(self):
        if self._chat_repository is None:
            from backend.repository.chat_repository import get_chat_repository

            return get_chat_repository()
        return self._chat_repository

    @property
    def projects(self):
        if self._project_repository is None:
            from backend.repository.project_repository import get_project_repository

            return get_project_repository()
        return self._project_repository

    @property
    def toolbox(self):
        if self._toolbox is None:
            from backend.services.chat_tools import ChatToolbox

            self._toolbox = ChatToolbox(
                self._project_repository,
                self._object_repository_store,
                result_chars=self.settings.chat.tool_result_chars,
            )
        return self._toolbox

    @property
    def actions(self):
        if self._action_executor is None:
            from backend.services.chat_actions import ChatActionExecutor

            self._action_executor = ChatActionExecutor(self._project_repository)
        return self._action_executor

    def _system_template(self) -> str:
        if self._system_prompt is None:
            self._system_prompt = _load_text(PROMPT_DIR / "system_prompt.txt")
        return self._system_prompt

    def guide(self) -> str:
        if self._guide is None:
            self._guide = load_guide(self.settings.chat.guide_chars)
        return self._guide

    # ----------------------------------------------------------------- threads

    def get_owned_thread(self, project_id: UUID, user_id: UUID, thread_id: UUID) -> ChatThread:
        """AC-21: someone else's thread, or one on another project, is simply not found."""
        thread = self.chats.get_thread(thread_id)
        if thread is None or thread.user_id != user_id or thread.project_id != project_id:
            raise ChatNotFound("Chat thread not found")
        return thread

    def list_threads(self, project_id: UUID, user_id: UUID, cursor: str | None, limit: int):
        self.purge_idle(project_id=project_id, user_id=user_id)
        try:
            return self.chats.list_threads(project_id, user_id, cursor=cursor, limit=limit)
        except InvalidCursorError as exc:
            raise ChatInvalid(str(exc)) from exc

    def list_messages(self, project_id: UUID, user_id: UUID, thread_id: UUID, before_seq: int | None, limit: int):
        self.get_owned_thread(project_id, user_id, thread_id)
        return self.chats.list_messages(thread_id, before_seq=before_seq, limit=limit)

    # ------------------------------------------------------------ view context

    def sanitize_view_context(self, project, raw: dict | None) -> ChatViewContext | None:
        """AC-4: unknown views or item types are dropped, and an item that is not
        in this project is dropped, all without an error."""
        if not isinstance(raw, dict):
            return None
        view = raw.get("view")
        if not isinstance(view, str) or view not in VIEW_ITEM_TYPES:
            return None
        item_type = raw.get("item_type")
        item_id = raw.get("item_id")
        if not isinstance(item_type, str) or item_type not in VIEW_ITEM_TYPES[view] or not item_id:
            return ChatViewContext(view=view)
        try:
            item_uuid = UUID(str(item_id))
        except ValueError:
            return ChatViewContext(view=view)
        try:
            belongs = self._item_in_project(project, item_type, item_uuid)
        except Exception:
            logger.warning("Chat view context lookup failed for %s %s", item_type, item_uuid, exc_info=True)
            belongs = False
        if not belongs:
            return ChatViewContext(view=view)
        return ChatViewContext(view=view, item_type=item_type, item_id=item_uuid)

    def _item_in_project(self, project, item_type: str, item_id: UUID) -> bool:
        requirement_ids = {str(r) for r in (getattr(project, "requirements", None) or [])}
        repo = self.projects

        def scenario_in_project(scenario_id) -> bool:
            scenario = repo.get_scenario(scenario_id)
            return scenario is not None and str(scenario.requirement_id) in requirement_ids

        if item_type == "requirement":
            return str(item_id) in requirement_ids
        if item_type == "scenario":
            return scenario_in_project(item_id)
        if item_type == "test_case":
            test_case = repo.get_test_case(item_id)
            return test_case is not None and scenario_in_project(test_case.scenario_id)
        if item_type == "execution":
            return repo.get_execution_result(project.id, item_id) is not None
        if item_type == "object_repository_page":
            store = self._object_repository_store
            if store is None:
                from backend.repository.object_repository_store import get_object_repository_store

                store = get_object_repository_store(repo)
            if store is None:
                return False
            return any(page.id == item_id and not page.is_deleted for page in store.load(project.id).pages)
        return False

    # ------------------------------------------------------------------- turns

    def prepare_turn(
        self,
        project,
        user_id: UUID,
        content: str,
        thread_id: UUID | None = None,
        view_context: dict | None = None,
        allowed_actions: frozenset[str] | set[str] = frozenset(),
    ) -> PreparedTurn:
        """Check the thread and save the user message before any LLM call (AC-17)."""
        if thread_id is not None:
            self.get_owned_thread(project.id, user_id, thread_id)
            self._lock_thread(thread_id)
            try:
                thread = self.get_owned_thread(project.id, user_id, thread_id)
                self.check_rate_limit(user_id)
            except Exception:
                self._unlock_thread(thread_id)
                raise
            created = False
            # AC-14: a new message expires any earlier card still waiting for Run.
        else:
            self.check_rate_limit(user_id)
            thread = self.chats.create_thread(project.id, user_id, thread_title(content))
            self._lock_thread(thread.id)
            created = True
        try:
            context = self.sanitize_view_context(project, view_context)
            if not created:
                self.chats.expire_proposed_actions(thread.id)
            user_message = self.chats.append_message(
                thread.id,
                "user",
                content,
                view_context=context.model_dump(mode="json") if context else None,
            )
        except Exception:
            self._unlock_thread(thread.id)
            raise
        return PreparedTurn(
            project=project,
            thread=thread,
            user_message=user_message,
            view_context=context,
            created=created,
            allowed_actions=frozenset(allowed_actions) & frozenset(ACTION_CATALOG),
        )

    def _lock_thread(self, thread_id: UUID) -> None:
        with self._state_lock:
            if thread_id in self._active_threads:
                raise ChatConflict("A reply is still being written in this conversation")
            self._active_threads.add(thread_id)

    def _unlock_thread(self, thread_id: UUID) -> None:
        with self._state_lock:
            self._active_threads.discard(thread_id)

    def _acquire_stream_slot(self) -> bool:
        with self._state_lock:
            if self._stream_semaphore is None:
                self._stream_semaphore = threading.BoundedSemaphore(self.settings.chat.max_streams)
            semaphore = self._stream_semaphore
        return semaphore.acquire(blocking=False)

    def check_rate_limit(self, user_id: UUID) -> None:
        now = time.monotonic()
        cutoff = now - 60
        with self._state_lock:
            events = self._rate_events[user_id]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.settings.chat.rate_limit_per_minute:
                raise ChatRateLimited("Too many messages, wait a moment")
            events.append(now)

    def purge_idle(self, project_id: UUID | None = None, user_id: UUID | None = None) -> int:
        cutoff = self._clock() - timedelta(days=self.settings.chat.retention_days)
        with self._state_lock:
            return self.chats.purge_idle(
                cutoff,
                project_id=project_id,
                user_id=user_id,
                skip_thread_ids=set(self._active_threads),
            )

    def delete_thread(self, project_id: UUID, user_id: UUID, thread_id: UUID) -> None:
        with self._state_lock:
            if thread_id in self._active_threads:
                raise ChatConflict("A reply is still being written in this conversation")
            self._active_threads.add(thread_id)
        try:
            self.get_owned_thread(project_id, user_id, thread_id)
            self.chats.delete_thread(thread_id)
        finally:
            self._unlock_thread(thread_id)

    def prepare_retry(self, project, user_id: UUID, thread_id: UUID, allowed_actions=frozenset()) -> PreparedTurn:
        self.get_owned_thread(project.id, user_id, thread_id)
        self._lock_thread(thread_id)
        try:
            thread = self.get_owned_thread(project.id, user_id, thread_id)
            rows = self.chats.recent_messages_for_llm(thread.id, self.settings.chat.history_messages * 4)
            visible = [row for row in rows if row.visible]
            if not visible:
                raise ChatConflict("There is no message to retry")
            last = visible[-1]
            if last.role == "user":
                user_message = last
            elif last.role == "assistant" and (
                last.interrupted or (last.action and last.action.get("status") == "proposed")
            ):
                user_message = next((row for row in reversed(visible[:-1]) if row.role == "user"), None)
                if user_message is None:
                    raise ChatConflict("There is no user message to retry")
                if last.action and last.action.get("status") == "proposed":
                    self.chats.expire_proposed_actions(thread.id)
            else:
                raise ChatConflict("The last assistant turn cannot be retried")
            self.check_rate_limit(user_id)
            self.chats.delete_messages_after(thread.id, user_message.seq)
            context = self.sanitize_view_context(project, user_message.view_context)
            return PreparedTurn(
                project=project,
                thread=thread,
                user_message=user_message,
                view_context=context,
                created=False,
                allowed_actions=frozenset(allowed_actions) & frozenset(ACTION_CATALOG),
            )
        except Exception:
            self._unlock_thread(thread.id)
            raise

    def build_messages(self, turn: PreparedTurn) -> list[dict]:
        context = turn.view_context
        context_text = "no item open"
        if context:
            context_text = f"view '{context.view}'"
            if context.item_type:
                context_text += f", {context.item_type} {context.item_id}"
        system = (
            self._system_template()
            .replace("{project_name}", getattr(turn.project, "name", "") or "")
            .replace("{today}", self._clock().date().isoformat())
            .replace("{view_context}", context_text)
        )
        guide = self.guide()
        if guide:
            system = f"{system}\n\n{guide}"
        return [{"role": "system", "content": system}, *self.history_for_llm(turn.thread.id)]

    def history_for_llm(self, thread_id: UUID) -> list[dict]:
        """AC-9: newest first within CHAT_HISTORY_MESSAGES and CHAT_HISTORY_CHARS.
        A hidden tool_calls row and its tool rows are one unit, kept or dropped
        together, and a tool row is never sent without its call."""
        chat = self.settings.chat
        rows = _visible_reply_last(self.chats.recent_messages_for_llm(thread_id, chat.history_messages))

        units: list[list[ChatMessage]] = []
        pending_tools: list[ChatMessage] = []
        for row in reversed(rows):
            if row.role == "tool":
                pending_tools.insert(0, row)
                continue
            if row.role == "assistant" and row.tool_calls:
                call_ids = {call.get("id") for call in row.tool_calls}
                tools = [t for t in pending_tools if t.tool_call_id in call_ids]
                if len(tools) == len(call_ids):
                    units.append([row, *tools])
            else:
                units.append([row])
            # Tool rows not claimed by the call just before them are orphans.
            pending_tools = []

        selected: list[list[dict]] = []
        used = 0
        for unit in units:
            messages = [m for m in (self._to_llm(row) for row in unit) if m is not None]
            if not messages:
                continue
            size = sum(len(m.get("content") or "") + len(json.dumps(m.get("tool_calls") or "")) for m in messages)
            if selected and used + size > chat.history_chars:
                break
            selected.append(messages)
            used += size
        selected.reverse()
        return [message for unit in selected for message in unit]

    @staticmethod
    def _to_llm(row: ChatMessage) -> dict | None:
        if row.role == "user":
            line = _view_line(row.view_context)
            return {"role": "user", "content": f"{line}\n{row.content}" if line else row.content}
        if row.role == "tool":
            return {"role": "tool", "tool_call_id": row.tool_call_id, "content": row.tool_result or ""}
        if row.tool_calls:
            # The step's text is also in the turn's visible row, which is
            # replayed after the tool results; sending it twice adds nothing.
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call.get("id"),
                        "type": "function",
                        "function": {"name": call.get("name"), "arguments": json.dumps(call.get("arguments") or {})},
                    }
                    for call in row.tool_calls
                ],
            }
        content = row.content
        if row.interrupted:
            content = f"{content} [interrupted]".strip()
        if row.action:
            names = ", ".join(t.get("name", "") for t in row.action.get("targets") or [])
            content = f"{content} [action {row.action.get('type')} on {names}: {row.action.get('status')}]".strip()
        if not content:
            return None
        return {"role": "assistant", "content": content}

    def stream_turn(self, turn: PreparedTurn) -> Iterator[str]:
        """SSE frames for one turn (AC-7). When `done` is sent, the reply is saved."""
        from backend.services.llm import LLMRateLimitTimeout, LLMServiceError

        assistant: ChatMessage | None = None
        parts: list[str] = []
        finished = False
        if not self._acquire_stream_slot():
            try:
                yield sse("error", {"code": "llm_busy", "message": BUSY_MESSAGE, "retryable": True})
            finally:
                self._unlock_thread(turn.thread.id)
            return

        def save_partial() -> None:
            if assistant is not None:
                self.chats.update_message_content(assistant.id, "".join(parts), interrupted=True)

        try:
            if turn.created:
                yield sse("thread", {"thread_id": str(turn.thread.id), "title": turn.thread.title})
            messages = self.build_messages(turn)
            assistant = self.chats.append_message(turn.thread.id, "assistant", "")
            yield sse("message_start", {
                "user_message_id": str(turn.user_message.id),
                "assistant_message_id": str(assistant.id),
            })
            max_calls = self.settings.chat.max_tool_steps
            tools = [*self.toolbox.definitions(), *propose_tool_definitions(turn.allowed_actions)]
            calls_made = 0
            capped = False
            proposal: dict | None = None
            while True:
                offer_tools = calls_made < max_calls
                step_text: list[str] = []
                calls = []
                # After the cap, the tools stay listed (some providers reject tool
                # history without them) but the model may not call any.
                choice = None if offer_tools else "none"
                for chunk in self.llm.stream_chat(messages, tools=tools, tool_choice=choice):
                    if chunk.text:
                        parts.append(chunk.text)
                        step_text.append(chunk.text)
                        yield sse("token", {"text": chunk.text})
                    if chunk.tool_call is not None and offer_tools:
                        calls.append(chunk.tool_call)
                if not calls:
                    break

                # A tool boundary: save the visible text so far, then the hidden
                # tool_calls row before its tool rows (AC-8, turn rules).
                self.chats.update_message_content(assistant.id, "".join(parts), interrupted=False)
                call_records = [{"id": c.id, "name": c.name, "arguments": c.arguments or {}} for c in calls]
                self.chats.append_message(turn.thread.id, "assistant", "".join(step_text), tool_calls=call_records)
                messages.append(self._to_llm(ChatMessage(thread_id=turn.thread.id, role="assistant", tool_calls=call_records)))
                propose_seen = False
                for call in calls:
                    action_type = action_type_for_tool(call.name)
                    if action_type is not None and action_type in turn.allowed_actions:
                        # Turn rules: only the first propose call of a step counts.
                        if propose_seen:
                            result = ONE_ACTION_RESULT
                        else:
                            propose_seen = True
                            try:
                                proposal = self.actions.propose(turn.project, action_type, call.arguments)
                                result = PROPOSED_RESULT
                            except ActionInvalid as exc:
                                # Not a proposal: the model reads the error and may
                                # try again, within the same budget as lookups.
                                calls_made += 1
                                result = json.dumps({"error": str(exc)})
                    elif calls_made >= max_calls:
                        # Every call needs an answer for the history to stay valid.
                        result = CAP_RESULT
                    else:
                        calls_made += 1
                        yield sse("tool", {"name": call.name, "state": "start"})
                        result = self.toolbox.run(turn.project, call.name, call.arguments)
                        yield sse("tool", {"name": call.name, "state": "end"})
                    self.chats.append_message(
                        turn.thread.id, "tool", "",
                        tool_name=call.name, tool_call_id=call.id, tool_args=call.arguments, tool_result=result,
                    )
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
                if proposal is not None:
                    # A proposal ends the turn (AC-10): the card waits for Run.
                    break
                if calls_made >= max_calls:
                    capped = True
                    messages.append({"role": "system", "content": CAP_INSTRUCTION})
            if capped and proposal is None:
                parts.append(CAP_NOTE)
                yield sse("token", {"text": CAP_NOTE})
            self.chats.update_message_content(assistant.id, "".join(parts), interrupted=False)
            if proposal is not None:
                self.chats.set_message_action(assistant.id, proposal)
                yield sse("action", {"message_id": str(assistant.id), "action": proposal})
            finished = True
            yield sse("done", {"assistant_message_id": str(assistant.id)})
        except LLMRateLimitTimeout:
            save_partial()
            finished = True
            yield sse("error", {"code": "llm_busy", "message": BUSY_MESSAGE, "retryable": True})
        except LLMServiceError as exc:
            logger.warning("Chat LLM call failed: %s", exc)
            save_partial()
            finished = True
            yield sse("error", {"code": "llm_error", "message": "The assistant could not answer. Try again.", "retryable": True})
        except Exception:
            logger.exception("Chat turn failed")
            save_partial()
            finished = True
            yield sse("error", {"code": "internal", "message": "Something went wrong. Try again.", "retryable": True})
        finally:
            # A client disconnect closes the generator: keep what was written.
            if not finished:
                try:
                    save_partial()
                except Exception:
                    logger.exception("Could not save the interrupted chat reply")
            with self._state_lock:
                if self._stream_semaphore is not None:
                    self._stream_semaphore.release()
            self._unlock_thread(turn.thread.id)

    def stream_help(self, user_id: UUID, content: str, history: list[dict] | None = None) -> Iterator[str]:
        """AC-3: answer from the built-in guide only; help turns are not persisted."""
        from uuid import uuid4

        from backend.services.llm import LLMRateLimitTimeout, LLMServiceError

        if not self._acquire_stream_slot():
            yield sse("error", {"code": "llm_busy", "message": BUSY_MESSAGE, "retryable": True})
            return
        user_message_id, assistant_message_id = str(uuid4()), str(uuid4())
        try:
            yield sse("message_start", {
                "user_message_id": user_message_id,
                "assistant_message_id": assistant_message_id,
            })
            messages = [{
                "role": "system",
                "content": (
                    "You are the application's help assistant. Answer only from the built-in guide below. "
                    "If asked about project data or to perform an action, ask the user to select a project. "
                    "Do not use external facts or invent features.\n\n" + self.guide()
                ),
            }]
            safe_history = []
            for item in (history or [])[-10:]:
                if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
                    continue
                text = item.get("content")
                if isinstance(text, str) and text.strip():
                    safe_history.append({"role": item["role"], "content": text[:4000]})
            messages.extend(safe_history)
            messages.append({"role": "user", "content": content})
            for chunk in self.llm.stream_chat(messages, tools=[]):
                if chunk.text:
                    yield sse("token", {"text": chunk.text})
            yield sse("done", {"assistant_message_id": assistant_message_id})
        except LLMRateLimitTimeout:
            yield sse("error", {"code": "llm_busy", "message": BUSY_MESSAGE, "retryable": True})
        except LLMServiceError:
            logger.warning("Chat help LLM call failed", exc_info=True)
            yield sse("error", {"code": "llm_error", "message": "The assistant could not answer. Try again.", "retryable": True})
        except Exception:
            logger.exception("Chat help turn failed")
            yield sse("error", {"code": "internal", "message": "Something went wrong. Try again.", "retryable": True})
        finally:
            with self._state_lock:
                if self._stream_semaphore is not None:
                    self._stream_semaphore.release()


    # ----------------------------------------------------------------- actions

    def _owned_action(self, project_id: UUID, user_id: UUID, message_id: UUID) -> ChatMessage:
        """The message holding the action, only for its thread's owner (404 otherwise)."""
        message = self.chats.get_message(message_id)
        if message is None or not message.action:
            raise ChatNotFound("Action not found")
        self.get_owned_thread(project_id, user_id, message.thread_id)
        return message

    def _conflict(self, message_id: UUID) -> ChatConflict:
        current = self.chats.get_message(message_id)
        status = (current.action or {}).get("status") if current else None
        return ChatConflict(CONFLICT_MESSAGES.get(status, "This action already ran"))

    def confirm_action(
        self,
        project,
        user_id: UUID,
        message_id: UUID,
        confirm_text: str | None,
        can: Callable[[str, str], bool],
    ) -> dict:
        """Confirm rules, in order: owner, permission (403), typed DELETE for
        deletes (422), targets and params recheck (422), then the atomic move
        out of `proposed` (409 if lost). Runs the saved params only (AC-12)."""
        message = self._owned_action(project.id, user_id, message_id)
        action = message.action
        permission = action.get("permission") or {}
        if not can(permission.get("module", ""), permission.get("action", "")):
            raise ChatForbidden(NO_PERMISSION_MESSAGE)
        if action.get("danger") == "delete" and confirm_text != DELETE_CONFIRM_TEXT:
            raise ChatInvalid("Type DELETE to confirm")
        try:
            self.actions.recheck(project, action)
        except ActionInvalid:
            raise ChatInvalid(CHANGED_MESSAGE) from None
        started = self.chats.compare_and_set_action_status(
            message_id, {"proposed"}, self.actions.start_status(action), {"confirmed_by": str(user_id)},
        )
        if started is None:
            raise self._conflict(message_id)
        self.actions.audit("confirmed", user_id=user_id, project_id=project.id, action=started)
        try:
            self.actions.start(self.chats, project, message_id, started, user_id)
        except Exception as exc:
            logger.exception("Chat action %s could not start", started.get("action_id"))
            return self.actions.finish(
                self.chats, message_id, started, "failed", {"error": f"Could not start: {exc}"[:300]},
                project_id=project.id,
            )
        current = self.chats.get_message(message_id)
        return current.action if current and current.action else started

    def cancel_action(self, project, user_id: UUID, message_id: UUID) -> dict:
        self._owned_action(project.id, user_id, message_id)
        cancelled = self.chats.compare_and_set_action_status(message_id, {"proposed"}, "cancelled")
        if cancelled is None:
            raise self._conflict(message_id)
        self.actions.audit("cancelled", user_id=user_id, project_id=project.id, action=cancelled)
        return cancelled

    def get_action(self, project, user_id: UUID, message_id: UUID) -> dict:
        """AC-15: the action, refreshed from its job while it is active."""
        message = self._owned_action(project.id, user_id, message_id)
        action = message.action
        if action.get("status") in ACTIVE:
            action = self.actions.refresh(self.chats, message_id, action, project_id=project.id)
        return action

    def recover_after_restart(self) -> int:
        """Startup recovery: replies cut off by the restart become interrupted,
        and every action still queued or running is refreshed once (inline
        work died with the process and becomes failed)."""
        self.chats.mark_empty_replies_interrupted()
        recovered = 0
        for message, project_id in self.chats.list_active_actions():
            try:
                self.actions.recover(self.chats, message.id, message.action, project_id=project_id)
                recovered += 1
            except Exception:
                logger.exception("Chat action recovery failed for message %s", message.id)
        return recovered


def _visible_reply_last(rows: list[ChatMessage]) -> list[ChatMessage]:
    """A turn's visible assistant row is created before its tool rows (it is
    the row the browser streams into), so its seq comes first. The LLM must see
    the calls and results before the answer built from them: move each visible
    reply after the rest of its turn."""
    ordered: list[ChatMessage] = []
    deferred: list[ChatMessage] = []
    for row in rows:
        if row.role == "user":
            ordered.extend(deferred)
            deferred = []
            ordered.append(row)
        elif row.role == "assistant" and not row.tool_calls:
            deferred.append(row)
        else:
            ordered.append(row)
    ordered.extend(deferred)
    return ordered


async def guard_disconnect(request, frames: Iterator[str]) -> AsyncIterator[str]:
    """Run the sync turn generator in the threadpool and stop it when the
    client goes away, so the partial reply is saved as interrupted."""
    from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

    try:
        async for frame in iterate_in_threadpool(frames):
            if await request.is_disconnected():
                break
            yield frame
    finally:
        close = getattr(frames, "close", None)
        if close is not None:
            await run_in_threadpool(close)
