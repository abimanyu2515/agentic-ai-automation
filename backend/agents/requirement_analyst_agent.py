"""
RequirementAnalystAgent

Purpose:
    Enriches and validates imported requirements using deterministic parsing and optional LLM analysis.
    Standardizes requirement metadata (priority, business_domain, acceptance_criteria, risks, assumptions).

Responsibilities:
    - Perform deterministic keyword-based parsing for priority and domain
    - Conditionally invoke LLM for deep enrichment when requirement lacks structure
    - Extract functional/non-functional requirements, business rules, acceptance criteria
    - Identify risks and assumptions
    - Persist enriched requirement to project repository

Workflow Position:
    START
        ↓
    SupervisorAgent (INGEST operation)
        ↓
    RequirementAnalystAgent
        ↓
    FeatureInventoryAgent (if RAG enabled)
        ↓
    END

Inputs:
    - state.requirement: Raw requirement from file import or manual entry

Outputs:
    - state.requirement: Enriched requirement with structured metadata
    - Persisted to database via project repository

LLM Invocation Logic:
    LLM enrichment triggered when:
    - Requirement title contains "REQ-MANUAL"
    - Title is "Requirement Block" (default placeholder)
    - Business domain resolved to "general" (no specific keywords detected)
    
    Otherwise, uses fast deterministic parser.

Deterministic Parser:
    - Priority: High if contains ["high", "critical", "urgent", "must"]
    - Priority: Low if contains ["low", "minor", "nice to have"]
    - Priority: Medium (default)
    - Business Domain: Extracted from keywords (banking, finance, auth, billing, etc.)

LLM Enrichment Schema:
    - title: Clean, concise title (3-8 words)
    - description: Formatted description with resolved ambiguities
    - priority: low | medium | high
    - business_domain: Capitalized single word (Finance, Auth, Billing, etc.)
    - functional_requirements: List of functional capabilities
    - non_functional_requirements: List of non-functional constraints
    - business_rules: List of core business rules
    - acceptance_criteria: List of acceptance criteria
    - risks: List of identified risks
    - assumptions: List of assumptions

Design Philosophy:
    Fast path for well-structured requirements (deterministic parser).
    LLM path for ambiguous or unstructured requirements needing enrichment.
"""

from backend.agents.base import BaseAgent
from backend.models.state import WorkflowState
from backend.services.llm import LLMService
from backend.repository.project_repository import get_project_repository
from backend.utils.prompts import load_prompt
from pydantic import BaseModel, Field
from backend.models.requirement import Requirement


# ==========================================================
# LLM Output Schema
# ==========================================================

class RequirementEnrichment(BaseModel):
    """Enriched requirement metadata from LLM analysis."""
    title: str = Field(description="A clean, concise title for the requirement (3-8 words)")
    description: str = Field(description="Enriched, formatted description. Clean up formatting and resolve any obvious ambiguities.")
    priority: str = Field(description="Must be one of: low, medium, high")
    business_domain: str = Field(description="Must be a capitalized single word business domain (e.g. Finance, Banking, Auth, Billing, General)")
    functional_requirements: list[str] = Field(default_factory=list, description="List of functional capabilities.")
    non_functional_requirements: list[str] = Field(default_factory=list, description="List of non-functional checks/constraints.")
    business_rules: list[str] = Field(default_factory=list, description="List of core business rules.")
    acceptance_criteria: list[str] = Field(default_factory=list, description="List of acceptance criteria items.")
    risks: list[str] = Field(default_factory=list, description="List of identified risks.")
    assumptions: list[str] = Field(default_factory=list, description="List of standard assumptions.")


class RequirementAnalystAgent(BaseAgent):
    """
    Requirement enrichment and validation agent.
    
    Uses hybrid approach: deterministic parsing for well-structured requirements,
    LLM enrichment for ambiguous requirements.
    """
    
    name = "Requirement Analyst Agent"

    def __init__(self, llm_service: LLMService | None = None):
        """
        Initialize requirement analyst agent.
        
        Args:
            llm_service: LLM service instance (injected for testing)
        """
        self.llm_service = llm_service or LLMService()
        self.repo = get_project_repository()

    def run(self, state: WorkflowState) -> WorkflowState:
        """
        Enrich requirement with structured metadata.
        
        Args:
            state: Workflow state containing raw requirement
            
        Returns:
            Updated state with enriched requirement
            
        Raises:
            ValueError: If state.requirement is None
            
        Process:
            1. Extract priority and business_domain via deterministic parser
            2. Check if LLM enrichment is needed (title contains REQ-MANUAL, general domain, etc.)
            3. If needed, invoke LLM with enrichment prompt
            4. Reconstruct Requirement object with enriched fields
            5. Persist to project repository
            6. Update state.requirement
        """
        if not state.requirement:
            raise ValueError(f"{self.name} requires state.requirement to be set")

        state.add_log(f"{self.name} started")
        req = state.requirement

        # Perform deterministic parsing first as baseline
        priority = "medium"
        if any(k in req.description.lower() for k in ["high", "critical", "urgent", "must"]):
            priority = "high"
        elif any(k in req.description.lower() for k in ["low", "minor", "nice to have"]):
            priority = "low"

        business_domain = "general"
        for k in ["banking", "finance", "auth", "login", "profile", "billing", "payment", "checkout", "search", "upload", "insurance", "retail", "healthcare"]:
            if k in req.description.lower():
                business_domain = k.capitalize()
                break

        title = req.title

        # Check if LLM enrichment is requested
        needs_llm = (
            "REQ-MANUAL" in req.title or
            req.title == "Requirement Block" or
            "general" in business_domain.lower()
        )

        if needs_llm:
            state.add_log(f"{self.name}: Invoking LLM for metadata enrichment and ambiguity resolution.")
            try:
                prompt = load_prompt(
                    "agents/requirement_analyst/roles_and_responsibilities.md",
                    title=req.title,
                    description=req.description
                )
                enrichment = self.llm_service.structured_generate(
                    user=prompt,
                    response_model=RequirementEnrichment
                )
                
                # Re-create the Requirement object with enriched values
                enriched_req = Requirement(
                    id=req.id,
                    title=enrichment.title,
                    description=enrichment.description,
                    source=req.source,
                    uploaded_at=req.uploaded_at,
                    original_filename=req.original_filename,
                    requirement_id=req.requirement_id,
                    requirement_title=enrichment.title,
                    functional_requirements=enrichment.functional_requirements,
                    non_functional_requirements=enrichment.non_functional_requirements,
                    business_rules=enrichment.business_rules,
                    acceptance_criteria=enrichment.acceptance_criteria,
                    risks=enrichment.risks,
                    assumptions=enrichment.assumptions,
                    jira_issue_key=req.jira_issue_key,
                    jira_issue_url=req.jira_issue_url,
                    jira_sync_status=req.jira_sync_status,
                    jira_last_synced_at=req.jira_last_synced_at,
                    release_id=req.release_id
                )
                state.requirement = enriched_req
                priority = enrichment.priority
                business_domain = enrichment.business_domain
                state.add_log(f"{self.name}: LLM enrichment complete (Domain: {business_domain}, Priority: {priority})")
            except Exception as e:
                state.add_log(f"{self.name}: LLM enrichment failed, falling back to deterministic parser: {e}")
        else:
            state.add_log(f"{self.name}: Using deterministic parser metadata (Domain: {business_domain}, Priority: {priority})")

        # Persist the finalized requirement to the project repository
        self.repo.save_requirement(state.requirement, priority=priority, business_domain=business_domain)
        state.add_log(f"{self.name}: Finalized requirement persisted successfully.")

        return state
