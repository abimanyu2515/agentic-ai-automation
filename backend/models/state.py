"""
Workflow state.

This is the object that flows through every node in the LangGraph. Each
agent reads what it needs and writes only its own slice of this — the
Scenario Agent appends to `generated_scenarios`, it does not touch
`requirement`, and so on.

Selection is tracked as a list of ids, not by flipping `Scenario.approved`
in place. That keeps "what's selected" answerable by looking at State
alone, without scanning every scenario object for a mutated flag. The
same pattern applies to human approval: `human_approved_test_case_ids`
is a separate list, not a mutation of `TestCase.evaluation_status`.
That's deliberate - evaluation_status is EvaluationAgent's permanent
record of what the AI judged; a human approving a NEEDS_REVIEW item adds
a second, independent decision on top of it rather than rewriting
history. You can always tell "AI was confident" apart from "AI was
uncertain but a human said yes anyway" by checking both fields.

`execution_results` is a separate list on State, not a field on TestCase,
unlike playwright_script - one test case can be run more than once
(retries, re-runs after a fix), so its execution history is a list of
independent events, not a single overwritable field. Look up a test
case's results by matching `execution_result.test_case_id`.
"""

from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, Field

from backend.models.requirement import Requirement
from backend.models.scenario import Scenario
from backend.models.execution_result import ExecutionResult
from backend.models.execution_report import ExecutionReport
from backend.models.test_case import EvaluationStatus, TestCase


class WorkflowState(BaseModel):
    requirement: Requirement | None = None
    generated_scenarios: list[Scenario] = Field(default_factory=list)
    selected_scenario_ids: list[UUID] = Field(default_factory=list)
    generated_test_cases: list[TestCase] = Field(default_factory=list)
    pending_approval_ids: list[UUID] = Field(default_factory=list)
    human_approved_test_case_ids: list[UUID] = Field(default_factory=list)
    execution_results: list[ExecutionResult] = Field(default_factory=list)
    execution_report: ExecutionReport | None = None
    logs: list[str] = Field(default_factory=list)
    user_stories: list[dict] = Field(default_factory=list)
    testing_context: dict | None = None

    def add_log(self, message: str) -> None:
        self.logs.append(f"[{datetime.now(timezone.utc).isoformat()}] {message}")

    def select_scenario(self, scenario_id: UUID) -> None:
        known_ids = {s.id for s in self.generated_scenarios}
        if scenario_id not in known_ids:
            raise ValueError(f"Scenario {scenario_id} is not in generated_scenarios")
        if scenario_id not in self.selected_scenario_ids:
            self.selected_scenario_ids.append(scenario_id)

    def selected_scenarios(self) -> list[Scenario]:
        return [s for s in self.generated_scenarios if s.id in self.selected_scenario_ids]

    def request_test_case_approval(self, test_case_id: UUID) -> None:
        """Records that a human wants to approve this test case. Only
        captures intent - doesn't check whether approval actually makes
        sense (e.g. the test case was already rejected as a duplicate).
        That business-rule check belongs to HumanApprovalAgent, not here;
        this method's only job, same as select_scenario()'s, is checking
        the id actually exists."""
        known_ids = {tc.id for tc in self.generated_test_cases}
        if test_case_id not in known_ids:
            raise ValueError(f"TestCase {test_case_id} is not in generated_test_cases")
        if test_case_id not in self.pending_approval_ids:
            self.pending_approval_ids.append(test_case_id)

    def approved_test_cases(self) -> list[TestCase]:
        """Test cases ready for PlaywrightAgent: either EvaluationAgent
        approved them automatically, or a human approved them despite the
        AI flagging them for review. Rejected test cases never appear
        here regardless of any human action - HumanApprovalAgent enforces
        that a rejected test case can't be approved through this path.

        Ranked by confidence descending. This is a computed view, not a
        stored reordering - generated_test_cases keeps its original
        generation order so the raw record stays intact for auditing."""
        approved = [
            tc
            for tc in self.generated_test_cases
            if tc.evaluation_status == EvaluationStatus.APPROVED or tc.id in self.human_approved_test_case_ids
        ]
        return sorted(approved, key=lambda tc: tc.confidence, reverse=True)