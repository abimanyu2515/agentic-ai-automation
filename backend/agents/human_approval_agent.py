"""
HumanApprovalAgent

Purpose:
    Validates and finalizes human approval decisions recorded in workflow state.
    Enforces business rules for test case approval gates.

Responsibilities:
    - Process pending approval decisions from state.pending_approval_ids
    - Validate that test cases exist and are eligible for approval
    - Reject approval attempts for REJECTED test cases (duplicates, irrelevant)
    - Move approved test case IDs to state.human_approved_test_case_ids
    - Log approval summary

Workflow Position:
    (Used twice in the workflow at different approval gates)
    
    Gate 1 - Scenario Approval:
    ScenarioAgent
        ↓
    HumanApprovalAgent
        ↓
    TestCaseAgent
    
    Gate 2 - Test Case Approval:
    EvaluationAgent
        ↓
    HumanApprovalAgent
        ↓
    PlaywrightAgent

Inputs:
    - state.pending_approval_ids: List of test case IDs awaiting approval
    - state.generated_test_cases: All test cases with evaluation status

Outputs:
    - state.human_approved_test_case_ids: Updated list of approved IDs

Business Rules:
    - Cannot approve REJECTED test cases (duplicates or irrelevant)
    - Can only approve NEEDS_REVIEW test cases
    - Approval decisions must reference existing test cases

Design Philosophy:
    This agent does not call LLMs or make decisions.
    It validates and records decisions that were made elsewhere
    (via WorkflowState.request_test_case_approval()).

Human-in-the-Loop:
    Current implementation expects pending_approval_ids to be pre-populated.
    Full human-in-the-loop requires LangGraph interrupt/resume (not yet implemented).
    The agent's logic is correct regardless of when/how it's invoked.

Error Handling:
    - Raises ValueError if pending approval references unknown test case
    - Raises ValueError if attempting to approve REJECTED test case
    - This is intentional - approval gate violations should fail fast
"""

from backend.agents.base import BaseAgent
from backend.models.state import WorkflowState
from backend.models.test_case import EvaluationStatus


class HumanApprovalAgent(BaseAgent):
    """
    Human approval validation and recording agent.
    
    This agent does not call LLMs. It enforces approval business rules
    and records human decisions.
    """
    
    name = "Human Approval Agent"

    def run(self, state: WorkflowState) -> WorkflowState:
        """
        Process pending human approval decisions.
        
        Args:
            state: Workflow state with pending approvals and test cases
            
        Returns:
            Updated state with human_approved_test_case_ids populated
            
        Raises:
            ValueError: If pending approval references unknown test case
            ValueError: If attempting to approve REJECTED test case
            
        Process:
            1. Check if any pending approvals exist
            2. Map test cases by ID for lookup
            3. For each pending approval:
                a. Validate test case exists
                b. Reject if evaluation_status == REJECTED
                c. Add to human_approved_test_case_ids
            4. Log approval summary
        """
        if state.requirement is None:
            raise ValueError(f"{self.name} requires state.requirement to be set")

        state.add_log(f"{self.name} started")

        if not state.pending_approval_ids:
            state.add_log(f"{self.name}: no pending human decisions to process")
            return state

        test_cases_by_id = {tc.id: tc for tc in state.generated_test_cases}
        newly_approved = 0

        for test_case_id in state.pending_approval_ids:
            test_case = test_cases_by_id.get(test_case_id)
            if test_case is None:
                raise ValueError(
                    f"{self.name}: pending approval for unknown test case {test_case_id} - "
                    f"this should have been caught by request_test_case_approval()"
                )

            if test_case.evaluation_status == EvaluationStatus.REJECTED:
                raise ValueError(
                    f"{self.name}: cannot approve '{test_case.title}' - it was rejected "
                    f"(duplicate or below the relevance floor). This agent only resolves "
                    f"items needing review, it doesn't override an evaluation rejection."
                )

            if test_case_id not in state.human_approved_test_case_ids:
                state.human_approved_test_case_ids.append(test_case_id)
                newly_approved += 1

        state.add_log(
            f"{self.name} finished: {newly_approved} test case(s) human-approved, "
            f"{len(state.approved_test_cases())} total approved for automation"
        )
        return state