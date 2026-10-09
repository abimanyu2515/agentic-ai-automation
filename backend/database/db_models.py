from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Column, String, Text, Float, Boolean, DateTime, ForeignKey, Integer, JSON, UniqueConstraint
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
    target_url = Column(String(500), nullable=True, default="https://adactinhotelapp.com/")
    target_username = Column(String(255), nullable=True)
    target_password_enc = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_projects_owner_name"),
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

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    test_case_id = Column(UUID(as_uuid=True), ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    test_cycle_id = Column(UUID(as_uuid=True), ForeignKey("test_cycles.id", ondelete="SET NULL"), nullable=True, index=True)
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
    jira_bug_id = Column(String(100), nullable=True)
    jira_bug_url = Column(String(500), nullable=True)

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
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    project = relationship("ProjectDB", back_populates="documents")


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
