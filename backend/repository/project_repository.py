import json
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from backend.models.project import Project
from backend.models.requirement import Requirement, RequirementSource
from backend.models.scenario import Scenario
from backend.models.test_case import TestCase
from backend.models.document import Document
from backend.models.execution_result import ExecutionResult
from backend.models.release import Release
from backend.models.test_cycle import TestCycle

STORE_PATH = Path(__file__).parent.parent / "database" / "project_store.json"


class ProjectRepository(ABC):
    @abstractmethod
    def list_projects(self, include_deleted: bool = False) -> list[Project]:
        pass

    @abstractmethod
    def get_project(self, project_id: UUID, include_deleted: bool = False) -> Project | None:
        pass

    @abstractmethod
    def create_project(self, name: str, description: str, line_of_business: str = "general", framework: str = "playwright", jira_project_key: str | None = None, target_url: str | None = "https://adactinhotelapp.com/", target_username: str | None = None, target_password_enc: str | None = None) -> Project:
        pass

    @abstractmethod
    def update_project(self, project: Project) -> Project | None:
        pass

    @abstractmethod
    def delete_project(self, project_id: UUID, deleted_by: UUID | None = None) -> bool:
        pass

    @abstractmethod
    def get_requirements(self, project_id: UUID, include_deleted: bool = False) -> list[Requirement]:
        pass

    @abstractmethod
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
        pass

    @abstractmethod
    def save_requirement(
        self,
        requirement: Requirement,
        priority: str | None = None,
        business_domain: str | None = None,
        attachments: list[str] | None = None
    ) -> None:
        pass

    @abstractmethod
    def get_scenarios(self, requirement_id: UUID, include_deleted: bool = False) -> list[Scenario]:
        pass

    @abstractmethod
    def save_scenarios(self, scenarios: list[Scenario]) -> None:
        pass

    @abstractmethod
    def update_scenario(self, scenario: Scenario) -> Scenario | None:
        pass

    @abstractmethod
    def delete_scenario(self, scenario_id: UUID, deleted_by: UUID | None = None) -> bool:
        pass

    @abstractmethod
    def clear_scenarios_for_requirement(self, requirement_id: UUID) -> None:
        pass

    @abstractmethod
    def get_test_cases(self, scenario_id: UUID, include_deleted: bool = False) -> list[TestCase]:
        pass

    @abstractmethod
    def save_test_cases(self, test_cases: list[TestCase]) -> None:
        pass

    @abstractmethod
    def update_test_case(self, test_case: TestCase) -> TestCase | None:
        pass

    @abstractmethod
    def get_test_case(self, test_case_id: UUID, include_deleted: bool = False) -> TestCase | None:
        pass

    @abstractmethod
    def delete_test_case(self, test_case_id: UUID, deleted_by: UUID | None = None) -> bool:
        pass

    @abstractmethod
    def delete_test_cases_for_scenario(self, scenario_id: UUID) -> None:
        pass

    @abstractmethod
    def delete_execution(self, execution_id: UUID, deleted_by: UUID | None = None) -> bool:
        pass

    @abstractmethod
    def delete_failed_executions(self, project_id: UUID, deleted_by: UUID | None = None) -> int:
        pass

    @abstractmethod
    def get_documents(self, project_id: UUID) -> list[Document]:
        pass

    @abstractmethod
    def get_document(self, project_id: UUID, document_id: UUID) -> Document | None:
        pass

    @abstractmethod
    def save_document(self, document: Document) -> None:
        pass

    @abstractmethod
    def delete_document(self, project_id: UUID, document_id: UUID) -> bool:
        pass

    @abstractmethod
    def get_execution_results(self, project_id: UUID) -> list[ExecutionResult]:
        pass

    @abstractmethod
    def get_execution_result(self, project_id: UUID, execution_id: UUID) -> ExecutionResult | None:
        pass

    @abstractmethod
    def save_execution_result(self, project_id: UUID, result: ExecutionResult) -> None:
        pass

    @abstractmethod
    def get_scenario_notes(self, scenario_id: UUID) -> list[str]:
        pass

    @abstractmethod
    def add_scenario_note(self, scenario_id: UUID, note: str) -> None:
        pass

    @abstractmethod
    def get_test_case_notes(self, test_case_id: UUID) -> list[str]:
        pass

    @abstractmethod
    def add_test_case_note(self, test_case_id: UUID, note: str) -> None:
        pass

    @abstractmethod
    def begin_transaction(self) -> None:
        pass

    @abstractmethod
    def commit(self) -> None:
        pass

    @abstractmethod
    def rollback(self) -> None:
        pass

    @abstractmethod
    def get_requirement(self, requirement_id: UUID) -> Requirement | None:
        pass

    @abstractmethod
    def get_scenario(self, scenario_id: UUID) -> Scenario | None:
        pass

    @abstractmethod
    def get_report_by_execution(self, execution_id: UUID) -> dict | None:
        pass

    @abstractmethod
    def save_report(self, project_id: UUID, execution_id: UUID, report_payload: dict) -> dict:
        pass

    @abstractmethod
    def list_releases(self, project_id: UUID) -> list[Release]:
        pass

    @abstractmethod
    def create_release(self, project_id: UUID, name: str, description: str | None = None, status: str = "Active", start_date: datetime | None = None, end_date: datetime | None = None) -> Release:
        pass

    @abstractmethod
    def get_release(self, release_id: UUID) -> Release | None:
        pass

    @abstractmethod
    def list_test_cycles(self, release_id: UUID) -> list[TestCycle]:
        pass

    @abstractmethod
    def create_test_cycle(self, release_id: UUID, name: str, description: str | None = None, status: str = "Active") -> TestCycle:
        pass

    @abstractmethod
    def get_test_cycle(self, cycle_id: UUID) -> TestCycle | None:
        pass

    def get_scenarios_for_project(self, project_id: UUID) -> list[Scenario]:
        reqs = self.get_requirements(project_id)
        scs = []
        for r in reqs:
            scs.extend(self.get_scenarios(r.id))
        return scs

    def get_test_cases_for_project(self, project_id: UUID) -> list[TestCase]:
        scs = self.get_scenarios_for_project(project_id)
        tcs = []
        for s in scs:
            tcs.extend(self.get_test_cases(s.id))
        return tcs

    @abstractmethod
    def reset_demo_data(self) -> None:
        pass


