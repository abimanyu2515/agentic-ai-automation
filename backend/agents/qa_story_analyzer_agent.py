"""
QAStoryAnalyzerAgent

Purpose:
    Analyzes user stories to extract testing context (risks, test strategy, scope, coverage suggestions).
    Provides strategic test planning guidance before scenario generation.

Responsibilities:
    - Perform risk analysis on user story acceptance criteria
    - Determine appropriate test strategy (UI, API, manual, performance)
    - Define test scope (in-scope vs out-of-scope capabilities)
    - Suggest coverage areas (edge cases, negative flows, boundary conditions)
    - Populate state.testing_context for ScenarioAgent consumption

Workflow Position:
    BacklogCreationAgent
        ↓
    QAStoryAnalyzerAgent
        ↓
    ScenarioAgent (uses testing_context to inform scenario generation)

Inputs:
    - state.user_stories: User stories from BacklogCreationAgent
    - state.requirement: Fallback if no user stories exist

Outputs:
    - state.testing_context: Dictionary with:
        - risk_analysis: List of RiskItem objects
        - test_strategy: TestStrategyDetails (approach, environment)
        - test_scope: TestScopeDetails (in_scope, out_scope)
        - coverage_suggestions: List of coverage recommendations

LLM Output Schema:
    TestingContextResponse:
        - risk_analysis: List of RiskItem
            - area: Risk area (e.g., "Authentication")
            - risk_level: "high" | "medium" | "low"
            - mitigation: Mitigation strategy
        - test_strategy:
            - approach: "Automated functional" | "API" | "Performance" | "Manual"
            - environment: "QA" | "UAT" | "Staging" | "Production"
        - test_scope:
            - in_scope: List of capabilities to test
            - out_scope: List of capabilities excluded
        - coverage_suggestions: List of coverage recommendations

Use Cases:
    - Identify high-risk areas requiring more scenarios
    - Determine automation feasibility (UI vs API testing)
    - Define test environment requirements
    - Guide ScenarioAgent toward edge cases and negative flows
    - Inform test data requirements

Error Handling:
    If LLM analysis fails:
    - Fall back to empty context with defaults:
        - risk_analysis: []
        - test_strategy: {"approach": "standard QA", "environment": "QA"}
        - test_scope: {"in_scope": [], "out_scope": []}
        - coverage_suggestions: []
    - Log error but continue workflow

Design Philosophy:
    Optional strategic planning layer before tactical scenario generation.
    ScenarioAgent can work without testing_context (defaults to basic scenarios).
    With testing_context, ScenarioAgent produces more comprehensive test coverage.
"""

from backend.agents.base import BaseAgent
from backend.models.state import WorkflowState
from backend.services.llm import LLMService
from backend.utils.prompts import load_prompt
from pydantic import BaseModel, Field


# ==========================================================
# LLM Output Schema
# ==========================================================

class RiskItem(BaseModel):
    """Identified risk area with mitigation strategy."""
    area: str = Field(description="Area of risk or potential failure point")
    risk_level: str = Field(description="high, medium, or low")
    mitigation: str = Field(description="Proposed mitigation strategy")


class TestStrategyDetails(BaseModel):
    """Test approach and environment configuration."""
    approach: str = Field(description="Automated functional, API, performance, or manual strategy details")
    environment: str = Field(description="Target execution environment (e.g. QA, UAT, Staging, Production)")


class TestScopeDetails(BaseModel):
    """Test scope boundaries (what's included vs excluded)."""
    in_scope: list[str] = Field(default_factory=list, description="Capabilities in scope")
    out_scope: list[str] = Field(default_factory=list, description="Capabilities out of scope")


class TestingContextResponse(BaseModel):
    """Complete testing context analysis."""
    risk_analysis: list[RiskItem] = Field(default_factory=list)
    test_strategy: TestStrategyDetails
    test_scope: TestScopeDetails
    coverage_suggestions: list[str] = Field(default_factory=list)


class QAStoryAnalyzerAgent(BaseAgent):
    """
    User story testing context analysis agent.
    
    Extracts risk analysis, test strategy, scope, and coverage recommendations
    to guide scenario generation.
    """
    
    name = "QA Story Analyzer Agent"

    def __init__(self, llm_service: LLMService | None = None):
        """
        Initialize QA story analyzer agent.
        
        Args:
            llm_service: LLM service instance (injected for testing)
        """
        self.llm_service = llm_service or LLMService()

    def run(self, state: WorkflowState) -> WorkflowState:
        """
        Analyze user story for testing context.
        
        Args:
            state: Workflow state with user_stories or requirement
            
        Returns:
            Updated state with testing_context populated
            
        Process:
            1. Extract user story text and acceptance criteria
            2. Fallback to requirement description if no user stories
            3. Skip if neither exists
            4. Load QA story analyzer prompt template
            5. Call LLM with TestingContextResponse schema
            6. Populate state.testing_context
            7. On error, fall back to empty context defaults
        """
        state.add_log(f"{self.name} started")

        # Compile user story text from user_stories list or fall back to requirement description
        story_text = ""
        ac_text = ""
        if state.user_stories:
            story = state.user_stories[0]
            story_text = story.get("description", story.get("title", ""))
            ac_text = "\n".join(story.get("acceptance_criteria", []))
        elif state.requirement:
            story_text = state.requirement.description
            ac_text = "\n".join(state.requirement.acceptance_criteria)
        else:
            state.add_log(f"{self.name}: No story or requirement provided. Bypassing analysis.")
            return state

        try:
            prompt = load_prompt(
                "agents/qa_story_analyzer/roles_and_responsibilities.md",
                user_story=story_text,
                acceptance_criteria=ac_text
            )
            analysis = self.llm_service.structured_generate(
                user=prompt,
                response_model=TestingContextResponse
            )
            state.testing_context = analysis.model_dump()
            state.add_log(f"{self.name}: Analysis complete. Risks identified: {len(analysis.risk_analysis)}")
        except Exception as e:
            state.add_log(f"{self.name}: Story analysis failed, falling back to empty context: {e}")
            state.testing_context = {
                "risk_analysis": [],
                "test_strategy": {"approach": "standard QA", "environment": "QA"},
                "test_scope": {"in_scope": [], "out_scope": []},
                "coverage_suggestions": []
            }

        return state
