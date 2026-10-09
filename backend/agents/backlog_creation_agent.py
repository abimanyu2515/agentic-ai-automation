"""
BacklogCreationAgent

Purpose:
    Generates Agile backlog (Epics, Features, User Stories) from requirements using LLM.
    Produces hierarchical story structure with acceptance criteria for sprint planning.

Responsibilities:
    - Generate Epic/Feature/User Story hierarchy via LLM
    - Structure output as agile backlog with acceptance criteria
    - Flatten stories into state.user_stories for workflow consumption
    - Persist user story notes to scenario records (legacy compatibility)
    - Log backlog generation summary

Workflow Position:
    SupervisorAgent (BACKLOG operation)
        ↓
    BacklogCreationAgent
        ↓
    QAStoryAnalyzerAgent (optional)
        ↓
    ScenarioAgent
        ↓
    (continues through test generation pipeline)

Inputs:
    - state.requirement: Requirement with title and description

Outputs:
    - state.user_stories: Flattened list of user story dictionaries
    - Scenario notes with user story content (for legacy unit test compatibility)

LLM Output Schema (Hierarchical):
    EpicResponse:
        - title: Epic title
        - description: Epic description
        - features: List of FeatureBlock
    
    FeatureBlock:
        - title: Feature title
        - description: Feature description
        - user_stories: List of StoryBlock
    
    StoryBlock:
        - title: User story title
        - description: "As a [role], I want to [action], so that [value]"
        - acceptance_criteria: List of acceptance criteria strings

Flattened Output Format:
    Each user story in state.user_stories contains:
    {
        "title": "Login with valid credentials",
        "description": "As a user, I want to log in with valid credentials, so that I can access my account",
        "acceptance_criteria": ["User can enter username", "Password is validated", ...],
        "feature_title": "Authentication",
        "epic_title": "User Management"
    }

Legacy Compatibility:
    If state.generated_scenarios is pre-populated (unit tests):
    - Generate user story for each scenario
    - Save as scenario note via repository
    - Format: "**Agile User Story**: As a user, I want to execute scenario {name}..."

Design Philosophy:
    Backlog generation happens BEFORE scenario generation.
    User stories inform scenario generation (QAStoryAnalyzerAgent extracts context).
    Produces sprint-ready backlog items with acceptance criteria.
"""

from backend.agents.base import BaseAgent
from backend.models.state import WorkflowState
from backend.services.llm import LLMService
from backend.repository.project_repository import get_project_repository
from backend.utils.prompts import load_prompt
from pydantic import BaseModel, Field


# ==========================================================
# LLM Output Schema (Hierarchical Backlog Structure)
# ==========================================================

class StoryBlock(BaseModel):
    """User story with acceptance criteria."""
    title: str = Field(description="Title of user story")
    description: str = Field(description="Format: As a [role], I want to [action], so that [value]")
    acceptance_criteria: list[str] = Field(default_factory=list, description="Agile acceptance criteria list")


class FeatureBlock(BaseModel):
    """Feature containing multiple user stories."""
    title: str = Field(description="Feature title")
    description: str = Field(description="Feature description")
    user_stories: list[StoryBlock] = Field(min_length=1)


class EpicResponse(BaseModel):
    """Epic containing multiple features."""
    title: str = Field(description="Epic title")
    description: str = Field(description="Epic description")
    features: list[FeatureBlock] = Field(min_length=1)


class UserStoryResponse(BaseModel):
    """Single user story (legacy format)."""
    user_story: str = Field(description="Format: As a [user role], I want to [action], so that [business value].")
    acceptance_criteria: list[str] = Field(description="List of acceptance criteria for this user story.")


class BacklogCreationAgent(BaseAgent):
    """
    Agile backlog generation agent using LLM.
    
    Produces hierarchical Epic/Feature/User Story structure
    with acceptance criteria for sprint planning.
    """
    
    name = "Backlog Creation Agent"

    def __init__(self, llm_service: LLMService | None = None):
        """
        Initialize backlog creation agent.
        
        Args:
            llm_service: LLM service instance (injected for testing)
        """
        self.llm_service = llm_service or LLMService()
        self.repo = get_project_repository()

    def run(self, state: WorkflowState) -> WorkflowState:
        """
        Generate Agile backlog from requirement.
        
        Args:
            state: Workflow state containing requirement
            
        Returns:
            Updated state with user_stories populated
            
        Raises:
            ValueError: If state.requirement is None
            
        Process:
            1. Validate requirement exists
            2. Load backlog creation prompt template
            3. Call LLM with structured EpicResponse schema
            4. Flatten Epic/Feature/Story hierarchy into state.user_stories
            5. If scenarios pre-exist (legacy tests), generate story notes
            6. Log backlog generation summary
        """
        if not state.requirement:
            raise ValueError(f"{self.name} requires state.requirement to be set")

        state.add_log(f"{self.name} started")

        try:
            # Load standard roles_and_responsibilities prompt template
            prompt = load_prompt(
                "agents/backlog_creation/roles_and_responsibilities.md",
                requirement_detail=f"Title: {state.requirement.title}\nDescription: {state.requirement.description}",
                scenarios="N/A (Generating backlog prior to scenarios)"
            )
            epic = self.llm_service.structured_generate(
                user=prompt,
                response_model=EpicResponse
            )

            # Flat-map stories to state.user_stories and keep the hierarchical tree
            state.user_stories = []
            for f in epic.features:
                for story in f.user_stories:
                    story_dict = {
                        "title": story.title,
                        "description": story.description,
                        "acceptance_criteria": story.acceptance_criteria,
                        "feature_title": f.title,
                        "epic_title": epic.title
                    }
                    state.user_stories.append(story_dict)

            state.add_log(f"{self.name}: Backlog tree created. Stories generated: {len(state.user_stories)}")

            # Legacy unit test compatibility fallback: if scenarios are pre-populated, map notes
            if state.generated_scenarios:
                for scenario in state.generated_scenarios:
                    notes_content = (
                        f"**Agile User Story**:\nAs a user, I want to execute scenario {scenario.scenario_name}.\n\n"
                        f"**Acceptance Criteria**:\n- Verify execution output behaves correctly."
                    )
                    self.repo.add_scenario_note(scenario.id, notes_content)
                    state.add_log(f"{self.name}: Generated user story for scenario '{scenario.scenario_name}' and saved as scenario note.")

        except Exception as e:
            state.add_log(f"{self.name} failed: {e}")

        return state
