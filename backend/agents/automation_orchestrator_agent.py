"""
QAAutomationOrchestratorAgent

Purpose:
    Routes test cases to appropriate execution strategy (UI Automation, API Automation, Manual Testing).
    Determines automation feasibility and framework selection for each test case.

Responsibilities:
    - Analyze test case steps and expected results
    - Determine optimal execution type via LLM
    - Route to UI Automation, API Automation, or Manual Testing
    - Provide reasoning for routing decision
    - Update test_case.execution_type field

Workflow Position:
    (Optional agent - can be inserted between TestCaseAgent and EvaluationAgent)
    
    TestCaseAgent
        ↓
    QAAutomationOrchestratorAgent
        ↓
    EvaluationAgent

Inputs:
    - state.generated_test_cases: Test cases with steps and expected results

Outputs:
    - test_case.execution_type: "UI Automation" | "API Automation" | "Manual Testing"

Routing Logic (LLM-Powered):
    - UI Automation:
        - Browser interactions (click, type, select)
        - Visual validation (screenshots, element visibility)
        - Multi-step user workflows
        
    - API Automation:
        - HTTP requests/responses
        - JSON/XML payload validation
        - Endpoint testing without UI
        - Integration testing
        
    - Manual Testing:
        - Exploratory testing
        - Usability/UX validation
        - Complex business logic requiring human judgment
        - Not feasible for automation (3rd party integrations, hardware dependencies)

LLM Output Schema:
    OrchestrationResponse:
        - test_case_title: Echo of test case title
        - execution_type: "UI Automation" | "API Automation" | "Manual Testing" (exact match required)
        - reason: Explanation for routing decision

Default Behavior:
    - If execution_type already set (not "UI Automation"), skip orchestration
    - On LLM error, default to "UI Automation"

Design Philosophy:
    Smart routing reduces execution time by using API tests where appropriate.
    Identifies manual test candidates to avoid wasted automation effort.
    Provides audit trail for execution strategy decisions.
"""

from backend.agents.base import BaseAgent
from backend.models.state import WorkflowState
from backend.services.llm import LLMService
from backend.utils.prompts import load_prompt
from pydantic import BaseModel, Field


# ==========================================================
# LLM Output Schema
# ==========================================================

class OrchestrationResponse(BaseModel):
    """Test case execution routing decision."""
    test_case_title: str
    execution_type: str = Field(description="Must be exactly one of: UI Automation, API Automation, Manual Testing")
    reason: str


class QAAutomationOrchestratorAgent(BaseAgent):
    """
    Test case execution strategy routing agent.
    
    Uses LLM to determine optimal execution type for each test case.
    """
    
    name = "QA Automation Orchestrator Agent"

    def __init__(self, llm_service: LLMService | None = None):
        """
        Initialize automation orchestrator agent.
        
        Args:
            llm_service: LLM service instance (injected for testing)
        """
        self.llm_service = llm_service or LLMService()

    def run(self, state: WorkflowState) -> WorkflowState:
        """
        Route test cases to appropriate execution strategies.
        
        Args:
            state: Workflow state with generated_test_cases
            
        Returns:
            Updated state with execution_type assigned to each test case
            
        Process:
            1. Skip if no test cases exist
            2. For each test case:
                a. Skip if execution_type already explicitly set
                b. Load orchestration prompt with test case details
                c. Call LLM with OrchestrationResponse schema
                d. Update test_case.execution_type
                e. Log routing decision and reason
            3. On LLM error, default to "UI Automation"
        """
        state.add_log(f"{self.name} started")

        if not state.generated_test_cases:
            state.add_log(f"{self.name}: No generated test cases to orchestrate.")
            return state

        for tc in state.generated_test_cases:
            # Skip if execution_type is already explicitly configured
            if tc.execution_type and tc.execution_type != "UI Automation":
                continue

            try:
                prompt = load_prompt(
                    "agents/automation_orchestrator/roles_and_responsibilities.md",
                    title=tc.title,
                    steps="\n".join(f"- {step}" for step in tc.steps),
                    expected_result=tc.expected_result
                )
                res = self.llm_service.structured_generate(
                    user=prompt,
                    response_model=OrchestrationResponse
                )
                tc.execution_type = res.execution_type
                state.add_log(f"{self.name}: Routed test case '{tc.title}' to '{tc.execution_type}' ({res.reason})")
            except Exception as e:
                state.add_log(f"{self.name}: Failed to orchestrate test case '{tc.title}': {e}")
                tc.execution_type = "UI Automation"

        return state
