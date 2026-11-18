"""
FeatureInventoryAgent

Purpose:
    Enriches requirements with existing feature context from knowledge base using RAG.
    Detects feature overlap and provides related documentation to inform scenario generation.

Responsibilities:
    - Query RAG vector database for related feature documentation
    - Retrieve top-k similar feature contexts
    - Persist feature mapping report to requirement metadata
    - Gracefully skip if RAG service is unavailable or misconfigured
    - Never fail requirement ingestion due to RAG errors

Workflow Position:
    SupervisorAgent (INGEST operation)
        ↓
    RequirementAnalystAgent
        ↓
    FeatureInventoryAgent (only if RAG enabled)
        ↓
    END

Inputs:
    - state.requirement: Enriched requirement from RequirementAnalystAgent
    - state.project_id: the project whose own knowledge base is searched (spec 0006, AC-23)
    - RAG configuration: settings.rag.enabled and the project's rag_dataset_id

Outputs:
    - requirement.feature_mapping: Retrieved feature context text
    - Updated requirement persisted to repository

RAG Query:
    Query format: "Feature capabilities and user scenarios related to: {requirement.description}"
    Top-k: 3 (configurable)
    
    Vector database searches for semantically similar feature documentation,
    user stories, and existing test scenarios.

Use Case:
    - Avoid re-implementing existing features
    - Reference existing test coverage for similar capabilities
    - Inform scenario generation with related acceptance criteria
    - Detect API/component dependencies

Configuration Validation:
    - Check settings.rag.enabled = True and the provider is ragflow
    - Check the state has a project and the project has a dataset
    - Skip if any validation fails

Error Handling:
    - Catch all exceptions from RAG service
    - Log graceful skip message
    - Never raise exceptions (optional enrichment, not critical path)
    - Requirement ingestion proceeds even if RAG fails

Design Philosophy:
    RAG enrichment is optional value-add, not a hard dependency.
    Failure to retrieve feature context should not block requirement ingestion.
    This agent is a "nice-to-have" enrichment layer.
"""

from backend.agents.base import BaseAgent
from backend.models.state import WorkflowState
from backend.repository.project_repository import get_project_repository
from backend.services.knowledge_service import KnowledgeNotReady, KnowledgeService


class FeatureInventoryAgent(BaseAgent):
    """
    RAG-powered feature context enrichment agent.

    Searches the project's own knowledge base (spec 0006, AC-23) for related
    features to inform scenario generation. Gracefully degrades if RAG is unavailable.
    """

    name = "Feature Inventory Agent"

    def __init__(self, knowledge_service: KnowledgeService | None = None, repo=None):
        """
        Args:
            knowledge_service: Knowledge service (injected for testing); it uses
                the shared retriever and the project's own dataset.
            repo: Project repository (injected for testing)
        """
        self.knowledge_service = knowledge_service or KnowledgeService()
        self.repo = repo or get_project_repository()

    def run(self, state: WorkflowState) -> WorkflowState:
        """
        Enrich requirement with feature context from the project's knowledge base.

        Raises:
            ValueError: If state.requirement is None

        Never fails ingestion: a missing project, a project without a dataset,
        a disabled retriever, or a search error only logs a skip.
        """
        if not state.requirement:
            raise ValueError(f"{self.name} requires state.requirement to be set")

        state.add_log(f"{self.name} started")
        req = state.requirement

        project = self.repo.get_project(state.project_id) if state.project_id else None
        if project is None:
            state.add_log(f"{self.name}: No project on the workflow state. Skipping feature inventory mapping.")
            return state

        try:
            query = f"Feature capabilities and user scenarios related to: {req.description}"
            items = self.knowledge_service.search(project, query, top_k=3)
        except KnowledgeNotReady:
            state.add_log(f"{self.name}: The project knowledge base is not set up. Skipping feature inventory mapping.")
            return state
        except Exception as e:
            # Failure of RAG / knowledge sources must never fail requirement ingestion
            state.add_log(f"{self.name}: Gracefully skipped optional feature mapping after error: {e}")
            return state

        context = "\n\n".join(item["text"] for item in items if item.get("text"))
        if not context.strip():
            state.add_log(f"{self.name}: No overlapping features detected in the project knowledge base.")
            return state

        try:
            state.add_log(f"{self.name}: Retrieved relevant feature context from the project knowledge base.")
            new_req = req.model_copy(update={"feature_mapping": context})
            self.repo.save_requirement(new_req)
            state.requirement = new_req
            state.add_log(f"{self.name}: Feature overlap mapped and persisted in requirement metadata.")
        except Exception as e:
            state.add_log(f"{self.name}: Gracefully skipped optional feature mapping after error: {e}")

        return state
