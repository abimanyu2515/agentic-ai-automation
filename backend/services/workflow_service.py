import logging
import re
import threading
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from backend.agents.scenario_agent import ScenarioAgent
from backend.agents.test_data_agent import TestDataAgent
from backend.agents.evaluation_agent import EvaluationAgent
from backend.models.state import WorkflowState
from backend.models.scenario import Scenario
from backend.models.test_case import EvaluationStatus, ScriptValidationStatus, TestCase
from backend.models.common import Priority
from backend.agents.playwright_agent import PlaywrightAgent
from backend.agents.execution_agent import ExecutionAgent
from backend.models.execution_result import ExecutionResult
from backend.models.requirement import Requirement
from backend.agents.execution_analysis_agent import ExecutionAnalysisAgent
from backend.agents.defect_management_agent import DefectManagementAgent
from backend.repository.project_repository import get_project_repository
from backend.services.playwright_script_validation import validate_playwright_script

logger = logging.getLogger(__name__)

# A locator call whose argument is a comment or TODO note instead of a selector.
_PLACEHOLDER_LOCATOR = re.compile(
    r"""\.(?:locator|getByText|getByLabel|getByPlaceholder|getByTestId|getByRole)\(\s*"""
    r"""(['"`])\s*(?://\s|TODO\b)[^'"`]*\1"""
)
# page.locator('getByLabel') - a locator method name passed as a CSS selector.
_METHOD_AS_SELECTOR = re.compile(
    r"""\.locator\(\s*(['"`])\s*(?:page\.)?(?:getBy\w+|locator)\b"""
)
# Selector-string actions bypass the runner's Locator screenshot hooks.
_PAGE_SELECTOR_ACTION = re.compile(
    r"""\bpage\.(?:click|fill|type|check|uncheck|selectOption|press)\(\s*['"`]"""
)
_FORCED_FAILURE = re.compile(
    r"""expect\(\s*false\s*\)|\btest\.(?:skip|fixme|fail)\("""
)


_test_data_generation_locks: dict[UUID, threading.Lock] = {}
_test_case_generation_locks: dict[tuple[UUID, UUID], threading.Lock] = {}
_test_data_generation_locks_guard = threading.Lock()


def _test_data_generation_lock(project_id: UUID) -> threading.Lock:
    with _test_data_generation_locks_guard:
        lock = _test_data_generation_locks.get(project_id)
        if lock is None:
            lock = threading.Lock()
            _test_data_generation_locks[project_id] = lock
        return lock


def _test_case_generation_lock(
    project_id: UUID,
    test_case_id: UUID,
) -> threading.Lock:
    key = (project_id, test_case_id)
    with _test_data_generation_locks_guard:
        lock = _test_case_generation_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _test_case_generation_locks[key] = lock
        return lock



