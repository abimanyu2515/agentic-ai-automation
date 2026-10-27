from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from backend.agents.application_reconnaissance_agent import (
    ApplicationInspectionError,
    ApplicationReconnaissanceAgent,
)
from backend.agents.playwright_agent import PlaywrightAgent
from backend.models.state import WorkflowState
from backend.models.test_case import EvaluationStatus, ScriptValidationStatus, TestCase
from backend.repository.project_repository import get_project_repository
from backend.services.playwright_runner import PlaywrightRunner
from backend.services.playwright_script_validation import validate_playwright_script

logger = logging.getLogger(__name__)


class GroundedPlaywrightScriptService:
    _active_projects: set[UUID] = set()
    _active_projects_lock = threading.Lock()

    def __init__(
        self,
        repo=None,
        runner=None,
        inspector=None,
        agent_factory: Callable[[], PlaywrightAgent] | None = None,
    ):
        self._repo = repo
        self.runner = runner or PlaywrightRunner()
        self.inspector = inspector
        self.agent_factory = agent_factory

    @property
    def repo(self):
        return self._repo or get_project_repository()

    def reserve_batch(self, project_id: UUID, test_case_ids: list[UUID]) -> list[TestCase]:
        cases = self._load_approved_cases(project_id, test_case_ids)
        with self._active_projects_lock:
            if project_id in self._active_projects:
                raise ValueError("A script validation batch is already running for this project")
            self._active_projects.add(project_id)
        try:
            for test_case in cases:
                test_case.script_validation_status = ScriptValidationStatus.RUNNING
                test_case.script_validation_attempts = 0
                test_case.script_validation_completed_at = None
                test_case.script_validation_diagnostic = None
                self.repo.update_test_case(test_case)
        except Exception:
            with self._active_projects_lock:
                self._active_projects.discard(project_id)
            raise
        return cases

    def run_reserved_batch(self, project_id: UUID, test_case_ids: list[UUID]) -> None:
        try:
            try:
                self._run_batch(project_id, test_case_ids)
            except SQLAlchemyError:
                logger.exception(
                    "Grounded script validation persistence failed: project_id=%s",
                    project_id,
                )
                raise
            except Exception:
                logger.exception(
                    "Grounded script validation batch failed: project_id=%s",
                    project_id,
                )
                cases = [
                    test_case
                    for test_case_id in test_case_ids
                    if (test_case := self.repo.get_test_case(test_case_id)) is not None
                ]
                self._mark_unverified(
                    cases,
                    "The validation batch could not complete safely.",
                )
        finally:
            with self._active_projects_lock:
                self._active_projects.discard(project_id)

    def _load_approved_cases(
        self,
        project_id: UUID,
        test_case_ids: list[UUID],
    ) -> list[TestCase]:
        if not test_case_ids:
            raise ValueError("Select at least one approved test case")
        if len(set(test_case_ids)) != len(test_case_ids):
            raise ValueError("Duplicate test case ids are not allowed")
        project = self.repo.get_project(project_id)
        if not project:
            raise ValueError("Selected project was not found")
        if not project.is_safe_test_environment:
            raise ValueError("Project is not marked as a safe test environment")
        if (project.framework or "").casefold() != "playwright":
            raise ValueError("Grounded script validation currently requires Playwright")

        cases = []
        for case_id in test_case_ids:
            test_case = self.repo.get_test_case(case_id)
            if not test_case:
                raise ValueError(f"Test case {case_id} was not found")
            if test_case.evaluation_status != EvaluationStatus.APPROVED:
                raise ValueError(f"Test case {case_id} is not approved")
            scenario = self.repo.get_scenario(test_case.scenario_id)
            if not scenario or scenario.requirement_id not in project.requirements:
                raise ValueError(f"Test case {case_id} does not belong to the selected project")
            cases.append(test_case)
        return cases

    def _run_batch(self, project_id: UUID, test_case_ids: list[UUID]) -> None:
        cases = self._load_approved_cases(project_id, test_case_ids)

        try:
            evidence = self._inspector().inspect_project(
                project_id,
                cases,
                allow_invalid_login_probe=True,
            )
            if not evidence.get("pages"):
                raise ApplicationInspectionError(
                    "insufficient_evidence",
                    "The selected project returned no page evidence",
                )
        except SQLAlchemyError:
            raise
        except Exception:
            self._mark_unverified(cases, "The selected site could not be inspected safely.")
            return

        for test_case in cases:
            try:
                requirement = self._requirement_for(test_case)
                self._validate_and_run_case(
                    project_id,
                    requirement,
                    test_case,
                    evidence,
                )
            except Exception as exc:
                if isinstance(exc, SQLAlchemyError):
                    raise
                latest_case = self.repo.get_test_case(test_case.id) or test_case
                latest_case.script_validation_status = ScriptValidationStatus.FAILED
                latest_case.script_validation_completed_at = datetime.now(timezone.utc)
                latest_case.script_validation_diagnostic = (
                    f"Script validation could not complete ({type(exc).__name__})."
                )
                self.repo.update_test_case(latest_case)

    def _validate_and_run_case(self, project_id, requirement, test_case, evidence) -> None:
        feedback = None
        latest_issues: list[str] = []
        for attempt in (1, 2):
            test_case.script_validation_attempts = attempt
            self.repo.update_test_case(test_case)
            state = WorkflowState(
                project_id=project_id,
                requirement=requirement,
                generated_test_cases=[test_case],
                human_approved_test_case_ids=[test_case.id],
                dom_context=evidence,
                repair_feedback=feedback,
            )
            generated_state = self._agent(test_case).run(state)
            test_case = generated_state.generated_test_cases[0]
            if not test_case.playwright_script:
                latest_issues = ["No script was generated"]
            else:
                self.repo.update_test_case(test_case)
                try:
                    latest_issues = validate_playwright_script(
                        test_case.playwright_script,
                        evidence,
                        test_case.expected_result,
                    )
                except RuntimeError:
                    test_case.script_validation_status = ScriptValidationStatus.UNVERIFIED
                    test_case.script_validation_completed_at = datetime.now(timezone.utc)
                    test_case.script_validation_diagnostic = (
                        "The local TypeScript validator is unavailable."
                    )
                    self.repo.update_test_case(test_case)
                    return

                if not latest_issues:
                    result = self.runner.run(
                        test_case.playwright_script,
                        str(uuid4()),
                        project_id=str(project_id),
                    )
                    if result.status != "passed":
                        latest_issues = ["Playwright execution did not pass"]

            if not latest_issues:
                test_case.script_validation_status = ScriptValidationStatus.VALIDATED
                test_case.script_validation_completed_at = datetime.now(timezone.utc)
                test_case.script_validation_diagnostic = None
                self.repo.update_test_case(test_case)
                return

            if attempt == 1:
                feedback = (
                    "The first script did not pass these checks: "
                    + "; ".join(latest_issues[:8])
                    + ". Use the refreshed page evidence and preserve the expected result."
                )
                try:
                    evidence = self._inspector().inspect_project(
                        project_id,
                        [test_case],
                        allow_invalid_login_probe=True,
                    )
                except ApplicationInspectionError:
                    latest_issues = ["Fresh page evidence was unavailable for the repair"]
                    break

        test_case.script_validation_status = ScriptValidationStatus.FAILED
        test_case.script_validation_completed_at = datetime.now(timezone.utc)
        test_case.script_validation_diagnostic = self._safe_diagnostic(
            "; ".join(latest_issues[:3]) or "Script validation failed.",
            test_case,
        )
        self.repo.update_test_case(test_case)

    def _requirement_for(self, test_case: TestCase):
        scenario = self.repo.get_scenario(test_case.scenario_id)
        if not scenario:
            raise ValueError("Test case scenario was not found")
        requirement = self.repo.get_requirement(scenario.requirement_id)
        if not requirement:
            raise ValueError("Test case requirement was not found")
        return requirement

    def _mark_unverified(self, cases: list[TestCase], diagnostic: str) -> None:
        completed_at = datetime.now(timezone.utc)
        for test_case in cases:
            test_case.script_validation_status = ScriptValidationStatus.UNVERIFIED
            test_case.script_validation_completed_at = completed_at
            test_case.script_validation_diagnostic = diagnostic
            self.repo.update_test_case(test_case)

    @staticmethod
    def _safe_diagnostic(diagnostic: str, test_case: TestCase) -> str:
        safe_diagnostic = diagnostic
        for data_set in [test_case.test_data, *test_case.test_data_sets]:
            for value in data_set.values():
                if value:
                    safe_diagnostic = safe_diagnostic.replace(str(value), "[redacted]")
        return safe_diagnostic[:500]

    def _inspector(self):
        return self.inspector or ApplicationReconnaissanceAgent(repo=self.repo)

    def _agent(self, test_case: TestCase):
        # Cases with TestDataAgent datasets keep one test per dataset.
        return (
            self.agent_factory()
            if self.agent_factory
            else PlaywrightAgent(
                repo=self.repo,
                test_data_template=bool(test_case.test_data_sets),
            )
        )
