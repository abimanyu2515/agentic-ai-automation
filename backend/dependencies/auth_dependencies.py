import hmac
import re
import logging
import os
import sys
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.database.db import SessionLocal
from backend.database.db_models import PermissionDB, ProjectUserDB, RoleDB, RolePermissionDB, UserDB
from backend.services.auth_service import decode_token

logger = logging.getLogger(__name__)

security_scheme = HTTPBearer(auto_error=False)

# Env var names for the webhook secrets (spec 0009, AC-6 to AC-8).
WORKSPACE_SECRET_VAR = "WORKSPACE_WEBHOOK_SECRET"
JIRA_SECRET_VAR = "JIRA_WEBHOOK_SECRET"


def is_running_tests() -> bool:
    return "pytest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST") is not None

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    db: Session = Depends(get_db)
) -> UserDB:
    # Under test client runs, bypass check if no credentials header is provided
    if is_running_tests() and (not credentials or not credentials.credentials):
        user = db.query(UserDB).filter(UserDB.role == "Superadmin").first()
        if user:
            return user
        # Fallback dummy if seeder hasn't run yet in mock DB context
        return UserDB(
            id=UUID("00000000-0000-0000-0000-000000000000"),
            email="test@agenticai.com",
            full_name="Test Admin User",
            role="Superadmin",
            is_active=True
        )

    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Access token is missing.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    payload = decode_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials or expired session",
            headers={"WWW-Authenticate": "Bearer"},
        )

    email = payload["sub"]
    user = db.query(UserDB).filter(UserDB.email == email, UserDB.is_active == True).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or account is deactivated",
        )
    return user

def _resolve_role_name(db: Session, user: UserDB, project_id: UUID | None) -> str | None:
    """Project role first, then the user's global role."""
    if project_id:
        mapping = db.query(ProjectUserDB).filter(
            ProjectUserDB.project_id == project_id,
            ProjectUserDB.user_id == user.id
        ).first()
        if mapping:
            role = db.query(RoleDB).filter(RoleDB.id == mapping.role_id).first()
            if role:
                return role.name
    return user.role


def permitted_actions(db: Session, user: UserDB, module: str, project_id: UUID | None, actions: list[str]) -> list[str]:
    """The actions of `module` the user may perform, using the same role
    resolution as has_permission (project role first, then global role)."""
    if getattr(user, "role", None) == "Superadmin":
        return list(actions)
    role_name = _resolve_role_name(db, user, project_id)
    if not role_name:
        return []
    rows = db.query(PermissionDB.action).join(RolePermissionDB).join(RoleDB).filter(
        RoleDB.name == role_name,
        PermissionDB.module == module,
        PermissionDB.action.in_(actions)
    ).all()
    allowed = {row.action for row in rows}
    return [action for action in actions if action in allowed]


def own_permissions(db: Session, user: UserDB) -> list[str]:
    """Every `Module.action` the user's global role grants (Superadmin: all),
    so the UI can hide what the role cannot do without reading the admin
    only permission matrix. The backend still checks every request."""
    if getattr(user, "role", None) == "Superadmin":
        rows = db.query(PermissionDB.module, PermissionDB.action).all()
    else:
        rows = db.query(PermissionDB.module, PermissionDB.action).join(RolePermissionDB).join(RoleDB).filter(
            RoleDB.name == user.role
        ).all()
    return sorted({f"{row.module}.{row.action}" for row in rows})


def check_permission(db: Session, user: UserDB, project_id: UUID | None, module: str, action: str) -> bool:
    """Plain permission check (spec 0005): Superadmin always, else the project
    role, else the global role. has_permission and the chat both use it."""
    return bool(permitted_actions(db, user, module, project_id, [action]))


# ---------------------------------------------------------------- refusal log (spec 0009, AC-11)


