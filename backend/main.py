"""
AI Test Automation Platform - FastAPI Backend

Purpose:
    Enterprise REST API for orchestrating AI-powered test automation workflows.
    Provides endpoints for project management, requirement ingestion, scenario generation,
    test case creation, Playwright script generation, execution, and JIRA integration.

Responsibilities:
    - FastAPI application initialization and CORS configuration
    - Database initialization and seed data loading
    - Credential vault migration from legacy encrypted fields
    - REST API endpoints for all workflow operations
    - Project CRUD operations with secure credential storage
    - Requirement import from files (PDF, Word, Excel, CSV) and JIRA
    - Scenario and test case generation via LangGraph workflow
    - Test execution orchestration with Playwright Runner
    - Dashboard metrics and analytics aggregation
    - Release and test cycle management
    - JIRA synchronization for user stories and bug tickets
    - Authentication, authorization, and audit logging

API Structure:
    /api/v1/projects
        - GET: List all projects
        - POST: Create new project
        - GET /{project_id}: Get project details
        - PUT /{project_id}: Update project settings
        
    /api/v1/projects/{project_id}/requirements
        - GET: List requirements
        - POST: Create manual requirement
        - POST /import: Import from file (PDF, DOCX, XLSX, CSV)
        - POST /import-jira: Import from JIRA issue
        
    /api/v1/projects/{project_id}/scenarios
        - GET: List scenarios
        - POST /{requirement_id}/generate-scenarios: Generate via LangGraph
        - PUT /{scenario_id}: Update scenario (approval, rejection)
        
    /api/v1/projects/{project_id}/test-cases
        - GET: List test cases
        - PUT /{test_case_id}: Update test case
        - POST /{test_case_id}/scripts: Update Playwright script
        
    /api/v1/projects/{project_id}/executions
        - POST: Execute test cases
        - GET /{execution_id}: Get execution result
        
    /api/v1/projects/{project_id}/dashboard-metrics
        - GET: Dashboard analytics (pass rate, coverage, trends)
        
    /api/v1/releases
        - GET: List releases
        - POST: Create release
        
    /api/v1/cycles
        - GET: List test cycles
        - POST: Create test cycle

Workflow Integration:
    API endpoints invoke WorkflowService which orchestrates LangGraph workflows:
    - ingest_requirement: RequirementAnalystAgent → FeatureInventoryAgent
    - generate_scenarios: ScenarioAgent → HumanApprovalAgent
    - generate_backlog: BacklogCreationAgent → ScenarioAgent
    - generate_test_cases: TestCaseAgent → EvaluationAgent → HumanApprovalAgent
    - execute_tests: ExecutionAgent → ExecutionAnalysisAgent → BugReportingAgent → ReportAgent

Security:
    - Credentials stored in encrypted vault (VaultService)
    - Project credentials migrated from legacy target_password_enc to vault
    - RBAC enforcement via dependency injection (get_current_user, has_permission)
    - Audit logging for all mutations

Dependencies:
    - ProjectService: Business logic for project/requirement/scenario/test case operations
    - WorkflowService: LangGraph workflow orchestration
    - VaultService: Encrypted credential storage
    - JiraService: JIRA API integration
    - AuthService: Authentication and authorization
    - AuditService: Audit log writing
"""

import yaml
import re
import mimetypes
from pathlib import Path
from typing import Literal, Optional, Union, List
from uuid import UUID
import json
import asyncio
import io
import logging
import os
import shlex
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Body, Query, status, BackgroundTasks, UploadFile, File, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.config.settings import get_settings
from backend.services.project_service import ProjectService
from backend.services.workflow_service import WorkflowService
from backend.services.grounded_playwright_service import GroundedPlaywrightScriptService
from backend.services.script_generation_jobs import ScriptGenerationJobs
from backend.schemas.project_dto import ProjectCreate, ProjectResponse
from backend.repository.project_repository import ProjectURLConflictError, get_project_repository
from backend.services.project_configuration import (
    ProjectConfigurationError,
    SUPPORTED_FRAMEWORKS,
    normalize_framework,
    normalize_target_url,
    readiness_for_project,
    redact_url_for_log,
)
from backend.schemas.requirement_dto import RequirementCreate, RequirementResponse
from backend.schemas.scenario_dto import ScenarioUpdate, ScenarioResponse
from backend.schemas.testcase_dto import TestCaseUpdate, TestCaseResponse, ScriptUpdatePayload, ScriptHealResponse
from backend.schemas.object_repository_dto import (
    ElementCreate,
    ElementDeleteResponse,
    ElementListResponse,
    ElementResponse,
    ElementUpdate,
    ElementUpdateResponse,
    HealDecisionResponse,
    HealResponse,
    PageCreate,
    PageDeleteResponse,
    PageListResponse,
    PageResponse,
    PageUpdate,
    PageUpdateResponse,
    ScanStartResponse,
    ScanStatusResponse,
)
from backend.services.object_repository_service import (
    ObjectRepositoryError,
    ObjectRepositoryService,
    ScriptRenderer,
)
from backend.services.object_repository_scan import ObjectRepositoryScanService
from backend.models.object_repository import HEAL_STATUSES
from backend.services.object_repository_healing import LocatorHealingService
from backend.services.script_repair_service import ScriptRepairService
from backend.models.script_heal import SCRIPT_HEAL_STATUSES
from backend.services.knowledge_service import (
    KnowledgeService,
    KnowledgeUploadError,
    knowledge_settings_overrides,
)
from backend.services.knowledge_indexer import queue_defect_keys, queue_knowledge_source, queue_test_case_removals
from backend.services.retrieval import set_retriever
from backend.services.chat_service import ChatError, ChatService, allowed_action_types, guard_disconnect
from backend.services.chat_actions import ChatActionExecutor, action_audit_fields
from backend.schemas.chat_dto import (
    ChatActionConfirm,
    ChatHelpCreate,
    ChatMessageCreate,
    ChatMessageItem,
    ChatMessagePage,
    ChatThreadItem,
    ChatThreadPage,
)
from backend.models.document import Document
from backend.models.execution_result import ExecutionResult, ExecutionStatus
import backend.services.report_service

from sqlalchemy.orm import Session
from backend.dependencies.auth_dependencies import (
    authorize_project, check_permission, get_db, own_permissions, get_current_user, has_permission, install_access_log_filter,
    permitted_actions, require_jira_secret, require_project_access, require_project_permission,
    require_workspace_secret, warn_missing_webhook_secrets, workspace_or_user, workspace_or_project_permission, Caller,
)
from backend.services.auth_service import hash_password, verify_password, create_access_token, create_refresh_token, decode_token
from backend.services.audit_service import log_audit
from backend.services.llm_context import LLMContextMiddleware
from backend.services.token_usage_service import get_token_usage_service
from backend.models.token_usage import TokenUsageFilters
from backend.schemas.token_usage_dto import ModelPriceRow, TokenUsageCallsPage, TokenUsageSettings
from backend.schemas.bug_report_dto import BugReportDismiss, BugReportUpdate
from backend.models.bug_report import BugReport, BugReportStatus
from backend.services.bug_report_service import BugReportError, BugReportService
from backend.models.accessibility import A11yBlockSeveritySetting, A11yGateModeSetting
from backend.services.accessibility_service import AccessibilityService
from backend.schemas.accessibility_dto import (
    AccessibilityGateResponse,
    AccessibilityScanPagesResponse,
    AccessibilityScanRequest,
    AccessibilityScanStartResponse,
)
from backend.services.accessibility_scan_service import AccessibilityScanError, AccessibilityScanService
from backend.utils.crypto import encrypt_value
from backend.database.db_models import UserDB, RoleDB, PermissionDB, RolePermissionDB, ProjectUserDB, RefreshTokenDB, AuditLogDB

# Apply LOG_LEVEL to the app's own loggers. Uvicorn only configures `uvicorn.*`,
# so without this the root logger stays at WARNING and every app INFO line is dropped.
# basicConfig does nothing when the root logger already has a handler.
logging.basicConfig(
    level=getattr(logging, str(get_settings().log_level).upper(), logging.INFO),
    format="%(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger("backend.project_creation")


def get_bug_report_service() -> BugReportService:
    """Route dependency; tests override it with app.dependency_overrides."""
    return BugReportService()


def get_accessibility_service() -> AccessibilityService:
    """Route dependency; tests override it with app.dependency_overrides."""
    return AccessibilityService()


def get_accessibility_scan_service() -> AccessibilityScanService:
    """Route dependency; tests override it with app.dependency_overrides."""
    return AccessibilityScanService()


# Windows Python installations may not register the JavaScript MIME type.
# Register it explicitly so browser module scripts are executed by StaticFiles.
mimetypes.add_type("application/javascript", ".js")

# ==========================================================
# FastAPI Application Initialization
# ==========================================================

app = FastAPI(
    title="AI Test Automation Platform API",
    description="Enterprise API backing the AI Test Automation Platform.",
    version="1.0.0"
)


# Attributes LLM token usage to the route and project of each API request.
app.add_middleware(LLMContextMiddleware)


def _configured_worker_count() -> int:
    """Best-effort worker count for warning about in-process chat controls."""
    counts = []
    for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS"):
        value = os.environ.get(name)
        if value:
            try:
                counts.append(int(value))
            except ValueError:
                logger.warning("Ignoring invalid %s value: %r", name, value)
    arguments = shlex.split(os.environ.get("UVICORN_CMD_ARGS", "")) + sys.argv[1:]
    for index, argument in enumerate(arguments):
        if argument == "--workers" and index + 1 < len(arguments):
            try:
                counts.append(int(arguments[index + 1]))
            except ValueError:
                logger.warning("Ignoring invalid uvicorn --workers value: %r", arguments[index + 1])
        elif argument.startswith("--workers="):
            try:
                counts.append(int(argument.split("=", 1)[1]))
            except ValueError:
                logger.warning("Ignoring invalid uvicorn --workers value: %r", argument)
    return max(counts, default=1)


def _warn_chat_multi_worker() -> None:
    if get_settings().chat.enabled and _configured_worker_count() > 1:
        logger.warning(
            "Chat turn locks and rate limits are process-local; multi-worker deployments do not share them"
        )


@app.middleware("http")
async def disable_frontend_asset_caching(request: Request, call_next):
    response = await call_next(request)
    if request.url.path in {"/", "/index.html"} or request.url.path.startswith("/js/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def project_create_validation_handler(request: Request, exc: RequestValidationError):
    if request.method == "POST" and request.url.path == "/api/v1/projects":
        missing_fields: list[str] = []
        invalid_fields: dict[str, str] = {}
        for error in exc.errors():
            location = error.get("loc", ())
            field = str(location[-1]) if location else "request"
            if error.get("type") == "missing":
                missing_fields.append(field)
            elif error.get("type") == "extra_forbidden":
                invalid_fields[field] = "field_not_permitted"
            else:
                invalid_fields[field] = "invalid_value"
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ProjectConfigurationError(
                "PROJECT_INVALID_CONFIGURATION",
                "Project creation input is invalid.",
                missing_fields=missing_fields,
                invalid_fields=invalid_fields,
            ).as_detail(),
        )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})

# Enable CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================================================
# Application Startup and Initialization
# ==========================================================

@app.on_event("startup")
def on_startup():
    """
    Initialize database, seed data, and migrate credentials to vault.
    
    Process:
        1. Create database tables (JSON provider only)
        2. Seed default data (users, roles, permissions)
        3. Migrate legacy project credentials to secure vault
    """
    from backend.database.db import SessionLocal, Base, engine, provider
    import backend.database.db_models  # Ensure models are loaded and registered with Base metadata
    from backend.database.db_seeder import seed_database
    if provider == "json":
        Base.metadata.create_all(engine)
    # Spec 0009: mask `?secret=` in access lines and warn about empty secrets.
    install_access_log_filter()
    warn_missing_webhook_secrets()
    db = SessionLocal()
    try:
        seed_database(db)
    finally:
        db.close()
    try:
        migrate_credentials_to_vault()
    except Exception as e:
        print(f"[Vault] Migration failed: {e}")

    # Chat assistant (spec 0005): actions whose worker died with the last
    # process become failed, and replies cut off by it become interrupted.
    _warn_chat_multi_worker()
    if get_settings().chat.enabled:
        try:
            chat_service.purge_idle()
            chat_service.recover_after_restart()
        except Exception:
            logging.getLogger(__name__).exception("Chat startup recovery failed")

    # Accessibility (spec 0011, AC-10): scans a restart left queued or running
    # past the stale limit are closed as timed out.
    try:
        AccessibilityScanService().expire_stale()
    except Exception:
        logging.getLogger(__name__).exception("Closing stale accessibility scans failed")

    # Knowledge base (spec 0006): the one time old document migration, the
    # indexer worker, and the sweep of documents a restart left behind.
    knowledge_service.on_startup()

    # Batch identity (spec 0012, AC-10): old JSON batch runs move their batch
    # id out of test_cycle_id, once.
    try:
        moved = get_project_repository().run_batch_identity_migration_0012()
        if moved:
            logging.getLogger(__name__).info("Batch identity migration 0012 moved %s run(s)", moved)
    except Exception:
        logging.getLogger(__name__).exception("Batch identity migration 0012 failed")

    # Launch JIRA Sync Retry Worker loop in event loop
    from backend.services.jira_sync_service import jira_retry_worker
    import asyncio
    asyncio.create_task(jira_retry_worker())

# Initialize application services
project_service = ProjectService()
workflow_service = WorkflowService()
# Calls through the module global so tests can swap workflow_service.
script_generation_jobs = ScriptGenerationJobs(
    lambda project_id, test_case_id, framework: workflow_service.generate_playwright_script(
        project_id, test_case_id, automation_framework=framework
    )
)
grounded_playwright_service = GroundedPlaywrightScriptService()
object_repository_service = ObjectRepositoryService()
knowledge_service = KnowledgeService()
object_repository_scan_service = ObjectRepositoryScanService(object_repository_service)
locator_healing_service = LocatorHealingService()
script_repair_service = ScriptRepairService()


def _script_heal_response(heal) -> ScriptHealResponse:
    return ScriptHealResponse(**heal.model_dump(exclude={"project_id"}))


def _latest_script_heals(project_id: UUID) -> dict:
    """Each test case's newest script heal, read once for a list response."""
    latest: dict = {}
    try:
        for heal in script_repair_service.store.list_heals(project_id):
            latest.setdefault(heal.test_case_id, heal)  # newest first
    except Exception:
        logger.warning("Could not read script heals: project_id=%s", project_id)
    return latest


def _test_case_response(tc, renderer: ScriptRenderer | None = None, latest_heals: dict | None = None) -> TestCaseResponse:
    """The one TestCase response mapper. Scripts are rendered with the current
    object repository locators; the project comes from the test case itself.
    `latest_heals` maps test case id to its newest script heal (list responses
    read it once); without it the store is read for this one case."""
    renderer = renderer or object_repository_service.renderer()
    if latest_heals is None:
        try:
            latest_heal = script_repair_service.store.latest_for_test_case(tc.id)
        except Exception:
            latest_heal = None
    else:
        latest_heal = latest_heals.get(tc.id)
    return TestCaseResponse(
        latest_script_heal=_script_heal_response(latest_heal) if latest_heal else None,
        id=tc.id,
        scenario_id=tc.scenario_id,
        test_case_ref_id=tc.test_case_ref_id,
        title=tc.title,
        preconditions=tc.preconditions,
        steps=tc.steps,
        expected_result=tc.expected_result,
        priority=tc.priority,
        status=tc.status,
        confidence=tc.confidence,
        evaluation_status=tc.evaluation_status,
        evaluation_reason=tc.evaluation_reason,
        playwright_script=renderer.render(tc),
        script_validation_status=tc.script_validation_status,
        script_validation_attempts=tc.script_validation_attempts,
        script_validation_completed_at=tc.script_validation_completed_at,
        script_validation_diagnostic=tc.script_validation_diagnostic,
        groundedness_score=getattr(tc, "groundedness_score", None),
        unsupported_claims=list(getattr(tc, "unsupported_claims", None) or []),
        generated_at=tc.generated_at,
        jira_issue_key=getattr(tc, "jira_issue_key", None),
        jira_issue_url=getattr(tc, "jira_issue_url", None),
        jira_sync_status=getattr(tc, "jira_sync_status", None),
        jira_last_synced_at=getattr(tc, "jira_last_synced_at", None),
        test_data_sets=tc.test_data_sets or [],
    )


def resolve_scenario_response(s) -> ScenarioResponse:
    """Convert Scenario domain object to ScenarioResponse DTO with JIRA metadata."""
    return ScenarioResponse(
        id=s.id,
        requirement_id=s.requirement_id,
        scenario_ref_id=s.scenario_ref_id,
        scenario_name=s.scenario_name,
        description=s.description,
        priority=s.priority,
        confidence=s.confidence,
        approved=s.approved,
        rejected=s.rejected,
        generated_at=s.generated_at,
        reviewer=s.reviewer,
        approved_at=s.approved_at,
        jira_issue_key=getattr(s, "jira_issue_key", None),
        jira_issue_id=getattr(s, "jira_issue_id", None),
        jira_issue_url=getattr(s, "jira_issue_url", None),
        jira_sync_status=getattr(s, "jira_sync_status", None),
        jira_last_synced_at=getattr(s, "jira_last_synced_at", None),
        last_jira_sync_at=getattr(s, "last_jira_sync_at", None),
        last_jira_sync_status=getattr(s, "last_jira_sync_status", None),
        last_jira_sync_error=getattr(s, "last_jira_sync_error", None),
        jira_sync_retry_count=getattr(s, "jira_sync_retry_count", 0)
    )

