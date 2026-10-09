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
    - RAG configuration: settings.rag.enabled, ragflow_api_key, ragflow_dataset_id

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
    - Check settings.rag.enabled = True
    - Check vector_db_provider is configured
    - For RAGFlow: Validate api_key and dataset_id exist
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
from backend.services.rag_service import RAGFlowService
from backend.repository.project_repository import get_project_repository


class FeatureInventoryAgent(BaseAgent):
    """
    RAG-powered feature context enrichment agent.
    
    Queries knowledge base for related features to inform scenario generation.
    Gracefully degrades if RAG is unavailable.
    """
    
    name = "Feature Inventory Agent"

    def __init__(self, rag_service: RAGFlowService | None = None):
        """
        Initialize feature inventory agent.
        
        Args:
            rag_service: RAG service instance (injected for testing)
        """
        self.rag_service = rag_service or RAGFlowService()
        self.repo = get_project_repository()

    def run(self, state: WorkflowState) -> WorkflowState:
        """
        Enrich requirement with RAG feature context.
        
        Args:
            state: Workflow state containing enriched requirement
            
        Returns:
            Updated state with requirement.feature_mapping populated
            
        Raises:
            ValueError: If state.requirement is None
            
        Process:
            1. Validate requirement exists
            2. Check if RAG service is active and configured
            3. Query vector database for related feature contexts
            4. If context retrieved, persist to requirement.feature_mapping
            5. If no context or RAG unavailable, log graceful skip
            6. Catch all exceptions and log without failing workflow
        """
        if not state.requirement:
            raise ValueError(f"{self.name} requires state.requirement to be set")

        state.add_log(f"{self.name} started")
        req = state.requirement

        if not self.rag_service.is_active():
            state.add_log(f"{self.name}: RAGFlow service is not active or not configured. Skipping feature inventory mapping.")
            return state

        try:
            # Query vector store for related feature contexts
            query = f"Feature capabilities and user scenarios related to: {req.description}"
            context = self.rag_service.retrieve_context(query, top_k=3)

            if context.strip():
                state.add_log(f"{self.name}: Retrieved relevant feature context from RAG dataset.")
                # Save feature mapping report inside the requirement
                new_req = req.model_copy(update={"feature_mapping": context})
                self.repo.save_requirement(new_req)
                state.requirement = new_req
                state.add_log(f"{self.name}: Feature overlap mapped and persisted in requirement metadata.")
            else:
                state.add_log(f"{self.name}: No overlapping features detected in RAG database.")
        except Exception as e:
            # Failure of RAG / knowledge sources must never fail requirement ingestion
            state.add_log(f"{self.name}: Gracefully skipped optional feature mapping after error: {e}")

        return state