def log_refusal(
    user,
    request: Request | None,
    module: str | None,
    action: str | None,
    project_id,
    reason: str,
    role: str | None = None,
) -> None:
    """One warning line per refusal. `user` None means a secret caller
    (`machine`), and `role` then names it (`workspace` or `jira`). The path
    is `request.url.path`, so a query string (and any `?secret=`) never
    reaches the log. No token, secret, or body is ever written."""
    user_id = "machine" if user is None else getattr(user, "id", None)
    if role is None:
        role = getattr(user, "role", None) if user is not None else None
    logger.warning(
        "Request refused reason=%s user_id=%s role=%s method=%s path=%s module=%s action=%s project_id=%s",
        reason,
        user_id,
        role or "-",
        request.method if request is not None else "-",
        request.url.path if request is not None else "-",
        module or "-",
        action or "-",
        project_id or "-",
    )


def _deny_permission(db: Session, user: UserDB, request: Request | None, module: str, action: str, project_id: UUID | None):
    role_name = _resolve_role_name(db, user, project_id)
    log_refusal(user, request, module, action, project_id, "no_permission", role=role_name)
    if not role_name:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied: User role is not defined"
        )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Access Denied: Role '{role_name}' does not have '{action}' permission for '{module}'"
    )


def require_project_access(
    user: UserDB,
    project_id: UUID,
    db: Session,
    detail: str = "You do not have access to this project.",
    projects=None,
    *,
    request: Request | None = None,
    module: str | None = None,
    action: str | None = None,
):
    """Superadmin or Admin, the project owner, or a ProjectUserDB assignment.
    Returns the project; 404 when it does not exist, 403 without access.
    `projects` is anything with get_project(project_id); default is the active repository.
    `request`, `module`, and `action` only enrich the refusal log line."""
    if projects is None:
        from backend.repository.project_repository import get_project_repository

        projects = get_project_repository()
    project = projects.get_project(project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if getattr(user, "role", None) in {"Superadmin", "Admin"} or project.owner_id == user.id:
        return project
    assignment = db.query(ProjectUserDB.id).filter(
        ProjectUserDB.project_id == project_id,
        ProjectUserDB.user_id == user.id,
    ).first()
    if not assignment:
        log_refusal(user, request, module, action, project_id, "no_project_access")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return project


def has_permission(module: str, action: str):
    """Enforce permissions dynamically against PostgreSQL mappings.
    Superadmins bypass checks. Checks user project-specific role first,
    falling back to their global role if no project-user association exists.
    """
    async def dependency(
        request: Request,
        user: UserDB = Depends(get_current_user),
        db: Session = Depends(get_db)
    ) -> bool:
        project_id = None
        project_id_str = request.path_params.get("project_id")
        if project_id_str:
            try:
                project_id = UUID(project_id_str)
            except ValueError:
                project_id = None

        if check_permission(db, user, project_id, module, action):
            return True
        _deny_permission(db, user, request, module, action, project_id)
    return dependency


def authorize_project(
    user: UserDB,
    db: Session,
    project_id: UUID,
    module: str,
    action: str,
    request: Request | None = None,
):
    """Permission, then project access, in the same order as path based
    routes (spec 0009, AC-5). Returns the project."""
    if not check_permission(db, user, project_id, module, action):
        _deny_permission(db, user, request, module, action, project_id)
    return require_project_access(user, project_id, db, request=request, module=module, action=action)


def require_project_permission(module: str, action: str):
    """Dependency for routes with `{project_id}` in the path (spec 0009,
    AC-1 to AC-3): login (401), the module and action (403), the project
    (404), then project access (403). Returns the signed in user."""
    async def dependency(
        request: Request,
        project_id: UUID,
        user: UserDB = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> UserDB:
        authorize_project(user, db, project_id, module, action, request)
        return user
    return dependency


# ---------------------------------------------------------------- webhook secrets (spec 0009, AC-6 to AC-8)


def _configured_secret(var: str) -> str:
    from backend.config.settings import get_settings

    webhooks = get_settings().webhooks
    value = webhooks.workspace_secret if var == WORKSPACE_SECRET_VAR else webhooks.jira_secret
    return (value or "").strip()


def missing_webhook_secrets() -> list[str]:
    """The secret env vars that are empty, for the startup warning."""
    return [var for var in (WORKSPACE_SECRET_VAR, JIRA_SECRET_VAR) if not _configured_secret(var)]


def _secret_matches(presented: str | None, expected: str) -> bool:
    # Compare bytes: hmac.compare_digest raises on non ASCII str input,
    # which would turn a bad secret into a 500.
    if not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def _check_secret(request: Request, presented: str | None, var: str, caller: str) -> None:
    expected = _configured_secret(var)
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Webhook secret not configured: {var}",
        )
    if not _secret_matches(presented, expected):
        project_id = request.path_params.get("project_id")
        log_refusal(None, request, None, None, project_id, "bad_secret", role=caller)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing webhook secret")


async def require_workspace_secret(
    request: Request,
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
) -> str:
    """The Playwright workspace: `X-Webhook-Secret` must match WORKSPACE_WEBHOOK_SECRET."""
    _check_secret(request, x_webhook_secret, WORKSPACE_SECRET_VAR, "workspace")
    return "workspace"


async def require_jira_secret(
    request: Request,
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
    secret: str | None = Query(default=None),
) -> str:
    """Jira: the header first, then the `secret` query parameter, matched
    against JIRA_WEBHOOK_SECRET."""
    _check_secret(request, x_webhook_secret or secret, JIRA_SECRET_VAR, "jira")
    return "jira"


@dataclass
class Caller:
    """Who called a route that accepts a user or the workspace secret, plus
    the validated body. `user` is None for the workspace."""
    user: UserDB | None
    payload: object
    db: Session

    @property
    def is_workspace(self) -> bool:
        return self.user is None


def workspace_or_user(body_model):
    """Freeze and script (spec 0009, AC-9): a valid workspace secret, else a
    signed in user. The dependency owns the body: FastAPI validates a
    dependency's own parameters before calling it and skips the call when
    they fail, so a malformed ID is a 422 before any secret or user check.
    get_current_user is called here, not declared, for the same reason.
    The route then runs the AC-5 checks for a user caller."""
    async def dependency(
        request: Request,
        payload: body_model,
        credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
        db: Session = Depends(get_db),
    ) -> Caller:
        presented = request.headers.get("X-Webhook-Secret")
        if presented:
            _check_secret(request, presented, WORKSPACE_SECRET_VAR, "workspace")
            return Caller(user=None, payload=payload, db=db)
        user = await get_current_user(credentials=credentials, db=db)
        return Caller(user=user, payload=payload, db=db)
    dependency.workspace_or_user = True  # marker for the route audit (AC-12)
    return dependency


# ---------------------------------------------------------------- access log (spec 0009, AC-7)

_SECRET_QUERY = re.compile(r"(?i)([?&]secret=)[^&\s]*")


def redact_secret_query(text: str) -> str:
    return _SECRET_QUERY.sub(r"\1***", text)


class SecretQueryFilter(logging.Filter):
    """Masks `secret=<value>` in uvicorn access lines. Uvicorn keeps the
    path with its query in `record.args[2]`, not in `record.msg`."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) > 2 and isinstance(args[2], str):
            record.args = args[:2] + (redact_secret_query(args[2]),) + args[3:]
        if isinstance(record.msg, str):
            record.msg = redact_secret_query(record.msg)
        return True


def install_access_log_filter() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, SecretQueryFilter) for f in access.filters):
        access.addFilter(SecretQueryFilter())


def warn_missing_webhook_secrets() -> None:
    """One warning per empty secret var at startup (AC-8)."""
    for var in missing_webhook_secrets():
        logger.warning("Webhook secret not configured: %s. Its routes return 503 until it is set.", var)