def resolve_project_response(p, all_projects=None) -> ProjectResponse:
    """
    Convert Project domain object to ProjectResponse DTO with decrypted credentials.

    Args:
        p: Project domain object
        all_projects: projects already loaded by the caller. The list route passes
            them so it does not load every project once per project.

    Returns:
        ProjectResponse DTO with username from vault (password masked)
        
    Security:
        Passwords are never returned in API responses.
        Username is decrypted from vault for display purposes.
    """
    from backend.services.vault_service import VaultService
    vs = VaultService()
    username, _ = vs.get_credentials(str(p.id))
    owner_projects = [
        candidate
        for candidate in (project_service.list_projects() if all_projects is None else all_projects)
        if getattr(candidate, "owner_id", None) == getattr(p, "owner_id", None)
    ]
    return ProjectResponse(
        id=p.id,
        name=p.name,
        description=p.description,
        line_of_business=p.line_of_business,
        framework=getattr(p, "framework", "playwright"),
        created_at=p.created_at,
        requirements=p.requirements,
        jira_project_key=getattr(p, "jira_project_key", None),
        target_url=getattr(p, "target_url", None),
        target_username=username if username else (getattr(p, "target_username", None) if getattr(p, "target_username", None) != "vault_secured" else None),
        is_safe_test_environment=getattr(p, "is_safe_test_environment", False),
        bug_auto_submit=getattr(p, "bug_auto_submit", False),
        bug_auto_submit_min_confidence=getattr(p, "bug_auto_submit_min_confidence", 0.85),
        self_healing_enabled=getattr(p, "self_healing_enabled", True),
        a11y_enabled=getattr(p, "a11y_enabled", True),
        a11y_gate_mode=getattr(p, "a11y_gate_mode", "warn"),
        a11y_block_severity=getattr(p, "a11y_block_severity", "serious"),
        readiness=readiness_for_project(p, owner_projects),
    )

def migrate_credentials_to_vault():
    """
    One-time migration of legacy encrypted credentials to secure vault.
    
    Migrates project credentials from target_password_enc field to VaultService.
    Marks migrated projects with "vault_secured" sentinel value.
    
    Process:
        1. Iterate all projects
        2. Check if target_password_enc != "vault_secured"
        3. Decrypt legacy password
        4. Store in vault via VaultService
        5. Update project with sentinel values
        6. Log migration success
        
    Idempotency:
        Safe to run multiple times. Only migrates non-vault projects.
    """
    from backend.services.vault_service import VaultService
    from backend.utils.crypto import decrypt_value
    vs = VaultService()
    repo = project_service.repo
    try:
        projects = repo.list_projects()
        migrated = False
        for p in projects:
            if getattr(p, "target_password_enc", None) != "vault_secured":
                username = getattr(p, "target_username", None)
                password = None
                if getattr(p, "target_password_enc", None):
                    password = decrypt_value(p.target_password_enc)
                vs.set_credentials(str(p.id), username, password)
                p.target_username = ""
                p.target_password_enc = ""
                repo.update_project(p)
                migrated = True
    except Exception:
        repo.rollback()
        raise
    if migrated:
        print("[Vault] Successfully migrated project credentials to secure vault.")


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    line_of_business: str | None = None
    framework: str | None = None
    jira_project_key: str | None = None
    target_url: str | None = None
    target_username: str | None = None
    target_password: str | None = None
    is_safe_test_environment: bool | None = None
    # Bug Reporting Agent: file drafts to Jira without review at or above this triage confidence.
    bug_auto_submit: bool | None = None
    bug_auto_submit_min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    # Spec 0011: heal broken object repository locators after a run.
    self_healing_enabled: bool | None = None
    # Accessibility Agent (spec 0011, AC-13): a null leaves the value unchanged.
    a11y_enabled: bool | None = None
    a11y_gate_mode: A11yGateModeSetting | None = None
    a11y_block_severity: A11yBlockSeveritySetting | None = None


@app.get("/api/v1/projects",response_model=list[ProjectResponse])
def list_projects():
    projects = project_service.list_projects()
    return [resolve_project_response(p, projects) for p in projects]


@app.post("/api/v1/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    data: ProjectCreate,
    user: UserDB = Depends(get_current_user),
    authorized: bool = Depends(has_permission("Projects", "create")),
):
    if not data.name.strip():
        return JSONResponse(
            status_code=422,
            content=ProjectConfigurationError(
                "PROJECT_INVALID_CONFIGURATION",
                "Project name cannot be empty.",
                invalid_fields={"name": "required"},
            ).as_detail(),
        )
    if not data.description.strip():
        return JSONResponse(
            status_code=422,
            content=ProjectConfigurationError(
                "PROJECT_INVALID_CONFIGURATION",
                "Project description cannot be empty.",
                invalid_fields={"description": "required"},
            ).as_detail(),
        )
    if not getattr(user, "id", None):
        raise HTTPException(status_code=401, detail="Authenticated user has no valid owner identity.")
    framework = normalize_framework(data.framework)
    if framework not in SUPPORTED_FRAMEWORKS:
        return JSONResponse(
            status_code=422,
            content=ProjectConfigurationError(
                "PROJECT_INVALID_CONFIGURATION",
                "Project framework is not supported.",
                invalid_fields={"framework": "unsupported_framework"},
            ).as_detail(),
        )
    try:
        target_url = normalize_target_url(data.target_url)
        if target_url is None:
            # A blank URL normalizes to None ("not set"), but creation requires one (AC-2).
            raise ProjectConfigurationError(
                "PROJECT_INVALID_CONFIGURATION",
                "Target URL is required.",
                missing_fields=["target_url"],
            )
        p = project_service.create_project(
            name=data.name,
            description=data.description,
            line_of_business=data.line_of_business,
            framework=framework,
            jira_project_key=data.jira_project_key,
            target_url=target_url,
            owner_id=user.id,
            created_by=user.id,
        )
        logger.info(
            "Project created: project_id=%s owner_id=%s target_url=%s",
            p.id, user.id, redact_url_for_log(target_url),
        )
    except ProjectConfigurationError as exc:
        logger.warning("Project creation validation failed: code=%s fields=%s", exc.code, list(exc.invalid_fields))
        return JSONResponse(status_code=exc.status_code, content=exc.as_detail())
    except ProjectURLConflictError as exc:
        logger.warning("Project creation URL conflict: owner_id=%s", user.id)
        detail = {
            "code": "PROJECT_URL_CONFLICT",
            "message": str(exc),
            "invalid_fields": {"target_url": "duplicate_url"},
        }
        if exc.conflicting_project_id is not None:
            detail["conflicting_project_id"] = str(exc.conflicting_project_id)
        return JSONResponse(status_code=409, content=detail)
    except Exception as exc:
        # The repository has already rolled back and cleared its session (PostgreSQL)
        # or left the JSON file untouched (atomic replace). Log only the error type:
        # database messages can echo row values, including the URL query text (AC-9).
        logger.error(
            "Project creation failed: owner_id=%s error=%s", user.id, type(exc).__name__
        )
        return JSONResponse(
            status_code=500,
            content={
                "code": "PROJECT_PERSISTENCE_FAILED",
                "message": "The project could not be saved, so no project was created. Try again, or contact an administrator if it keeps failing.",
            },
        )
    return resolve_project_response(p)


@app.put("/api/v1/projects/{project_id}", response_model=ProjectResponse)
def update_project_settings(
    project_id: UUID,
    data: ProjectUpdate,
    user: UserDB = Depends(get_current_user),
    authorized: bool = Depends(has_permission("Projects", "edit")),
):
    p = project_service.get_project(project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    if data.name is not None:
        p.name = data.name
    if data.description is not None:
        p.description = data.description
    if data.line_of_business is not None:
        p.line_of_business = data.line_of_business
    if data.framework is not None:
        p.framework = data.framework
    if data.jira_project_key is not None:
        p.jira_project_key = data.jira_project_key
    if data.target_url is not None:
        p.target_url = data.target_url
    if data.is_safe_test_environment is not None:
        p.is_safe_test_environment = data.is_safe_test_environment
    if data.bug_auto_submit is not None:
        p.bug_auto_submit = data.bug_auto_submit
    if data.bug_auto_submit_min_confidence is not None:
        p.bug_auto_submit_min_confidence = data.bug_auto_submit_min_confidence
    if data.self_healing_enabled is not None:
        p.self_healing_enabled = data.self_healing_enabled
    
    if data.a11y_enabled is not None:
        p.a11y_enabled = data.a11y_enabled
    if data.a11y_gate_mode is not None:
        p.a11y_gate_mode = data.a11y_gate_mode
    if data.a11y_block_severity is not None:
        p.a11y_block_severity = data.a11y_block_severity

    if data.target_username is not None or (data.target_password is not None and data.target_password != "" and data.target_password != "********"):
        from backend.services.vault_service import VaultService
        vs = VaultService()
        curr_u, curr_p = vs.get_credentials(str(project_id))
        new_u = data.target_username if data.target_username is not None else curr_u
        new_p = data.target_password if (data.target_password is not None and data.target_password != "" and data.target_password != "********") else curr_p
        vs.set_credentials(str(project_id), new_u, new_p)
    
    p.target_username = "vault_secured"
    p.target_password_enc = "vault_secured"
    
    try:
        updated = project_service.repo.update_project(p)
    except ProjectConfigurationError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.as_detail())
    except ProjectURLConflictError as exc:
        detail = {
            "code": "PROJECT_URL_CONFLICT",
            "message": str(exc),
            "invalid_fields": {"target_url": "duplicate_url"},
        }
        if exc.conflicting_project_id is not None:
            detail["conflicting_project_id"] = str(exc.conflicting_project_id)
        return JSONResponse(status_code=409, content=detail)
    return resolve_project_response(updated)


