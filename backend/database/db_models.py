from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Column, String, Text, Float, Boolean, DateTime, ForeignKey, Integer, JSON, UniqueConstraint, Index, CheckConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from backend.database.db import Base

class UserDB(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(200), nullable=True)
    role = Column(String(50), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    projects = relationship("ProjectDB", back_populates="owner", foreign_keys="[ProjectDB.owner_id]")
    project_associations = relationship("ProjectUserDB", back_populates="user", cascade="all, delete-orphan")


class ProjectDB(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(200), nullable=False, index=True)
    description = Column(Text, nullable=False, default="")
    line_of_business = Column(String(100), nullable=False, default="general")
    framework = Column(String(100), nullable=False, default="playwright")
    jira_project_key = Column(String(100), nullable=True)
    target_url = Column(String(500), nullable=True, default=None)
    target_username = Column(String(255), nullable=True)
    target_password_enc = Column(String(500), nullable=True)
    is_safe_test_environment = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    rag_dataset_id = Column(String(100), nullable=True)
    # Bug Reporting Agent: file drafts to Jira without review above this confidence.
    bug_auto_submit = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    bug_auto_submit_min_confidence = Column(Float, nullable=False, default=0.85, server_default=text("0.85"))
    # Accessibility Agent (spec 0011, AC-13).
    a11y_enabled = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    a11y_gate_mode = Column(String(10), nullable=False, default="warn", server_default="warn")
    a11y_block_severity = Column(String(10), nullable=False, default="serious", server_default="serious")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_projects_owner_name"),
        Index(
            "uq_projects_owner_target_url",
            "owner_id",
            "target_url",
            unique=True,
            postgresql_where=text("owner_id IS NOT NULL AND target_url IS NOT NULL AND is_deleted = false"),
            sqlite_where=text("owner_id IS NOT NULL AND target_url IS NOT NULL AND is_deleted = 0"),
        ),
    )

    owner = relationship("UserDB", back_populates="projects", foreign_keys=[owner_id])
    requirements = relationship("RequirementDB", back_populates="project", cascade="all, delete-orphan")
    executions = relationship("ExecutionDB", back_populates="project", cascade="all, delete-orphan")
    reports = relationship("ReportDB", back_populates="project", cascade="all, delete-orphan")
    documents = relationship("DocumentDB", back_populates="project", cascade="all, delete-orphan")
    user_associations = relationship("ProjectUserDB", back_populates="project", cascade="all, delete-orphan")


class ReleaseDB(Base):
    __tablename__ = "releases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(50), nullable=False, default="Active")
    start_date = Column(DateTime(timezone=True), nullable=True)
    end_date = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    project = relationship("ProjectDB")


class TestCycleDB(Base):
    __tablename__ = "test_cycles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    release_id = Column(UUID(as_uuid=True), ForeignKey("releases.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(50), nullable=False, default="Active")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    release = relationship("ReleaseDB")


class RequirementDB(Base):
    __tablename__ = "requirements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    release_id = Column(UUID(as_uuid=True), ForeignKey("releases.id", ondelete="SET NULL"), nullable=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    source = Column(String(50), nullable=False, default="manual")
    uploaded_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    original_filename = Column(String(255), nullable=True)
    requirement_id = Column(String(100), nullable=True, index=True)
    requirement_title = Column(String(200), nullable=True)
    
    priority = Column(String(50), nullable=False, default="medium")
    business_domain = Column(String(100), nullable=False, default="general")
    attachments = Column(JSON, nullable=False, default=list)
    feature_mapping = Column(Text, nullable=True)
    jira_issue_key = Column(String(100), nullable=True, index=True)
    jira_issue_url = Column(String(500), nullable=True)
    jira_sync_status = Column(String(50), nullable=True)
    jira_last_synced_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "requirement_id", name="uq_requirements_project_req_id"),
    )

    project = relationship("ProjectDB", back_populates="requirements")
    scenarios = relationship("ScenarioDB", back_populates="requirement", cascade="all, delete-orphan")


