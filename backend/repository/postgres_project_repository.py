from datetime import datetime, timezone
from uuid import UUID, uuid4
import logging
from sqlalchemy.orm import scoped_session
from sqlalchemy import select, func

from backend.models.project import Project
from backend.models.requirement import Requirement, RequirementSource
from backend.models.scenario import Scenario
from backend.models.test_case import TestCase
from backend.models.document import Document
from backend.models.execution_result import ExecutionResult
from backend.models.release import Release
from backend.models.test_cycle import TestCycle
from backend.repository.project_repository import ProjectRepository
from backend.database.db import SessionLocal
from backend.database.db_models import (
    ProjectDB, RequirementDB, ScenarioDB, TestCaseDB, 
    ExecutionDB, ReportDB, DocumentDB, ScenarioNoteDB, TestCaseNoteDB,
    ReleaseDB, TestCycleDB
)

logger = logging.getLogger("backend.repository.postgres_project_repository")

def _get_val(x):
    if x is None:
        return None
    return x.value if hasattr(x, "value") else x

# Set up scoped session
db_session = scoped_session(SessionLocal)


class PostgresProjectRepository(ProjectRepository):
    def __init__(self):
        super().__init__()
        self._in_explicit_transaction = False

    @property
    def session(self):
        return db_session()

    def begin_transaction(self) -> None:
        session = self.session
        self._in_explicit_transaction = True
        if not session.in_transaction():
            session.begin()

    def commit(self) -> None:
        session = self.session
        self._in_explicit_transaction = False
        if session.in_transaction():
            session.commit()
        db_session.remove()

    def rollback(self) -> None:
        session = self.session
        self._in_explicit_transaction = False
        if session.in_transaction():
            session.rollback()
        db_session.remove()

    def _flush_or_commit(self) -> None:
        session = self.session
        if not getattr(self, "_in_explicit_transaction", False):
            try:
                session.commit()
            except Exception as e:
                session.rollback()
                raise e
            finally:
                db_session.remove()
        else:
            session.flush()

    def list_projects(self, include_deleted: bool = False) -> list[Project]:
        session = self.session
        stmt = select(ProjectDB)
        if not include_deleted:
            stmt = stmt.where(ProjectDB.is_deleted.is_(False))
        db_projects = session.scalars(stmt).all()
        return [
            Project(
                id=p.id,
                name=p.name,
                description=p.description,
                line_of_business=p.line_of_business,
                framework=p.framework,
                jira_project_key=p.jira_project_key,
                target_url=getattr(p, "target_url", None),
                target_username=getattr(p, "target_username", None),
                target_password_enc=getattr(p, "target_password_enc", None),
                created_at=p.created_at,
                requirements=[r.id for r in p.requirements if include_deleted or not r.is_deleted]
            )
            for p in db_projects
        ]

    def get_project(self, project_id: UUID, include_deleted: bool = False) -> Project | None:
        session = self.session
        stmt = select(ProjectDB).where(ProjectDB.id == project_id)
        if not include_deleted:
            stmt = stmt.where(ProjectDB.is_deleted.is_(False))
        p = session.scalar(stmt)
        if not p:
            return None
        return Project(
            id=p.id,
            name=p.name,
            description=p.description,
            line_of_business=p.line_of_business,
            framework=p.framework,
            jira_project_key=p.jira_project_key,
            target_url=getattr(p, "target_url", None),
            target_username=getattr(p, "target_username", None),
            target_password_enc=getattr(p, "target_password_enc", None),
            created_at=p.created_at,
            requirements=[r.id for r in p.requirements if include_deleted or not r.is_deleted]
        )

    def create_project(self, name: str, description: str, line_of_business: str = "general", framework: str = "playwright", jira_project_key: str | None = None, target_url: str | None = "https://adactinhotelapp.com/", target_username: str | None = None, target_password_enc: str | None = None) -> Project:
        from datetime import datetime, timezone
        from uuid import uuid4
        session = self.session
        created_at = datetime.now(timezone.utc)
        proj_id = uuid4()
        db_proj = ProjectDB(
            id=proj_id,
            name=name,
            description=description,
            line_of_business=line_of_business,
            framework=framework,
            jira_project_key=jira_project_key,
            target_url=target_url,
            target_username=target_username,
            target_password_enc=target_password_enc,
            created_at=created_at
        )
        session.add(db_proj)
        self._flush_or_commit()
        return Project(
            id=proj_id,
            name=name,
            description=description,
            line_of_business=line_of_business,
            framework=framework,
            jira_project_key=jira_project_key,
            target_url=target_url,
            target_username=target_username,
            target_password_enc=target_password_enc,
            created_at=created_at,
            requirements=[]
        )

    def update_project(self, project: Project) -> Project | None:
        session = self.session
        stmt = select(ProjectDB).where(ProjectDB.id == project.id, ProjectDB.is_deleted.is_(False))
        db_proj = session.scalar(stmt)
        if not db_proj:
            return None
        db_proj.name = project.name
        db_proj.description = project.description
        db_proj.line_of_business = project.line_of_business
        db_proj.framework = project.framework
        db_proj.jira_project_key = project.jira_project_key
        db_proj.target_url = project.target_url
        db_proj.target_username = project.target_username
        db_proj.target_password_enc = project.target_password_enc
        self._flush_or_commit()
        return project

    def delete_project(self, project_id: UUID, deleted_by: UUID | None = None) -> bool:
        session = self.session
        now = datetime.now(timezone.utc)
        stmt = select(ProjectDB).where(ProjectDB.id == project_id, ProjectDB.is_deleted.is_(False))
        db_proj = session.scalar(stmt)
        if not db_proj:
            return False
        
        db_proj.is_deleted = True
        db_proj.deleted_at = now
        db_proj.deleted_by = deleted_by
        # Cascade soft-deletes
        for req in db_proj.requirements:
            req.is_deleted = True
            req.deleted_at = now
            req.deleted_by = deleted_by
            for sc in req.scenarios:
                sc.is_deleted = True
                sc.deleted_at = now
                sc.deleted_by = deleted_by
                for tc in sc.test_cases:
                    tc.is_deleted = True
                    tc.deleted_at = now
                    tc.deleted_by = deleted_by
        
        self._flush_or_commit()
        return True

    def get_requirements(self, project_id: UUID, include_deleted: bool = False) -> list[Requirement]:
        session = self.session
        stmt = select(RequirementDB).where(RequirementDB.project_id == project_id)
        if not include_deleted:
            stmt = stmt.where(RequirementDB.is_deleted.is_(False))
        db_reqs = session.scalars(stmt).all()
        return [
            Requirement(
                id=r.id,
                title=r.title,
                description=r.description,
                source=RequirementSource(r.source),
                uploaded_at=r.uploaded_at,
                original_filename=r.original_filename,
                requirement_id=r.requirement_id,
                requirement_title=r.requirement_title,
                priority=r.priority,
                business_domain=r.business_domain,
                attachments=r.attachments,
                feature_mapping=r.feature_mapping,
                jira_issue_key=r.jira_issue_key,
                jira_issue_url=r.jira_issue_url,
                jira_sync_status=r.jira_sync_status,
                jira_last_synced_at=r.jira_last_synced_at,
                release_id=r.release_id
            )
            for r in db_reqs
        ]

    def create_requirement(
        self,
        project_id: UUID,
        title: str,
        description: str,
        priority: str,
        business_domain: str,
        attachments: list[str] | None = None,
        original_filename: str | None = None,
        requirement_id: str | None = None,
        requirement_title: str | None = None,
        release_id: UUID | None = None
    ) -> Requirement:
        session = self.session
        uploaded_at = datetime.now(timezone.utc)
        
        # Check if a requirement with the same project_id and requirement_id already exists
        db_req = None
        if requirement_id:
            stmt = select(RequirementDB).where(
                RequirementDB.project_id == project_id,
                RequirementDB.requirement_id == requirement_id
            )
            db_req = session.scalar(stmt)
            
        if db_req:
            # Update the existing requirement
            db_req.title = title
            db_req.description = description
            db_req.priority = priority
            db_req.business_domain = business_domain
            db_req.attachments = attachments or []
            db_req.original_filename = original_filename
            db_req.requirement_title = requirement_title
            db_req.release_id = release_id
            db_req.uploaded_at = uploaded_at
            db_req.is_deleted = False
            db_req.deleted_at = None
            db_req.deleted_by = None
            req_id = db_req.id
        else:
            # Create new requirement
            from uuid import uuid4
            req_id = uuid4()
            db_req = RequirementDB(
                id=req_id,
                project_id=project_id,
                release_id=release_id,
                title=title,
                description=description,
                source="manual",
                uploaded_at=uploaded_at,
                original_filename=original_filename,
                requirement_id=requirement_id,
                requirement_title=requirement_title,
                priority=priority,
                business_domain=business_domain,
                attachments=attachments or []
            )
            session.add(db_req)
            
        # Extract attributes BEFORE flush/commit so we don't trigger DetachedInstanceError afterwards
        feature_mapping = getattr(db_req, "feature_mapping", None)
        jira_issue_key = getattr(db_req, "jira_issue_key", None)
        jira_issue_url = getattr(db_req, "jira_issue_url", None)
        jira_sync_status = getattr(db_req, "jira_sync_status", None)
        jira_last_synced_at = getattr(db_req, "jira_last_synced_at", None)

        self._flush_or_commit()
        return Requirement(
            id=req_id,
            title=title,
            description=description,
            source=RequirementSource("manual"),
            uploaded_at=uploaded_at,
            original_filename=original_filename,
            requirement_id=requirement_id,
            requirement_title=requirement_title,
            priority=priority,
            business_domain=business_domain,
            attachments=attachments or [],
            feature_mapping=feature_mapping,
            jira_issue_key=jira_issue_key,
            jira_issue_url=jira_issue_url,
            jira_sync_status=jira_sync_status,
            jira_last_synced_at=jira_last_synced_at,
            release_id=release_id
        )

    def save_requirement(
        self,
        requirement: Requirement,
        priority: str | None = None,
        business_domain: str | None = None,
        attachments: list[str] | None = None
    ) -> None:
        session = self.session
        stmt = select(RequirementDB).where(RequirementDB.id == requirement.id, RequirementDB.deleted_at.is_(None))
        db_req = session.scalar(stmt)
        if not db_req:
            db_req = RequirementDB(
                id=requirement.id,
                title=requirement.title,
                description=requirement.description,
                source=_get_val(requirement.source),
                uploaded_at=requirement.uploaded_at,
                original_filename=requirement.original_filename,
                requirement_id=requirement.requirement_id,
                requirement_title=requirement.requirement_title,
                priority=priority or _get_val(requirement.priority) or "medium",
                business_domain=business_domain or requirement.business_domain or "general",
                attachments=attachments if attachments is not None else (requirement.attachments or []),
                feature_mapping=requirement.feature_mapping,
                jira_issue_key=requirement.jira_issue_key,
                jira_issue_url=requirement.jira_issue_url,
                jira_sync_status=requirement.jira_sync_status,
                jira_last_synced_at=requirement.jira_last_synced_at,
                release_id=requirement.release_id
            )
            session.add(db_req)
        else:
            db_req.title = requirement.title
            db_req.description = requirement.description
            db_req.source = _get_val(requirement.source)
            db_req.uploaded_at = requirement.uploaded_at
            db_req.original_filename = requirement.original_filename
            db_req.requirement_id = requirement.requirement_id
            db_req.requirement_title = requirement.requirement_title
            db_req.feature_mapping = requirement.feature_mapping
            db_req.jira_issue_key = requirement.jira_issue_key
            db_req.jira_issue_url = requirement.jira_issue_url
            db_req.jira_sync_status = requirement.jira_sync_status
            db_req.jira_last_synced_at = requirement.jira_last_synced_at
            db_req.release_id = requirement.release_id
            if priority is not None:
                db_req.priority = priority
            if business_domain is not None:
                db_req.business_domain = business_domain
            if attachments is not None:
                db_req.attachments = attachments

    def get_scenarios_for_project(self, project_id: UUID) -> list[Scenario]:
        session = self.session
        stmt = (
            select(ScenarioDB)
            .join(RequirementDB, ScenarioDB.requirement_id == RequirementDB.id)
            .where(RequirementDB.project_id == project_id, ScenarioDB.is_deleted.is_(False), RequirementDB.is_deleted.is_(False))
        )
        db_scs = session.scalars(stmt).all()
        return [Scenario.model_validate(s, from_attributes=True) for s in db_scs]

    def get_test_cases_for_project(self, project_id: UUID) -> list[TestCase]:
        session = self.session
        stmt = (
            select(TestCaseDB)
            .join(ScenarioDB, TestCaseDB.scenario_id == ScenarioDB.id)
            .join(RequirementDB, ScenarioDB.requirement_id == RequirementDB.id)
            .where(RequirementDB.project_id == project_id, TestCaseDB.is_deleted.is_(False), ScenarioDB.is_deleted.is_(False), RequirementDB.is_deleted.is_(False))
        )
        db_tcs = session.scalars(stmt).all()
        return [TestCase.model_validate(tc, from_attributes=True) for tc in db_tcs]

    def get_scenarios(self, requirement_id: UUID, include_deleted: bool = False) -> list[Scenario]:
        session = self.session
        stmt = select(ScenarioDB).where(ScenarioDB.requirement_id == requirement_id)
        if not include_deleted:
            stmt = stmt.where(ScenarioDB.is_deleted.is_(False))
        db_scenarios = session.scalars(stmt).all()
        return [Scenario.model_validate(s, from_attributes=True) for s in db_scenarios]

    def save_scenarios(self, scenarios: list[Scenario]) -> None:
        session = self.session
        
        # Get the requirement_id from the first scenario (all scenarios in batch should have same requirement)
        if scenarios:
            requirement_id = scenarios[0].requirement_id
            
            # Get the current max ref_id for this requirement once before the loop
            max_stmt = select(func.max(ScenarioDB.scenario_ref_id)).where(
                ScenarioDB.requirement_id == requirement_id,
                ScenarioDB.is_deleted == False,
                ScenarioDB.scenario_ref_id.isnot(None)
            )
            max_ref = session.scalar(max_stmt)
            
            print(f"[save_scenarios] Max ref_id for requirement {requirement_id}: {max_ref}")
            
            if max_ref and max_ref.startswith("US"):
                # Extract number from existing ref_id (e.g., "US03" -> 3)
                try:
                    current_max = int(max_ref[2:])
                    print(f"[save_scenarios] Extracted current_max: {current_max}")
                except ValueError:
                    current_max = 0
                    print(f"[save_scenarios] Failed to parse max_ref, defaulting to 0")
            else:
                current_max = 0
                print(f"[save_scenarios] No valid max_ref found, starting from 0")
        else:
            current_max = 0
            print(f"[save_scenarios] No scenarios provided, starting from 0")
        
        for s in scenarios:
            stmt = select(ScenarioDB).where(ScenarioDB.id == s.id)
            db_sc = session.scalar(stmt)
            if not db_sc:
                # Auto-generate scenario_ref_id if not provided (US01, US02, etc.)
                if not s.scenario_ref_id:
                    current_max += 1
                    s.scenario_ref_id = f"US{current_max:02d}"
                    print(f"[save_scenarios] Generated ref_id {s.scenario_ref_id} for scenario '{s.scenario_name}'")
                
                db_sc = ScenarioDB(
                    id=s.id,
                    requirement_id=s.requirement_id,
                    scenario_ref_id=s.scenario_ref_id,
                    scenario_name=s.scenario_name,
                    description=s.description,
                    priority=_get_val(s.priority),
                    confidence=s.confidence,
                    approved=s.approved,
                    rejected=s.rejected,
                    path_type=s.path_type,
                    tags=s.tags,
                    reviewer=s.reviewer,
                    approved_at=s.approved_at,
                    generated_at=s.generated_at,
                    jira_issue_key=s.jira_issue_key,
                    jira_issue_id=s.jira_issue_id,
                    jira_issue_url=s.jira_issue_url,
                    jira_sync_status=s.last_jira_sync_status or s.jira_sync_status,
                    jira_last_synced_at=s.last_jira_sync_at or s.jira_last_synced_at,
                    last_jira_sync_error=s.last_jira_sync_error,
                    jira_sync_retry_count=s.jira_sync_retry_count
                )
                session.add(db_sc)
            else:
                db_sc.scenario_name = s.scenario_name
                db_sc.description = s.description
                db_sc.priority = _get_val(s.priority)
                db_sc.confidence = s.confidence
                db_sc.approved = s.approved
                db_sc.rejected = s.rejected
                db_sc.path_type = s.path_type
                db_sc.tags = s.tags
                db_sc.reviewer = s.reviewer
                db_sc.approved_at = s.approved_at
                db_sc.generated_at = s.generated_at
                db_sc.jira_issue_key = s.jira_issue_key
                db_sc.jira_issue_id = s.jira_issue_id
                db_sc.jira_issue_url = s.jira_issue_url
                db_sc.jira_sync_status = s.last_jira_sync_status or s.jira_sync_status
                db_sc.jira_last_synced_at = s.last_jira_sync_at or s.jira_last_synced_at
                db_sc.last_jira_sync_error = s.last_jira_sync_error
                db_sc.jira_sync_retry_count = s.jira_sync_retry_count
                db_sc.is_deleted = False
        self._flush_or_commit()

    def update_scenario(self, scenario: Scenario) -> Scenario | None:
        session = self.session
        stmt = select(ScenarioDB).where(ScenarioDB.id == scenario.id, ScenarioDB.is_deleted.is_(False))
        db_sc = session.scalar(stmt)
        if not db_sc:
            return None
        db_sc.scenario_name = scenario.scenario_name
        db_sc.description = scenario.description
        db_sc.priority = _get_val(scenario.priority)
        db_sc.confidence = scenario.confidence
        db_sc.approved = scenario.approved
        db_sc.rejected = scenario.rejected
        db_sc.path_type = scenario.path_type
        db_sc.tags = scenario.tags
        db_sc.reviewer = scenario.reviewer
        db_sc.approved_at = scenario.approved_at
        db_sc.generated_at = scenario.generated_at
        db_sc.jira_issue_key = scenario.jira_issue_key
        db_sc.jira_issue_id = scenario.jira_issue_id
        db_sc.jira_issue_url = scenario.jira_issue_url
        db_sc.jira_sync_status = scenario.last_jira_sync_status or scenario.jira_sync_status
        db_sc.jira_last_synced_at = scenario.last_jira_sync_at or scenario.jira_last_synced_at
        db_sc.last_jira_sync_error = scenario.last_jira_sync_error
        db_sc.jira_sync_retry_count = scenario.jira_sync_retry_count
        self._flush_or_commit()
        return scenario

    def delete_scenario(self, scenario_id: UUID, deleted_by: UUID | None = None) -> bool:
        session = self.session
        now = datetime.now(timezone.utc)
        stmt = select(ScenarioDB).where(ScenarioDB.id == scenario_id, ScenarioDB.is_deleted.is_(False))
        db_sc = session.scalar(stmt)
        if not db_sc:
            return False
        db_sc.is_deleted = True
        db_sc.deleted_at = now
        db_sc.deleted_by = deleted_by
        for tc in db_sc.test_cases:
            tc.is_deleted = True
            tc.deleted_at = now
            tc.deleted_by = deleted_by
        self._flush_or_commit()
        return True

    def clear_scenarios_for_requirement(self, requirement_id: UUID) -> None:
        session = self.session
        now = datetime.now(timezone.utc)
        stmt = select(ScenarioDB).where(ScenarioDB.requirement_id == requirement_id, ScenarioDB.is_deleted.is_(False))
        db_scenarios = session.scalars(stmt).all()
        for s in db_scenarios:
            s.is_deleted = True
            s.deleted_at = now
            for tc in s.test_cases:
                tc.is_deleted = True
                tc.deleted_at = now
        self._flush_or_commit()

    def get_test_cases(self, scenario_id: UUID, include_deleted: bool = False) -> list[TestCase]:
        session = self.session
        stmt = select(TestCaseDB).where(TestCaseDB.scenario_id == scenario_id)
        if not include_deleted:
            stmt = stmt.where(TestCaseDB.is_deleted.is_(False))
        db_tcs = session.scalars(stmt).all()
        return [TestCase.model_validate(tc, from_attributes=True) for tc in db_tcs]

    def save_test_cases(self, test_cases: list[TestCase]) -> None:
        session = self.session
        for tc in test_cases:
            stmt = select(TestCaseDB).where(TestCaseDB.id == tc.id)
            db_tc = session.scalar(stmt)
            if not db_tc:
                # Auto-generate test_case_ref_id if not provided (US01-TC01, US01-TC02, etc.)
                if not tc.test_case_ref_id:
                    # Get the parent scenario's ref_id
                    scenario_stmt = select(ScenarioDB).where(ScenarioDB.id == tc.scenario_id)
                    scenario = session.scalar(scenario_stmt)
                    scenario_ref = scenario.scenario_ref_id if scenario and scenario.scenario_ref_id else "US00"
                    
                    # Get count of existing test cases for this scenario
                    count_stmt = select(func.count(TestCaseDB.id)).where(
                        TestCaseDB.scenario_id == tc.scenario_id,
                        TestCaseDB.is_deleted == False
                    )
                    existing_count = session.scalar(count_stmt) or 0
                    tc.test_case_ref_id = f"{scenario_ref}-TC{existing_count + 1:02d}"
                
                db_tc = TestCaseDB(
                    id=tc.id,
                    scenario_id=tc.scenario_id,
                    test_case_ref_id=tc.test_case_ref_id,
                    title=tc.title,
                    preconditions=tc.preconditions,
                    steps=tc.steps,
                    expected_result=tc.expected_result,
                    priority=_get_val(tc.priority),
                    status=_get_val(tc.status),
                    confidence=tc.confidence,
                    evaluation_status=_get_val(tc.evaluation_status),
                    evaluation_reason=tc.evaluation_reason,
                    playwright_script=tc.playwright_script,
                    reviewer=tc.reviewer,
                    approved_at=tc.approved_at,
                    generated_at=tc.generated_at,
                    is_frozen=tc.is_frozen,
                    jira_issue_key=tc.jira_issue_key,
                    jira_issue_url=tc.jira_issue_url,
                    jira_sync_status=tc.jira_sync_status,
                    jira_last_synced_at=tc.jira_last_synced_at
                )
                session.add(db_tc)
            else:
                db_tc.title = tc.title
                db_tc.preconditions = tc.preconditions
                db_tc.steps = tc.steps
                db_tc.expected_result = tc.expected_result
                db_tc.priority = _get_val(tc.priority)
                db_tc.status = _get_val(tc.status)
                db_tc.confidence = tc.confidence
                db_tc.evaluation_status = _get_val(tc.evaluation_status)
                db_tc.evaluation_reason = tc.evaluation_reason
                db_tc.playwright_script = tc.playwright_script
                db_tc.reviewer = tc.reviewer
                db_tc.approved_at = tc.approved_at
                db_tc.generated_at = tc.generated_at
                db_tc.is_frozen = tc.is_frozen
                db_tc.jira_issue_key = tc.jira_issue_key
                db_tc.jira_issue_url = tc.jira_issue_url
                db_tc.jira_sync_status = tc.jira_sync_status
                db_tc.jira_last_synced_at = tc.jira_last_synced_at
                db_tc.is_deleted = False
        self._flush_or_commit()

    def update_test_case(self, test_case: TestCase) -> TestCase | None:
        session = self.session
        stmt = select(TestCaseDB).where(TestCaseDB.id == test_case.id, TestCaseDB.is_deleted.is_(False))
        db_tc = session.scalar(stmt)
        if not db_tc:
            return None
        db_tc.title = test_case.title
        db_tc.preconditions = test_case.preconditions
        db_tc.steps = test_case.steps
        db_tc.expected_result = test_case.expected_result
        db_tc.priority = _get_val(test_case.priority)
        db_tc.status = _get_val(test_case.status)
        db_tc.confidence = test_case.confidence
        db_tc.evaluation_status = _get_val(test_case.evaluation_status)
        db_tc.evaluation_reason = test_case.evaluation_reason
        db_tc.playwright_script = test_case.playwright_script
        db_tc.reviewer = test_case.reviewer
        db_tc.approved_at = test_case.approved_at
        db_tc.generated_at = test_case.generated_at
        db_tc.is_frozen = test_case.is_frozen
        db_tc.jira_issue_key = test_case.jira_issue_key
        db_tc.jira_issue_url = test_case.jira_issue_url
        db_tc.jira_sync_status = test_case.jira_sync_status
        db_tc.jira_last_synced_at = test_case.jira_last_synced_at
        self._flush_or_commit()
        return test_case

    def get_test_case(self, test_case_id: UUID, include_deleted: bool = False) -> TestCase | None:
        session = self.session
        stmt = select(TestCaseDB).where(TestCaseDB.id == test_case_id)
        if not include_deleted:
            stmt = stmt.where(TestCaseDB.is_deleted.is_(False))
        db_tc = session.scalar(stmt)
        if not db_tc:
            return None
        return TestCase.model_validate(db_tc, from_attributes=True)

    def delete_test_case(self, test_case_id: UUID, deleted_by: UUID | None = None) -> bool:
        session = self.session
        stmt = select(TestCaseDB).where(TestCaseDB.id == test_case_id, TestCaseDB.is_deleted.is_(False))
        db_tc = session.scalar(stmt)
        if not db_tc:
            return False
        db_tc.is_deleted = True
        db_tc.deleted_at = datetime.now(timezone.utc)
        db_tc.deleted_by = deleted_by
        self._flush_or_commit()
        return True

    def delete_test_cases_for_scenario(self, scenario_id: UUID) -> None:
        session = self.session
        stmt = select(TestCaseDB).where(TestCaseDB.scenario_id == scenario_id, TestCaseDB.is_deleted.is_(False))
        db_tcs = session.scalars(stmt).all()
        now = datetime.now(timezone.utc)
        for tc in db_tcs:
            tc.is_deleted = True
            tc.deleted_at = now
        self._flush_or_commit()

    def delete_execution(self, execution_id: UUID, deleted_by: UUID | None = None) -> bool:
        session = self.session
        stmt = select(ExecutionDB).where(ExecutionDB.id == execution_id)
        db_ex = session.scalar(stmt)
        if not db_ex:
            return False
        session.delete(db_ex)
        self._flush_or_commit()
        return True

    def delete_failed_executions(self, project_id: UUID, deleted_by: UUID | None = None) -> int:
        session = self.session
        stmt = select(ExecutionDB).where(
            ExecutionDB.project_id == project_id,
            ExecutionDB.status == 'failed'
        )
        db_execs = session.scalars(stmt).all()
        for ex in db_execs:
            session.delete(ex)
        self._flush_or_commit()
        return len(db_execs)

    def get_documents(self, project_id: UUID) -> list[Document]:
        session = self.session
        stmt = select(DocumentDB).where(DocumentDB.project_id == project_id)
        db_docs = session.scalars(stmt).all()
        return [
            Document(
                id=d.id,
                project_id=d.project_id,
                filename=d.filename,
                original_filename=d.original_filename,
                mime_type=d.mime_type,
                size=d.size,
                uploaded_at=d.uploaded_at,
                storage_path=d.storage_key,  # Expose storage_key as path for models
                embedding_status=d.embedding_status,
                vector_collection=d.vector_collection,
                metadata=d.metadata_json
            )
            for d in db_docs
        ]

    def get_document(self, project_id: UUID, document_id: UUID) -> Document | None:
        session = self.session
        stmt = select(DocumentDB).where(DocumentDB.project_id == project_id, DocumentDB.id == document_id)
        d = session.scalar(stmt)
        if not d:
            return None
        return Document(
            id=d.id,
            project_id=d.project_id,
            filename=d.filename,
            original_filename=d.original_filename,
            mime_type=d.mime_type,
            size=d.size,
            uploaded_at=d.uploaded_at,
            storage_path=d.storage_key,
            embedding_status=d.embedding_status,
            vector_collection=d.vector_collection,
            metadata=d.metadata_json
        )

    def save_document(self, document: Document) -> None:
        session = self.session
        stmt = select(DocumentDB).where(DocumentDB.id == document.id)
        db_doc = session.scalar(stmt)
        if not db_doc:
            db_doc = DocumentDB(
                id=document.id,
                project_id=document.project_id,
                filename=document.filename,
                original_filename=document.original_filename,
                mime_type=document.mime_type,
                size=document.size,
                uploaded_at=document.uploaded_at,
                storage_provider="local",
                storage_key=document.storage_path,
                embedding_status=document.embedding_status,
                vector_collection=document.vector_collection,
                metadata_json=document.metadata
            )
            session.add(db_doc)
        else:
            db_doc.filename = document.filename
            db_doc.original_filename = document.original_filename
            db_doc.mime_type = document.mime_type
            db_doc.size = document.size
            db_doc.uploaded_at = document.uploaded_at
            db_doc.storage_key = document.storage_path
            db_doc.embedding_status = document.embedding_status
            db_doc.vector_collection = document.vector_collection
            db_doc.metadata_json = document.metadata
        self._flush_or_commit()

    def delete_document(self, project_id: UUID, document_id: UUID) -> bool:
        session = self.session
        stmt = select(DocumentDB).where(DocumentDB.project_id == project_id, DocumentDB.id == document_id)
        db_doc = session.scalar(stmt)
        if not db_doc:
            return False
        session.delete(db_doc)
        self._flush_or_commit()
        return True

    def get_execution_results(self, project_id: UUID) -> list[ExecutionResult]:
        session = self.session
        stmt = select(ExecutionDB).where(ExecutionDB.project_id == project_id)
        db_execs = session.scalars(stmt).all()
        return [
            ExecutionResult(
                id=ex.id,
                test_case_id=ex.test_case_id,
                status=ex.status,
                duration_seconds=ex.duration_seconds,
                error_message=ex.error_message,
                screenshot_path=ex.screenshot_path,
                video_path=ex.video_path,
                trace_path=ex.trace_path,
                browser_version=ex.browser_version,
                executed_at=ex.executed_at,
                test_cycle_id=ex.test_cycle_id,
                failure_category=ex.failure_category,
                root_cause_summary=ex.root_cause_summary,
                suggest_retry=ex.suggest_retry,
                retest_pending_candidate=ex.retest_pending_candidate,
                jira_bug_id=ex.jira_bug_id,
                jira_bug_url=ex.jira_bug_url
            )
            for ex in db_execs
        ]

    def get_execution_result(self, project_id: UUID, execution_id: UUID) -> ExecutionResult | None:
        session = self.session
        stmt = select(ExecutionDB).where(ExecutionDB.project_id == project_id, ExecutionDB.id == execution_id)
        ex = session.scalar(stmt)
        if not ex:
            return None
        return ExecutionResult(
            id=ex.id,
            test_case_id=ex.test_case_id,
            status=ex.status,
            duration_seconds=ex.duration_seconds,
            error_message=ex.error_message,
            screenshot_path=ex.screenshot_path,
            video_path=ex.video_path,
            trace_path=ex.trace_path,
            browser_version=ex.browser_version,
            executed_at=ex.executed_at,
            test_cycle_id=ex.test_cycle_id,
            timeline=ex.timeline if hasattr(ex, 'timeline') else [],
            screenshots=ex.screenshots if hasattr(ex, 'screenshots') else [],
            failure_category=ex.failure_category,
            root_cause_summary=ex.root_cause_summary,
            suggest_retry=ex.suggest_retry,
            retest_pending_candidate=ex.retest_pending_candidate,
            jira_bug_id=ex.jira_bug_id,
            jira_bug_url=ex.jira_bug_url
        )

    def save_execution_result(self, project_id: UUID, result: ExecutionResult) -> None:
        session = self.session
        stmt = select(ExecutionDB).where(ExecutionDB.id == result.id)
        db_ex = session.scalar(stmt)
        
        # Validate test_cycle_id exists in database before using it
        test_cycle_id_to_use = None
        if result.test_cycle_id:
            stmt_cycle = select(TestCycleDB).where(TestCycleDB.id == result.test_cycle_id)
            db_cycle = session.scalar(stmt_cycle)
            if db_cycle:
                test_cycle_id_to_use = result.test_cycle_id
            else:
                print(f"[Repository] test_cycle_id {result.test_cycle_id} not found in test_cycles table, setting to None")
        
        if not db_ex:
            db_ex = ExecutionDB(
                id=result.id,
                test_case_id=result.test_case_id,
                project_id=project_id,
                test_cycle_id=test_cycle_id_to_use,
                status=result.status.value,
                duration_seconds=result.duration_seconds,
                error_message=result.error_message,
                screenshot_path=result.screenshot_path,
                video_path=result.video_path,
                trace_path=result.trace_path,
                browser_version=result.browser_version,
                executed_at=result.executed_at,
                timeline=result.timeline,
                screenshots=result.screenshots,
                failure_category=result.failure_category,
                root_cause_summary=result.root_cause_summary,
                suggest_retry=result.suggest_retry,
                retest_pending_candidate=result.retest_pending_candidate,
                jira_bug_id=result.jira_bug_id,
                jira_bug_url=result.jira_bug_url
            )
            session.add(db_ex)
        else:
            db_ex.status = result.status.value
            db_ex.duration_seconds = result.duration_seconds
            db_ex.error_message = result.error_message
            db_ex.screenshot_path = result.screenshot_path
            db_ex.video_path = result.video_path
            db_ex.trace_path = result.trace_path
            db_ex.browser_version = result.browser_version
            db_ex.executed_at = result.executed_at
            db_ex.test_cycle_id = test_cycle_id_to_use
            db_ex.timeline = result.timeline
            db_ex.screenshots = result.screenshots
            db_ex.failure_category = result.failure_category
            db_ex.root_cause_summary = result.root_cause_summary
            db_ex.suggest_retry = result.suggest_retry
            db_ex.retest_pending_candidate = result.retest_pending_candidate
            db_ex.jira_bug_id = result.jira_bug_id
            db_ex.jira_bug_url = result.jira_bug_url
        self._flush_or_commit()

    def get_scenario_notes(self, scenario_id: UUID) -> list[str]:
        session = self.session
        stmt = select(ScenarioNoteDB).where(ScenarioNoteDB.scenario_id == scenario_id)
        db_notes = session.scalars(stmt).all()
        return [n.note for n in db_notes]

    def add_scenario_note(self, scenario_id: UUID, note: str) -> None:
        session = self.session
        timestamp = datetime.now().strftime("%Y-%m-%d %I:%M %p")
        formatted = f"[{timestamp}] {note}"
        db_note = ScenarioNoteDB(scenario_id=scenario_id, note=formatted)
        session.add(db_note)
        self._flush_or_commit()

    def get_test_case_notes(self, test_case_id: UUID) -> list[str]:
        session = self.session
        stmt = select(TestCaseNoteDB).where(TestCaseNoteDB.test_case_id == test_case_id)
        db_notes = session.scalars(stmt).all()
        return [n.note for n in db_notes]

    def add_test_case_note(self, test_case_id: UUID, note: str) -> None:
        session = self.session
        timestamp = datetime.now().strftime("%Y-%m-%d %I:%M %p")
        formatted = f"[{timestamp}] {note}"
        db_note = TestCaseNoteDB(test_case_id=test_case_id, note=formatted)
        session.add(db_note)
        self._flush_or_commit()

    def get_requirement(self, requirement_id: UUID) -> Requirement | None:
        session = self.session
        stmt = select(RequirementDB).where(RequirementDB.id == requirement_id, RequirementDB.deleted_at.is_(None))
        r = session.scalar(stmt)
        if not r:
            return None
        return Requirement(
            id=r.id,
            title=r.title,
            description=r.description,
            source=RequirementSource(r.source),
            uploaded_at=r.uploaded_at,
            original_filename=r.original_filename,
            requirement_id=r.requirement_id,
            requirement_title=r.requirement_title,
            priority=r.priority,
            business_domain=r.business_domain,
            attachments=r.attachments,
            release_id=r.release_id
        )

    def get_scenario(self, scenario_id: UUID) -> Scenario | None:
        session = self.session
        stmt = select(ScenarioDB).where(ScenarioDB.id == scenario_id, ScenarioDB.deleted_at.is_(None))
        s = session.scalar(stmt)
        if not s:
            return None
        return Scenario.model_validate(s, from_attributes=True)

    def get_report_by_execution(self, execution_id: UUID) -> dict | None:
        session = self.session
        stmt = select(ReportDB).where(ReportDB.execution_id == execution_id)
        rep = session.scalar(stmt)
        if not rep:
            return None
        return {
            "id": str(rep.id),
            "project_id": str(rep.project_id),
            "execution_id": str(rep.execution_id),
            "junit_path": rep.junit_path,
            "html_path": rep.html_path,
            "pdf_path": rep.pdf_path,
            "created_at": rep.created_at.isoformat()
        }

    def save_report(self, project_id: UUID, execution_id: UUID, report_payload: dict) -> dict:
        session = self.session
        stmt = select(ReportDB).where(ReportDB.execution_id == execution_id)
        db_rep = session.scalar(stmt)
        if not db_rep:
            db_rep = ReportDB(
                id=report_payload.get("id") or uuid4(),
                project_id=project_id,
                execution_id=execution_id,
                junit_path=report_payload.get("junit_path"),
                html_path=report_payload.get("html_path"),
                pdf_path=report_payload.get("pdf_path"),
                jira_attachment_id=report_payload.get("jira_attachment_id"),
                jira_last_uploaded_at=report_payload.get("jira_last_uploaded_at")
            )
            session.add(db_rep)
            rep_id = db_rep.id
            created_at = db_rep.created_at
        else:
            db_rep.junit_path = report_payload.get("junit_path")
            db_rep.html_path = report_payload.get("html_path")
            db_rep.pdf_path = report_payload.get("pdf_path")
            db_rep.jira_attachment_id = report_payload.get("jira_attachment_id")
            db_rep.jira_last_uploaded_at = report_payload.get("jira_last_uploaded_at")
            rep_id = db_rep.id
            created_at = db_rep.created_at
        self._flush_or_commit()
        return {
            "id": str(rep_id),
            "project_id": str(project_id),
            "execution_id": str(execution_id),
            "junit_path": report_payload.get("junit_path"),
            "html_path": report_payload.get("html_path"),
            "pdf_path": report_payload.get("pdf_path"),
            "jira_attachment_id": report_payload.get("jira_attachment_id"),
            "jira_last_uploaded_at": report_payload.get("jira_last_uploaded_at") if report_payload.get("jira_last_uploaded_at") else (created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)),
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)
        }

    def list_releases(self, project_id: UUID) -> list[Release]:
        session = self.session
        stmt = select(ReleaseDB).where(ReleaseDB.project_id == project_id)
        db_releases = session.scalars(stmt).all()
        return [
            Release(
                id=r.id,
                project_id=r.project_id,
                name=r.name,
                description=r.description,
                status=r.status,
                start_date=r.start_date,
                end_date=r.end_date
            )
            for r in db_releases
        ]

    def create_release(self, project_id: UUID, name: str, description: str | None = None, status: str = "Active", start_date: datetime | None = None, end_date: datetime | None = None) -> Release:
        from uuid import uuid4
        session = self.session
        release_id = uuid4()
        db_release = ReleaseDB(
            id=release_id,
            project_id=project_id,
            name=name,
            description=description,
            status=status,
            start_date=start_date,
            end_date=end_date
        )
        session.add(db_release)
        self._flush_or_commit()
        return Release(
            id=release_id,
            project_id=project_id,
            name=name,
            description=description,
            status=status,
            start_date=start_date,
            end_date=end_date
        )

    def get_release(self, release_id: UUID) -> Release | None:
        session = self.session
        stmt = select(ReleaseDB).where(ReleaseDB.id == release_id)
        r = session.scalar(stmt)
        if not r:
            return None
        return Release(
            id=r.id,
            project_id=r.project_id,
            name=r.name,
            description=r.description,
            status=r.status,
            start_date=r.start_date,
            end_date=r.end_date
        )

    def list_test_cycles(self, release_id: UUID) -> list[TestCycle]:
        session = self.session
        stmt = select(TestCycleDB).where(TestCycleDB.release_id == release_id)
        db_cycles = session.scalars(stmt).all()
        return [
            TestCycle(
                id=c.id,
                release_id=c.release_id,
                name=c.name,
                description=c.description,
                status=c.status,
                created_at=c.created_at
            )
            for c in db_cycles
        ]

    def create_test_cycle(self, release_id: UUID, name: str, description: str | None = None, status: str = "Active") -> TestCycle:
        from uuid import uuid4
        from datetime import datetime, timezone
        session = self.session
        cycle_id = uuid4()
        created_at = datetime.now(timezone.utc)
        db_cycle = TestCycleDB(
            id=cycle_id,
            release_id=release_id,
            name=name,
            description=description,
            status=status,
            created_at=created_at
        )
        session.add(db_cycle)
        self._flush_or_commit()
        return TestCycle(
            id=cycle_id,
            release_id=release_id,
            name=name,
            description=description,
            status=status,
            created_at=created_at
        )

    def get_test_cycle(self, cycle_id: UUID) -> TestCycle | None:
        session = self.session
        stmt = select(TestCycleDB).where(TestCycleDB.id == cycle_id)
        c = session.scalar(stmt)
        if not c:
            return None
        return TestCycle(
            id=c.id,
            release_id=c.release_id,
            name=c.name,
            description=c.description,
            status=c.status,
            created_at=c.created_at
        )

    def reset_demo_data(self) -> None:
        session = self.session
        try:
            # Find projects to delete matching sample names
            projects = session.query(ProjectDB).all()
            projects_to_delete = []
            for p in projects:
                name_lower = p.name.lower()
                if any(k in name_lower for k in ["bank", "vehicle", "tricentis", "adactin"]):
                    projects_to_delete.append(p)

            project_ids = [p.id for p in projects_to_delete]
            
            if project_ids:
                # 1. Delete associated ProjectUsers
                from backend.database.db_models import ProjectUserDB
                session.query(ProjectUserDB).filter(ProjectUserDB.project_id.in_(project_ids)).delete(synchronize_session=False)

                # 2. Releases
                releases = session.query(ReleaseDB).filter(ReleaseDB.project_id.in_(project_ids)).all()
                release_ids = [r.id for r in releases]

                # 3. TestCycles
                cycle_ids = []
                if release_ids:
                    cycles = session.query(TestCycleDB).filter(TestCycleDB.release_id.in_(release_ids)).all()
                    cycle_ids = [c.id for c in cycles]

                # 4. Requirements
                reqs = session.query(RequirementDB).filter(RequirementDB.project_id.in_(project_ids)).all()
                req_ids = [r.id for r in reqs]

                # 5. Scenarios
                scenario_ids = []
                if req_ids:
                    scenarios = session.query(ScenarioDB).filter(ScenarioDB.requirement_id.in_(req_ids)).all()
                    scenario_ids = [s.id for s in scenarios]

                # 6. TestCases
                tc_ids = []
                if scenario_ids:
                    tcs = session.query(TestCaseDB).filter(TestCaseDB.scenario_id.in_(scenario_ids)).all()
                    tc_ids = [t.id for t in tcs]

                # 7. Executions
                exec_ids = []
                if tc_ids:
                    execs = session.query(ExecutionDB).filter(ExecutionDB.test_case_id.in_(tc_ids)).all()
                    exec_ids = [e.id for e in execs]

                # Deletes:
                if scenario_ids:
                    session.query(ScenarioNoteDB).filter(ScenarioNoteDB.scenario_id.in_(scenario_ids)).delete(synchronize_session=False)
                if tc_ids:
                    session.query(TestCaseNoteDB).filter(TestCaseNoteDB.test_case_id.in_(tc_ids)).delete(synchronize_session=False)
                if exec_ids:
                    session.query(ReportDB).filter(ReportDB.execution_id.in_(exec_ids)).delete(synchronize_session=False)
                    session.query(ExecutionDB).filter(ExecutionDB.id.in_(exec_ids)).delete(synchronize_session=False)
                if tc_ids:
                    session.query(TestCaseDB).filter(TestCaseDB.id.in_(tc_ids)).delete(synchronize_session=False)
                if scenario_ids:
                    session.query(ScenarioDB).filter(ScenarioDB.id.in_(scenario_ids)).delete(synchronize_session=False)
                if req_ids:
                    session.query(RequirementDB).filter(RequirementDB.id.in_(req_ids)).delete(synchronize_session=False)
                if cycle_ids:
                    session.query(TestCycleDB).filter(TestCycleDB.id.in_(cycle_ids)).delete(synchronize_session=False)
                if release_ids:
                    session.query(ReleaseDB).filter(ReleaseDB.id.in_(release_ids)).delete(synchronize_session=False)
                
                session.query(DocumentDB).filter(DocumentDB.project_id.in_(project_ids)).delete(synchronize_session=False)
                session.query(ProjectDB).filter(ProjectDB.id.in_(project_ids)).delete(synchronize_session=False)

            # Clean orphaned entries
            all_projects = session.query(ProjectDB).all()
            all_project_ids = [p.id for p in all_projects]
            if all_project_ids:
                session.query(ReleaseDB).filter(~ReleaseDB.project_id.in_(all_project_ids)).delete(synchronize_session=False)
                session.query(RequirementDB).filter(~RequirementDB.project_id.in_(all_project_ids)).delete(synchronize_session=False)
                session.query(DocumentDB).filter(~DocumentDB.project_id.in_(all_project_ids)).delete(synchronize_session=False)
            else:
                session.query(ReleaseDB).delete(synchronize_session=False)
                session.query(RequirementDB).delete(synchronize_session=False)
                session.query(DocumentDB).delete(synchronize_session=False)

            all_releases = session.query(ReleaseDB).all()
            all_release_ids = [r.id for r in all_releases]
            if all_release_ids:
                session.query(TestCycleDB).filter(~TestCycleDB.release_id.in_(all_release_ids)).delete(synchronize_session=False)
            else:
                session.query(TestCycleDB).delete(synchronize_session=False)

            all_requirements = session.query(RequirementDB).all()
            all_req_ids = [r.id for r in all_requirements]
            if all_req_ids:
                session.query(ScenarioDB).filter(~ScenarioDB.requirement_id.in_(all_req_ids)).delete(synchronize_session=False)
            else:
                session.query(ScenarioDB).delete(synchronize_session=False)

            all_scenarios = session.query(ScenarioDB).all()
            all_scenario_ids = [s.id for s in all_scenarios]
            if all_scenario_ids:
                session.query(TestCaseDB).filter(~TestCaseDB.scenario_id.in_(all_scenario_ids)).delete(synchronize_session=False)
                session.query(ScenarioNoteDB).filter(~ScenarioNoteDB.scenario_id.in_(all_scenario_ids)).delete(synchronize_session=False)
            else:
                session.query(TestCaseDB).delete(synchronize_session=False)
                session.query(ScenarioNoteDB).delete(synchronize_session=False)

            all_testcases = session.query(TestCaseDB).all()
            all_tc_ids = [t.id for t in all_testcases]
            if all_tc_ids:
                session.query(ExecutionDB).filter(~ExecutionDB.test_case_id.in_(all_tc_ids)).delete(synchronize_session=False)
                session.query(TestCaseNoteDB).filter(~TestCaseNoteDB.test_case_id.in_(all_tc_ids)).delete(synchronize_session=False)
            else:
                session.query(ExecutionDB).delete(synchronize_session=False)
                session.query(TestCaseNoteDB).delete(synchronize_session=False)

            all_executions = session.query(ExecutionDB).all()
            all_exec_ids = [e.id for e in all_executions]
            if all_exec_ids:
                session.query(ReportDB).filter(~ReportDB.execution_id.in_(all_exec_ids)).delete(synchronize_session=False)
            else:
                session.query(ReportDB).delete(synchronize_session=False)

            session.commit()
        except Exception as e:
            session.rollback()
            raise e