class JSONProjectRepository(ProjectRepository):
    def __init__(self, file_path: Path = STORE_PATH):
        self.file_path = file_path
        self._ensure_store_exists()

    def _ensure_store_exists(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self._write_raw({
                "projects": [],
                "requirements": {},
                "scenarios": {},
                "test_cases": {},
                "documents": {},
                "execution_results": {},
                "scenario_notes": {},
                "test_case_notes": {},
                "releases": {},
                "test_cycles": {}
            })

    def _read_raw(self) -> dict:
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if "releases" not in raw:
                raw["releases"] = {}
            if "test_cycles" not in raw:
                raw["test_cycles"] = {}
            return raw
        except json.JSONDecodeError as jde:
            print(f"[ERROR] JSON decode error reading {self.file_path}: {jde}")
            if self.file_path.exists() and self.file_path.stat().st_size > 0:
                raise jde
            return {
                "projects": [],
                "requirements": {},
                "scenarios": {},
                "test_cases": {},
                "documents": {},
                "execution_results": {},
                "scenario_notes": {},
                "test_case_notes": {},
                "releases": {},
                "test_cycles": {}
            }
        except Exception as e:
            print(f"[ERROR] Failed to read {self.file_path}: {e}")
            raise e

    def _write_raw(self, data: dict) -> None:
        import os
        temp_path = self.file_path.with_suffix(".tmp")
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(temp_path, self.file_path)
        except Exception as e:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            raise e

    def list_projects(self, include_deleted: bool = False) -> list[Project]:
        raw = self._read_raw()
        return [
            Project.model_validate(p)
            for p in raw.get("projects", [])
            if include_deleted or not p.get("is_deleted", False)
        ]

    def get_project(self, project_id: UUID, include_deleted: bool = False) -> Project | None:
        projects = self.list_projects(include_deleted=include_deleted)
        for p in projects:
            if p.id == project_id:
                return p
        return None

    def create_project(self, name: str, description: str, line_of_business: str = "general", framework: str = "playwright", jira_project_key: str | None = None, target_url: str | None = "https://adactinhotelapp.com/", target_username: str | None = None, target_password_enc: str | None = None) -> Project:
        raw = self._read_raw()
        project = Project(
            id=uuid4(),
            name=name,
            description=description,
            line_of_business=line_of_business,
            framework=framework,
            jira_project_key=jira_project_key,
            target_url=target_url,
            target_username=target_username,
            target_password_enc=target_password_enc,
            created_at=datetime.now(timezone.utc),
            requirements=[]
        )
        raw.setdefault("projects", []).append(project.model_dump(mode="json"))
        self._write_raw(raw)
        return project

    def update_project(self, project: Project) -> Project | None:
        raw = self._read_raw()
        projects = raw.setdefault("projects", [])
        p_id_str = str(project.id)
        for idx, p in enumerate(projects):
            if p["id"] == p_id_str:
                projects[idx] = project.model_dump(mode="json")
                self._write_raw(raw)
                return project
        return None

    def delete_project(self, project_id: UUID, deleted_by: UUID | None = None) -> bool:
        raw = self._read_raw()
        projects = raw.setdefault("projects", [])
        p_id_str = str(project_id)
        
        project_idx = -1
        for idx, p in enumerate(projects):
            if p["id"] == p_id_str and not p.get("is_deleted", False):
                project_idx = idx
                break
                
        if project_idx == -1:
            return False

        # Soft delete instead of hard delete
        now = datetime.now(timezone.utc).isoformat()
        projects[project_idx]["is_deleted"] = True
        projects[project_idx]["deleted_at"] = now
        projects[project_idx]["deleted_by"] = str(deleted_by) if deleted_by else None

        # Clean up child elements
        proj = Project.model_validate(projects[project_idx])
        
        # Clean up requirements and their descendants
        reqs_raw = raw.setdefault("requirements", {})
        scenarios_raw = raw.setdefault("scenarios", {})
        tcs_raw = raw.setdefault("test_cases", {})
        
        for r_id in proj.requirements:
            r_str = str(r_id)
            if r_str in reqs_raw:
                reqs_raw[r_str]["is_deleted"] = True
                reqs_raw[r_str]["deleted_at"] = now
                reqs_raw[r_str]["deleted_by"] = str(deleted_by) if deleted_by else None
                
            # Clean up scenarios belonging to requirement
            for s_id, s_data in scenarios_raw.items():
                if s_data.get("requirement_id") == r_str:
                    s_data["is_deleted"] = True
                    s_data["deleted_at"] = now
                    s_data["deleted_by"] = str(deleted_by) if deleted_by else None
                    # Clean up test cases belonging to scenario
                    for tc_id, tc_data in tcs_raw.items():
                        if tc_data.get("scenario_id") == s_id:
                            tc_data["is_deleted"] = True
                            tc_data["deleted_at"] = now
                            tc_data["deleted_by"] = str(deleted_by) if deleted_by else None

        # Clean up documents
        docs_raw = raw.setdefault("documents", {})
        for d_id, d_data in docs_raw.items():
            if d_data.get("project_id") == p_id_str:
                d_data["is_deleted"] = True
                d_data["deleted_at"] = now
                d_data["deleted_by"] = str(deleted_by) if deleted_by else None
            
        # Clean up executions
        execs_raw = raw.setdefault("execution_results", {})
        for e_id, e_data in execs_raw.items():
            if e_data.get("project_id") == p_id_str:
                e_data["is_deleted"] = True
                e_data["deleted_at"] = now
                e_data["deleted_by"] = str(deleted_by) if deleted_by else None

        self._write_raw(raw)
        return True

    def get_requirements(self, project_id: UUID, include_deleted: bool = False) -> list[Requirement]:
        project = self.get_project(project_id, include_deleted=include_deleted)
        if not project:
            return []
        
        raw = self._read_raw()
        reqs_raw = raw.get("requirements", {})
        result = []
        for r_id in project.requirements:
            r_str = str(r_id)
            if r_str in reqs_raw:
                req_data = reqs_raw[r_str]
                if include_deleted or not req_data.get("is_deleted", False):
                    result.append(Requirement.model_validate(req_data))
        return result

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
        raw = self._read_raw()
        projects = raw.setdefault("projects", [])
        project_idx = -1
        for idx, p in enumerate(projects):
            if p["id"] == str(project_id):
                project_idx = idx
                break
        
        if project_idx == -1:
            raise ValueError(f"Project {project_id} not found")

        reqs_raw = raw.setdefault("requirements", {})
        existing_req_id = None
        if requirement_id:
            proj_reqs = projects[project_idx].get("requirements", [])
            for r_id_str in proj_reqs:
                r_data = reqs_raw.get(r_id_str)
                if r_data and r_data.get("requirement_id") == requirement_id:
                    existing_req_id = UUID(r_id_str)
                    break

        if existing_req_id:
            req_id = existing_req_id
            req = Requirement(
                id=req_id,
                title=title,
                description=description,
                source=RequirementSource.MANUAL,
                uploaded_at=datetime.now(timezone.utc),
                original_filename=original_filename,
                requirement_id=requirement_id,
                requirement_title=requirement_title,
                release_id=release_id
            )
            req_dump = req.model_dump(mode="json")
            req_dump["priority"] = priority
            req_dump["business_domain"] = business_domain
            req_dump["attachments"] = attachments or []
            req_dump["is_deleted"] = False
            req_dump["deleted_at"] = None
            req_dump["deleted_by"] = None
            reqs_raw[str(req_id)] = req_dump
        else:
            req_id = uuid4()
            req = Requirement(
                id=req_id,
                title=title,
                description=description,
                source=RequirementSource.MANUAL,
                uploaded_at=datetime.now(timezone.utc),
                original_filename=original_filename,
                requirement_id=requirement_id,
                requirement_title=requirement_title,
                release_id=release_id
            )
            
            req_dump = req.model_dump(mode="json")
            req_dump["priority"] = priority
            req_dump["business_domain"] = business_domain
            req_dump["attachments"] = attachments or []
            
            reqs_raw[str(req_id)] = req_dump
            projects[project_idx].setdefault("requirements", []).append(str(req_id))
        
        self._write_raw(raw)
        return req

    def save_requirement(
        self,
        requirement: Requirement,
        priority: str | None = None,
        business_domain: str | None = None,
        attachments: list[str] | None = None
    ) -> None:
        raw = self._read_raw()
        req_id_str = str(requirement.id)
        reqs_raw = raw.setdefault("requirements", {})
        
        # Merge existing metadata fields (priority, business_domain, attachments) if not provided
        existing = reqs_raw.get(req_id_str, {})
        
        req_dump = requirement.model_dump(mode="json")
        req_dump["priority"] = priority or existing.get("priority", "medium")
        req_dump["business_domain"] = business_domain or existing.get("business_domain", "general")
        req_dump["attachments"] = attachments if attachments is not None else existing.get("attachments", [])
        
        reqs_raw[req_id_str] = req_dump
        self._write_raw(raw)

    def get_scenarios(self, requirement_id: UUID, include_deleted: bool = False) -> list[Scenario]:
        raw = self._read_raw()
        scenarios_raw = raw.get("scenarios", {})
        result = []
        for s_id, s_data in scenarios_raw.items():
            if s_data.get("requirement_id") == str(requirement_id):
                if include_deleted or not s_data.get("is_deleted", False):
                    result.append(Scenario.model_validate(s_data))
        return result

    def save_scenarios(self, scenarios: list[Scenario]) -> None:
        raw = self._read_raw()
        scenarios_raw = raw.setdefault("scenarios", {})
        for s in scenarios:
            scenarios_raw[str(s.id)] = s.model_dump(mode="json")
        self._write_raw(raw)

    def update_scenario(self, scenario: Scenario) -> Scenario | None:
        raw = self._read_raw()
        scenarios_raw = raw.setdefault("scenarios", {})
        s_id_str = str(scenario.id)
        if s_id_str in scenarios_raw:
            scenarios_raw[s_id_str] = scenario.model_dump(mode="json")
            self._write_raw(raw)
            return scenario
        return None

    def delete_scenario(self, scenario_id: UUID, deleted_by: UUID | None = None) -> bool:
        raw = self._read_raw()
        scenarios_raw = raw.setdefault("scenarios", {})
        s_id_str = str(scenario_id)
        if s_id_str in scenarios_raw and not scenarios_raw[s_id_str].get("is_deleted", False):
            now = datetime.now(timezone.utc).isoformat()
            scenarios_raw[s_id_str]["is_deleted"] = True
            scenarios_raw[s_id_str]["deleted_at"] = now
            scenarios_raw[s_id_str]["deleted_by"] = str(deleted_by) if deleted_by else None
            
            # Soft delete child test cases
            tcs_raw = raw.setdefault("test_cases", {})
            for tc_id, tc_data in tcs_raw.items():
                if tc_data.get("scenario_id") == s_id_str:
                    tc_data["is_deleted"] = True
                    tc_data["deleted_at"] = now
                    tc_data["deleted_by"] = str(deleted_by) if deleted_by else None
            self._write_raw(raw)
            return True
        return False

    def clear_scenarios_for_requirement(self, requirement_id: UUID) -> None:
        raw = self._read_raw()
        scenarios_raw = raw.setdefault("scenarios", {})
        tcs_raw = raw.setdefault("test_cases", {})
        
        req_id_str = str(requirement_id)
        now = datetime.now(timezone.utc).isoformat()
        for s_id, s_data in scenarios_raw.items():
            if s_data.get("requirement_id") == req_id_str:
                s_data["is_deleted"] = True
                s_data["deleted_at"] = now
                for tc_id, tc_data in tcs_raw.items():
                    if tc_data.get("scenario_id") == s_id:
                        tc_data["is_deleted"] = True
                        tc_data["deleted_at"] = now
        self._write_raw(raw)

    def get_test_cases(self, scenario_id: UUID, include_deleted: bool = False) -> list[TestCase]:
        raw = self._read_raw()
        tcs_raw = raw.get("test_cases", {})
        result = []
        for tc_id, tc_data in tcs_raw.items():
            if tc_data.get("scenario_id") == str(scenario_id):
                if include_deleted or not tc_data.get("is_deleted", False):
                    result.append(TestCase.model_validate(tc_data))
        return result

    def save_test_cases(self, test_cases: list[TestCase]) -> None:
        raw = self._read_raw()
        tcs_raw = raw.setdefault("test_cases", {})
        for tc in test_cases:
            tcs_raw[str(tc.id)] = tc.model_dump(mode="json")
        self._write_raw(raw)

    def update_test_case(self, test_case: TestCase) -> TestCase | None:
        raw = self._read_raw()
        tcs_raw = raw.setdefault("test_cases", {})
        tc_id_str = str(test_case.id)
        if tc_id_str in tcs_raw:
            tcs_raw[tc_id_str] = test_case.model_dump(mode="json")
            self._write_raw(raw)
            return test_case
        return None

    def get_test_case(self, test_case_id: UUID, include_deleted: bool = False) -> TestCase | None:
        raw = self._read_raw()
        tcs_raw = raw.get("test_cases", {})
        tc_id_str = str(test_case_id)
        if tc_id_str in tcs_raw:
            tc_data = tcs_raw[tc_id_str]
            if include_deleted or not tc_data.get("is_deleted", False):
                return TestCase.model_validate(tc_data)
        return None

    def delete_test_case(self, test_case_id: UUID, deleted_by: UUID | None = None) -> bool:
        raw = self._read_raw()
        tcs_raw = raw.setdefault("test_cases", {})
        tc_id_str = str(test_case_id)
        if tc_id_str in tcs_raw and not tcs_raw[tc_id_str].get("is_deleted", False):
            now = datetime.now(timezone.utc).isoformat()
            tcs_raw[tc_id_str]["is_deleted"] = True
            tcs_raw[tc_id_str]["deleted_at"] = now
            tcs_raw[tc_id_str]["deleted_by"] = str(deleted_by) if deleted_by else None
            self._write_raw(raw)
            return True
        return False

    def delete_test_cases_for_scenario(self, scenario_id: UUID) -> None:
        raw = self._read_raw()
        tcs_raw = raw.setdefault("test_cases", {})
        s_id_str = str(scenario_id)
        now = datetime.now(timezone.utc).isoformat()
        for tc_id, tc_data in tcs_raw.items():
            if tc_data.get("scenario_id") == s_id_str:
                tc_data["is_deleted"] = True
                tc_data["deleted_at"] = now
        self._write_raw(raw)

    def get_documents(self, project_id: UUID) -> list[Document]:
        raw = self._read_raw()
        docs_raw = raw.get("documents", {})
        result = []
        p_id_str = str(project_id)
        for doc_id, doc_data in docs_raw.items():
            if doc_data.get("project_id") == p_id_str:
                result.append(Document.model_validate(doc_data))
        return result

    def get_document(self, project_id: UUID, document_id: UUID) -> Document | None:
        raw = self._read_raw()
        doc_data = raw.get("documents", {}).get(str(document_id))
        if doc_data and doc_data.get("project_id") == str(project_id):
            return Document.model_validate(doc_data)
        return None

    def save_document(self, document: Document) -> None:
        raw = self._read_raw()
        docs_raw = raw.setdefault("documents", {})
        docs_raw[str(document.id)] = document.model_dump(mode="json")
        self._write_raw(raw)

    def delete_document(self, project_id: UUID, document_id: UUID) -> bool:
        raw = self._read_raw()
        docs_raw = raw.setdefault("documents", {})
        doc_id_str = str(document_id)
        if doc_id_str in docs_raw and docs_raw[doc_id_str].get("project_id") == str(project_id):
            del docs_raw[doc_id_str]
            self._write_raw(raw)
            return True
        return False

    def get_execution_results(self, project_id: UUID) -> list[ExecutionResult]:
        raw = self._read_raw()
        execs_raw = raw.get("execution_results", {})
        result = []
        p_id_str = str(project_id)
        for ex_id, ex_data in execs_raw.items():
            if ex_data.get("project_id") == p_id_str:
                # ExecutionResult doesn't natively have project_id in standard Pydantic schema, 
                # but we validate it after cleaning the extra field so validation matches.
                cleaned = dict(ex_data)
                cleaned.pop("project_id", None)
                result.append(ExecutionResult.model_validate(cleaned))
        return result

    def get_execution_result(self, project_id: UUID, execution_id: UUID) -> ExecutionResult | None:
        raw = self._read_raw()
        execs_raw = raw.get("execution_results", {})
        ex_id_str = str(execution_id)
        if ex_id_str in execs_raw and execs_raw[ex_id_str].get("project_id") == str(project_id):
            cleaned = dict(execs_raw[ex_id_str])
            cleaned.pop("project_id", None)
            return ExecutionResult.model_validate(cleaned)
        return None

    def save_execution_result(self, project_id: UUID, result: ExecutionResult) -> None:
        raw = self._read_raw()
        execs_raw = raw.setdefault("execution_results", {})
        dump = result.model_dump(mode="json")
        dump["project_id"] = str(project_id)
        execs_raw[str(result.id)] = dump
        self._write_raw(raw)

    def get_scenario_notes(self, scenario_id: UUID) -> list[str]:
        raw = self._read_raw()
        notes_raw = raw.get("scenario_notes", {})
        return notes_raw.get(str(scenario_id), [])

    def add_scenario_note(self, scenario_id: UUID, note: str) -> None:
        raw = self._read_raw()
        notes_raw = raw.setdefault("scenario_notes", {})
        timestamp = datetime.now().strftime("%Y-%m-%d %I:%M %p")
        formatted = f"[{timestamp}] {note}"
        notes_raw.setdefault(str(scenario_id), []).append(formatted)
        self._write_raw(raw)

    def get_test_case_notes(self, test_case_id: UUID) -> list[str]:
        raw = self._read_raw()
        notes_raw = raw.get("test_case_notes", {})
        return notes_raw.get(str(test_case_id), [])

    def add_test_case_note(self, test_case_id: UUID, note: str) -> None:
        raw = self._read_raw()
        notes_raw = raw.setdefault("test_case_notes", {})
        timestamp = datetime.now().strftime("%Y-%m-%d %I:%M %p")
        formatted = f"[{timestamp}] {note}"
        notes_raw.setdefault(str(test_case_id), []).append(formatted)
        self._write_raw(raw)

    def begin_transaction(self) -> None:
        pass

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def get_requirement(self, requirement_id: UUID) -> Requirement | None:
        raw = self._read_raw()
        reqs_raw = raw.get("requirements", {})
        req_id_str = str(requirement_id)
        if req_id_str in reqs_raw:
            return Requirement.model_validate(reqs_raw[req_id_str])
        return None

    def get_scenario(self, scenario_id: UUID) -> Scenario | None:
        raw = self._read_raw()
        scenarios_raw = raw.get("scenarios", {})
        sc_id_str = str(scenario_id)
        if sc_id_str in scenarios_raw:
            return Scenario.model_validate(scenarios_raw[sc_id_str])
        return None

    def get_report_by_execution(self, execution_id: UUID) -> dict | None:
        raw = self._read_raw()
        reports_raw = raw.get("reports", {})
        ex_id_str = str(execution_id)
        for r_info in reports_raw.values():
            if r_info.get("execution_id") == ex_id_str:
                return r_info
        return None

    def save_report(self, project_id: UUID, execution_id: UUID, report_payload: dict) -> dict:
        raw = self._read_raw()
        reports_raw = raw.setdefault("reports", {})
        report_id = report_payload.get("id") or str(uuid4())
        payload = dict(report_payload)
        payload["id"] = report_id
        payload["project_id"] = str(project_id)
        payload["execution_id"] = str(execution_id)
        reports_raw[report_id] = payload
        self._write_raw(raw)
        return payload

    def list_releases(self, project_id: UUID) -> list[Release]:
        raw = self._read_raw()
        releases_raw = raw.get("releases", {})
        results = []
        for r_id_str, r_data in releases_raw.items():
            if r_data.get("project_id") == str(project_id):
                results.append(Release.model_validate(r_data))
        return results

    def create_release(self, project_id: UUID, name: str, description: str | None = None, status: str = "Active", start_date: datetime | None = None, end_date: datetime | None = None) -> Release:
        raw = self._read_raw()
        releases_raw = raw.setdefault("releases", {})
        release = Release(
            id=uuid4(),
            project_id=project_id,
            name=name,
            description=description,
            status=status,
            start_date=start_date,
            end_date=end_date
        )
        releases_raw[str(release.id)] = release.model_dump(mode="json")
        self._write_raw(raw)
        return release

    def get_release(self, release_id: UUID) -> Release | None:
        raw = self._read_raw()
        releases_raw = raw.get("releases", {})
        r_data = releases_raw.get(str(release_id))
        if r_data:
            return Release.model_validate(r_data)
        return None

    def list_test_cycles(self, release_id: UUID) -> list[TestCycle]:
        raw = self._read_raw()
        cycles_raw = raw.get("test_cycles", {})
        results = []
        for c_id_str, c_data in cycles_raw.items():
            if c_data.get("release_id") == str(release_id):
                results.append(TestCycle.model_validate(c_data))
        return results

    def create_test_cycle(self, release_id: UUID, name: str, description: str | None = None, status: str = "Active") -> TestCycle:
        raw = self._read_raw()
        cycles_raw = raw.setdefault("test_cycles", {})
        cycle = TestCycle(
            id=uuid4(),
            release_id=release_id,
            name=name,
            description=description,
            status=status
        )
        cycles_raw[str(cycle.id)] = cycle.model_dump(mode="json")
        self._write_raw(raw)
        return cycle

    def get_test_cycle(self, cycle_id: UUID) -> TestCycle | None:
        raw = self._read_raw()
        cycles_raw = raw.get("test_cycles", {})
        c_data = cycles_raw.get(str(cycle_id))
        if c_data:
            return TestCycle.model_validate(c_data)
        return None

    def reset_demo_data(self) -> None:
        raw = self._read_raw()
        
        # 1. Find projects to delete matching sample names
        project_ids_to_delete = []
        remaining_projects = []
        for p in raw.get("projects", []):
            name_lower = p.get("name", "").lower()
            if any(k in name_lower for k in ["bank", "vehicle", "tricentis", "adactin"]):
                project_ids_to_delete.append(str(p.get("id")))
            else:
                remaining_projects.append(p)
        raw["projects"] = remaining_projects

        proj_ids_str = set(project_ids_to_delete)

        # 2. Filter requirements
        remaining_reqs = {}
        for req_id, req in raw.get("requirements", {}).items():
            if str(req.get("project_id")) not in proj_ids_str:
                remaining_reqs[req_id] = req
        raw["requirements"] = remaining_reqs

        req_ids_set = set(remaining_reqs.keys())

        # 3. Filter scenarios
        remaining_scenarios = {}
        for sc_id, sc in raw.get("scenarios", {}).items():
            if str(sc.get("requirement_id")) in req_ids_set:
                remaining_scenarios[sc_id] = sc
        raw["scenarios"] = remaining_scenarios

        scenario_ids_set = set(remaining_scenarios.keys())

        # 4. Filter test cases
        remaining_tcs = {}
        for tc_id, tc in raw.get("test_cases", {}).items():
            if str(tc.get("scenario_id")) in scenario_ids_set:
                remaining_tcs[tc_id] = tc
        raw["test_cases"] = remaining_tcs

        tc_ids_set = set(remaining_tcs.keys())

        # 5. Filter execution results
        remaining_execs = {}
        for exec_id, ex in raw.get("execution_results", {}).items():
            if str(ex.get("test_case_id")) in tc_ids_set:
                remaining_execs[exec_id] = ex
        raw["execution_results"] = remaining_execs

        # 6. Filter releases
        remaining_releases = {}
        for rel_id, rel in raw.get("releases", {}).items():
            if str(rel.get("project_id")) not in proj_ids_str:
                remaining_releases[rel_id] = rel
        raw["releases"] = remaining_releases

        release_ids_set = set(remaining_releases.keys())

        # 7. Filter test cycles
        remaining_cycles = {}
        for cyc_id, cyc in raw.get("test_cycles", {}).items():
            if str(cyc.get("release_id")) in release_ids_set:
                remaining_cycles[cyc_id] = cyc
        raw["test_cycles"] = remaining_cycles

        # 8. Filter documents
        remaining_docs = {}
        for doc_id, doc in raw.get("documents", {}).items():
            if str(doc.get("project_id")) not in proj_ids_str:
                remaining_docs[doc_id] = doc
        raw["documents"] = remaining_docs

        # 9. Scenario notes
        remaining_sc_notes = {}
        for sc_id, notes in raw.get("scenario_notes", {}).items():
            if sc_id in scenario_ids_set:
                remaining_sc_notes[sc_id] = notes
        raw["scenario_notes"] = remaining_sc_notes

        # 10. Test case notes
        remaining_tc_notes = {}
        for tc_id, notes in raw.get("test_case_notes", {}).items():
            if tc_id in tc_ids_set:
                remaining_tc_notes[tc_id] = notes
        raw["test_case_notes"] = remaining_tc_notes

        self._write_raw(raw)

        # Update vault.json
        vault_path = Path(__file__).parent.parent / "database" / "vault.json"
        if vault_path.exists():
            try:
                with open(vault_path, "r", encoding="utf-8") as vf:
                    vault_data = json.load(vf)
                for pid in proj_ids_str:
                    if pid in vault_data:
                        del vault_data[pid]
                with open(vault_path, "w", encoding="utf-8") as vf:
                    json.dump(vault_data, vf, indent=2)
            except Exception as ve:
                print(f"[ERROR] Failed to clean vault.json: {ve}")


class RepositoryProvider:
    _instance: ProjectRepository | None = None

    @classmethod
    def get_repository(cls) -> ProjectRepository:
        if cls._instance is None:
            from backend.config.settings import get_settings
            settings = get_settings()
            provider_name = settings.repository.provider
            if provider_name == "postgres":
                from backend.repository.postgres_project_repository import PostgresProjectRepository
                cls._instance = PostgresProjectRepository()
            else:
                cls._instance = JSONProjectRepository()
        return cls._instance


_active_project_repo = None


def get_project_repository() -> ProjectRepository:
    global _active_project_repo
    if _active_project_repo is not None:
        return _active_project_repo
    return RepositoryProvider.get_repository()