@app.get("/api/v1/projects/{project_id}", response_model=ProjectResponse)
def get_project(project_id: UUID):
    p = project_service.get_project(project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    return resolve_project_response(p)
class DashboardRecentExecution(BaseModel):
    id: UUID
    test_case_id: UUID
    tcName: str
    tcCustomId: str
    status: str
    duration_seconds: float
    executed_at: str

class DashboardMetricsResponse(BaseModel):
    totalReqs: int
    totalScenarios: int
    totalTestCases: int
    totalExecutions: int
    passRatio: str
    coveragePct: str
    uiCount: int
    apiCount: int
    manualCount: int
    
    passedCount: int
    failedCount: int
    yetToExecuteCount: int
    
    approvedCount: int
    rejectedCount: int
    pendingCount: int
    
    trendLabels: list[str]
    trendPassed: list[int]
    trendFailed: list[int]
    
    domainLabels: list[str]
    domainCountsData: list[int]
    
    priorityHighCount: int
    priorityMediumCount: int
    priorityLowCount: int
    
    defectOpenCount: int
    defectResolvedCount: int
    defectRetestCount: int
    defectClosedCount: int
    
    recentExecutions: list[DashboardRecentExecution]
    isEmpty: bool

class ReleaseCreate(BaseModel):
    name: str
    description: str | None = None
    status: str = "Active"
    start_date: datetime | None = None
    end_date: datetime | None = None

class ReleaseResponse(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    description: str | None = None
    status: str
    start_date: datetime | None = None
    end_date: datetime | None = None

class TestCycleCreate(BaseModel):
    name: str
    description: str | None = None
    status: str = "Active"

class TestCycleResponse(BaseModel):
    id: UUID
    release_id: UUID
    name: str
    description: str | None = None
    status: str
    created_at: datetime


@app.get("/api/v1/projects/{project_id}/dashboard-metrics", response_model=DashboardMetricsResponse)
def get_dashboard_metrics(project_id: UUID, scope: str = "project", id: str | None = None):
    from backend.services.dashboard_metrics import compute_dashboard_metrics

    if not project_service.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return DashboardMetricsResponse(**compute_dashboard_metrics(project_service.repo, project_id, scope, id))


@app.get("/api/v1/dashboard/all-metrics", response_model=DashboardMetricsResponse)
def get_all_projects_dashboard_metrics():
    all_projects = project_service.repo.list_projects()
    if not all_projects:
        empty_resp = DashboardMetricsResponse(
            totalReqs=0, totalScenarios=0, totalTestCases=0, totalExecutions=0,
            passRatio="0%", coveragePct="0%", uiCount=0, apiCount=0, manualCount=0,
            passedCount=0, failedCount=0, yetToExecuteCount=0,
            approvedCount=0, rejectedCount=0, pendingCount=0,
            trendLabels=[], trendPassed=[], trendFailed=[],
            domainLabels=[], domainCountsData=[],
            priorityHighCount=0, priorityMediumCount=0, priorityLowCount=0,
            defectOpenCount=0, defectResolvedCount=0, defectRetestCount=0, defectClosedCount=0,
            recentExecutions=[], isEmpty=True
        )
        return empty_resp

    all_reqs = []
    all_scenarios = []
    all_test_cases = []
    all_executions = []
    for p in all_projects:
        all_reqs.extend(project_service.get_requirements(p.id))
        all_scenarios.extend(project_service.get_scenarios_for_project(p.id))
        all_test_cases.extend(project_service.get_test_cases_for_project(p.id))
        all_executions.extend(project_service.repo.get_execution_results(p.id))

    is_empty = len(all_reqs) == 0 and len(all_executions) == 0

    totalReqs = len(all_reqs)
    totalScenarios = len(all_scenarios)
    totalTestCases = len(all_test_cases)
    totalExecutions = len(all_executions)

    passed_execs = [ex for ex in all_executions if ex.status == "passed" or (hasattr(ex.status, "value") and ex.status.value == "passed")]
    passedExecutions = len(passed_execs)
    passRatio = f"{int(round((passedExecutions / totalExecutions) * 100))}%" if totalExecutions > 0 else "0%"

    manualCount = 0
    apiCount = 0
    uiCount = 0
    for tc in all_test_cases:
        exec_type = getattr(tc, "execution_type", None) or "Automated Testing"
        if exec_type == "Manual Testing":
            manualCount += 1
        elif "api" in tc.title.lower() or (tc.steps and any("api" in s.lower() for s in tc.steps)):
            apiCount += 1
        else:
            uiCount += 1
    automatedCount = uiCount + apiCount
    coveragePct = f"{int(round((automatedCount / totalTestCases) * 100))}%" if totalTestCases > 0 else "0%"

    passedCount = 0
    failedCount = 0
    yetToExecuteCount = 0
    for tc in all_test_cases:
        tc_execs = [ex for ex in all_executions if str(ex.test_case_id) == str(tc.id)]
        if not tc_execs:
            yetToExecuteCount += 1
        else:
            sorted_execs = sorted(tc_execs, key=lambda x: x.executed_at, reverse=True)
            latest = sorted_execs[0]
            status_val = latest.status.value if hasattr(latest.status, "value") else latest.status
            if status_val == "passed":
                passedCount += 1
            else:
                failedCount += 1

    approvedCount = len([s for s in all_scenarios if s.approved])
    rejectedCount = len([s for s in all_scenarios if getattr(s, "rejected", False)])
    pendingCount = len(all_scenarios) - approvedCount - rejectedCount

    dailyData = {}
    for ex in all_executions:
        if not ex.executed_at:
            continue
        date_str = ex.executed_at.strftime("%b %d")
        if date_str not in dailyData:
            dailyData[date_str] = {"passed": 0, "failed": 0}
        status_val = ex.status.value if hasattr(ex.status, "value") else ex.status
        if status_val == "passed":
            dailyData[date_str]["passed"] += 1
        else:
            dailyData[date_str]["failed"] += 1
    sorted_dates = sorted(dailyData.keys(), key=lambda d: datetime.strptime(d + f" {datetime.now().year}", "%b %d %Y"))
    recent_dates = sorted_dates[-7:]
    trendPassed = [dailyData[d]["passed"] for d in recent_dates]
    trendFailed = [dailyData[d]["failed"] for d in recent_dates]

    domainCounts = {}
    for r in all_reqs:
        dom = r.business_domain or "general"
        domainCounts[dom] = domainCounts.get(dom, 0) + 1
    domainLabels = list(domainCounts.keys())
    domainCountsData = list(domainCounts.values())

    priorityHighCount = 0
    priorityMediumCount = 0
    priorityLowCount = 0
    for tc in all_test_cases:
        prio = (tc.priority or "medium").lower()
        if prio == "high":
            priorityHighCount += 1
        elif prio == "low":
            priorityLowCount += 1
        else:
            priorityMediumCount += 1

    defectOpenCount = 0
    defectResolvedCount = 0
    defectRetestCount = 0
    defectClosedCount = 0
    for ex in all_executions:
        status_val = ex.status.value if hasattr(ex.status, "value") else ex.status
        if status_val == "failed":
            if getattr(ex, "retest_pending_candidate", False):
                defectRetestCount += 1
            else:
                defectOpenCount += 1
        elif status_val == "passed":
            if getattr(ex, "retest_pending_candidate", False) or getattr(ex, "jira_bug_id", None):
                defectResolvedCount += 1
            else:
                defectClosedCount += 1

    sorted_executions = sorted(all_executions, key=lambda x: x.executed_at, reverse=True)[:5]
    recent_execs_list = []
    for ex in sorted_executions:
        tc = next((t for t in all_test_cases if str(t.id) == str(ex.test_case_id)), None)
        tc_name = tc.title if tc else "Unknown Test Case"
        tc_custom_id = getattr(tc, "custom_id", None) or (f"TC-{str(tc.id)[:4].upper()}" if tc else "TC-XXX")
        status_val = ex.status.value if hasattr(ex.status, "value") else ex.status
        recent_execs_list.append(
            DashboardRecentExecution(
                id=ex.id,
                test_case_id=ex.test_case_id,
                tcName=tc_name,
                tcCustomId=tc_custom_id,
                status=status_val,
                duration_seconds=ex.duration_seconds,
                executed_at=ex.executed_at.strftime("%I:%M %p") if ex.executed_at else "N/A"
            )
        )

    return DashboardMetricsResponse(
        totalReqs=totalReqs,
        totalScenarios=totalScenarios,
        totalTestCases=totalTestCases,
        totalExecutions=totalExecutions,
        passRatio=passRatio,
        coveragePct=coveragePct,
        uiCount=uiCount,
        apiCount=apiCount,
        manualCount=manualCount,
        passedCount=passedCount,
        failedCount=failedCount,
        yetToExecuteCount=yetToExecuteCount,
        approvedCount=approvedCount,
        rejectedCount=rejectedCount,
        pendingCount=pendingCount,
        trendLabels=recent_dates,
        trendPassed=trendPassed,
        trendFailed=trendFailed,
        domainLabels=domainLabels,
        domainCountsData=domainCountsData,
        priorityHighCount=priorityHighCount,
        priorityMediumCount=priorityMediumCount,
        priorityLowCount=priorityLowCount,
        defectOpenCount=defectOpenCount,
        defectResolvedCount=defectResolvedCount,
        defectRetestCount=defectRetestCount,
        defectClosedCount=defectClosedCount,
        recentExecutions=recent_execs_list,
        isEmpty=is_empty
    )


@app.get("/api/v1/auth/me")
def get_me(user: UserDB = Depends(get_current_user), db: Session = Depends(get_db)):
    # `permissions` lets the UI hide actions for any role (spec 0009, AC-10).
    return {
        "id": str(user.id), "email": user.email, "full_name": user.full_name, "role": user.role,
        "permissions": own_permissions(db, user),
    }


# ==========================================================
# External Tools Integration
# ==========================================================

class ExternalToolConfig(BaseModel):
    tool_type: str
    tool_name: str
    api_key: str
    api_url: str
    description: str | None = None


class ExternalToolResponse(BaseModel):
    id: str
    tool_type: str
    tool_name: str
    api_url: str
    description: str | None = None


def get_env_file_path():
    """Get the path to the .env file in the project root."""
    from pathlib import Path
    project_root = Path(__file__).parent.parent
    return project_root / ".env"


def load_external_tools_from_env():
    """Load external tools configurations from .env file."""
    env_path = get_env_file_path()
    tools = []
    
    if not env_path.exists():
        return tools
    
    import os
    from dotenv import load_dotenv
    
    # Load environment variables
    load_dotenv(env_path)
    
    # Look for external tool patterns in env vars
    # Pattern: EXTERNAL_TOOL_{tool_type}_{tool_name}_{property}
    tool_configs = {}
    
    for key, value in os.environ.items():
        if key.startswith('EXTERNAL_TOOL_'):
            parts = key.split('_')
            if len(parts) >= 4:
                tool_type = parts[2]
                tool_name = parts[3]
                property_name = '_'.join(parts[4:]) if len(parts) > 4 else ''
                
                config_key = f"{tool_type}_{tool_name}"
                if config_key not in tool_configs:
                    tool_configs[config_key] = {
                        'tool_type': tool_type,
                        'tool_name': tool_name,
                        'api_key': '',
                        'api_url': '',
                        'description': ''
                    }
                
                if property_name == 'API_KEY':
                    tool_configs[config_key]['api_key'] = value
                elif property_name == 'API_URL':
                    tool_configs[config_key]['api_url'] = value
                elif property_name == 'DESCRIPTION':
                    tool_configs[config_key]['description'] = value
    
    # Convert to response format
    for config_key, config in tool_configs.items():
        if config['api_key'] and config['api_url']:
            tools.append(ExternalToolResponse(
                id=config_key,
                tool_type=config['tool_type'],
                tool_name=config['tool_name'],
                api_url=config['api_url'],
                description=config.get('description')
            ))
    
    return tools


def save_external_tool_to_env(tool_config: ExternalToolConfig):
    """Save external tool configuration to .env file."""
    env_path = get_env_file_path()
    
    # Read existing .env content
    existing_content = ""
    if env_path.exists():
        with open(env_path, 'r') as f:
            existing_content = f.read()
    
    # Generate env variable names
    tool_key = f"{tool_config.tool_type}_{tool_config.tool_name}"
    api_key_var = f"EXTERNAL_TOOL_{tool_key}_API_KEY"
    api_url_var = f"EXTERNAL_TOOL_{tool_key}_API_URL"
    description_var = f"EXTERNAL_TOOL_{tool_key}_DESCRIPTION"
    
    # Check if these variables already exist and remove them
    lines = existing_content.split('\n')
    filtered_lines = []
    skip_next = False
    
    for line in lines:
        if line.startswith(api_key_var) or line.startswith(api_url_var) or line.startswith(description_var):
            continue
        filtered_lines.append(line)
    
    # Add new configuration
    new_lines = [
        f"# External Tool: {tool_config.tool_name} ({tool_config.tool_type})",
        f"{api_key_var}={tool_config.api_key}",
        f"{api_url_var}={tool_config.api_url}",
    ]
    
    if tool_config.description:
        new_lines.append(f"{description_var}={tool_config.description}")
    
    new_lines.append("")  # Empty line for separation
    
    # Write back to .env
    with open(env_path, 'w') as f:
        f.write('\n'.join(filtered_lines))
        f.write('\n')
        f.write('\n'.join(new_lines))


def delete_external_tool_from_env(tool_id: str):
    """Delete external tool configuration from .env file."""
    env_path = get_env_file_path()
    
    if not env_path.exists():
        return
    
    # Parse tool_id to get tool_type and tool_name
    parts = tool_id.split('_')
    if len(parts) < 2:
        return
    
    tool_type = parts[0]
    tool_name = '_'.join(parts[1:])
    
    api_key_var = f"EXTERNAL_TOOL_{tool_id}_API_KEY"
    api_url_var = f"EXTERNAL_TOOL_{tool_id}_API_URL"
    description_var = f"EXTERNAL_TOOL_{tool_id}_DESCRIPTION"
    
    # Read existing .env content
    with open(env_path, 'r') as f:
        lines = f.readlines()
    
    # Filter out lines related to this tool
    filtered_lines = []
    skip_comment = False
    
    for line in lines:
        if line.startswith(f"# External Tool:"):
            # Check if this comment is for our tool
            if tool_name in line and tool_type in line:
                skip_comment = True
                continue
        if skip_comment:
            if line.strip() == "":
                skip_comment = False
                continue
            if line.startswith(api_key_var) or line.startswith(api_url_var) or line.startswith(description_var):
                continue
            else:
                skip_comment = False
        
        if not (line.startswith(api_key_var) or line.startswith(api_url_var) or line.startswith(description_var)):
            filtered_lines.append(line)
    
    # Write back to .env
    with open(env_path, 'w') as f:
        f.writelines(filtered_lines)


@app.get("/api/v1/external-tools", response_model=list[ExternalToolResponse])
def get_external_tools():
    """Get all configured external tools."""
    try:
        tools = load_external_tools_from_env()
        return tools
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load external tools: {str(e)}")


@app.post("/api/v1/external-tools", response_model=ExternalToolResponse, status_code=status.HTTP_201_CREATED)
def create_external_tool(
    tool_config: ExternalToolConfig,
    authorized: bool = Depends(has_permission("Settings", "edit")),
):
    """Create a new external tool configuration."""
    try:
        save_external_tool_to_env(tool_config)
        
        # Return the created tool
        tool_id = f"{tool_config.tool_type}_{tool_config.tool_name}"
        return ExternalToolResponse(
            id=tool_id,
            tool_type=tool_config.tool_type,
            tool_name=tool_config.tool_name,
            api_url=tool_config.api_url,
            description=tool_config.description
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save external tool: {str(e)}")


@app.delete("/api/v1/external-tools/{tool_id}")
def delete_external_tool(
    tool_id: str,
    authorized: bool = Depends(has_permission("Settings", "edit")),
):
    """Delete an external tool configuration."""
    try:
        delete_external_tool_from_env(tool_id)
        return {"message": "External tool deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete external tool: {str(e)}")


@app.get("/api/v1/projects/{project_id}/releases", response_model=list[ReleaseResponse])
def get_releases(project_id: UUID):
    repo = project_service.repo
    return repo.list_releases(project_id)


@app.post("/api/v1/projects/{project_id}/releases", response_model=ReleaseResponse, status_code=status.HTTP_201_CREATED)
def create_release(
    project_id: UUID,
    data: ReleaseCreate,
    user: UserDB = Depends(require_project_permission("Executions", "create")),
):
    repo = project_service.repo
    return repo.create_release(
        project_id=project_id,
        name=data.name,
        description=data.description,
        status=data.status,
        start_date=data.start_date,
        end_date=data.end_date
    )


@app.get("/api/v1/releases/{release_id}/cycles", response_model=list[TestCycleResponse])
def get_test_cycles(release_id: UUID):
    repo = project_service.repo
    return repo.list_test_cycles(release_id)


@app.post("/api/v1/releases/{release_id}/cycles", response_model=TestCycleResponse, status_code=status.HTTP_201_CREATED)
def create_test_cycle(
    request: Request,
    release_id: UUID,
    data: TestCycleCreate,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    repo = project_service.repo
    # Spec 0009, AC-5: the project comes from the release.
    release = repo.get_release(release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found")
    authorize_project(user, db, release.project_id, "Executions", "create", request)
    return repo.create_test_cycle(
        release_id=release_id,
        name=name if 'name' in locals() else data.name,
        description=data.description,
        status=data.status
    )


@app.get("/api/v1/projects/{project_id}/cycles", response_model=list[TestCycleResponse])
def get_project_cycles(project_id: UUID):
    repo = project_service.repo
    releases = repo.list_releases(project_id)
    all_cycles = []
    for r in releases:
        all_cycles.extend(repo.list_test_cycles(r.id))
    return all_cycles


@app.get("/api/v1/projects/{project_id}/requirements", response_model=list[RequirementResponse])
def get_requirements(project_id: UUID):
    p = project_service.get_project(project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    
    reqs = project_service.get_requirements(project_id)
    result = []
    for r in reqs:
        result.append(
            RequirementResponse(
                id=r.id,
                title=r.title,
                description=r.description,
                source=r.source,
                uploaded_at=r.uploaded_at,
                priority=r.priority,
                business_domain=r.business_domain,
                attachments=r.attachments,
                original_filename=r.original_filename,
                requirement_id=r.requirement_id,
                requirement_title=r.requirement_title,
                jira_issue_key=getattr(r, "jira_issue_key", None),
                jira_issue_url=getattr(r, "jira_issue_url", None),
                jira_sync_status=getattr(r, "jira_sync_status", None),
                jira_last_synced_at=getattr(r, "jira_last_synced_at", None),
                release_id=getattr(r, "release_id", None)
            )
        )
    return result


@app.post("/api/v1/projects/{project_id}/requirements", response_model=RequirementResponse, status_code=status.HTTP_201_CREATED)
def create_requirement(
    project_id: UUID,
    data: RequirementCreate,
    user: UserDB = Depends(require_project_permission("Requirements", "create")),
):
    p = project_service.get_project(project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    
    try:
        r = project_service.add_requirement(
            project_id=project_id,
            title=data.title,
            description=data.description,
            priority=data.priority,
            business_domain=data.business_domain,
            attachments=[],
            original_filename=None,
            requirement_id=None,
            requirement_title=data.title,
            release_id=data.release_id
        )
        return RequirementResponse(
            id=r.id,
            title=r.title,
            description=r.description,
            source=r.source,
            uploaded_at=r.uploaded_at,
            priority=data.priority,
            business_domain=data.business_domain,
            attachments=[],
            original_filename=r.original_filename,
            requirement_id=r.requirement_id,
            requirement_title=r.requirement_title,
            jira_issue_key=getattr(r, "jira_issue_key", None),
            jira_issue_url=getattr(r, "jira_issue_url", None),
            jira_sync_status=getattr(r, "jira_sync_status", None),
            jira_last_synced_at=getattr(r, "jira_last_synced_at", None),
            release_id=getattr(r, "release_id", None)
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/v1/projects/{project_id}/requirements/import", response_model=list[RequirementResponse])
async def import_requirements(
    project_id: UUID,
    release_id: UUID | None = None,
    file: UploadFile = File(...),
    user: UserDB = Depends(require_project_permission("Requirements", "create")),
):
    p = project_service.get_project(project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    file_bytes = await file.read()
    filename = file.filename

    try:
        parsed_blocks = project_service.parse_requirements_from_file(filename, file_bytes)
        
        finalized_requirements = []
        for block in parsed_blocks:
            # 1. Create a draft requirement in DB
            req = project_service.add_requirement(
                project_id=project_id,
                title=block["title"],
                description=block["description"],
                priority=block["priority"],
                business_domain=block["business_domain"],
                attachments=[filename],
                original_filename=filename,
                requirement_id=block["requirement_id"],
                requirement_title=block["requirement_title"],
                release_id=release_id
            )
            
            # 2. Run the LangGraph INGEST workflow for LLM enrichment + optional Feature Inventory mapping
            final_req = workflow_service.ingest_requirement(project_id, req)
            finalized_requirements.append(final_req)

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"File import failed: {exc}")

    res = []
    for r in finalized_requirements:
        res.append(
            RequirementResponse(
                id=r.id,
                title=r.title,
                description=r.description,
                source=r.source,
                uploaded_at=r.uploaded_at,
                priority=r.priority,
                business_domain=r.business_domain,
                attachments=r.attachments,
                original_filename=r.original_filename,
                requirement_id=r.requirement_id,
                requirement_title=r.requirement_title,
                jira_issue_key=getattr(r, "jira_issue_key", None),
                jira_issue_url=getattr(r, "jira_issue_url", None),
                jira_sync_status=getattr(r, "jira_sync_status", None),
                jira_last_synced_at=getattr(r, "jira_last_synced_at", None),
                release_id=getattr(r, "release_id", None)
            )
        )
    return res


@app.post("/api/v1/projects/{project_id}/requirements/import-jira", response_model=RequirementResponse)
def import_jira_story(
    project_id: UUID,
    issue_key: str,
    release_id: UUID | None = None,
    user: UserDB = Depends(require_project_permission("Requirements", "create")),
):
    p = project_service.get_project(project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")

    from backend.services.jira_service import JiraService
    jira_svc = JiraService()
    issue = jira_svc.get_issue(issue_key)
    if not issue:
        raise HTTPException(status_code=404, detail=f"JIRA issue {issue_key} not found or inaccessible.")

    fields = issue.get("fields", {})
    summary = fields.get("summary") or f"JIRA User Story: {issue_key}"
    description = fields.get("description") or ""
    priority_name = fields.get("priority", {}).get("name", "Medium")
    
    # Create the requirement draft
    req = project_service.create_requirement(
        project_id=project_id,
        title=summary,
        description=description,
        priority=priority_name,
        business_domain="JIRA Import",
        attachments=[],
        original_filename=None,
        requirement_id=issue_key,
        requirement_title=summary,
        release_id=release_id
    )
    
    # Update fields with JIRA-specific linkage
    req = req.model_copy(update={
        "jira_issue_key": issue_key,
        "jira_issue_url": f"{jira_svc.config.base_url.rstrip('/')}/browse/{issue_key}",
        "jira_sync_status": "synced",
        "jira_last_synced_at": datetime.now(timezone.utc)
    })
    project_service.save_requirement(req)
    
    # Run analyst pipeline to enrich the requirement
    final_req = workflow_service.ingest_requirement(project_id, req)
    queue_knowledge_source(project_id, "jira_story", issue_key)

    return RequirementResponse(
        id=final_req.id,
        title=final_req.title,
        description=final_req.description,
        source=final_req.source,
        uploaded_at=final_req.uploaded_at,
        priority=final_req.priority,
        business_domain=final_req.business_domain,
        attachments=final_req.attachments,
        original_filename=final_req.original_filename,
        requirement_id=final_req.requirement_id,
        requirement_title=final_req.requirement_title,
        jira_issue_key=getattr(final_req, "jira_issue_key", None),
        jira_issue_url=getattr(final_req, "jira_issue_url", None),
        jira_sync_status=getattr(final_req, "jira_sync_status", None),
        jira_last_synced_at=getattr(final_req, "jira_last_synced_at", None),
        release_id=getattr(final_req, "release_id", None)
    )


@app.post("/api/v1/projects/{project_id}/requirements/{requirement_id}/generate-scenarios", response_model=list[ScenarioResponse])
def generate_scenarios(
    project_id: UUID,
    requirement_id: UUID,
    payload: dict = Body(...),
    user: UserDB = Depends(require_project_permission("Scenarios", "create")),
):
    _require_requirement_in_project(project_id, requirement_id)
    count = payload.get("count", 3)
    mode = payload.get("mode", "append")
    try:
        scenarios = workflow_service.generate_scenarios(project_id, requirement_id, count, mode)
        return [resolve_scenario_response(s) for s in scenarios]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Scenario generation failed: {exc}")


@app.post("/api/v1/projects/{project_id}/requirements/{requirement_id}/generate-backlog", response_model=list[ScenarioResponse])
def generate_backlog(
    project_id: UUID,
    requirement_id: UUID,
    user: UserDB = Depends(require_project_permission("Requirements", "create")),
):
    _require_requirement_in_project(project_id, requirement_id)
    try:
        scenarios = workflow_service.generate_backlog(project_id, requirement_id)
        return [resolve_scenario_response(s) for s in scenarios]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backlog generation failed: {exc}")


@app.get("/api/v1/projects/{project_id}/scenarios", response_model=list[ScenarioResponse])
def get_scenarios(project_id: UUID):
    try:
        scenarios = project_service.get_scenarios_for_project(project_id)
        return [resolve_scenario_response(s) for s in scenarios]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.put("/api/v1/projects/{project_id}/scenarios/{scenario_id}", response_model=ScenarioResponse)
def update_scenario(
    project_id: UUID,
    scenario_id: UUID,
    data: ScenarioUpdate,
    background_tasks: BackgroundTasks,
    user: UserDB = Depends(require_project_permission("Scenarios", "edit")),
):
    _require_scenario_in_project(project_id, scenario_id)
    original = project_service.repo.get_scenario(scenario_id)
    updated = project_service.update_scenario(
        scenario_id=scenario_id,
        scenario_name=data.scenario_name,
        description=data.description,
        priority=data.priority,
        approved=data.approved,
        rejected=data.rejected
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Scenario not found")
        
    from backend.services.jira_sync_service import sync_scenario_created, sync_scenario_updated
    if updated.approved:
        if original and original.jira_issue_key:
            background_tasks.add_task(sync_scenario_updated, scenario_id)
        else:
            background_tasks.add_task(sync_scenario_created, scenario_id)
    elif updated.jira_issue_key:
        background_tasks.add_task(sync_scenario_updated, scenario_id)
        
    return resolve_scenario_response(updated)


@app.delete("/api/v1/projects/{project_id}/scenarios/{scenario_id}")
def delete_scenario(
    project_id: UUID,
    scenario_id: UUID,
    user: UserDB = Depends(require_project_permission("Scenarios", "delete")),
):
    _require_scenario_in_project(project_id, scenario_id)
    # Its test cases are soft deleted with it, so read them first to drop their knowledge entries.
    test_cases = get_project_repository().get_test_cases(scenario_id)
    success = project_service.delete_scenario(scenario_id)
    if not success:
        raise HTTPException(status_code=404, detail="Scenario not found")
    queue_test_case_removals(project_id, test_cases)
    return {"detail": "Scenario deleted successfully"}


@app.post("/api/v1/projects/{project_id}/scenarios/duplicate/{scenario_id}", response_model=ScenarioResponse)
def duplicate_scenario(
    project_id: UUID,
    scenario_id: UUID,
    user: UserDB = Depends(require_project_permission("Scenarios", "create")),
):
    _require_scenario_in_project(project_id, scenario_id)
    duplicated = project_service.duplicate_scenario(scenario_id)
    if not duplicated:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return resolve_scenario_response(duplicated)


@app.post("/api/v1/projects/{project_id}/requirements/{requirement_id}/generate-testcases", response_model=list[TestCaseResponse])
def generate_test_cases(
    project_id: UUID,
    requirement_id: UUID,
    user: UserDB = Depends(require_project_permission("TestCases", "create")),
):
    _require_requirement_in_project(project_id, requirement_id)
    try:
        test_cases = workflow_service.generate_test_cases(project_id, requirement_id)
        renderer = object_repository_service.renderer()
        return [
            _test_case_response(tc, renderer, latest_heals={})  # new cases have no script heals
            for tc in test_cases
        ]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Test case generation failed: {exc}")


@app.post("/api/v1/projects/{project_id}/requirements/{requirement_id}/generate-testdata-tests")
def generate_test_data_tests(
    project_id: UUID,
    requirement_id: UUID,
    generate_scripts: bool = True,
    user: UserDB = Depends(require_project_permission("TestCases", "create")),
):
    _require_requirement_in_project(project_id, requirement_id)
    try:
        report, test_cases = workflow_service.generate_test_data_test_cases(
            project_id, requirement_id, generate_scripts=generate_scripts
        )
        return {
            "test_data_report": report,
            "test_case_ids": [str(test_case.id) for test_case in test_cases],
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Test-data test generation failed: {exc}",
        )


@app.post(
    "/api/v1/projects/{project_id}/requirements/{requirement_id}"
    "/testcases/{test_case_id}/generate-testdata"
)
def generate_test_data_for_test_case(
    project_id: UUID,
    requirement_id: UUID,
    test_case_id: UUID,
    user: UserDB = Depends(require_project_permission("TestCases", "edit")),
):
    _require_requirement_in_project(project_id, requirement_id)
    _require_test_case_in_project(project_id, test_case_id)
    try:
        report, test_case = workflow_service.generate_test_data_for_test_case(
            project_id,
            requirement_id,
            test_case_id,
        )
        return {
            "test_data_report": report,
            "test_case_ids": [str(test_case.id)],
            "script_ready": bool(test_case.playwright_script),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Test-data generation for test case failed: {exc}",
        )


@app.post(
    "/api/v1/projects/{project_id}/requirements/{requirement_id}"
    "/testcases/{test_case_id}/generate-testdata-script"
)
def generate_test_data_script_for_test_case(
    project_id: UUID,
    requirement_id: UUID,
    test_case_id: UUID,
    user: UserDB = Depends(require_project_permission("Playwright", "create")),
):
    _require_requirement_in_project(project_id, requirement_id)
    _require_test_case_in_project(project_id, test_case_id)
    try:
        test_case = workflow_service.generate_test_data_script_for_test_case(
            project_id,
            requirement_id,
            test_case_id,
        )
        return {"test_case_ids": [str(test_case.id)]}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Playwright script generation for test case failed: {exc}",
        )


@app.get("/api/v1/projects/{project_id}/testcases", response_model=list[TestCaseResponse])
def get_test_cases(project_id: UUID):
    try:
        test_cases = project_service.get_test_cases_for_project(project_id)
        renderer = object_repository_service.renderer()
        latest_heals = _latest_script_heals(project_id)
        return [
            _test_case_response(tc, renderer, latest_heals)
            for tc in test_cases
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# Script heals (spec 0011, AC-12, AC-13). Registered before the
# /testcases/{test_case_id} routes so `script-heals` is never read as an id.

def _script_heal_audit(db: Session, user: UserDB, project_id: UUID):
    return lambda action, changes: log_audit(db, user.id, project_id, action, "TESTCASE", changes)


@app.get("/api/v1/projects/{project_id}/testcases/script-heals", response_model=list[ScriptHealResponse])
def list_script_heals(
    project_id: UUID,
    heal_status: Optional[str] = Query(None, alias="status"),
    authorized: bool = Depends(has_permission("TestCases", "view")),
):
    if heal_status is not None and heal_status not in SCRIPT_HEAL_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {', '.join(SCRIPT_HEAL_STATUSES)}")
    try:
        heals = script_repair_service.list_heals(project_id, heal_status)
    except ObjectRepositoryError as exc:
        raise _objrepo_error(exc)
    return [_script_heal_response(heal) for heal in heals]


def _decide_script_heal(decide, project_id: UUID, heal_id: UUID, user: UserDB, db: Session) -> ScriptHealResponse:
    try:
        heal = decide(project_id, heal_id, user.id, audit=_script_heal_audit(db, user, project_id))
    except ObjectRepositoryError as exc:
        raise _objrepo_error(exc)
    return _script_heal_response(heal)


@app.post("/api/v1/projects/{project_id}/testcases/script-heals/{heal_id}/accept", response_model=ScriptHealResponse)
def accept_script_heal(
    project_id: UUID,
    heal_id: UUID,
    user: UserDB = Depends(require_project_permission("TestCases", "edit")),
    db: Session = Depends(get_db),
):
    return _decide_script_heal(script_repair_service.accept_heal, project_id, heal_id, user, db)


@app.post("/api/v1/projects/{project_id}/testcases/script-heals/{heal_id}/reject", response_model=ScriptHealResponse)
def reject_script_heal(
    project_id: UUID,
    heal_id: UUID,
    user: UserDB = Depends(require_project_permission("TestCases", "edit")),
    db: Session = Depends(get_db),
):
    return _decide_script_heal(script_repair_service.reject_heal, project_id, heal_id, user, db)


@app.post("/api/v1/projects/{project_id}/testcases/script-heals/{heal_id}/revert", response_model=ScriptHealResponse)
def revert_script_heal(
    project_id: UUID,
    heal_id: UUID,
    user: UserDB = Depends(require_project_permission("TestCases", "edit")),
    db: Session = Depends(get_db),
):
    return _decide_script_heal(script_repair_service.revert_heal, project_id, heal_id, user, db)


# Ownership (spec 0009, AC-4 and AC-5). Neither a scenario nor a test case
# stores a project_id, so each resolves up its parent chain:
# test case -> scenario -> requirement -> project. A missing or deleted link
# anywhere in that chain means "not found".


def _project_of_scenario(scenario_id: UUID) -> UUID | None:
    repo = get_project_repository()
    scenario = repo.get_scenario(scenario_id)
    if scenario is None:
        return None
    # The JSON store's get_scenario also returns soft deleted rows; the
    # requirement's scenario list never does, in either store.
    if not any(s.id == scenario_id for s in repo.get_scenarios(scenario.requirement_id)):
        return None
    return repo.get_requirement_project_id(scenario.requirement_id)


def _project_of_test_case(test_case_id: UUID) -> UUID | None:
    test_case = get_project_repository().get_test_case(test_case_id)
    if test_case is None:
        return None
    return _project_of_scenario(test_case.scenario_id)


def _require_requirement_in_project(project_id: UUID, requirement_id: UUID) -> None:
    if get_project_repository().get_requirement_project_id(requirement_id) != project_id:
        raise HTTPException(status_code=404, detail="Requirement not found")


def _require_scenario_in_project(project_id: UUID, scenario_id: UUID) -> None:
    if _project_of_scenario(scenario_id) != project_id:
        raise HTTPException(status_code=404, detail="Scenario not found")


def _require_test_case_in_project(project_id: UUID, test_case_id: UUID) -> None:
    """404 unless the test case belongs to this project, so a project grant never reaches another project's test case."""
    if _project_of_test_case(test_case_id) != project_id:
        raise HTTPException(status_code=404, detail="Test case not found")


def _require_execution_in_project(project_id: UUID, execution_id: UUID) -> None:
    if get_project_repository().get_execution_result(project_id, execution_id) is None:
        raise HTTPException(status_code=404, detail="Execution not found")


@app.put("/api/v1/projects/{project_id}/testcases/{test_case_id}", response_model=TestCaseResponse)
def update_test_case(
    project_id: UUID,
    test_case_id: UUID,
    data: TestCaseUpdate,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("TestCases", "edit")),
):
    require_project_access(user, project_id, db, projects=project_service)
    _require_test_case_in_project(project_id, test_case_id)
    updated = project_service.update_test_case(
        test_case_id=test_case_id,
        title=data.title,
        preconditions=data.preconditions,
        steps=data.steps,
        expected_result=data.expected_result,
        priority=data.priority,
        status=data.status,
        confidence=data.confidence,
        evaluation_status=data.evaluation_status,
        evaluation_reason=data.evaluation_reason
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Test case not found")
    queue_knowledge_source(project_id, "test_case", updated.id)
    return _test_case_response(updated)


@app.delete("/api/v1/projects/{project_id}/testcases/{test_case_id}")
def delete_test_case(
    project_id: UUID,
    test_case_id: UUID,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("TestCases", "delete")),
):
    require_project_access(user, project_id, db, projects=project_service)
    _require_test_case_in_project(project_id, test_case_id)
    # Read the bug key before the delete, so its defect entry can be checked after (AC-14a).
    existing = get_project_repository().get_test_case(test_case_id)
    success = project_service.delete_test_case(test_case_id)
    if not success:
        raise HTTPException(status_code=404, detail="Test case not found")
    queue_knowledge_source(project_id, "test_case", test_case_id, remove=True)
    queue_defect_keys(project_id, [getattr(existing, "jira_issue_key", None)])
    return {"detail": "Test case deleted successfully"}


# Declared before the `{execution_id}` route, so `failed` is not parsed as an execution ID.
@app.delete("/api/v1/projects/{project_id}/executions/failed")
def delete_failed_executions(
    project_id: UUID,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("Executions", "delete")),
):
    require_project_access(user, project_id, db, projects=project_service)
    repo = get_project_repository()
    failed = [
        ex for ex in project_service.get_execution_results(project_id)
        if getattr(ex.status, "value", ex.status) in {"failed", "error"}
        and not repo.is_execution_deleted(project_id, ex.id)
    ]
    count = project_service.delete_failed_executions(project_id)
    for ex in failed:
        queue_knowledge_source(project_id, "execution_failure", ex.id, remove=True)
    # Bug keys of the deleted rows, captured in the same pass (AC-14a).
    queue_defect_keys(project_id, [ex.jira_bug_id for ex in failed])
    return {"detail": f"Deleted {count} failed executions"}


@app.delete("/api/v1/projects/{project_id}/executions/{execution_id}")
def delete_execution(
    project_id: UUID,
    execution_id: UUID,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("Executions", "delete")),
):
    require_project_access(user, project_id, db, projects=project_service)
    # Read the bug key before the delete; PostgreSQL deletes the row outright (AC-14a).
    # The lookup is scoped to this project, so another project's execution is a plain 404.
    existing = get_project_repository().get_execution_result(project_id, execution_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    success = project_service.delete_execution(execution_id)
    if not success:
        raise HTTPException(status_code=404, detail="Execution not found")
    queue_knowledge_source(project_id, "execution_failure", execution_id, remove=True)
    queue_defect_keys(project_id, [getattr(existing, "jira_bug_id", None)])
    return {"detail": "Execution deleted successfully"}


@app.put("/api/v1/projects/{project_id}/testcases/{test_case_id}/script", response_model=TestCaseResponse)
def update_test_case_script(
    project_id: UUID,
    test_case_id: UUID,
    payload: ScriptUpdatePayload,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("TestCases", "edit")),
):
    require_project_access(user, project_id, db, projects=project_service)
    _require_test_case_in_project(project_id, test_case_id)
    updated = project_service.update_test_case_script(test_case_id, payload.script)
    if not updated:
        raise HTTPException(status_code=404, detail="Test case not found")
    return _test_case_response(updated)


@app.get("/api/v1/settings")
def read_settings():
    settings = get_settings()
    return {
        "llm": {
            "provider": settings.llm.provider,
            "model": settings.llm.model,
            "temperature": settings.llm.temperature,
            "api_base": settings.llm.api_base,
        },
        # Read only, from .env (spec 0003); API keys are never returned.
        "llm_roles": {
            role: config.model_dump(exclude={"api_key", "api_base"})
            for role, config in settings.llm_roles.items()
        },
        # Spec 0005: the frontend renders the chat button only when this is true.
        "chat_enabled": settings.chat.enabled,
        "workflow": {
            "mode": settings.workflow.mode,
            "human_review_enabled": settings.workflow.human_review_enabled,
            "evaluation_threshold": settings.workflow.evaluation_threshold,
        },
        "browser": {
            "type": settings.browser.type,
            "headless": settings.browser.headless,
        },
        "rag": {
            "enabled": settings.rag.enabled,
            "vector_db_provider": settings.rag.vector_db_provider,
            "vector_db_path": settings.rag.vector_db_path,
            "ragflow_api_base": settings.rag.ragflow_api_base,
            # Spec 0006: the key is write only and never returned.
            "ragflow_api_key_set": bool(settings.rag.ragflow_api_key),
        },
        "generation": {
            "scenario_count": settings.generation.scenario_count,
        },
        "evaluation": {
            "duplicate_similarity_threshold": settings.evaluation.duplicate_similarity_threshold,
            "relevance_rejection_threshold": settings.evaluation.relevance_rejection_threshold,
        },
        "playwright": {
            "base_url": settings.playwright.base_url,
            "browser": settings.playwright.browser,
            "headless": settings.playwright.headless,
            "timeout": settings.playwright.timeout,
            "retries": settings.playwright.retries,
            "workspace_url": settings.playwright.workspace_url
        },
        "jira": {
            "base_url": settings.jira.base_url,
            "email": settings.jira.email,
            "api_token": settings.jira.api_token,
            "project_key": settings.jira.project_key,
            "default_issue_type": settings.jira.default_issue_type,
            "verify_ssl": settings.jira.verify_ssl,
            "resolved_statuses": settings.jira.resolved_statuses
        }
    }


@app.get("/api/v1/projects/{project_id}/credentials")
def get_project_credentials(project_id: UUID, caller: str = Depends(require_workspace_secret)):
    """Decrypted credentials for the workspace only (spec 0009, AC-6). Only
    this project's own vault entry; 404 when it has none, never another
    project's credentials."""
    from backend.services.vault_service import VaultService

    try:
        username, password = VaultService().get_credentials(str(project_id))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not username and not password:
        raise HTTPException(status_code=404, detail="No credentials stored for this project")
    return {"username": username or None, "password": password or None}


@app.put("/api/v1/settings")
def save_settings(
    payload: dict = Body(...),
    user: UserDB = Depends(get_current_user),
    authorized: bool = Depends(has_permission("Settings", "edit")),
):
    # Settings edit is Superadmin only, so nobody else can repoint ragflow_api_base or the key.
    override_path = Path(__file__).parent / "config" / "override.yaml"
    try:
        llm_payload = payload.get("llm", {}) if isinstance(payload, dict) else {}
        if "provider" in llm_payload and not (llm_payload.get("provider") or "").strip():
            raise HTTPException(status_code=422, detail="LLM provider cannot be blank. Pick OpenAI, Groq, Nvidia, Gemini, Ollama, or Anthropic.")
        overrides = {}
        for section in ["llm", "workflow", "browser", "rag", "generation", "evaluation", "playwright", "jira"]:
            if section in payload:
                overrides[section] = payload[section]
        if isinstance(overrides.get("rag"), dict):
            overrides["rag"] = knowledge_settings_overrides(overrides["rag"])

        with open(override_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(overrides, f)

        get_settings.cache_clear()
        # RAG settings may have changed: rebuild the retriever on next use.
        set_retriever(None)

        return {"status": "success", "settings": read_settings()}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save settings: {exc}")
@app.delete("/api/v1/projects/{project_id}")
def delete_project(
    project_id: UUID,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("Projects", "delete")),
):
    # Read before the soft delete: the knowledge cleanup needs its dataset ID (spec 0006, AC-10).
    project = require_project_access(user, project_id, db, projects=project_service)
    success = project_service.delete_project(project_id)
    if not success:
        raise HTTPException(status_code=404, detail="Project not found")
    if project is not None:
        knowledge_service.project_deleted(project)
    return {"detail": "Project deleted successfully"}


# ==========================================================
# Knowledge base (spec 0006). Behavior lives in KnowledgeService.
# ==========================================================

@app.get("/api/v1/projects/{project_id}/documents", response_model=list[Document])
def get_documents(
    project_id: UUID,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("Knowledge", "view")),
):
    require_project_access(user, project_id, db, projects=project_service)
    return knowledge_service.list_documents(project_id)


@app.post(
    "/api/v1/projects/{project_id}/documents/upload",
    response_model=Document,
    status_code=status.HTTP_201_CREATED,
)
def upload_document(
    project_id: UUID,
    file: UploadFile = File(...),
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("Knowledge", "create")),
):
    project = require_project_access(user, project_id, db, projects=project_service)
    try:
        return knowledge_service.upload(project, file.file, file.filename or "", file.content_type)
    except KnowledgeUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@app.delete("/api/v1/projects/{project_id}/documents/{document_id}")
def delete_document(
    project_id: UUID,
    document_id: UUID,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("Knowledge", "delete")),
):
    # Access is checked before the lookup, so another project's document is a plain 404.
    project = require_project_access(user, project_id, db, projects=project_service)
    if not knowledge_service.delete(project, document_id):
        raise HTTPException(status_code=404, detail="Document not found")
    return {"detail": "Document deleted successfully"}


@app.get("/api/v1/projects/{project_id}/knowledge/status")
def get_knowledge_status(
    project_id: UUID,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("Knowledge", "view")),
):
    project = require_project_access(user, project_id, db, projects=project_service)
    return knowledge_service.status(project)


@app.post("/api/v1/projects/{project_id}/import-framework")
def import_framework(
    project_id: UUID,
    file: UploadFile = File(None),
    git_url: str = Body(None),
    user: UserDB = Depends(require_project_permission("Playwright", "create")),
):
    import os
    import tempfile
    from backend.utils.pom_parser import index_zip_file
    
    project = project_service.repo.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    dest_dir = os.path.join("data", "projects", str(project_id), "imported_framework")
    index_file_path = os.path.join("data", "projects", str(project_id), "pom_index.json")
    os.makedirs(os.path.dirname(index_file_path), exist_ok=True)

    pom_index = {}
    if file:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            tmp.write(file.file.read())
            tmp_path = tmp.name

        try:
            pom_index = index_zip_file(tmp_path, dest_dir)
            with open(index_file_path, "w", encoding="utf-8") as f:
                json.dump(pom_index, f, indent=2)
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
    elif git_url:
        pom_index = {
            "src/pages/login.page.ts": [
                {
                    "class_name": "LoginPage",
                    "methods": [
                        {"method_name": "login", "parameters": ["username", "password"]},
                        {"method_name": "verifyError", "parameters": ["message"]}
                    ]
                }
            ],
            "src/pages/vehicle.page.ts": [
                {
                    "class_name": "VehiclePage",
                    "methods": [
                        {"method_name": "selectMake", "parameters": ["make"]},
                        {"method_name": "clickNext", "parameters": []}
                    ]
                }
            ]
        }
        with open(index_file_path, "w", encoding="utf-8") as f:
            json.dump(pom_index, f, indent=2)
    else:
        raise HTTPException(status_code=400, detail="Either file or git_url must be provided")

    return {"detail": "Framework imported successfully", "pom_classes": len(pom_index)}


class GenerateScriptPayload(BaseModel):
    framework: str = "Playwright"


class ScriptValidationBatchRequest(BaseModel):
    test_case_ids: list[UUID] = Field(min_length=1)
    confirmed_safe_test_run: bool


@app.post("/api/v1/projects/{project_id}/testcases/{test_case_id}/generate-script", response_model=TestCaseResponse)
def generate_playwright_script(
    project_id: UUID,
    test_case_id: UUID,
    payload: GenerateScriptPayload = Body(default=GenerateScriptPayload()),
    user: UserDB = Depends(require_project_permission("Playwright", "create")),
):
    _require_test_case_in_project(project_id, test_case_id)
    try:
        updated_tc = workflow_service.generate_playwright_script(project_id, test_case_id, automation_framework=payload.framework)
        return _test_case_response(updated_tc)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Script generation failed: {exc}")


def _script_generation_job_response(job) -> dict:
    return {
        "job_id": str(job.id),
        "test_case_id": str(job.test_case_id),
        "status": job.status,
        "error": job.error or None,
        "error_status": job.error_status,
        "test_case": _test_case_response(job.test_case) if job.test_case is not None else None,
    }


@app.post(
    "/api/v1/projects/{project_id}/testcases/{test_case_id}/generate-script/jobs",
    status_code=status.HTTP_202_ACCEPTED,
)
def start_script_generation_job(
    project_id: UUID,
    test_case_id: UUID,
    payload: GenerateScriptPayload = Body(default=GenerateScriptPayload()),
    user: UserDB = Depends(require_project_permission("Playwright", "create")),
):
    """Start script generation in the background; poll the returned job until it finishes."""
    _require_test_case_in_project(project_id, test_case_id)
    job = script_generation_jobs.start(project_id, test_case_id, payload.framework)
    return _script_generation_job_response(job)


@app.get("/api/v1/projects/{project_id}/script-generation-jobs/{job_id}")
def get_script_generation_job(
    project_id: UUID,
    job_id: UUID,
    user: UserDB = Depends(get_current_user),
):
    job = script_generation_jobs.get(project_id, job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Script generation job not found. The backend may have restarted; generate the script again.",
        )
    return _script_generation_job_response(job)


@app.post(
    "/api/v1/projects/{project_id}/testcases/validate-scripts",
    status_code=status.HTTP_202_ACCEPTED,
)
def validate_project_scripts(
    project_id: UUID,
    payload: ScriptValidationBatchRequest,
    background_tasks: BackgroundTasks,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("Playwright", "create")),
):
    if not payload.confirmed_safe_test_run:
        raise HTTPException(
            status_code=400,
            detail="Confirm that the selected project is a safe test environment.",
        )
    require_project_access(
        user, project_id, db,
        detail="You must be assigned to this project to validate its scripts.",
        projects=project_service,
    )
    try:
        cases = grounded_playwright_service.reserve_batch(
            project_id,
            payload.test_case_ids,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 409 if "safe test environment" in message or "already running" in message else 422
        raise HTTPException(status_code=status_code, detail=message)

    case_ids = [test_case.id for test_case in cases]
    logger.info(
        "Grounded script validation batch started: project_id=%s actor_id=%s case_count=%s",
        project_id,
        user.id,
        len(case_ids),
    )
    background_tasks.add_task(
        grounded_playwright_service.run_reserved_batch,
        project_id,
        case_ids,
    )
    return {"status": "started", "test_case_ids": case_ids}


@app.post("/api/v1/projects/{project_id}/testcases/{test_case_id}/execute")
def execute_test_case(
    project_id: UUID,
    test_case_id: UUID,
    background_tasks: BackgroundTasks,
    user: UserDB = Depends(require_project_permission("Executions", "create")),
):
    from backend.services.execution_service import ExecutionService

    _require_test_case_in_project(project_id, test_case_id)

    try:
        execution_id = ExecutionService().start_test_case_run(
            project_id, test_case_id, background_tasks, workflow_service=workflow_service,
        )
    except ProjectConfigurationError as exc:
        # Spec 0001, AC-3: an unready project is refused before anything is queued.
        return JSONResponse(status_code=exc.status_code, content=exc.as_detail())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"status": "started", "execution_id": str(execution_id)}


@app.get("/api/v1/projects/{project_id}/testcases/{test_case_id}/execution-stream")
def get_execution_stream(project_id: UUID, test_case_id: UUID):
    tc_id_str = str(test_case_id)
    
    async def event_generator():
        # Clear out previous runs first so connections don't bleed
        from backend.services.workflow_service import active_execution_events
        yield f"data: {json.dumps({'status': 'Generating', 'timeline': 'Connecting...', 'log': 'Connecting to event log stream...'})}\n\n"
        
        last_index = 0
        while True:
            events = active_execution_events.get(tc_id_str, [])
            if last_index < len(events):
                for i in range(last_index, len(events)):
                    yield f"data: {json.dumps(events[i])}\n\n"
                    if events[i]["status"] == "Completed":
                        return
                last_index = len(events)
            
            await asyncio.sleep(0.4)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


from backend.services.execution_service import active_batch_events


@app.post("/api/v1/projects/{project_id}/batches/execute")
def execute_batch(
    project_id: UUID,
    test_case_ids: list[UUID],
    background_tasks: BackgroundTasks,
    test_cycle_id: UUID | None = None,
    user: UserDB = Depends(require_project_permission("Executions", "create")),
):
    from backend.services.execution_service import ExecutionService

    # Spec 0009, AC-4: an empty list is a 422 and duplicates count once, both
    # before ownership. One foreign test case refuses the whole batch.
    if not test_case_ids:
        raise HTTPException(status_code=422, detail="test_case_ids must name at least one test case")
    unique_ids = list(dict.fromkeys(test_case_ids))
    owned = {tc.id for tc in project_service.get_test_cases_for_project(project_id)}
    if any(test_case_id not in owned for test_case_id in unique_ids):
        raise HTTPException(status_code=404, detail="Test case not found")

    # Spec 0012, AC-2: a cycle must be this project's, checked before anything
    # is queued. Another project's cycle is a 404 too (spec 0009).
    service = ExecutionService()
    if test_cycle_id is not None and not service.cycle_in_project(project_id, test_cycle_id):
        raise HTTPException(status_code=404, detail="Test cycle not found")

    try:
        batch_id = service.start_batch_run(
            project_id, unique_ids, background_tasks, enforce_project_scope=False,
            test_cycle_id=test_cycle_id,
        )
    except ProjectConfigurationError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.as_detail())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "started", "batch_id": str(batch_id)}


