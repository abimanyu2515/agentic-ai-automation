"""Route audit (spec 0009, AC-12): every data changing route must check who is
calling, either with a signed in user (get_current_user) or a webhook secret.

PENDING lists routes still open while a spec is being built. It may only
shrink: when a route gets its check, remove it (the second test fails until
you do). Spec 0009 emptied it; keep it empty."""

from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute

from backend.dependencies.auth_dependencies import (
    get_current_user,
    require_jira_secret,
    require_workspace_secret,
    workspace_or_user,
)
from backend.main import app

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Callers that prove who they are: a signed in user or a webhook secret.
IDENTITY_DEPENDENCIES = {get_current_user, require_workspace_secret, require_jira_secret}


def _is_identity(call) -> bool:
    # workspace_or_user builds one dependency per body model; each carries a marker.
    return call in IDENTITY_DEPENDENCIES or getattr(call, "workspace_or_user", False)


# Open by design: you can't sign in if signing in needs a login.
ALLOWED_OPEN = {
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
}

# Routes still open while a spec is being built (see the module docstring).
PENDING: set[tuple[str, str]] = set()


def _dependency_calls(dependant):
    for dependency in dependant.dependencies:
        yield dependency.call
        yield from _dependency_calls(dependency)


def unchecked_routes(application: FastAPI) -> set[tuple[str, str]]:
    """Every (method, path) of a data changing route with no identity dependency
    anywhere in its dependency tree."""
    found = set()
    for route in application.routes:
        if not isinstance(route, APIRoute):
            continue
        if any(_is_identity(call) for call in _dependency_calls(route.dependant)):
            continue
        for method in route.methods & MUTATING_METHODS:
            found.add((method, route.path))
    return found


# covers: AC-12
def test_every_data_changing_route_checks_its_caller():
    unexpected = unchecked_routes(app) - ALLOWED_OPEN - PENDING
    assert not unexpected, (
        "These data changing routes have no login or secret check. "
        "Add get_current_user (with has_permission) or a secret dependency: "
        f"{sorted(unexpected)}"
    )


# covers: AC-12
def test_pending_list_only_names_routes_that_are_still_open():
    closed = PENDING - unchecked_routes(app)
    assert not closed, f"These routes are checked now. Remove them from PENDING: {sorted(closed)}"


# covers: AC-12
def test_audit_catches_a_new_unauthenticated_post_route():
    probe = FastAPI()

    @probe.post("/api/v1/open-door")
    def open_door():
        return {}

    @probe.post("/api/v1/locked-door")
    def locked_door(user=Depends(get_current_user)):
        return {}

    assert unchecked_routes(probe) == {("POST", "/api/v1/open-door")}


# covers: AC-12
def test_audit_accepts_secret_and_workspace_dependencies():
    from pydantic import BaseModel

    class Body(BaseModel):
        value: int

    probe = FastAPI()

    @probe.post("/api/v1/workspace-door")
    def workspace_door(caller=Depends(require_workspace_secret)):
        return {}

    @probe.post("/api/v1/jira-door")
    def jira_door(caller=Depends(require_jira_secret)):
        return {}

    @probe.put("/api/v1/either-door")
    def either_door(caller=Depends(workspace_or_user(Body))):
        return {}

    assert unchecked_routes(probe) == set()
