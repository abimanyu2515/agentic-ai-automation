from uuid import UUID
from backend.agents.scenario_agent import ScenarioAgent
from backend.agents.test_case_agent import TestCaseAgent
from backend.agents.evaluation_agent import EvaluationAgent
from backend.models.state import WorkflowState
from backend.models.scenario import Scenario
from backend.models.test_case import TestCase
from backend.agents.playwright_agent import PlaywrightAgent
from backend.agents.execution_agent import ExecutionAgent
from backend.models.execution_result import ExecutionResult
from backend.models.requirement import Requirement
from backend.agents.execution_analysis_agent import ExecutionAnalysisAgent
from backend.agents.defect_management_agent import DefectManagementAgent
from backend.repository.project_repository import get_project_repository



class WorkflowService:
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

        if mode == "replace":
            self.repo.clear_scenarios_for_requirement(requirement_id)

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

        # Clear existing test cases for these approved scenarios first to prevent stale records
        for s in approved_scenarios:
            self.repo.delete_test_cases_for_scenario(s.id)

        # New LangGraph-based Orchestration:
        from backend.graph.workflow import build_graph
        from backend.models.operation import WorkflowOperation
        
        local_graph = build_graph()
        state = WorkflowState(requirement=requirement, generated_scenarios=approved_scenarios)
        state.add_log("Starting test case generation and evaluation steps from service via LangGraph")
        
        config = {"configurable": {"operation": WorkflowOperation.GENERATE_TESTCASES}}
        raw_result = local_graph.invoke(state, config)
        final_state = WorkflowState(**raw_result)

        # Persist generated test cases
        self.repo.save_test_cases(final_state.generated_test_cases)
        return final_state.generated_test_cases
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

        # Classify the automation pathway using QA Automation Orchestrator
        from backend.agents.automation_orchestrator_agent import QAAutomationOrchestratorAgent
        test_case.automation_framework = automation_framework
        state = WorkflowState(
            requirement=requirement,
            generated_test_cases=[test_case],
            human_approved_test_case_ids=[test_case.id]
        )
        orchestrator = QAAutomationOrchestratorAgent()
        state = orchestrator.run(state)
        classified_tc = state.generated_test_cases[0]

        if classified_tc.execution_type == "Manual Testing":
            classified_tc.playwright_script = (
                f"// Manual Execution Pathway\n"
                f"// Framework: {automation_framework}\n"
                f"// Steps to perform:\n" +
                "\n".join(f"// - {step}" for step in classified_tc.steps)
            )
            self.repo.save_test_cases([classified_tc])
            return classified_tc

        # New LangGraph-based Orchestration:
        from backend.graph.workflow import build_graph
        from backend.models.operation import WorkflowOperation
        
        local_graph = build_graph(playwright_agent=PlaywrightAgent(repo=self.repo))
        
        config = {
            "configurable": {
                "operation": WorkflowOperation.GENERATE_PLAYWRIGHT,
                "test_case_id": str(test_case_id)
            }
        }
        raw_result = local_graph.invoke(state, config)
        final_state = WorkflowState(**raw_result)

        updated_tc = final_state.generated_test_cases[0]
        self.repo.save_test_cases([updated_tc])
        return updated_tc


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