UNTRACKED_BATCH_EVENT = {
    "status": "Error",
    "untracked": True,
    "log": "This batch is not tracked on this server. It may belong to another project, or the server restarted.",
}


@app.get("/api/v1/projects/{project_id}/batches/{batch_id}/stream")
def get_batch_stream(project_id: UUID, batch_id: str):
    from backend.services.execution_service import ExecutionService

    # Spec 0012, AC-17: only this project's tracked batch streams. Anything else
    # (unknown id, not a UUID, another project's batch, a restart) gets one
    # untracked Error event and the stream ends.
    if ExecutionService.batch_log_state(project_id, batch_id) is None:
        async def untracked_generator():
            yield f"data: {json.dumps(UNTRACKED_BATCH_EVENT)}\n\n"
        return StreamingResponse(untracked_generator(), media_type="text/event-stream")

    async def batch_event_generator():
        yield f"data: {json.dumps({'status': 'Connecting', 'log': 'Connecting to batch event stream...'})}\n\n"
        last_idx = 0
        while True:
            events = active_batch_events.get(batch_id, [])
            if last_idx < len(events):
                for i in range(last_idx, len(events)):
                    yield f"data: {json.dumps(events[i])}\n\n"
                    if events[i]["status"] in ["Completed", "Error"]:
                        return
                last_idx = len(events)
            await asyncio.sleep(0.4)
    return StreamingResponse(batch_event_generator(), media_type="text/event-stream")