class WorkflowService:
    def __init__(self, inspector=None, script_generator=None):
        # Read only page inspector for grounded script generation; injectable for tests.
        self.inspector = inspector
        # Live-browser script generator coroutine; injectable so tests skip the browser and LLM.
        self.script_generator = script_generator

    @property
    def repo(self):
        return get_project_repository()

    def ingest_requirement(self, project_id: UUID, requirement: Requirement) -> Requirement:
        """Orchestrate requirement ingestion, LLM enrichment, and optional feature mapping via LangGraph."""
        from backend.graph.workflow import build_graph
        from backend.models.operation import WorkflowOperation

        local_graph = build_graph()
        state = WorkflowState(requirement=requirement)
        state.add_log("Starting requirement ingestion via LangGraph")

        config = {"configurable": {"operation": WorkflowOperation.INGEST}}
        raw_result = local_graph.invoke(state, config)
        final_state = WorkflowState(**raw_result)

        return final_state.requirement

    def generate_scenarios(self, project_id: UUID, requirement_id: UUID, count: int, mode: str = "append") -> list[Scenario]:
        project = self.repo.get_project(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        requirements = self.repo.get_requirements(project_id)
        requirement = next((r for r in requirements if r.id == requirement_id), None)
        if not requirement:
            raise ValueError(f"Requirement {requirement_id} not found in project {project_id}")

        # New LangGraph-based Orchestration:
        from backend.graph.workflow import build_graph
        from backend.models.operation import WorkflowOperation
        
        scenario_agent = ScenarioAgent(scenario_count=count)
        local_graph = build_graph(scenario_agent=scenario_agent)
        
        state = WorkflowState(requirement=requirement)
        state.add_log("Starting scenario generation from service via LangGraph")
        
        config = {"configurable": {"operation": WorkflowOperation.GENERATE_SCENARIOS}}
        raw_result = local_graph.invoke(state, config)
        final_state = WorkflowState(**raw_result)
        
        # Persist scenarios
        if mode == "replace":
            self.repo.clear_scenarios_for_requirement(requirement_id)
        self.repo.save_scenarios(final_state.generated_scenarios)
        return final_state.generated_scenarios

    def generate_backlog(self, project_id: UUID, requirement_id: UUID) -> list[Scenario]:
        """Orchestrate backlog generation from scenarios via LangGraph."""
        project = self.repo.get_project(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        requirements = self.repo.get_requirements(project_id)
        requirement = next((r for r in requirements if r.id == requirement_id), None)
        if not requirement:
            raise ValueError(f"Requirement {requirement_id} not found in project {project_id}")

        scenarios = self.repo.get_scenarios(requirement_id)
        if not scenarios:
            raise ValueError("No scenarios found. Cannot generate backlog items.")

        from backend.graph.workflow import build_graph
        from backend.models.operation import WorkflowOperation

        local_graph = build_graph()
        state = WorkflowState(requirement=requirement, generated_scenarios=scenarios)
        state.add_log("Starting backlog creation via LangGraph")

        config = {"configurable": {"operation": WorkflowOperation.BACKLOG}}
        raw_result = local_graph.invoke(state, config)
        final_state = WorkflowState(**raw_result)

        return final_state.generated_scenarios

    def generate_test_cases(self, project_id: UUID, requirement_id: UUID) -> list[TestCase]:
        project = self.repo.get_project(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        requirements = self.repo.get_requirements(project_id)
        requirement = next((r for r in requirements if r.id == requirement_id), None)
        if not requirement:
            raise ValueError(f"Requirement {requirement_id} not found in project {project_id}")

        scenarios = self.repo.get_scenarios(requirement_id)
        # Business rule check: only approved scenarios can proceed to test case generation
        approved_scenarios = [s for s in scenarios if s.approved]
        if not approved_scenarios:
            raise ValueError("Cannot generate test cases: no approved scenarios exist for this requirement")

        # New LangGraph-based Orchestration:
        from backend.graph.workflow import build_graph
        from backend.models.operation import WorkflowOperation
        from backend.agents.test_case_agent import TestCaseAgent
        
        local_graph = build_graph(test_case_agent=TestCaseAgent(max_workers=5))
        state = WorkflowState(requirement=requirement, generated_scenarios=approved_scenarios)
        state.add_log("Starting test case generation and evaluation steps from service via LangGraph")
        
        config = {"configurable": {"operation": WorkflowOperation.GENERATE_TESTCASES}}
        raw_result = local_graph.invoke(state, config)
        final_state = WorkflowState(**raw_result)

        # Replace existing cases only after generation succeeds, so failures preserve them.
        for scenario in approved_scenarios:
            self.repo.delete_test_cases_for_scenario(scenario.id)
        self.repo.save_test_cases(final_state.generated_test_cases)
        return final_state.generated_test_cases

    def generate_test_data_test_cases(
        self,
        project_id: UUID,
        requirement_id: UUID,
        generate_scripts: bool = True,
    ) -> tuple[dict, list[TestCase]]:
        with _test_data_generation_lock(project_id):
            return self._generate_test_data_test_cases(
                project_id,
                requirement_id,
                generate_scripts,
            )

    def generate_test_data_for_test_case(
        self,
        project_id: UUID,
        requirement_id: UUID,
        test_case_id: UUID,
    ) -> tuple[dict, TestCase]:
        with _test_case_generation_lock(project_id, test_case_id):
            with _test_data_generation_lock(project_id):
                return self._generate_test_data_for_test_case(
                    project_id,
                    requirement_id,
                    test_case_id,
                )

    def _generate_test_data_for_test_case(
        self,
        project_id: UUID,
        requirement_id: UUID,
        test_case_id: UUID,
    ) -> tuple[dict, TestCase]:
        repo = self.repo
        project = repo.get_project(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")
        if (project.framework or "").lower() != "playwright":
            raise ValueError(
                "Per-test-case test-data execution currently requires the Playwright framework"
            )

        requirement = next(
            (item for item in repo.get_requirements(project_id) if item.id == requirement_id),
            None,
        )
        if not requirement:
            raise ValueError(f"Requirement {requirement_id} not found in project {project_id}")

        test_case = repo.get_test_case(test_case_id)
        if not test_case:
            raise ValueError(f"Test case {test_case_id} not found")
        if (test_case.automation_framework or "").lower() != "playwright":
            raise ValueError(
                "Per-test-case test-data execution requires a Playwright test case"
            )
        scenario = repo.get_scenario(test_case.scenario_id)
        if not scenario or scenario.requirement_id != requirement_id:
            raise ValueError(
                f"Test case {test_case_id} does not belong to requirement {requirement_id}"
            )

        reserved_test_data = []
        for related_scenario in repo.get_scenarios_for_project(project_id):
            for related_case in repo.get_test_cases(related_scenario.id):
                if related_case.id == test_case.id:
                    continue
                if related_case.test_data:
                    reserved_test_data.append(related_case.test_data)
                else:
                    legacy_test_data = TestDataAgent._scenario_test_data(
                        related_scenario
                    )
                    if legacy_test_data:
                        reserved_test_data.append({
                            key: value
                            for key, value in legacy_test_data.items()
                            if key not in {
                                "test_data_id",
                                "targeted_validation_rule",
                                "expected_result",
                                "test_type",
                                "expected_outcome",
                            }
                        })
                reserved_test_data.extend(related_case.test_data_sets)

        state = WorkflowState(
            requirement=requirement,
            generated_scenarios=[scenario],
            generated_test_cases=[test_case],
            human_approved_test_case_ids=[test_case.id],
            testing_context={
                "reserved_test_data": reserved_test_data,
                "application_context": {
                    "name": getattr(project, "name", None),
                    "target_url": getattr(project, "target_url", None),
                },
            },
        )
        previous_test_data_sets = [
            dict(dataset) for dataset in test_case.test_data_sets
        ]
        previous_playwright_script = test_case.playwright_script
        state = TestDataAgent().run(state)
        if len(state.generated_test_data) != 20:
            raise ValueError(
                "TestDataAgent must generate exactly 20 datasets for the selected test case"
            )
        fingerprints = {
            tuple(sorted(dataset.items()))
            for dataset in test_case.test_data_sets
        }
        if len(test_case.test_data_sets) != 20 or len(fingerprints) != 20:
            raise ValueError(
                "TestDataAgent must return 20 distinct datasets for the selected test case"
            )
        test_case.test_data = test_case.test_data_sets[0]
        datasets_unchanged = (
            previous_test_data_sets == test_case.test_data_sets
        )
        script_matches_datasets = (
            bool(previous_playwright_script)
            and previous_playwright_script.count("test(") == 20
            and all(
                f"TD-{test_case.id.hex[:8].upper()}-{index:02d}"
                in previous_playwright_script
                for index in range(1, 21)
            )
        )
        test_case.playwright_script = (
            previous_playwright_script
            if datasets_unchanged and script_matches_datasets
            else None
        )
        repo.save_test_cases([test_case])

        return state.test_data_report or {}, test_case

    def generate_test_data_script_for_test_case(
        self,
        project_id: UUID,
        requirement_id: UUID,
        test_case_id: UUID,
    ) -> TestCase:
        with _test_case_generation_lock(project_id, test_case_id):
            repo = self.repo
            project = repo.get_project(project_id)
            if not project:
                raise ValueError(f"Project {project_id} not found")
            if (project.framework or "").lower() != "playwright":
                raise ValueError(
                    "Per-test-case script generation currently requires the Playwright framework"
                )

            requirement = next(
                (item for item in repo.get_requirements(project_id) if item.id == requirement_id),
                None,
            )
            if not requirement:
                raise ValueError(
                    f"Requirement {requirement_id} not found in project {project_id}"
                )
            test_case = repo.get_test_case(test_case_id)
            if not test_case:
                raise ValueError(f"Test case {test_case_id} not found")
            scenario = repo.get_scenario(test_case.scenario_id)
            if not scenario or scenario.requirement_id != requirement_id:
                raise ValueError(
                    f"Test case {test_case_id} does not belong to requirement {requirement_id}"
                )
            fingerprints = {
                tuple(sorted(dataset.items()))
                for dataset in test_case.test_data_sets
            }
            if len(test_case.test_data_sets) != 20 or len(fingerprints) != 20:
                raise ValueError(
                    "Generate 20 unique datasets for this test case before creating its script"
                )

            dom_context = self._read_only_page_evidence(project, test_case)
            repair_feedback = None
            for attempt in (1, 2):
                state = WorkflowState(
                    project_id=project_id,
                    requirement=requirement,
                    generated_scenarios=[scenario],
                    generated_test_cases=[test_case],
                    human_approved_test_case_ids=[test_case.id],
                    dom_context=dom_context,
                    repair_feedback=repair_feedback,
                )
                PlaywrightAgent(
                    repo=repo,
                    max_workers=1,
                    test_data_template=True,
                ).run(state)
                if not test_case.playwright_script:
                    raise ValueError(
                        f"PlaywrightAgent did not generate a script for '{test_case.title}'"
                    )
                issues = self._script_issues(test_case.playwright_script, dom_context)
                if not issues or attempt == 2:
                    break
                logger.info(
                    "Repairing generated script: test_case_id=%s issue_count=%s",
                    test_case.id,
                    len(issues),
                )
                repair_feedback = (
                    "The first script did not pass these checks: "
                    + "; ".join(issues[:8])
                    + ". Keep locators from the live page evidence on the pages it lists."
                )
            # A new script has not been through grounded validation yet.
            test_case.script_validation_status = ScriptValidationStatus.UNVERIFIED
            test_case.script_validation_attempts = 0
            test_case.script_validation_completed_at = None
            test_case.script_validation_diagnostic = None
            repo.save_test_cases([test_case])
            return test_case

    def _read_only_page_evidence(self, project, test_case: TestCase) -> dict | None:
        """Inspect the project's target app without submitting anything.

        Only projects marked as a safe test environment are inspected. When
        inspection is not allowed or fails, scripts are generated without
        evidence, as before.
        """
        if not getattr(project, "is_safe_test_environment", False):
            return None
        inspector = self.inspector
        if inspector is None:
            from backend.agents.application_reconnaissance_agent import (
                ApplicationReconnaissanceAgent,
            )
            inspector = ApplicationReconnaissanceAgent(repo=self.repo)
        try:
            evidence = inspector.inspect_project(
                project.id,
                [test_case],
                allow_invalid_login_probe=False,
            )
        except SQLAlchemyError:
            raise
        except Exception as exc:
            logger.warning(
                "Page inspection for script generation failed: project_id=%s error=%s",
                project.id,
                type(exc).__name__,
            )
            return None
        return evidence if evidence.get("pages") else None

    @staticmethod
    def _script_issues(script: str, dom_context: dict | None) -> list[str]:
        """Defects worth one regeneration: placeholders, method names used as
        selectors, selector-string actions, forced failures, fixed sleeps,
        compile errors, and no assertion.

        "Locator not observed" findings are not used here. Inspection never logs
        in, so the evidence covers only pre-login pages; repairing against it
        replaced valid checkout locators with TODO placeholders.
        """
        issues = []
        placeholders = _PLACEHOLDER_LOCATOR.findall(script)
        if placeholders:
            issues.append(
                f"{len(placeholders)} locator calls contain a comment or TODO instead of a "
                "selector; replace each with the most likely real locator for that control"
            )
        misused = _METHOD_AS_SELECTOR.findall(script)
        if misused:
            issues.append(
                f"{len(misused)} locator() calls receive a Playwright method name as the "
                "selector; call page.getByRole/getByLabel/getByPlaceholder directly instead"
            )
        if _PAGE_SELECTOR_ACTION.search(script):
            issues.append(
                "Actions call page.click/page.fill with a selector string; act through "
                "Locator objects such as page.getByRole(...).click()"
            )
        if _FORCED_FAILURE.search(script):
            issues.append(
                "The script fails or skips on purpose; perform every step with the best "
                "accessible locator instead"
            )
        if "waitForTimeout(" in script:
            issues.append("The script uses waitForTimeout; wait on the expected outcome instead")
        if "test(" in script and "expect(" not in script:
            issues.append("Script does not assert the expected result")
        if not dom_context:
            return issues
        try:
            found = validate_playwright_script(script, dom_context)
        except RuntimeError:
            # The local TypeScript compiler is unavailable; skip those checks.
            return issues
        asserts_checked = any(issue.startswith("Script does not assert") for issue in issues)
        return issues + sorted({
            issue for issue in found
            if issue.startswith("TypeScript")
            or (issue.startswith("Script does not assert") and not asserts_checked)
        })

    def _generate_test_data_test_cases(
        self,
        project_id: UUID,
        requirement_id: UUID,
        generate_scripts: bool,
    ) -> tuple[dict, list[TestCase]]:
        """Generate one approved test case per TestDataAgent row, optionally scripted."""
        repo = self.repo
        project = repo.get_project(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")
        if generate_scripts and (project.framework or "").lower() != "playwright":
            raise ValueError("Automatic test-data execution currently requires the Playwright framework")

        requirement = next(
            (item for item in repo.get_requirements(project_id) if item.id == requirement_id),
            None,
        )
        if not requirement:
            raise ValueError(f"Requirement {requirement_id} not found in project {project_id}")

        test_data_state = TestDataAgent().run(WorkflowState(requirement=requirement))
        rows = test_data_state.generated_test_data
        if len(rows) != 20:
            raise ValueError(
                f"TestDataAgent must generate exactly 20 rows; generated {len(rows)}"
            )

        priority_by_type = {
            "positive": Priority.HIGH,
            "boundary": Priority.HIGH,
            "invalid": Priority.MEDIUM,
            "missing_optional": Priority.MEDIUM,
            "workflow": Priority.LOW,
        }
        existing_by_data_id: dict[str, tuple[Scenario, TestCase]] = {}
        existing_scenarios = sorted(
            (
                scenario
                for scenario in repo.get_scenarios(requirement_id)
                if "test_data_agent" in scenario.tags
            ),
            key=lambda scenario: scenario.generated_at,
            reverse=True,
        )
        for scenario in existing_scenarios:
            data_id = next(
                (tag for tag in scenario.tags if tag.startswith("TD-")),
                None,
            )
            if not data_id:
                continue
            existing_cases = repo.get_test_cases(scenario.id)
            if existing_cases:
                candidate = (scenario, max(existing_cases, key=lambda case: case.generated_at))
                existing = existing_by_data_id.get(data_id)
                if (
                    existing is None
                    or (
                        candidate[1].playwright_script
                        and not existing[1].playwright_script
                    )
                ):
                    existing_by_data_id[data_id] = candidate

        scenarios: list[Scenario] = []
        generated_cases: list[TestCase] = []
        new_scenarios: list[Scenario] = []
        row_scenarios: list[Scenario] = []
        for row in rows:
            data_id = row["test_data_id"]
            existing = existing_by_data_id.get(data_id)
            if existing:
                scenario, case = existing
                row_scenarios.append(scenario)
                old_test_data = case.test_data
                old_expected_result = case.expected_result
                case.title = scenario.scenario_name
                case.steps = [scenario.description]
                case.test_data = {
                    key: value
                    for key, value in row.items()
                    if key not in {"test_data_id", "expected_result", "test_type"}
                }
                case.expected_result = row["expected_result"]
                if (
                    case.playwright_script
                    and (
                        old_test_data != case.test_data
                        or old_expected_result != case.expected_result
                    )
                ):
                    case.playwright_script = None
                generated_cases.append(case)
                continue

            scenario = Scenario(
                requirement_id=requirement_id,
                scenario_name=f"Test data {data_id}: {row['test_type']}",
                description=(
                    f"Exercise the requirement with the generated input data "
                    f"{data_id}: {row}. Expected result: {row['expected_result']}."
                ),
                priority=priority_by_type.get(row["test_type"], Priority.MEDIUM),
                confidence=1.0,
                approved=True,
                reviewer="TestDataAgent automatic run",
                path_type=row["test_type"],
                tags=["test_data_agent", data_id],
            )
            case = TestCase(
                scenario_id=scenario.id,
                title=scenario.scenario_name,
                steps=[scenario.description],
                expected_result=row["expected_result"],
                priority=scenario.priority,
            )
            row_scenarios.append(scenario)
            new_scenarios.append(scenario)
            generated_cases.append(case)

        case_state = WorkflowState(
            requirement=requirement,
            generated_scenarios=row_scenarios,
            generated_test_cases=generated_cases,
        )
        cases_by_scenario = {
            case.scenario_id: case for case in case_state.generated_test_cases
        }
        if len(cases_by_scenario) != len(rows):
            raise ValueError(
                "Test-data conversion did not create exactly one test case for each row"
            )

        for row, scenario in zip(rows, row_scenarios):
            case = cases_by_scenario.get(scenario.id)
            if case is None:
                raise ValueError(
                    f"Test-data conversion did not return a case for {row['test_data_id']}"
                )
            case.test_data = {
                key: value
                for key, value in row.items()
                if key not in {"test_data_id", "expected_result", "test_type"}
            }
            case.expected_result = row["expected_result"]
            case.evaluation_status = EvaluationStatus.APPROVED
            case.confidence = 1.0
            case.evaluation_reason = (
                "Generated from TestDataAgent output for the user-requested automatic run."
            )

        case_state.human_approved_test_case_ids = [
            case.id for case in case_state.generated_test_cases
        ]
        if new_scenarios:
            repo.save_scenarios(new_scenarios)
        repo.save_test_cases(case_state.generated_test_cases)

        if generate_scripts:
            cases_missing_scripts = [
                case for case in case_state.generated_test_cases
                if not case.playwright_script
            ]
            if cases_missing_scripts:
                script_state = WorkflowState(
                    requirement=requirement,
                    generated_scenarios=row_scenarios,
                    generated_test_cases=cases_missing_scripts,
                    human_approved_test_case_ids=[
                        case.id for case in cases_missing_scripts
                    ],
                )
                script_state = PlaywrightAgent(
                    repo=repo,
                    max_workers=5,
                    test_data_template=True,
                ).run(script_state)
                cases_by_id = {
                    case.id: case for case in script_state.generated_test_cases
                }
                case_state.generated_test_cases = [
                    cases_by_id.get(case.id, case)
                    for case in case_state.generated_test_cases
                ]
            if any(not case.playwright_script for case in case_state.generated_test_cases):
                raise ValueError("PlaywrightAgent did not generate scripts for every test-data case")
            repo.save_test_cases(case_state.generated_test_cases)

        return test_data_state.test_data_report, case_state.generated_test_cases

    def generate_playwright_script(self, project_id: UUID, test_case_id: UUID, automation_framework: str = "Playwright") -> TestCase:
        test_case = self.repo.get_test_case(test_case_id)
        if not test_case:
            raise ValueError(f"TestCase {test_case_id} not found")

        scenario = self.repo.get_scenario(test_case.scenario_id)
        if not scenario:
            raise ValueError(f"Scenario {test_case.scenario_id} not found for TestCase {test_case_id}")

        requirement = self.repo.get_requirement(scenario.requirement_id)
        if not requirement:
            raise ValueError(f"Requirement {scenario.requirement_id} not found for Scenario {scenario.id}")

        # Human approval gate: PlaywrightAgent must reject requests for unapproved test cases.
        if test_case.evaluation_status != "approved":
            raise ValueError("Playwright script generation rejected: Test case is not approved")

        project = self.repo.get_project(project_id)
        if (
            project is not None
            and (automation_framework or "").lower() == "playwright"
            and (project.framework or "playwright").lower() == "playwright"
        ):
            if project.target_url:
                return self._generate_live_verified_script(
                    project_id, project.target_url, test_case, automation_framework
                )
            return self._generate_data_driven_script(
                project_id, requirement.id, test_case, automation_framework
            )

        test_case.automation_framework = automation_framework
        state = WorkflowState(
            requirement=requirement,
            generated_test_cases=[test_case],
            human_approved_test_case_ids=[test_case.id]
        )
        state = PlaywrightAgent(repo=self.repo).run(state)
        updated_tc = state.generated_test_cases[0]
        self.repo.save_test_cases([updated_tc])
        return updated_tc

    def _generate_live_verified_script(
        self,
        project_id: UUID,
        target_url: str,
        test_case: TestCase,
        automation_framework: str,
    ) -> TestCase:
        """Build a TypeScript Playwright script by running the steps on the live target app."""
        import asyncio

        from backend.services.vault_service import VaultService

        script_generator = self.script_generator
        if script_generator is None:
            from backend.agents.script_generator_agent import generate_accurate_test_script

            script_generator = generate_accurate_test_script

        # Login steps need the project's credentials; the generator keeps them out of prompts.
        username, password = VaultService().get_credentials(str(project_id))
        script = asyncio.run(
            script_generator(
                target_url,
                list(test_case.steps),
                test_case.title,
                username=username,
                password=password,
            )
        )
        test_case.automation_framework = automation_framework
        test_case.playwright_script = script
        # A new script has not been through grounded validation yet.
        test_case.script_validation_status = ScriptValidationStatus.UNVERIFIED
        test_case.script_validation_attempts = 0
        test_case.script_validation_completed_at = None
        test_case.script_validation_diagnostic = None
        self.repo.save_test_cases([test_case])
        return test_case

    def _generate_data_driven_script(
        self,
        project_id: UUID,
        requirement_id: UUID,
        test_case: TestCase,
        automation_framework: str,
    ) -> TestCase:
        """Build a Playwright script with one test per TestDataAgent dataset.

        Runs TestDataAgent first when the case does not yet hold 20
        distinct datasets, so every generated script carries them.
        """
        if test_case.automation_framework != automation_framework:
            test_case.automation_framework = automation_framework
            self.repo.save_test_cases([test_case])
        fingerprints = {
            tuple(sorted(dataset.items()))
            for dataset in test_case.test_data_sets
        }
        if len(test_case.test_data_sets) != 20 or len(fingerprints) != 20:
            self.generate_test_data_for_test_case(project_id, requirement_id, test_case.id)
        return self.generate_test_data_script_for_test_case(
            project_id, requirement_id, test_case.id
        )


# Global stream event log buffer
active_execution_events = {}


def run_execution_and_stream(workflow_service: WorkflowService, project_id: UUID, test_case_id: UUID, execution_id: UUID = None, background_tasks = None) -> None:
    tc_id_str = str(test_case_id)
    events = []
    active_execution_events[tc_id_str] = events

    def log_event(status, timeline, log, screenshot=None, artifact=None):
        events.append({
            "status": status,
            "timeline": timeline,
            "log": log,
            "screenshot": screenshot,
            "artifact": artifact
        })

    try:
        log_event("Queued", "Queued in runner pipeline", "Waiting for runner slot allocation...")
        log_event("Preparing Environment", "Preparing execution runner environment", "Initiating process group for Playwright...")
        
        test_case = workflow_service.repo.get_test_case(test_case_id)
        if not test_case:
            raise ValueError(f"TestCase {test_case_id} not found")

        scenario = workflow_service.repo.get_scenario(test_case.scenario_id)
        if not scenario:
            raise ValueError(f"Scenario {test_case.scenario_id} not found")

        # Trigger execution started JIRA sync asynchronously
        from backend.services.jira_sync_service import run_with_background_tasks, sync_execution_started, sync_execution_finished
        run_with_background_tasks(background_tasks, sync_execution_started, scenario.id)

        requirement = workflow_service.repo.get_requirement(scenario.requirement_id)
        if not requirement:
            raise ValueError(f"Requirement {scenario.requirement_id} not found")

        log_event("Running", "Running Playwright TypeScript tests", "npx playwright test --reporter=json --trace=on")

        human_approved = []
        if test_case.evaluation_status != "approved":
            human_approved.append(test_case.id)

        state = WorkflowState(
            requirement=requirement,
            generated_test_cases=[test_case],
            human_approved_test_case_ids=human_approved
        )
        
        import re
        from backend.services.playwright_runner import ARTIFACTS_ROOT
        
        known_screenshots = set()

        def on_log_callback(log_line: str):
            timeline_msg = None
            timeline_match = re.search(r"\[Timeline\]\s*([^:\n]+)(?::\s*(.+))?", log_line, re.IGNORECASE)
            if timeline_match:
                title = timeline_match.group(1).strip()
                desc = timeline_match.group(2).strip() if timeline_match.group(2) else ""
                timeline_msg = f"{title}: {desc}" if desc else title
            
            screenshot_file = None
            try:
                run_dir = ARTIFACTS_ROOT / tc_id_str
                if run_dir.exists():
                    pngs = list(run_dir.glob("*.png")) + list(run_dir.glob("**/*.png"))
                    for png in pngs:
                        png_str = str(png)
                        if png_str not in known_screenshots:
                            known_screenshots.add(png_str)
                            screenshot_file = png_str
                            break
            except Exception:
                pass

            log_event("Running", timeline_msg, log_line, screenshot=screenshot_file)

        class LoggingExecutionAnalysisAgent(ExecutionAnalysisAgent):
            def run(self, state: WorkflowState) -> WorkflowState:
                log_event("Execution Analysis", "Analyzing execution failure", "Identifying root cause and flakiness...")
                return super().run(state)

        class LoggingDefectManagementAgent(DefectManagementAgent):
            def run(self, state: WorkflowState) -> WorkflowState:
                log_event("Jira Sync", "Checking Jira duplicates", "Searching or raising defect tickets...")
                return super().run(state)

        # New LangGraph-based Orchestration:
        from backend.graph.workflow import build_graph
        from backend.models.operation import WorkflowOperation
        
        exec_agent = ExecutionAgent(on_log=on_log_callback, project_id=project_id)
        local_graph = build_graph(
            execution_agent=exec_agent,
            execution_analysis_agent=LoggingExecutionAnalysisAgent(),
            defect_management_agent=LoggingDefectManagementAgent()
        )
        
        config = {"configurable": {"operation": WorkflowOperation.EXECUTE}}
        raw_result = local_graph.invoke(state, config)
        final_state = WorkflowState(**raw_result)
        
        result = final_state.execution_results[0]
        if execution_id:
            result.id = execution_id
        
        # Save updated test case status and execution result in repo
        workflow_service.repo.update_test_case(test_case)
        workflow_service.repo.save_execution_result(project_id, result)
        
        # Compile reports automatically via ReportService
        from backend.services.report_service import ReportService
        report_service = ReportService(workflow_service.repo)
        # Use engine selection method to respect feature flag
        report_service.compile_reports_with_engine_selection(project_id, result)

        # Trigger execution finished JIRA sync asynchronously
        run_with_background_tasks(background_tasks, sync_execution_finished, scenario.id, result.id)
        
        log_event("Reporting", "Finalizing execution report", "Exporting JUnit XML and JSON report format...")
        
        log_event(
            "Completed", 
            "Execution completed successfully", 
            result.error_message or "All steps passed.",
            screenshot=result.screenshot_path,
            artifact={
                "duration": result.duration_seconds,
                "status": result.status.value,
                "screenshot": result.screenshot_path,
                "video": result.video_path,
                "trace": result.trace_path,
                "failure_category": result.failure_category,
                "root_cause_summary": result.root_cause_summary,
                "jira_bug_id": result.jira_bug_id,
                "jira_bug_url": result.jira_bug_url
            }
        )
    except Exception as exc:
        log_event("Completed", "Execution completed with error", str(exc))
