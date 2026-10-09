"""
BaseAgent

Purpose:
    Abstract base class defining the contract for all agentic AI workflow nodes.
    Every agent in the LangGraph pipeline implements this interface.

Responsibilities:
    - Define standardized run() interface for all agents
    - Enforce state-based communication pattern
    - Enable polymorphic agent execution by SupervisorAgent

Workflow Position:
    BaseAgent is the root contract for all workflow agents:
        SupervisorAgent
        ScenarioAgent
        TestCaseAgent
        EvaluationAgent
        HumanApprovalAgent
        PlaywrightAgent
        ExecutionAgent
        ReportAgent
        RequirementAnalystAgent
        FeatureInventoryAgent
        BacklogCreationAgent
        JiraSyncAgent
        QAStoryAnalyzerAgent
        ExecutionAnalysisAgent
        DefectManagementAgent
        QAAutomationOrchestratorAgent

Design Pattern:
    Template Method Pattern - defines skeleton of algorithm in base class,
    delegates specific steps to concrete implementations via run() method.
"""

from abc import ABC, abstractmethod

from backend.models.state import WorkflowState


class BaseAgent(ABC):
    """
    Abstract base class for all AI agents in the workflow.
    
    All agents follow a consistent pattern:
    1. Read required data from WorkflowState
    2. Perform agent-specific processing (LLM calls, validations, integrations)
    3. Update only their designated fields in WorkflowState
    4. Return the modified state for the next agent
    """
    
    name: str

    @abstractmethod
    def run(self, state: WorkflowState) -> WorkflowState:
        """
        Execute the agent's core logic on the workflow state.
        
        Args:
            state: Current workflow state containing all accumulated context
            
        Returns:
            Updated workflow state with agent-specific modifications
            
        Raises:
            ValueError: If required state preconditions are not met
            
        Implementation Contract:
            - MUST validate required preconditions (e.g., requirement exists)
            - MUST log agent start/completion to state.logs
            - SHOULD only modify fields within agent's responsibility domain
            - MUST NOT modify other agents' output fields
        """
        raise NotImplementedError