@app.get("/api/v1/projects/{project_id}/executions", response_model=list[ExecutionResult])
def list_execution_results(project_id: UUID):
    return project_service.get_execution_results(project_id)


@app.get("/api/v1/projects/{project_id}/executions/{execution_id}")
def get_execution_detail(project_id: UUID, execution_id: UUID):
    result = project_service.get_execution_result(project_id, execution_id)
    if not result:
        raise HTTPException(status_code=404, detail="Execution not found")

    tc_id_str = str(result.test_case_id)
    from backend.services.workflow_service import active_execution_events
    events = active_execution_events.get(tc_id_str, [])

    timeline = []
    logs = []
    
    if events:
        for ev in events:
            if ev.get("timeline"):
                timeline.append({
                    "timestamp": datetime.now().isoformat(),
                    "event": ev["timeline"],
                    "type": "success" if ev["status"] == "Completed" else "info",
                    "details": ev.get("log", "")
                })
            if ev.get("log"):
                logs.append(ev["log"])
    else:
        timeline.append({
            "timestamp": result.executed_at.isoformat(),
            "event": "Completed",
            "type": "success" if result.status.value == "passed" else "error",
            "details": result.error_message or "All steps completed successfully."
        })
        logs.append(result.error_message or "Execution completed successfully.")

    # Query report from DB repo
    repo = project_service.repo
    report_data = repo.get_report_by_execution(execution_id) or {}

    return {
        "execution": {
            "id": str(result.id),
            "test_case_id": str(result.test_case_id),
            "status": result.status.value,
            "duration_seconds": result.duration_seconds,
            "error_message": result.error_message,
            "screenshot_path": result.screenshot_path,
            "video_path": result.video_path,
            "trace_path": result.trace_path,
            "executed_at": result.executed_at.isoformat(),
            # Spec 0012, AC-11: the run's batch and real test cycle, if any.
            "batch_id": str(result.batch_id) if result.batch_id else None,
            "test_cycle_id": str(result.test_cycle_id) if result.test_cycle_id else None,
            # Log analyser triage (ExecutionAnalysisAgent / LogAnalysisService).
            **result.model_dump(mode="json", include={
                "failure_category", "root_cause_summary", "trace_analysis", "screenshot_findings",
                "suggest_retry", "retest_pending_candidate", "failing_step", "evidence", "confidence",
                "flaky_score", "frontend_error_detected", "frontend_error_details", "run_history_summary",
                "jira_bug_id", "jira_bug_url", "bug_report_id", "heal_rerun_of", "heal_ids",
            }),
        },
        "timeline": timeline,
        "logs": logs,
        "artifacts": {
            "screenshot": result.screenshot_path,
            "video": result.video_path,
            "trace": result.trace_path
        },
        "report": report_data
    }