class ScenarioDB(Base):
    __tablename__ = "scenarios"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    requirement_id = Column(UUID(as_uuid=True), ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False, index=True)
    scenario_ref_id = Column(String(20), nullable=True, index=True)  # US01, US02, US03...
    scenario_name = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    priority = Column(String(50), nullable=False, default="medium")
    confidence = Column(Float, nullable=False, default=0.0)
    approved = Column(Boolean, nullable=False, default=False, index=True)
    rejected = Column(Boolean, nullable=False, default=False, index=True)
    path_type = Column(String(100), nullable=False, default="happy_path")
    tags = Column(JSON, nullable=False, default=list)
    reviewer = Column(String(100), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    generated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    jira_issue_key = Column(String(100), nullable=True, index=True)
    jira_issue_id = Column(String(100), nullable=True)
    jira_issue_url = Column(String(500), nullable=True)
    jira_sync_status = Column(String(50), nullable=True)
    jira_last_synced_at = Column(DateTime(timezone=True), nullable=True)
    last_jira_sync_error = Column(Text, nullable=True)
    jira_sync_retry_count = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    requirement = relationship("RequirementDB", back_populates="scenarios")
    test_cases = relationship("TestCaseDB", back_populates="scenario", cascade="all, delete-orphan")
    notes = relationship("ScenarioNoteDB", back_populates="notes", cascade="all, delete-orphan")


class TestCaseDB(Base):
    __tablename__ = "test_cases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    scenario_id = Column(UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False, index=True)
    test_case_ref_id = Column(String(30), nullable=True, index=True)  # US01-TC01, US01-TC02...
    title = Column(String(200), nullable=False)
    preconditions = Column(JSON, nullable=False, default=list)
    steps = Column(JSON, nullable=False, default=list)
    expected_result = Column(Text, nullable=False)
    priority = Column(String(50), nullable=False, default="medium")
    status = Column(String(50), nullable=False, default="pending")
    confidence = Column(Float, nullable=False, default=0.0)
    evaluation_status = Column(String(50), nullable=False, default="pending")
    evaluation_reason = Column(Text, nullable=True)
    playwright_script = Column(Text, nullable=True)
    reviewer = Column(String(100), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    generated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    is_frozen = Column(Boolean, default=False, nullable=False)
    jira_issue_key = Column(String(100), nullable=True, index=True)
    jira_issue_url = Column(String(500), nullable=True)
    jira_sync_status = Column(String(50), nullable=True)
    jira_last_synced_at = Column(DateTime(timezone=True), nullable=True)
    test_data = Column(JSON, nullable=False, default=dict)
    test_data_sets = Column(JSON, nullable=False, default=list)
    test_data_profiles = Column(JSON, nullable=False, default=list, server_default=text("'[]'"))
    script_validation_status = Column(String(30), nullable=False, default="unverified", server_default=text("'unverified'"))
    script_validation_attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    script_validation_completed_at = Column(DateTime(timezone=True), nullable=True)
    script_validation_diagnostic = Column(Text, nullable=True)
    groundedness_score = Column(Float, nullable=True)
    unsupported_claims = Column(JSON, nullable=False, default=list, server_default=text("'[]'"))

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    scenario = relationship("ScenarioDB", back_populates="test_cases")
    executions = relationship("ExecutionDB", back_populates="test_case", cascade="all, delete-orphan")
    notes = relationship("TestCaseNoteDB", back_populates="test_case", cascade="all, delete-orphan")


class ExecutionDB(Base):
    __tablename__ = "executions"
    # Spec 0012: every batch read filters by project first, then batch.
    __table_args__ = (Index("ix_executions_project_batch", "project_id", "batch_id"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    test_case_id = Column(UUID(as_uuid=True), ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    test_cycle_id = Column(UUID(as_uuid=True), ForeignKey("test_cycles.id", ondelete="SET NULL"), nullable=True, index=True)
    # Spec 0012: the batch this run belongs to. No table of its own, so no FK.
    batch_id = Column(UUID(as_uuid=True), nullable=True)
    status = Column(String(50), nullable=False)
    duration_seconds = Column(Float, nullable=False, default=0.0)
    error_message = Column(Text, nullable=True)
    screenshot_path = Column(String(500), nullable=True)
    video_path = Column(String(500), nullable=True)
    trace_path = Column(String(500), nullable=True)
    browser_version = Column(String(100), nullable=True)
    executed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    timeline = Column(JSON, nullable=True)
    screenshots = Column(JSON, nullable=True)

    failure_category = Column(String(100), nullable=True)
    root_cause_summary = Column(Text, nullable=True)
    suggest_retry = Column(Boolean, default=False, nullable=True)
    retest_pending_candidate = Column(Boolean, default=False, nullable=True)
    trace_analysis = Column(Text, nullable=True)
    screenshot_findings = Column(Text, nullable=True)
    # Log analyser evidence (LogAnalysisService).
    failing_step = Column(Text, nullable=True)
    evidence = Column(JSON, nullable=True)
    confidence = Column(Float, nullable=True)
    flaky_score = Column(Float, nullable=True)
    frontend_error_detected = Column(Boolean, nullable=True)
    frontend_error_details = Column(Text, nullable=True)
    run_history_summary = Column(JSON, nullable=True)
    jira_bug_id = Column(String(100), nullable=True)
    jira_bug_url = Column(String(500), nullable=True)
    # The bug report drafted for this run (BugReportingAgent). No FK: reports live
    # in their own store, which may be JSON while executions are in PostgreSQL.
    bug_report_id = Column(UUID(as_uuid=True), nullable=True)
    # Spec 0001: project configuration captured when the run was accepted.
    project_config_snapshot = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    test_case = relationship("TestCaseDB", back_populates="executions")
    project = relationship("ProjectDB", back_populates="executions")
    report = relationship("ReportDB", uselist=False, back_populates="execution", cascade="all, delete-orphan")


class ReportDB(Base):
    __tablename__ = "reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    execution_id = Column(UUID(as_uuid=True), ForeignKey("executions.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    junit_path = Column(String(500), nullable=True)
    html_path = Column(String(500), nullable=True)
    pdf_path = Column(String(500), nullable=True)
    jira_attachment_id = Column(String(100), nullable=True)
    jira_last_uploaded_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    project = relationship("ProjectDB", back_populates="reports")
    execution = relationship("ExecutionDB", back_populates="report")


class DocumentDB(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    mime_type = Column(String(100), nullable=False)
    size = Column(Integer, nullable=False)
    uploaded_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    storage_provider = Column(String(100), nullable=False, default="local")
    storage_key = Column(String(500), nullable=False)
    embedding_status = Column(String(50), nullable=False, default="pending")
    vector_collection = Column(String(255), nullable=True)
    remote_doc_id = Column(String(100), nullable=True)
    error = Column(Text, nullable=True)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    project = relationship("ProjectDB", back_populates="documents")

    # Matches migration f2b6c8d41a07, so autogenerate keeps it and create_all builds it.
    __table_args__ = (Index("ix_documents_project_remote_doc", "project_id", "remote_doc_id"),)


class KnowledgeEntryDB(Base):
    """An indexed project source other than an uploaded file (spec 0006, AC-14)."""

    __tablename__ = "knowledge_entries"
    __table_args__ = (
        UniqueConstraint("project_id", "source_type", "source_ref", name="uq_knowledge_entries_source"),
        Index("ix_knowledge_entries_project_remote_doc", "project_id", "remote_doc_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    source_type = Column(String(50), nullable=False)
    source_ref = Column(String(255), nullable=False)
    title = Column(String(500), nullable=False)
    remote_doc_id = Column(String(100), nullable=True)
    content_hash = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, default="indexed")
    error = Column(Text, nullable=True)
    indexed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class KnowledgeRemoteDeleteDB(Base):
    """A RAGFlow delete still to do (spec 0007). `project_id` has no foreign key
    on purpose: the row must outlive its project."""

    __tablename__ = "knowledge_remote_deletes"
    __table_args__ = (
        # Nulls are distinct in a unique index, so each kind gets its own partial index.
        Index(
            "uq_knowledge_remote_deletes_document", "dataset_id", "remote_doc_id", unique=True,
            postgresql_where=text("kind = 'document'"), sqlite_where=text("kind = 'document'"),
        ),
        Index(
            "uq_knowledge_remote_deletes_dataset", "dataset_id", unique=True,
            postgresql_where=text("kind = 'dataset'"), sqlite_where=text("kind = 'dataset'"),
        ),
        Index("ix_knowledge_remote_deletes_project_id", "project_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(UUID(as_uuid=True), nullable=False)
    kind = Column(String(20), nullable=False)
    dataset_id = Column(String(100), nullable=False)
    remote_doc_id = Column(String(100), nullable=True)
    reason = Column(String(50), nullable=False)
    attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)


class ScenarioNoteDB(Base):
    __tablename__ = "scenario_notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scenario_id = Column(UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False, index=True)
    note = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    notes = relationship("ScenarioDB", back_populates="notes")


class TestCaseNoteDB(Base):
    __tablename__ = "test_case_notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    test_case_id = Column(UUID(as_uuid=True), ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    note = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    test_case = relationship("TestCaseDB", back_populates="notes")


class RoleDB(Base):
    __tablename__ = "roles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(100), unique=True, nullable=False, index=True)

    permissions = relationship("RolePermissionDB", back_populates="role", cascade="all, delete-orphan")


class PermissionDB(Base):
    __tablename__ = "permissions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    module = Column(String(100), nullable=False, index=True)
    action = Column(String(100), nullable=False, index=True)


class RolePermissionDB(Base):
    __tablename__ = "role_permissions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    role_id = Column(UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True)
    permission_id = Column(UUID(as_uuid=True), ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False, index=True)

    role = relationship("RoleDB", back_populates="permissions")
    permission = relationship("PermissionDB")


class ProjectUserDB(Base):
    __tablename__ = "project_users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role_id = Column(UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True)

    project = relationship("ProjectDB", back_populates="user_associations")
    user = relationship("UserDB", back_populates="project_associations")
    role = relationship("RoleDB")


class RefreshTokenDB(Base):
    __tablename__ = "refresh_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token = Column(String(500), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked = Column(Boolean, default=False, nullable=False)


class AuditLogDB(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    action = Column(String(50), nullable=False, index=True)
    module = Column(String(50), nullable=False, index=True)
    field_changes = Column(JSON, nullable=False, default=dict)


class ObjectPageDB(Base):
    __tablename__ = "object_pages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    url_pattern = Column(String(500), nullable=False)
    last_scanned_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    is_deleted = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        Index(
            "uq_object_pages_project_name",
            "project_id",
            "name",
            unique=True,
            postgresql_where=text("is_deleted = false"),
            sqlite_where=text("is_deleted = 0"),
        ),
        Index(
            "uq_object_pages_project_pattern",
            "project_id",
            "url_pattern",
            unique=True,
            postgresql_where=text("is_deleted = false"),
            sqlite_where=text("is_deleted = 0"),
        ),
    )


class ObjectElementDB(Base):
    __tablename__ = "object_elements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    page_id = Column(UUID(as_uuid=True), ForeignKey("object_pages.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    element_type = Column(String(20), nullable=False)
    description = Column(Text, nullable=True)
    fingerprint = Column(String(64), nullable=True)
    health = Column(String(20), nullable=False, default="healthy", server_default=text("'healthy'"))
    manually_edited = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    last_execution_id = Column(UUID(as_uuid=True), nullable=True)
    last_locator_index = Column(Integer, nullable=True)
    failure_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    consecutive_failed_runs = Column(Integer, nullable=False, default=0, server_default=text("0"))
    applied_execution_ids = Column(JSON, nullable=False, default=list, server_default=text("'[]'"))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    is_deleted = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        Index(
            "uq_object_elements_page_name",
            "page_id",
            "name",
            unique=True,
            postgresql_where=text("is_deleted = false"),
            sqlite_where=text("is_deleted = 0"),
        ),
        # Soft deleted rows keep their fingerprint, so deleted elements block recreation.
        Index(
            "uq_object_elements_page_fingerprint",
            "page_id",
            "fingerprint",
            unique=True,
            postgresql_where=text("fingerprint IS NOT NULL"),
            sqlite_where=text("fingerprint IS NOT NULL"),
        ),
    )


class ObjectLocatorDB(Base):
    __tablename__ = "object_locators"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    element_id = Column(UUID(as_uuid=True), ForeignKey("object_elements.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    strategy = Column(String(20), nullable=False)
    value = Column(Text, nullable=False)
    options = Column(JSON, nullable=False, default=dict, server_default=text("'{}'"))
    success_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    failure_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    last_succeeded_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("element_id", "position", name="uq_object_locators_element_position"),
    )


class ChatThreadDB(Base):
    """Chat assistant thread (spec 0005). One user, one project."""
    __tablename__ = "chat_threads"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(80), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    last_activity_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("ix_chat_threads_project_user_activity", "project_id", "user_id", text("last_activity_at DESC")),
        Index("ix_chat_threads_last_activity_at", "last_activity_at"),
    )


class ChatMessageDB(Base):
    """One row of a chat thread: user, assistant, or a hidden tool row."""
    __tablename__ = "chat_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    thread_id = Column(UUID(as_uuid=True), ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False)
    seq = Column(Integer, nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False, default="")
    view_context = Column(JSON(none_as_null=True), nullable=True)
    tool_name = Column(String(100), nullable=True)
    tool_call_id = Column(String(200), nullable=True)
    tool_calls = Column(JSON(none_as_null=True), nullable=True)
    tool_args = Column(JSON(none_as_null=True), nullable=True)
    tool_result = Column(Text, nullable=True)
    interrupted = Column(Boolean, nullable=False, default=False)
    action = Column(JSON(none_as_null=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("thread_id", "seq", name="uq_chat_messages_thread_seq"),
    )


class TokenUsageDB(Base):
    """One billable LLM completion attempt (Token Utilization feature).
    project_id has no foreign key on purpose: usage history outlives a deleted project."""
    __tablename__ = "token_usage"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    agent = Column(String(100), nullable=False, index=True)
    operation = Column(String(200), nullable=True)
    project_id = Column(UUID(as_uuid=True), nullable=True)
    requirement_id = Column(UUID(as_uuid=True), nullable=True)
    test_case_id = Column(UUID(as_uuid=True), nullable=True)
    run_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    role = Column(String(50), nullable=True)
    provider = Column(String(50), nullable=False, default="")
    model = Column(String(200), nullable=False, default="", index=True)

    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)

    input_cost_usd = Column(Float, nullable=False, default=0.0)
    output_cost_usd = Column(Float, nullable=False, default=0.0)
    cost_usd = Column(Float, nullable=False, default=0.0)
    priced = Column(Boolean, nullable=False, default=False)
    price_source = Column(String(20), nullable=False, default="none")

    latency_ms = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="success")
    error_type = Column(String(200), nullable=True)
    attempt = Column(Integer, nullable=False, default=1)

    __table_args__ = (
        Index("ix_token_usage_project_created", "project_id", "created_at"),
    )


class BugReportDB(Base):
    """A bug report drafted by the Bug Reporting Agent. The indexed columns are the
    lookup keys; the full report is stored in `payload`."""
    __tablename__ = "bug_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    # Spec 0011: nullable so a standalone accessibility scan can own a bug.
    # The check constraint keeps at least one source on every row.
    execution_id = Column(UUID(as_uuid=True), nullable=True)
    test_case_id = Column(UUID(as_uuid=True), nullable=True)
    accessibility_report_id = Column(UUID(as_uuid=True), nullable=True)
    failure_category = Column(String(50), nullable=True)
    status = Column(String(30), nullable=False, default="draft")
    error_signature = Column(String(64), nullable=False, default="")
    jira_key = Column(String(100), nullable=True)
    duplicate_of_jira_key = Column(String(100), nullable=True)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_bug_reports_project_created", "project_id", text("created_at DESC")),
        Index("ix_bug_reports_signature", "project_id", "test_case_id", "error_signature"),
        Index("ix_bug_reports_execution_id", "execution_id"),
        Index("ix_bug_reports_jira_key", "jira_key"),
        Index("ix_bug_reports_category_signature", "project_id", "failure_category", "error_signature"),
        CheckConstraint(
            "execution_id IS NOT NULL OR accessibility_report_id IS NOT NULL",
            name="ck_bug_reports_has_source",
        ),
    )


class AccessibilityReportDB(Base):
    """An accessibility report (spec 0011): one per test run, or one per standalone scan."""
    __tablename__ = "accessibility_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    source = Column(String(20), nullable=False, default="execution")
    execution_id = Column(UUID(as_uuid=True), nullable=True, unique=True)
    batch_id = Column(String(100), nullable=True, index=True)
    gate_mode = Column(String(10), nullable=False, default="warn")
    test_case_id = Column(UUID(as_uuid=True), nullable=True)
    status = Column(String(20), nullable=False, default="succeeded")
    error = Column(String(50), nullable=True)
    login_status = Column(String(20), nullable=False, default="not_requested")
    wcag_tags = Column(JSON, nullable=False, default=list)
    pages = Column(JSON, nullable=False, default=list)
    truncated = Column(Boolean, nullable=False, default=False)
    findings = Column(JSON, nullable=False, default=list)
    counts = Column(JSON, nullable=False, default=dict)
    gate_status = Column(String(20), nullable=False, default="NOT_SCANNED")
    gate_reason = Column(String(50), nullable=True)
    created_by = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_accessibility_reports_project_created", "project_id", "created_at"),
        # AC-10: at most one queued or running standalone scan per project.
        Index(
            "uq_accessibility_reports_active_scan",
            "project_id",
            unique=True,
            postgresql_where=text("source = 'scan' AND status IN ('queued', 'running')"),
            sqlite_where=text("source = 'scan' AND status IN ('queued', 'running')"),
        ),
    )
