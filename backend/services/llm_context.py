"""
Attribution context for LLM token usage records.

A contextvars.ContextVar carries who is calling the LLM (agent, operation,
project, run) without threading it through every agent signature. The HTTP
middleware in main.py sets the project and operation, WorkflowService adds a
run id and requirement, and agents that share an LLMService (script_generator
helpers) set the agent name.

Context propagates into asyncio tasks, asyncio.to_thread, Starlette
background tasks and LangGraph nodes. It does NOT propagate into a plain
ThreadPoolExecutor or threading.Thread: submit `contextvars.copy_context().run`
there (see `run_in_context`).
"""

import contextvars
import functools
import inspect
import re
from contextlib import contextmanager
from typing import Any, Callable, Iterator
from uuid import uuid4

_llm_call_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "llm_call_context", default={}
)


def current_llm_context() -> dict[str, Any]:
    """A copy of the active attribution fields."""
    return dict(_llm_call_context.get())


@contextmanager
def llm_call_context(**fields: Any) -> Iterator[dict[str, Any]]:
    """Merge fields into the active context for the duration of the block.
    None values are ignored, so an inner block never erases an outer value."""
    merged = {**_llm_call_context.get()}
    for key, value in fields.items():
        if value is not None:
            merged[key] = str(value) if key.endswith("_id") else value
    token = _llm_call_context.set(merged)
    try:
        yield merged
    finally:
        _llm_call_context.reset(token)


def llm_operation(operation: str) -> Callable:
    """Decorator for service methods: tags every LLM call made inside with the
    operation name, a fresh run id and any project/requirement/test case id
    found among the call's arguments."""

    def decorator(func: Callable) -> Callable:
        signature = inspect.signature(func)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                bound = signature.bind_partial(*args, **kwargs).arguments
            except TypeError:
                bound = {}
            with llm_call_context(
                operation=operation,
                run_id=uuid4(),
                project_id=bound.get("project_id"),
                requirement_id=bound.get("requirement_id"),
                test_case_id=bound.get("test_case_id"),
            ):
                return func(*args, **kwargs)

        return wrapper

    return decorator


_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_PROJECT_RE = re.compile(r"^/api/v1/projects/(" + _UUID_RE.pattern + r")")


class LLMContextMiddleware:
    """Pure ASGI middleware: every /api/v1 request runs inside an llm_call_context
    carrying the route as the operation and the project id from the URL, so LLM
    calls made by any route (reports, analysis, background tasks) are attributed."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope.get("type") == "http" else ""
        if not path.startswith("/api/v1/") or path.startswith("/api/v1/token-usage"):
            await self.app(scope, receive, send)
            return
        match = _PROJECT_RE.match(path)
        operation = f"{scope.get('method', '')} {_UUID_RE.sub('{id}', path[len('/api/v1'):])}"
        with llm_call_context(operation=operation, project_id=match.group(1) if match else None):
            await self.app(scope, receive, send)


def run_in_context(func: Callable) -> Callable:
    """Wrap func so it runs inside a copy of the caller's context. Use it for
    callables handed to ThreadPoolExecutor.map/submit."""
    ctx = contextvars.copy_context()

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # A Context cannot be entered twice concurrently, so copy per call.
        return ctx.copy().run(func, *args, **kwargs)

    return wrapper