@app.get("/api/v1/projects/{project_id}/executions/{execution_id}/screenshot")
def get_execution_screenshot(project_id: UUID, execution_id: UUID, path: str = None):
    if not path:
        result = project_service.get_execution_result(project_id, execution_id)
        if not result or not result.screenshot_path:
            raise HTTPException(status_code=404, detail="Screenshot not found")
        path = result.screenshot_path
    
    file_path = Path(path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File does not exist")
    return FileResponse(file_path)


@app.get("/api/v1/projects/{project_id}/executions/{execution_id}/screenshot/{filename}")
@app.get("/api/v1/projects/{project_id}/executions/{execution_id}/screenshots/{filename}")
def get_execution_screenshot_by_filename(project_id: UUID, execution_id: UUID, filename: str):
    file_path = Path("backend/playwrightt/public/artifacts") / str(execution_id) / "screenshots" / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Screenshot does not exist")
    return FileResponse(file_path)


@app.get("/api/artifacts/{execution_id}/screenshot/{filename}")
@app.get("/api/artifacts/{execution_id}/screenshots/{filename}")
def get_execution_screenshot_alias(execution_id: UUID, filename: str):
    file_path = Path("backend/playwrightt/public/artifacts") / str(execution_id) / "screenshots" / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Screenshot does not exist")
    return FileResponse(file_path)



@app.get("/api/v1/projects/{project_id}/executions/{execution_id}/video")
def get_execution_video(project_id: UUID, execution_id: UUID, path: str = None):
    if not path:
        result = project_service.get_execution_result(project_id, execution_id)
        if not result or not result.video_path:
            raise HTTPException(status_code=404, detail="Video not found")
        path = result.video_path
        
    file_path = Path(path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File does not exist")
    return FileResponse(file_path)


@app.get("/api/v1/projects/{project_id}/executions/{execution_id}/trace")
def get_execution_trace(project_id: UUID, execution_id: UUID, path: str = None):
    if not path:
        result = project_service.get_execution_result(project_id, execution_id)
        if not result or not result.trace_path:
            raise HTTPException(status_code=404, detail="Trace not found")
        path = result.trace_path
        
    file_path = Path(path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File does not exist")
    return FileResponse(file_path, filename=file_path.name)


@app.get("/api/v1/projects/{project_id}/executions/{execution_id}/pdf")
def get_execution_pdf(project_id: UUID, execution_id: UUID):
    repo = project_service.repo
    report_info = repo.get_report_by_execution(execution_id)
    if report_info:
        pdf_path = report_info.get("pdf_path")
        if pdf_path and Path(pdf_path).exists():
            return FileResponse(pdf_path, filename=Path(pdf_path).name)
                
    # If not found, try to compile on the fly
    result = project_service.get_execution_result(project_id, execution_id)
    if result:
        from backend.services.report_service import ReportService
        try:
            # Use engine selection method to respect feature flag
            r_info = ReportService().compile_reports_with_engine_selection(project_id, result)
            pdf_path = r_info.get("pdf_path")
            if pdf_path and Path(pdf_path).exists():
                return FileResponse(pdf_path, filename=Path(pdf_path).name)
        except Exception as compile_err:
            print(f"[On-the-fly PDF compile error]: {compile_err}")
            
    raise HTTPException(status_code=404, detail="PDF report not found")


@app.get("/api/v1/projects/{project_id}/executions/{execution_id}/html")
def get_execution_html(project_id: UUID, execution_id: UUID):
    repo = project_service.repo
    report_info = repo.get_report_by_execution(execution_id)
    print(f"[API] get_execution_html called for execution {execution_id}")
    print(f"[API] report_info from repository: {report_info}")
    if report_info:
        html_path = report_info.get("html_path")
        engine = report_info.get("engine", "unknown")
        print(f"[API] html_path: {html_path}")
        print(f"[API] engine: {engine}")
        if html_path and Path(html_path).exists():
            print(f"[API] Serving HTML from: {html_path}")
            return FileResponse(html_path)
        else:
            print(f"[API] HTML path does not exist: {html_path}")
                
    # If not found, try to compile on the fly
    result = project_service.get_execution_result(project_id, execution_id)
    if result:
        from backend.services.report_service import ReportService
        try:
            # Use engine selection method to respect feature flag
            r_info = ReportService().compile_reports_with_engine_selection(project_id, result)
            html_path = r_info.get("html_path")
            if html_path and Path(html_path).exists():
                return FileResponse(html_path)
        except Exception as compile_err:
            print(f"[On-the-fly HTML compile error]: {compile_err}")
            
    raise HTTPException(status_code=404, detail="HTML report not found")


@app.get("/api/v1/projects/{project_id}/batches/{batch_id}/html")
def get_batch_html_report(
    project_id: UUID,
    batch_id: UUID,
    user: UserDB = Depends(require_project_permission("Executions", "view")),
):
    from backend.services.report_service import ReportService
    from fastapi.responses import HTMLResponse
    # Spec 0012, AC-8: only "no runs" is a 404. Anything else is logged and is a
    # 500 that never carries the exception text.
    try:
        html_content = ReportService().compile_combined_batch_report_html(project_id, batch_id)
    except ValueError as exc:
        if str(exc).startswith("No executions found"):
            raise HTTPException(status_code=404, detail="No executions found for this batch") from exc
        logging.getLogger(__name__).exception("Batch HTML report failed: batch_id=%s", batch_id)
        raise HTTPException(status_code=500, detail="Batch report could not be built") from exc
    except Exception as exc:
        logging.getLogger(__name__).exception("Batch HTML report failed: batch_id=%s", batch_id)
        raise HTTPException(status_code=500, detail="Batch report could not be built") from exc
    return HTMLResponse(content=html_content)


@app.get("/api/v1/projects/{project_id}/executions/{execution_id}/junit")
def get_execution_junit(project_id: UUID, execution_id: UUID):
    repo = project_service.repo
    report_info = repo.get_report_by_execution(execution_id)
    if report_info:
        junit_path = report_info.get("junit_path")
        if junit_path and Path(junit_path).exists():
            return FileResponse(junit_path, filename=Path(junit_path).name)
    raise HTTPException(status_code=404, detail="JUnit XML report not found")


@app.get("/api/v1/projects/{project_id}/traces")
def get_activity_traces(project_id: UUID):
    """LLM configuration plus the project's real recorded token usage."""
    settings = get_settings()
    filters = TokenUsageFilters(project_id=project_id)
    summary = get_token_usage_service().summary(filters)
    by_agent = get_token_usage_service().breakdown(filters, group_by="agent")["rows"]

    analysis = settings.llm_roles["analysis"]
    return {
        "model": analysis.models[0],
        "provider": analysis.provider,
        "llm_roles": {
            role: {"provider": config.provider, "models": config.models}
            for role, config in settings.llm_roles.items()
        },
        "temperature": settings.llm.temperature,
        "total_calls": summary["calls"],
        "input_tokens": summary["prompt_tokens"],
        "output_tokens": summary["completion_tokens"],
        "total_tokens": summary["total_tokens"],
        "estimated_cost_usd": round(summary["cost_usd"], 4),
        "traces": [
            {
                "agent": row["key"],
                "calls": row["calls"],
                "latency_sec": round(row["avg_latency_ms"] * row["calls"] / 1000, 2),
                "tokens": row["total_tokens"],
                "cost": round(row["cost_usd"], 4),
            }
            for row in by_agent
        ],
    }


# ==========================================================
# Token Utilization
# ==========================================================

def token_usage_filters(
    start: datetime | None = Query(None, alias="from"),
    end: datetime | None = Query(None, alias="to"),
    project_id: UUID | None = None,
    agent: str | None = None,
    model: str | None = None,
    role: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    operation: str | None = None,
) -> TokenUsageFilters:
    def aware(value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    return TokenUsageFilters(
        start=aware(start), end=aware(end), project_id=project_id, agent=agent, model=model,
        role=role, provider=provider, status=status, operation=operation,
    )


def _token_usage_call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/v1/token-usage/summary")
def get_token_usage_summary(
    filters: TokenUsageFilters = Depends(token_usage_filters),
    authorized: bool = Depends(has_permission("TokenUsage", "view")),
):
    return get_token_usage_service().summary(filters)


@app.get("/api/v1/token-usage/timeseries")
def get_token_usage_timeseries(
    bucket: str = "day",
    split_by: str | None = None,
    filters: TokenUsageFilters = Depends(token_usage_filters),
    authorized: bool = Depends(has_permission("TokenUsage", "view")),
):
    return _token_usage_call(get_token_usage_service().timeseries, filters, bucket=bucket, split_by=split_by)


@app.get("/api/v1/token-usage/breakdown")
def get_token_usage_breakdown(
    group_by: str = "agent",
    filters: TokenUsageFilters = Depends(token_usage_filters),
    authorized: bool = Depends(has_permission("TokenUsage", "view")),
):
    return _token_usage_call(get_token_usage_service().breakdown, filters, group_by=group_by)


@app.get("/api/v1/token-usage/calls", response_model=TokenUsageCallsPage)
def get_token_usage_calls(
    page: int = 1,
    page_size: int = 25,
    sort: str = "-created_at",
    filters: TokenUsageFilters = Depends(token_usage_filters),
    authorized: bool = Depends(has_permission("TokenUsage", "view")),
):
    return _token_usage_call(get_token_usage_service().calls, filters, page=page, page_size=page_size, sort=sort)


@app.get("/api/v1/token-usage/facets")
def get_token_usage_facets(
    filters: TokenUsageFilters = Depends(token_usage_filters),
    authorized: bool = Depends(has_permission("TokenUsage", "view")),
):
    return get_token_usage_service().facets(filters)


@app.get("/api/v1/token-usage/export.csv")
def export_token_usage_csv(
    filters: TokenUsageFilters = Depends(token_usage_filters),
    authorized: bool = Depends(has_permission("TokenUsage", "view")),
):
    content = get_token_usage_service().export_csv(filters)
    filename = f"token-usage-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.csv"
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/v1/token-usage/pricing", response_model=list[ModelPriceRow])
def get_token_usage_pricing(authorized: bool = Depends(has_permission("TokenUsage", "view"))):
    return get_token_usage_service().pricing()


@app.get("/api/v1/token-usage/settings", response_model=TokenUsageSettings)
def get_token_usage_settings(authorized: bool = Depends(has_permission("TokenUsage", "view"))):
    return get_token_usage_service().get_settings()


@app.put("/api/v1/token-usage/settings", response_model=TokenUsageSettings)
def update_token_usage_settings(
    payload: TokenUsageSettings,
    authorized: bool = Depends(has_permission("TokenUsage", "edit")),
):
    return get_token_usage_service().update_settings(payload)


@app.get("/api/v1/scenarios/{scenario_id}/notes")
def get_scenario_notes(scenario_id: UUID):
    return project_service.get_scenario_notes(scenario_id)


@app.post("/api/v1/scenarios/{scenario_id}/notes")
def add_scenario_note(
    request: Request,
    scenario_id: UUID,
    payload: dict = Body(...),
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Spec 0009, AC-5: the project comes from the scenario.
    project_id = _project_of_scenario(scenario_id)
    if project_id is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    authorize_project(user, db, project_id, "Scenarios", "edit", request)
    note = payload.get("note")
    if not note:
        raise HTTPException(status_code=400, detail="Note content is required")
    project_service.add_scenario_note(scenario_id, note)
    return {"status": "success"}


@app.get("/api/v1/testcases/{test_case_id}/notes")
def get_test_case_notes(test_case_id: UUID):
    return project_service.get_test_case_notes(test_case_id)


@app.post("/api/v1/testcases/{test_case_id}/notes")
def add_test_case_note(
    request: Request,
    test_case_id: UUID,
    payload: dict = Body(...),
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Spec 0009, AC-5: the project comes from the test case.
    project_id = _project_of_test_case(test_case_id)
    if project_id is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    authorize_project(user, db, project_id, "TestCases", "edit", request)
    note = payload.get("note")
    if not note:
        raise HTTPException(status_code=400, detail="Note content is required")
    project_service.add_test_case_note(test_case_id, note)
    return {"status": "success"}


@app.get("/api/v1/projects/{project_id}/testcases/{test_case_id}", response_model=TestCaseResponse)
def get_test_case(project_id: UUID, test_case_id: UUID):
    tc = project_service.get_test_case(test_case_id)
    if not tc:
        raise HTTPException(status_code=404, detail="Test case not found")
    return _test_case_response(tc)


@app.get("/api/v1/playwright/workspace")
def get_playwright_workspace(
    project_id: UUID, 
    test_case_id: Optional[UUID] = None, 
    test_case_ids: Optional[str] = None
):
    settings = get_settings()
    
    # If test_case_ids is provided, query bulk test cases
    if test_case_ids:
        tc_ids = [UUID(t.strip()) for t in test_case_ids.split(",") if t.strip()]
        test_cases_data = []
        renderer = object_repository_service.renderer()
        for tc_id in tc_ids:
            tc = project_service.get_test_case(tc_id)
            if tc:
                test_cases_data.append({
                    "id": str(tc.id),
                    "title": tc.title,
                    "playwright_script": renderer.render(tc),
                    "is_frozen": tc.is_frozen or tc.evaluation_status == "approved",
                })
        return {
            "project_id": str(project_id),
            "test_cases": test_cases_data,
            "base_url": settings.playwright.base_url,
            "browser": settings.playwright.browser,
        }

    if not test_case_id:
        raise HTTPException(status_code=400, detail="Either test_case_id or test_case_ids must be provided")

    tc = project_service.get_test_case(test_case_id)
    if not tc:
        raise HTTPException(status_code=404, detail="Test case not found")
    
    # Get scenario info
    scenario_info = None
    if tc.scenario_id:
        sc = project_service.repo.get_scenario(tc.scenario_id)
        if sc:
            scenario_info = {
                "id": str(sc.id),
                "scenario_name": sc.scenario_name,
                "description": sc.description
            }
            
    return {
        "project_id": str(project_id),
        "test_case_id": str(test_case_id),
        "playwright_script": object_repository_service.render_for_test_case(tc),
        "base_url": settings.playwright.base_url,
        "browser": settings.playwright.browser,
        "is_frozen": tc.is_frozen or tc.evaluation_status == "approved",
        "scenario": scenario_info
    }


class FreezeScriptBody(BaseModel):
    test_case_id: UUID
    is_frozen: bool = True


class SaveScriptBody(BaseModel):
    project_id: UUID
    test_case_id: UUID
    script: str


@app.put("/api/v1/playwright/freeze")
def freeze_playwright_script(
    request: Request,
    caller: Caller = Depends(workspace_or_user(FreezeScriptBody)),
):
    """The workspace secret or a user with TestCases.edit on the test case's project (spec 0009, AC-9)."""
    payload: FreezeScriptBody = caller.payload
    project_id = _project_of_test_case(payload.test_case_id)
    if project_id is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    if not caller.is_workspace:
        authorize_project(caller.user, caller.db, project_id, "TestCases", "edit", request)

    tc = project_service.get_test_case(payload.test_case_id)
    if not tc:
        raise HTTPException(status_code=404, detail="Test case not found")
    tc.is_frozen = payload.is_frozen
    project_service.repo.update_test_case(tc)
    return {"status": "success", "is_frozen": tc.is_frozen}


@app.put("/api/v1/playwright/script")
def save_playwright_script(
    request: Request,
    caller: Caller = Depends(workspace_or_user(SaveScriptBody)),
):
    """The body project_id must own the test case; it is never trusted alone (spec 0009, AC-9)."""
    payload: SaveScriptBody = caller.payload
    if _project_of_test_case(payload.test_case_id) != payload.project_id:
        raise HTTPException(status_code=404, detail="Test case not found")
    if not caller.is_workspace:
        authorize_project(caller.user, caller.db, payload.project_id, "TestCases", "edit", request)

    tc = project_service.get_test_case(payload.test_case_id)
    if tc and (tc.is_frozen or tc.evaluation_status == "approved"):
        raise HTTPException(status_code=403, detail="Cannot modify script because it is frozen or approved")

    updated = project_service.update_test_case_script(payload.test_case_id, payload.script)
    if not updated:
        raise HTTPException(status_code=404, detail="Test case not found")
    return {"status": "success"}


@app.post("/api/v1/projects/{project_id}/executions/webhook")
def process_execution_webhook(
    project_id: UUID,
    background_tasks: BackgroundTasks,
    payload: dict = Body(...),
    caller: str = Depends(require_workspace_secret),
):
    from backend.services.execution_service import ExecutionService
    from backend.services.workflow_service import active_execution_events
    
    # The scan and harvest fields can be about 1 MB, so they stay out of the log.
    print(f"[Webhook DEBUG] Received payload: { {k: v for k, v in payload.items() if k not in ('a11y', 'harvest')} }")
    
    event = payload.get("event")
    test_case_id_str = payload.get("test_case_id")
    execution_id_str = payload.get("execution_id")
    
    if not event:
        raise HTTPException(status_code=400, detail="event is required")
        
    test_case_key = test_case_id_str or execution_id_str or "draft-execution"
    events = active_execution_events.setdefault(test_case_key, [])
    
    if event == "started":
        events.clear()  # reset previous events
        events.append({
            "status": "Running",
            "timeline": "Browser Started",
            "log": "Next.js execution engine started..."
        })
    elif event == "updated":
        log_line = payload.get("log_line")
        timeline_event = payload.get("timeline_event")
        live_screenshot = payload.get("live_screenshot")
        
        events.append({
            "status": "Running",
            "timeline": timeline_event.get("event") if timeline_event else None,
            "log": log_line,
            "screenshot": live_screenshot
        })
    elif event == "completed":
        # Finalize execution in Python service layer
        exec_service = ExecutionService()
        # Failure triage (log analyser) runs after the response, so the
        # workspace never waits on the analysis LLM.
        result = exec_service.finalize_execution(project_id, payload, background_tasks=background_tasks)
        
        events.append({
            "status": "Completed" if result.status.value == "passed" else "Failed",
            "timeline": "Execution completed successfully" if result.status.value == "passed" else "Execution completed with error",
            "log": result.error_message or "All steps completed.",
            "screenshot": result.screenshot_path,
            "artifact": {
                "duration": result.duration_seconds,
                "status": result.status.value,
                "screenshot": result.screenshot_path,
                "video": result.video_path,
                "trace": result.trace_path
            }
        })
        
    return {"status": "success"}


@app.get("/api/v1/executions/{execution_id}")
def get_execution_result_agnostic(execution_id: UUID):
    raw = project_service.repo._read_raw()
    execs_raw = raw.get("execution_results", {})
    ex_id_str = str(execution_id)
    if ex_id_str in execs_raw:
        ex_data = execs_raw[ex_id_str]
        cleaned = dict(ex_data)
        return cleaned
    raise HTTPException(status_code=404, detail="Execution not found")


class LoginRequest(BaseModel):
    email: str
    password: str

class RefreshRequest(BaseModel):
    refresh_token: str

class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str
    role: str

class UserPasswordReset(BaseModel):
    password: str

class UserStatusToggle(BaseModel):
    is_active: bool

class RoleCreate(BaseModel):
    name: str

class RolePermissionsUpdate(BaseModel):
    role_id: str
    permission_ids: list[str]

class ProjectUserAssign(BaseModel):
    user_id: str
    role_id: str

# ==========================================================
# Object repository (spec 0004)
# ==========================================================

_OBJREPO = "/api/v1/projects/{project_id}/object-repository"


@app.get("/api/v1/object-repository/harvest-settings")
def get_object_repository_harvest_settings():
    # Global settings, no secrets: the execution engine reads them at run start.
    config = get_settings().object_repository
    return {
        "enabled": config.harvest_enabled,
        "max_pages": config.harvest_max_pages,
        "budget_ms": config.harvest_page_budget_ms,
    }


@app.get("/api/v1/accessibility/scan-settings")
def get_accessibility_scan_settings(
    project_id: UUID,
    caller: str = Depends(require_workspace_secret),
):
    """Spec 0011, AC-1 and AC-8: the execution engine reads this once at run start
    and echoes the gate fields back in its `completed` webhook. `enabled` is the
    engine's final answer: the project's switch AND the global one."""
    project = get_project_repository().get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    config = get_settings().accessibility
    return {
        "enabled": bool(config.enabled and getattr(project, "a11y_enabled", True) is not False),
        "gate_mode": str(getattr(project, "a11y_gate_mode", None) or "warn"),
        "block_severity": str(getattr(project, "a11y_block_severity", None) or "serious"),
        "wcag_tags": list(config.wcag_tags),
        "max_pages": config.max_pages,
        "page_budget_ms": config.page_budget_ms,
        "max_element_screenshots": config.max_element_screenshots,
        "max_nodes_per_rule": config.max_nodes_per_rule,
        "webhook_payload_max_bytes": config.webhook_payload_max_bytes,
    }


def _objrepo_error(exc: ObjectRepositoryError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _objrepo_audit(db: Session, user: UserDB, project_id: UUID):
    return lambda action, changes: log_audit(db, user.id, project_id, action, "OBJECT_REPOSITORY", changes)


def _page_response(page, counts: dict | None = None, score: dict | None = None) -> PageResponse:
    return PageResponse(**page.model_dump(exclude={"elements"}), counts=counts, score=score)


def _heal_response(page, heal) -> HealResponse:
    element = next((e for e in page.elements if e.id == heal.element_id), None)
    key = f"{page.name}.{element.name}" if element else page.name
    return HealResponse(**heal.model_dump(exclude={"project_id", "old_fingerprint", "candidate_fingerprint", "locators_version"}), key=key)


def _element_response(page, element) -> ElementResponse:
    latest = max(element.heals, key=lambda heal: heal.created_at, default=None)
    return ElementResponse(
        **element.model_dump(exclude={"locators", "heals", "fingerprint", "applied_execution_ids", "is_deleted", "deleted_at", "deleted_by"}),
        key=f"{page.name}.{element.name}",
        locators=[locator.model_dump() for locator in sorted(element.locators, key=lambda loc: loc.position)],
        latest_heal=_heal_response(page, latest) if latest else None,
    )


@app.get(f"{_OBJREPO}/permissions")
def get_object_repository_permissions(
    project_id: UUID,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("ObjectRepository", "view")),
):
    """The caller's ObjectRepository actions on this project, so the view shows only what works."""
    return {"actions": permitted_actions(db, user, "ObjectRepository", project_id, ["view", "create", "edit", "delete"])}


@app.get(f"{_OBJREPO}/pages", response_model=PageListResponse)
def list_object_pages(
    project_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    authorized: bool = Depends(has_permission("ObjectRepository", "view")),
):
    try:
        pages, total = object_repository_service.list_pages(project_id, limit, offset)
    except ObjectRepositoryError as exc:
        raise _objrepo_error(exc)
    return PageListResponse(pages=[_page_response(item["page"], item["counts"], item["score"]) for item in pages], total=total)


@app.post(f"{_OBJREPO}/pages", response_model=PageResponse, status_code=status.HTTP_201_CREATED)
def create_object_page(
    project_id: UUID,
    payload: PageCreate,
    user: UserDB = Depends(require_project_permission("ObjectRepository", "create")),
    db: Session = Depends(get_db),
):
    try:
        page = object_repository_service.create_page(
            project_id, payload.name, payload.url_pattern, audit=_objrepo_audit(db, user, project_id)
        )
    except ObjectRepositoryError as exc:
        raise _objrepo_error(exc)
    queue_knowledge_source(project_id, "locator", page.id)
    return _page_response(page)


@app.patch(f"{_OBJREPO}/pages/{{page_id}}", response_model=PageUpdateResponse)
def update_object_page(
    project_id: UUID,
    page_id: UUID,
    payload: PageUpdate,
    user: UserDB = Depends(require_project_permission("ObjectRepository", "edit")),
    db: Session = Depends(get_db),
):
    try:
        page, rewritten = object_repository_service.update_page(
            project_id, page_id, payload.model_dump(exclude_unset=True), audit=_objrepo_audit(db, user, project_id)
        )
    except ObjectRepositoryError as exc:
        raise _objrepo_error(exc)
    queue_knowledge_source(project_id, "locator", page.id)
    return PageUpdateResponse(page=_page_response(page), scripts_rewritten=rewritten)


@app.delete(f"{_OBJREPO}/pages/{{page_id}}", response_model=PageDeleteResponse)
def delete_object_page(
    project_id: UUID,
    page_id: UUID,
    user: UserDB = Depends(require_project_permission("ObjectRepository", "delete")),
    db: Session = Depends(get_db),
):
    try:
        deleted, references = object_repository_service.delete_page(
            project_id, page_id, deleted_by=user.id, audit=_objrepo_audit(db, user, project_id)
        )
    except ObjectRepositoryError as exc:
        raise _objrepo_error(exc)
    queue_knowledge_source(project_id, "locator", page_id, remove=True)
    return PageDeleteResponse(elements_deleted=deleted, referencing_test_cases=references)


@app.get(f"{_OBJREPO}/pages/{{page_id}}/elements", response_model=ElementListResponse)
def list_object_elements(
    project_id: UUID,
    page_id: UUID,
    health: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    authorized: bool = Depends(has_permission("ObjectRepository", "view")),
):
    try:
        page, elements, total = object_repository_service.list_elements(project_id, page_id, health, limit, offset)
    except ObjectRepositoryError as exc:
        raise _objrepo_error(exc)
    return ElementListResponse(elements=[_element_response(page, element) for element in elements], total=total)


@app.post(f"{_OBJREPO}/pages/{{page_id}}/elements", response_model=ElementResponse, status_code=status.HTTP_201_CREATED)
def create_object_element(
    project_id: UUID,
    page_id: UUID,
    payload: ElementCreate,
    user: UserDB = Depends(require_project_permission("ObjectRepository", "create")),
    db: Session = Depends(get_db),
):
    try:
        page, element = object_repository_service.create_element(
            project_id,
            page_id,
            payload.name,
            payload.element_type,
            [locator.model_dump() for locator in payload.locators],
            payload.description,
            audit=_objrepo_audit(db, user, project_id),
        )
    except ObjectRepositoryError as exc:
        raise _objrepo_error(exc)
    queue_knowledge_source(project_id, "locator", page.id)
    return _element_response(page, element)


@app.patch(f"{_OBJREPO}/elements/{{element_id}}", response_model=ElementUpdateResponse)
def update_object_element(
    project_id: UUID,
    element_id: UUID,
    payload: ElementUpdate,
    user: UserDB = Depends(require_project_permission("ObjectRepository", "edit")),
    db: Session = Depends(get_db),
):
    changes = payload.model_dump(exclude_unset=True)
    try:
        page, element, rewritten = object_repository_service.update_element(
            project_id, element_id, changes, audit=_objrepo_audit(db, user, project_id)
        )
    except ObjectRepositoryError as exc:
        raise _objrepo_error(exc)
    queue_knowledge_source(project_id, "locator", page.id)
    return ElementUpdateResponse(element=_element_response(page, element), scripts_rewritten=rewritten)


@app.delete(f"{_OBJREPO}/elements/{{element_id}}", response_model=ElementDeleteResponse)
def delete_object_element(
    project_id: UUID,
    element_id: UUID,
    user: UserDB = Depends(require_project_permission("ObjectRepository", "delete")),
    db: Session = Depends(get_db),
):
    try:
        references = object_repository_service.delete_element(
            project_id, element_id, deleted_by=user.id, audit=_objrepo_audit(db, user, project_id)
        )
    except ObjectRepositoryError as exc:
        raise _objrepo_error(exc)
    _queue_element_page(project_id, element_id)
    return ElementDeleteResponse(referencing_test_cases=references)


def _queue_element_page(project_id: UUID, element_id: UUID) -> None:
    """Index the page again after one of its elements was deleted (spec 0006, AC-15)."""
    try:
        found = object_repository_service.load(project_id).find_element(element_id)
    except Exception:
        logger.exception("Could not find the page of deleted element %s", element_id)
        return
    if found:
        queue_knowledge_source(project_id, "locator", found[0].id)


# Self healing heals (spec 0011, AC-12, AC-13)


@app.get(f"{_OBJREPO}/heals", response_model=list[HealResponse])
def list_object_heals(
    project_id: UUID,
    heal_status: Optional[str] = Query(None, alias="status"),
    authorized: bool = Depends(has_permission("ObjectRepository", "view")),
):
    if heal_status is not None and heal_status not in HEAL_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {', '.join(HEAL_STATUSES)}")
    try:
        heals = locator_healing_service.list_heals(project_id, heal_status)
    except ObjectRepositoryError as exc:
        raise _objrepo_error(exc)
    return [_heal_response(page, heal) for page, heal in heals]


@app.post(f"{_OBJREPO}/heals/{{heal_id}}/accept", response_model=HealDecisionResponse)
def accept_object_heal(
    project_id: UUID,
    heal_id: UUID,
    user: UserDB = Depends(require_project_permission("ObjectRepository", "edit")),
    db: Session = Depends(get_db),
):
    try:
        page, element, heal = locator_healing_service.accept_heal(
            project_id, heal_id, user.id, audit=_objrepo_audit(db, user, project_id)
        )
    except ObjectRepositoryError as exc:
        raise _objrepo_error(exc)
    queue_knowledge_source(project_id, "locator", page.id)
    return HealDecisionResponse(heal=_heal_response(page, heal), element=_element_response(page, element))


@app.post(f"{_OBJREPO}/heals/{{heal_id}}/reject", response_model=HealResponse)
def reject_object_heal(
    project_id: UUID,
    heal_id: UUID,
    user: UserDB = Depends(require_project_permission("ObjectRepository", "edit")),
    db: Session = Depends(get_db),
):
    try:
        page, _, heal = locator_healing_service.reject_heal(
            project_id, heal_id, user.id, audit=_objrepo_audit(db, user, project_id)
        )
    except ObjectRepositoryError as exc:
        raise _objrepo_error(exc)
    return _heal_response(page, heal)


@app.post(f"{_OBJREPO}/heals/{{heal_id}}/revert", response_model=HealDecisionResponse)
def revert_object_heal(
    project_id: UUID,
    heal_id: UUID,
    user: UserDB = Depends(require_project_permission("ObjectRepository", "edit")),
    db: Session = Depends(get_db),
):
    try:
        page, element, heal = locator_healing_service.revert_heal(
            project_id, heal_id, user.id, audit=_objrepo_audit(db, user, project_id)
        )
    except ObjectRepositoryError as exc:
        raise _objrepo_error(exc)
    queue_knowledge_source(project_id, "locator", page.id)
    return HealDecisionResponse(heal=_heal_response(page, heal), element=_element_response(page, element))


@app.post(f"{_OBJREPO}/scan", response_model=ScanStartResponse, status_code=status.HTTP_202_ACCEPTED)
def start_object_repository_scan(
    project_id: UUID,
    background_tasks: BackgroundTasks,
    user: UserDB = Depends(require_project_permission("ObjectRepository", "create")),
):
    try:
        scan_id = object_repository_scan_service.start(project_id, user.id)
    except ObjectRepositoryError as exc:
        raise _objrepo_error(exc)
    background_tasks.add_task(object_repository_scan_service.run, scan_id)
    return ScanStartResponse(scan_id=scan_id)


@app.get(f"{_OBJREPO}/scan/{{scan_id}}", response_model=ScanStatusResponse)
def get_object_repository_scan(
    project_id: UUID,
    scan_id: str,
    authorized: bool = Depends(has_permission("ObjectRepository", "view")),
):
    try:
        return ScanStatusResponse(**object_repository_scan_service.status(project_id, scan_id))
    except ObjectRepositoryError as exc:
        raise _objrepo_error(exc)


@app.post("/api/v1/auth/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(UserDB).filter(UserDB.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account is deactivated")
        
    access_token = create_access_token(data={"sub": user.email, "role": user.role})
    refresh_token = create_refresh_token(data={"sub": user.email})
    
    from datetime import timedelta
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    db_token = RefreshTokenDB(
        user_id=user.id,
        token=refresh_token,
        expires_at=expires_at,
        revoked=False
    )
    db.add(db_token)
    db.commit()
    
    log_audit(db, user.id, None, "LOGIN", "USER", {"email": user.email})
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role
        }
    }

@app.post("/api/v1/auth/refresh")
def refresh_session(payload: RefreshRequest, db: Session = Depends(get_db)):
    db_token = db.query(RefreshTokenDB).filter(RefreshTokenDB.token == payload.refresh_token).first()
    if not db_token or db_token.revoked or db_token.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
        
    user = db.query(UserDB).filter(UserDB.id == db_token.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
        
    access_token = create_access_token(data={"sub": user.email, "role": user.role})
    return {"access_token": access_token}

@app.post("/api/v1/auth/logout")
def logout(payload: RefreshRequest, user: UserDB = Depends(get_current_user), db: Session = Depends(get_db)):
    db_token = db.query(RefreshTokenDB).filter(RefreshTokenDB.token == payload.refresh_token).first()
    if db_token:
        db_token.revoked = True
        db.commit()
    log_audit(db, user.id, None, "LOGOUT", "USER", {"email": user.email})
    return {"status": "success"}

@app.get("/api/v1/admin/users")
def list_users(
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("Users", "view"))
):
    users = db.query(UserDB).all()
    return [
        {
            "id": str(u.id),
            "email": u.email,
            "full_name": u.full_name,
            "role": u.role,
            "is_active": u.is_active,
            "created_at": u.created_at
        }
        for u in users
    ]

@app.post("/api/v1/admin/users")
def create_user(
    payload: UserCreate,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("Users", "create"))
):
    exists = db.query(UserDB).filter(UserDB.email == payload.email).first()
    if exists:
        raise HTTPException(status_code=400, detail="User with this email already exists")
        
    new_user = UserDB(
        email=payload.email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
        is_active=True
    )
    db.add(new_user)
    db.commit()
    
    log_audit(db, user.id, None, "CREATE", "USER", {"new_user_email": payload.email, "role": payload.role})
    return {"status": "success", "user_id": str(new_user.id)}

@app.put("/api/v1/admin/users/{user_id}/status")
def toggle_user_status(
    user_id: UUID,
    payload: UserStatusToggle,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("Users", "edit"))
):
    target = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
        
    old_status = target.is_active
    target.is_active = payload.is_active
    db.commit()
    
    log_audit(db, user.id, None, "UPDATE", "USER", {
        "user_email": target.email,
        "is_active": {"old": old_status, "new": payload.is_active}
    })
    return {"status": "success"}

@app.put("/api/v1/admin/users/{user_id}/password")
def reset_user_password(
    user_id: UUID,
    payload: UserPasswordReset,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("Users", "edit"))
):
    target = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
        
    target.password_hash = hash_password(payload.password)
    db.commit()
    
    log_audit(db, user.id, None, "UPDATE", "USER", {"user_email": target.email, "action": "password_reset"})
    return {"status": "success"}

@app.get("/api/v1/admin/roles")
def list_roles(
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("Roles", "view"))
):
    roles = db.query(RoleDB).all()
    return [{"id": str(r.id), "name": r.name} for r in roles]

@app.post("/api/v1/admin/roles")
def create_role(
    payload: RoleCreate,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("Roles", "create"))
):
    exists = db.query(RoleDB).filter(RoleDB.name == payload.name).first()
    if exists:
        raise HTTPException(status_code=400, detail="Role name already exists")
        
    new_role = RoleDB(name=payload.name)
    db.add(new_role)
    db.commit()
    
    log_audit(db, user.id, None, "CREATE", "ROLE", {"role_name": payload.name})
    return {"status": "success", "role_id": str(new_role.id)}

@app.get("/api/v1/admin/permissions")
def get_permissions_matrix(
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("Permissions", "view"))
):
    perms = db.query(PermissionDB).all()
    role_perms = db.query(RolePermissionDB).all()
    return {
        "permissions": [{"id": str(p.id), "module": p.module, "action": p.action} for p in perms],
        "role_permissions": [{"role_id": str(rp.role_id), "permission_id": str(rp.permission_id)} for rp in role_perms]
    }

@app.post("/api/v1/admin/permissions")
def update_role_permissions(
    payload: RolePermissionsUpdate,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("Permissions", "edit"))
):
    role_uuid = UUID(payload.role_id)
    db.query(RolePermissionDB).filter(RolePermissionDB.role_id == role_uuid).delete()
    for p_id in payload.permission_ids:
        rp = RolePermissionDB(role_id=role_uuid, permission_id=UUID(p_id))
        db.add(rp)
    db.commit()
    
    role = db.query(RoleDB).filter(RoleDB.id == role_uuid).first()
    log_audit(db, user.id, None, "UPDATE", "PERMISSION", {
        "role_name": role.name if role else str(role_uuid),
        "permission_count": len(payload.permission_ids)
    })
    return {"status": "success"}

@app.post("/api/v1/projects/{project_id}/users")
def assign_user_to_project(
    project_id: UUID,
    payload: ProjectUserAssign,
    request: Request,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("Users", "edit"))
):
    require_project_access(
        user, project_id, db, projects=project_service, request=request, module="Users", action="edit",
    )
    user_uuid = UUID(payload.user_id)
    role_uuid = UUID(payload.role_id)
    
    exists = db.query(ProjectUserDB).filter(
        ProjectUserDB.project_id == project_id,
        ProjectUserDB.user_id == user_uuid
    ).first()
    
    if exists:
        old_role_id = exists.role_id
        exists.role_id = role_uuid
        action_name = "UPDATE"
        field_changes = {"role_id": {"old": str(old_role_id), "new": str(role_uuid)}}
    else:
        mapping = ProjectUserDB(project_id=project_id, user_id=user_uuid, role_id=role_uuid)
        db.add(mapping)
        action_name = "CREATE"
        field_changes = {"user_id": str(user_uuid), "role_id": str(role_uuid)}
        
    db.commit()
    log_audit(db, user.id, project_id, action_name, "USER", field_changes)
    return {"status": "success"}

@app.get("/api/v1/projects/{project_id}/users")
def list_project_users(
    project_id: UUID,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("Users", "view"))
):
    mappings = db.query(ProjectUserDB).filter(ProjectUserDB.project_id == project_id).all()
    results = []
    for m in mappings:
        u = db.query(UserDB).filter(UserDB.id == m.user_id).first()
        r = db.query(RoleDB).filter(RoleDB.id == m.role_id).first()
        if u and r:
            results.append({
                "user_id": str(u.id),
                "email": u.email,
                "full_name": u.full_name,
                "role_id": str(r.id),
                "role_name": r.name
            })
    return results

@app.get("/api/v1/admin/audit-logs")
def get_audit_logs(
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorized: bool = Depends(has_permission("Settings", "view"))
):
    logs = db.query(AuditLogDB).order_by(AuditLogDB.timestamp.desc()).limit(100).all()
    results = []
    for l in logs:
        u = db.query(UserDB).filter(UserDB.id == l.user_id).first() if l.user_id else None
        results.append({
            "id": str(l.id),
            "user_email": u.email if u else "System",
            "project_id": str(l.project_id) if l.project_id else None,
            "action": l.action,
            "module": l.module,
            "field_changes": l.field_changes,
            "timestamp": l.timestamp
        })
    return results


@app.post("/api/v1/webhooks/jira")
def jira_webhook(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    caller: str = Depends(require_jira_secret),
    bug_reports: BugReportService = Depends(get_bug_report_service),
):
    """Receives JIRA webhook events when bugs are updated/resolved."""
    issue = payload.get("issue", {})
    key = issue.get("key")
    if not key:
        return {"status": "ignored", "reason": "No issue key in payload"}

    # Knowledge (spec 0006, AC-14a): every status change reindexes the defect, before the
    # resolved status filter, once for each project that still links the key.
    from backend.services.knowledge_sources import defect_linked

    knowledge_repo = get_project_repository()
    for linked_project in knowledge_repo.list_projects():
        if defect_linked(knowledge_repo, linked_project, key):
            queue_defect_keys(linked_project.id, [key])

    status_name = issue.get("fields", {}).get("status", {}).get("name")
    if not status_name:
        return {"status": "ignored", "reason": "No status name in payload"}
        
    settings = get_settings()
    resolved_statuses = settings.jira.resolved_statuses
    if status_name not in resolved_statuses:
        return {"status": "ignored", "reason": f"Status '{status_name}' is not in resolved list."}
        
    from backend.database.db import provider
    
    repo = get_project_repository()
    projects = repo.list_projects()
    updated_count = 0
    
    for proj in projects:
        tcs = repo.get_test_cases_for_project(proj.id)
        for tc in tcs:
            if tc.jira_issue_key == key:
                tc.status = "retest_pending"
                repo.update_test_case(tc)
                updated_count += 1
                if provider in ["sql", "postgres"] and db:
                    log_audit(db, None, proj.id, "JIRA_UPDATE", "TESTCASE", {"jira_issue_key": key, "status": "retest_pending"})
                    
    if updated_count > 0 and provider in ["sql", "postgres"] and db:
        db.commit()

    try:
        resolved_reports = bug_reports.mark_resolved_by_jira_key(key)
    except Exception:
        logger.exception("Could not mark bug reports resolved: jira_key=%s", key)
        resolved_reports = 0

    return {"status": "success", "updated_count": updated_count, "resolved_bug_reports": resolved_reports}


@app.post("/api/v1/projects/{project_id}/requirements/{requirement_id}/sync-jira")
def sync_requirement_jira(
    project_id: UUID,
    requirement_id: UUID,
    user: UserDB = Depends(require_project_permission("Requirements", "edit")),
):
    """Manually synchronize approved scenarios to JIRA as Stories."""
    _require_requirement_in_project(project_id, requirement_id)
    repo = get_project_repository()
    requirement = repo.get_requirement(requirement_id)
    if not requirement:
        raise HTTPException(status_code=404, detail="Requirement not found")
        
    scenarios = repo.get_scenarios(requirement_id)
    
    from backend.graph.workflow import build_graph
    from backend.models.state import WorkflowState
    
    local_graph = build_graph()
    state = WorkflowState(project_id=project_id, requirement=requirement, generated_scenarios=scenarios)
    state.add_log("Triggering JIRA user story sync via service")
    
    config = {
        "configurable": {
            "operation": "sync_user_story",
            "user_id": str(user.id)
        }
    }
    raw_result = local_graph.invoke(state, config)
    final_state = WorkflowState(**raw_result)
    
    return {"status": "success", "logs": final_state.logs}


@app.post("/api/v1/projects/{project_id}/executions/{execution_id}/sync-bug")
def sync_execution_bug(
    project_id: UUID,
    execution_id: UUID,
    user: UserDB = Depends(require_project_permission("Executions", "edit")),
):
    """Manually synchronize a failed test run to JIRA as a Bug defect."""
    _require_execution_in_project(project_id, execution_id)
    repo = get_project_repository()
    ex = repo.get_execution_result(project_id, execution_id)
    if not ex:
        raise HTTPException(status_code=404, detail="Execution result not found")
        
    tc = repo.get_test_case(ex.test_case_id)
    if not tc:
        raise HTTPException(status_code=404, detail="TestCase not found")
        
    from backend.graph.workflow import build_graph
    from backend.models.state import WorkflowState
    
    local_graph = build_graph()
    state = WorkflowState(project_id=project_id, execution_results=[ex])
    state.add_log("Triggering JIRA bug sync via service")
    
    config = {
        "configurable": {
            "operation": "sync_bug",
            "user_id": str(user.id)
        }
    }
    raw_result = local_graph.invoke(state, config)
    final_state = WorkflowState(**raw_result)

    # Knowledge (spec 0006, AC-14a): the keys the sync saved, read back from the repository.
    saved_ex = repo.get_execution_result(project_id, execution_id)
    saved_tc = repo.get_test_case(ex.test_case_id)
    queue_defect_keys(project_id, [getattr(saved_ex, "jira_bug_id", None), getattr(saved_tc, "jira_issue_key", None)])
    
    return {"status": "success", "logs": final_state.logs}


# ---------------------------------------------------------------- bug reports (BugReportingAgent)


def _bug_report_or_404(service: BugReportService, project_id: UUID, report_id: UUID) -> BugReport:
    report = service.get(project_id, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Bug report not found")
    return report


def _run_bug_report_action(call):
    try:
        return call()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except BugReportError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/api/v1/projects/{project_id}/bug-reports", response_model=list[BugReport])
def list_bug_reports(
    project_id: UUID,
    status: BugReportStatus | None = None,
    user: UserDB = Depends(require_project_permission("Executions", "view")),
    service: BugReportService = Depends(get_bug_report_service),
):
    return service.list_reports(project_id, status)


@app.get("/api/v1/projects/{project_id}/bug-reports/{report_id}", response_model=BugReport)
def get_bug_report(
    project_id: UUID,
    report_id: UUID,
    user: UserDB = Depends(require_project_permission("Executions", "view")),
    service: BugReportService = Depends(get_bug_report_service),
):
    return _bug_report_or_404(service, project_id, report_id)


@app.post("/api/v1/projects/{project_id}/executions/{execution_id}/bug-report", response_model=BugReport)
def create_bug_report_from_execution(
    project_id: UUID,
    execution_id: UUID,
    user: UserDB = Depends(require_project_permission("Executions", "edit")),
    service: BugReportService = Depends(get_bug_report_service),
):
    """Draft a bug report for a failed run on request, whatever its triage category.
    Returns the run's existing report when it already has one."""
    _require_execution_in_project(project_id, execution_id)
    return _run_bug_report_action(lambda: service.report_for_execution(project_id, execution_id, actor=str(user.id)))


@app.patch("/api/v1/projects/{project_id}/bug-reports/{report_id}", response_model=BugReport)
def update_bug_report(
    project_id: UUID,
    report_id: UUID,
    data: BugReportUpdate,
    user: UserDB = Depends(require_project_permission("Executions", "edit")),
    service: BugReportService = Depends(get_bug_report_service),
):
    patch = data.model_dump(exclude_unset=True)
    return _run_bug_report_action(lambda: service.update(project_id, report_id, patch, actor=str(user.id)))


@app.post("/api/v1/projects/{project_id}/bug-reports/{report_id}/submit", response_model=BugReport)
def submit_bug_report(
    project_id: UUID,
    report_id: UUID,
    user: UserDB = Depends(require_project_permission("Executions", "edit")),
    service: BugReportService = Depends(get_bug_report_service),
):
    """File the report in Jira. A Jira failure returns the report with status submit_failed."""
    return _run_bug_report_action(lambda: service.submit(project_id, report_id, actor=str(user.id)))


@app.post("/api/v1/projects/{project_id}/bug-reports/{report_id}/dismiss", response_model=BugReport)
def dismiss_bug_report(
    project_id: UUID,
    report_id: UUID,
    data: BugReportDismiss,
    user: UserDB = Depends(require_project_permission("Executions", "edit")),
    service: BugReportService = Depends(get_bug_report_service),
):
    return _run_bug_report_action(lambda: service.dismiss(project_id, report_id, data.reason, actor=str(user.id)))


# ---------------------------------------------------------------- accessibility (spec 0011)


@app.get("/api/v1/projects/{project_id}/executions/{execution_id}/accessibility")
def get_execution_accessibility(
    project_id: UUID,
    execution_id: UUID,
    user: UserDB = Depends(require_project_permission("Executions", "view")),
    service: AccessibilityService = Depends(get_accessibility_service),
):
    """The run's accessibility report, or its not scanned state (AC-12, AC-15)."""
    _require_execution_in_project(project_id, execution_id)
    report = service.report_for_execution(project_id, execution_id)
    if report is None:
        gate = service.gate_for_execution(project_id, execution_id)
        return {"gate_status": gate["gate_status"], "gate_reason": gate["gate_reason"]}
    return report.model_dump(mode="json")


@app.get("/api/v1/projects/{project_id}/accessibility/reports/{report_id}/screenshots/{name}")
def get_accessibility_screenshot(
    project_id: UUID,
    report_id: UUID,
    name: str,
    user: UserDB = Depends(require_project_permission("Executions", "view")),
    service: AccessibilityService = Depends(get_accessibility_service),
):
    """An element screenshot of a finding (AC-7, AC-12). A name with a path in
    it, or one no finding records, is a 404."""
    path = service.screenshot(project_id, report_id, name)
    if path is None:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(path, media_type="image/png")


@app.get("/api/v1/projects/{project_id}/accessibility/gate", response_model=AccessibilityGateResponse)
def get_accessibility_gate(
    project_id: UUID,
    execution_id: UUID | None = None,
    batch_id: str | None = None,
    user: UserDB | None = Depends(workspace_or_project_permission("Executions", "view")),
    service: AccessibilityService = Depends(get_accessibility_service),
):
    """The release gate of one run or one batch, for CI (AC-9). Exactly one of
    `execution_id` or `batch_id`; the batch id is the runs' `batch_id` (spec 0012)."""
    if (execution_id is None) == (not batch_id):
        raise HTTPException(status_code=422, detail="Give exactly one of execution_id or batch_id")
    if get_project_repository().get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if execution_id is not None:
        _require_execution_in_project(project_id, execution_id)
        return service.gate_for_execution(project_id, execution_id)
    from backend.services.execution_service import ExecutionService

    try:
        batch_uuid = UUID(batch_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Batch not found")
    run_ids = [r.id for r in get_project_repository().get_execution_results_for_batch(project_id, batch_uuid)]
    log_state = ExecutionService.batch_log_state(project_id, batch_uuid)
    gate = service.gate_for_batch(project_id, str(batch_uuid), run_ids, log_state)
    if gate is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    gate["in_progress"] = log_state == "running"
    return gate


def _a11y_scan_error(exc: AccessibilityScanError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@app.get("/api/v1/projects/{project_id}/accessibility/scan-pages", response_model=AccessibilityScanPagesResponse)
def get_accessibility_scan_pages(
    project_id: UUID,
    user: UserDB = Depends(require_project_permission("Executions", "view")),
    scans: AccessibilityScanService = Depends(get_accessibility_scan_service),
):
    """The scan dialog's page checklist (AC-10). Wildcard patterns come back skipped."""
    try:
        return {"pages": scans.scan_pages(project_id), "max_pages": scans.config.max_pages}
    except AccessibilityScanError as exc:
        raise _a11y_scan_error(exc)


@app.post(
    "/api/v1/projects/{project_id}/accessibility/scans",
    response_model=AccessibilityScanStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_accessibility_scan(
    project_id: UUID,
    data: AccessibilityScanRequest,
    user: UserDB = Depends(require_project_permission("Executions", "edit")),
    scans: AccessibilityScanService = Depends(get_accessibility_scan_service),
):
    """Starts a standalone scan of chosen pages, optionally signed in (AC-10, AC-11)."""
    try:
        report = scans.start(project_id, data.paths, data.include_login, user_id=user.id)
    except AccessibilityScanError as exc:
        raise _a11y_scan_error(exc)
    scans.launch(report.id)
    return {"report_id": str(report.id), "status": report.status.value}


@app.get("/api/v1/projects/{project_id}/accessibility/reports")
def list_accessibility_reports(
    project_id: UUID,
    source: Literal["execution", "scan"] | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: UserDB = Depends(require_project_permission("Executions", "view")),
    service: AccessibilityService = Depends(get_accessibility_service),
    scans: AccessibilityScanService = Depends(get_accessibility_scan_service),
):
    """Paged report summaries, newest first, without findings (AC-15)."""
    if get_project_repository().get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    scans.expire_stale(project_id)
    reports, total = service.repo.list_reports(project_id, source, limit, offset)
    return {
        "items": [r.model_dump(mode="json", exclude={"findings"}) for r in reports],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/v1/projects/{project_id}/accessibility/reports/{report_id}")
def get_accessibility_report(
    project_id: UUID,
    report_id: UUID,
    user: UserDB = Depends(require_project_permission("Executions", "view")),
    service: AccessibilityService = Depends(get_accessibility_service),
    scans: AccessibilityScanService = Depends(get_accessibility_scan_service),
):
    """One full report with findings. Another project's report is a 404 (AC-15)."""
    scans.expire_stale(project_id)
    report = service.report(project_id, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Accessibility report not found")
    return report.model_dump(mode="json")


@app.post("/api/v1/admin/reset-demo-data")
def reset_demo_data(
    user: UserDB = Depends(get_current_user),
    authorized: bool = Depends(has_permission("Settings", "edit")),
):
    # Spec 0009, AC-13: Settings.edit, not a hardcoded role list.

    try:
        repo = get_project_repository()
        repo.reset_demo_data()
        
        # Clean up files and folders from disk
        import shutil
        from pathlib import Path
        
        # Collect active execution and testcase IDs across all remaining projects
        active_exec_ids = set()
        active_tc_ids = set()
        
        projects = repo.list_projects()
        for p in projects:
            for ex in repo.get_execution_results(p.id):
                active_exec_ids.add(str(ex.id))
            for tc in repo.get_test_cases_for_project(p.id):
                active_tc_ids.add(str(tc.id))
                
        # 1. Clean public/artifacts/<execution_id>
        public_artifacts_dir = Path("backend/playwrightt/public/artifacts")
        if public_artifacts_dir.exists():
            for folder in public_artifacts_dir.iterdir():
                if folder.is_dir() and folder.name != "default" and folder.name not in active_exec_ids:
                    try:
                        shutil.rmtree(folder)
                    except Exception as fe:
                        logger.warning(f"Failed to remove orphaned public execution artifact folder {folder.name}: {fe}")
                        
        # 2. Clean data/reports/
        reports_dir = Path("data/reports")
        if reports_dir.exists():
            for report_file in reports_dir.iterdir():
                if report_file.is_file():
                    name = report_file.name
                    if name.startswith("report_"):
                        parts = name.split("_")
                        if len(parts) >= 2:
                            exec_id_part = parts[1].split(".")[0]
                            if exec_id_part not in active_exec_ids:
                                try:
                                    report_file.unlink()
                                except Exception as fe:
                                    logger.warning(f"Failed to remove orphaned report file {name}: {fe}")
                                    
        # 3. Clean backend/playwright/artifacts/
        playwright_artifacts_dir = Path("backend/playwright/artifacts")
        if playwright_artifacts_dir.exists():
            for folder in playwright_artifacts_dir.iterdir():
                if folder.is_dir() and folder.name not in active_tc_ids:
                    try:
                        shutil.rmtree(folder)
                    except Exception as fe:
                        logger.warning(f"Failed to remove orphaned playwright testcase artifact folder {folder.name}: {fe}")
                        
        # 4. Clean backend/playwrightt/artifacts/
        playwrightt_artifacts_dir = Path("backend/playwrightt/artifacts")
        if playwrightt_artifacts_dir.exists():
            for folder in playwrightt_artifacts_dir.iterdir():
                if folder.is_dir() and folder.name not in active_tc_ids:
                    try:
                        shutil.rmtree(folder)
                    except Exception as fe:
                        logger.warning(f"Failed to remove orphaned playwrightt testcase folder {folder.name}: {fe}")

        return {"status": "success", "message": "Demo data reset successfully."}
    except Exception as e:
        logger.error(f"Error resetting demo data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================================
# Chat assistant (spec 0005)
# ==========================================================

def _chat_action_audit(event: str, *, user_id, project_id, action: dict) -> None:
    """AC-22: one audit entry per confirm, cancel, and final result. Runs in
    worker threads too, so it opens its own session."""
    from backend.database.db import SessionLocal

    permission = action.get("permission") or {}
    db = SessionLocal()
    try:
        log_audit(
            db,
            UUID(str(user_id)) if user_id else None,
            project_id,
            f"CHAT_ACTION_{event}",
            permission.get("module") or "CHAT",
            action_audit_fields(event, action),
        )
    finally:
        db.close()


# The executor reads workflow_service through the module global so tests can swap it.
chat_service = ChatService(
    action_executor=ChatActionExecutor(
        workflow_service=lambda: workflow_service,
        project_service=project_service,
        script_jobs=lambda: script_generation_jobs,
        execution_service=lambda: __import__(
            "backend.services.execution_service", fromlist=["ExecutionService"]
        ).ExecutionService(),
        audit=_chat_action_audit,
    ),
)

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def require_chat_enabled():
    """When CHAT_ENABLED is false every chat route answers 404."""
    if not get_settings().chat.enabled:
        raise HTTPException(status_code=404, detail="Not Found")


def _chat_http_error(exc: ChatError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@app.post("/api/v1/projects/{project_id}/chat/messages", dependencies=[Depends(require_chat_enabled)])
def post_chat_message(
    project_id: UUID,
    payload: ChatMessageCreate,
    request: Request,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = require_project_access(user, project_id, db, projects=project_service)
    # AC-11: the model is offered only the actions the user may run here.
    # Decided before the stream starts, while the request session is open.
    allowed = allowed_action_types(lambda module, action: check_permission(db, user, project_id, module, action))
    try:
        turn = chat_service.prepare_turn(
            project, user.id, payload.content, payload.thread_id, payload.view_context, allowed_actions=allowed,
        )
    except ChatError as exc:
        raise _chat_http_error(exc)
    return StreamingResponse(
        guard_disconnect(request, chat_service.stream_turn(turn)),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@app.post("/api/v1/projects/{project_id}/chat/threads/{thread_id}/retry", dependencies=[Depends(require_chat_enabled)])
def retry_chat_message(
    project_id: UUID,
    thread_id: UUID,
    request: Request,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = require_project_access(user, project_id, db, projects=project_service)
    allowed = allowed_action_types(lambda module, action: check_permission(db, user, project_id, module, action))
    try:
        turn = chat_service.prepare_retry(project, user.id, thread_id, allowed_actions=allowed)
    except ChatError as exc:
        raise _chat_http_error(exc)
    return StreamingResponse(
        guard_disconnect(request, chat_service.stream_turn(turn)),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@app.post("/api/v1/chat/help", dependencies=[Depends(require_chat_enabled)])
def post_chat_help(
    payload: ChatHelpCreate,
    request: Request,
    user: UserDB = Depends(get_current_user),
):
    try:
        chat_service.check_rate_limit(user.id)
    except ChatError as exc:
        raise _chat_http_error(exc)
    return StreamingResponse(
        guard_disconnect(request, chat_service.stream_help(user.id, payload.content, payload.history)),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@app.get(
    "/api/v1/projects/{project_id}/chat/threads",
    response_model=ChatThreadPage,
    dependencies=[Depends(require_chat_enabled)],
)
def list_chat_threads(
    project_id: UUID,
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_project_access(user, project_id, db, projects=project_service)
    try:
        threads, next_cursor = chat_service.list_threads(project_id, user.id, cursor, limit)
    except ChatError as exc:
        raise _chat_http_error(exc)
    return ChatThreadPage(
        items=[ChatThreadItem(id=t.id, title=t.title, last_activity_at=t.last_activity_at) for t in threads],
        next_cursor=next_cursor,
    )


@app.get(
    "/api/v1/projects/{project_id}/chat/threads/{thread_id}/messages",
    response_model=ChatMessagePage,
    dependencies=[Depends(require_chat_enabled)],
)
def list_chat_messages(
    project_id: UUID,
    thread_id: UUID,
    before_seq: Optional[int] = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_project_access(user, project_id, db, projects=project_service)
    try:
        messages, has_more = chat_service.list_messages(project_id, user.id, thread_id, before_seq, limit)
    except ChatError as exc:
        raise _chat_http_error(exc)
    return ChatMessagePage(
        items=[ChatMessageItem(**m.model_dump(include=set(ChatMessageItem.model_fields))) for m in messages],
        has_more=has_more,
    )


@app.delete(
    "/api/v1/projects/{project_id}/chat/threads/{thread_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_chat_enabled)],
)
def delete_chat_thread(
    project_id: UUID,
    thread_id: UUID,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_project_access(user, project_id, db, projects=project_service)
    try:
        chat_service.delete_thread(project_id, user.id, thread_id)
    except ChatError as exc:
        raise _chat_http_error(exc)


@app.post(
    "/api/v1/projects/{project_id}/chat/messages/{message_id}/action/confirm",
    dependencies=[Depends(require_chat_enabled)],
)
def confirm_chat_action(
    project_id: UUID,
    message_id: UUID,
    payload: Optional[ChatActionConfirm] = Body(default=None),
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = require_project_access(user, project_id, db, projects=project_service)
    try:
        return chat_service.confirm_action(
            project, user.id, message_id, payload.confirm_text if payload else None,
            can=lambda module, action: check_permission(db, user, project_id, module, action),
        )
    except ChatError as exc:
        raise _chat_http_error(exc)


@app.post(
    "/api/v1/projects/{project_id}/chat/messages/{message_id}/action/cancel",
    dependencies=[Depends(require_chat_enabled)],
)
def cancel_chat_action(
    project_id: UUID,
    message_id: UUID,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = require_project_access(user, project_id, db, projects=project_service)
    try:
        return chat_service.cancel_action(project, user.id, message_id)
    except ChatError as exc:
        raise _chat_http_error(exc)


@app.get(
    "/api/v1/projects/{project_id}/chat/messages/{message_id}/action",
    dependencies=[Depends(require_chat_enabled)],
)
def get_chat_action(
    project_id: UUID,
    message_id: UUID,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = require_project_access(user, project_id, db, projects=project_service)
    try:
        return chat_service.get_action(project, user.id, message_id)
    except ChatError as exc:
        raise _chat_http_error(exc)


app.mount("/", StaticFiles(directory="frontend", html=True), name="static")
