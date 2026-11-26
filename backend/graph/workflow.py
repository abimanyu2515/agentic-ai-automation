"""
LangGraph Workflow Orchestration

Purpose:
    Defines the complete AI test automation pipeline as a LangGraph state machine.
    Coordinates agent execution order, routing logic, and state transitions.

Responsibilities:
    - Build StateGraph with all agent nodes
    - Define conditional routing edges based on SupervisorAgent decisions
    - Wire agent dependencies and execution order
    - Provide default workflow instance for production use
    - Support dependency injection for testing with mocked agents

Workflow Architecture:
    The graph answers one question: "Who runs next?"
    It never calls LLMs directly - that's each agent's responsibility via BaseAgent.run().

Complete Pipeline Flow:
    START
        ↓
    SupervisorAgent (routing decision based on operation)
        ↓
    RequirementAnalystAgent (INGEST operation)
        ↓
    FeatureInventoryAgent (optional, if RAG enabled)
        ↓
    BacklogCreationAgent (BACKLOG operation)
        ↓
    QAStoryAnalyzerAgent (optional, backlog flow)
        ↓
    ScenarioAgent
        ↓
    HumanApprovalAgent (scenario approval)
        ↓
    TestCaseAgent
        ↓
    EvaluationAgent
        ↓
    HumanApprovalAgent (test case approval)
        ↓
    PlaywrightAgent
        ↓
    ExecutionAgent
        ↓
    ExecutionAnalysisAgent
        ↓
    SelfHealingAgent (spec 0011, only with a heal target or a heal to verify;
                      may route back to ExecutionAgent for one rerun)
        ↓
    BugReportingAgent
        ↓
    ReportAgent
        ↓
    END

Operation Types:
    - INGEST: Requirement ingestion and analysis
    - BACKLOG: Generate agile backlog from requirements
    - GENERATE_SCENARIOS: Create test scenarios
    - GENERATE_TESTCASES: Create detailed test cases
    - GENERATE_PLAYWRIGHT: Generate automation scripts
    - EXECUTE: Run automated tests
    - SYNC_USER_STORY: Sync scenarios to JIRA as Stories
    - SYNC_BUG: Sync failures to JIRA as Bugs

Testing Support:
    build_graph() accepts optional agent instances for dependency injection.
    Tests can provide mock agents to test routing logic without real LLM calls.

LangSmith Integration:
    All agent nodes are wrapped with @traceable decorator for observability.
    Gracefully degrades if langsmith is not installed.
"""

from uuid import UUID

from langgraph.graph import END, START, StateGraph

from backend.agents.base import BaseAgent
from backend.agents.supervisor_agent import SupervisorAgent
from backend.agents.evaluation_agent import EvaluationAgent
from backend.agents.execution_agent import ExecutionAgent
from backend.agents.human_approval_agent import HumanApprovalAgent
from backend.agents.playwright_agent import PlaywrightAgent
from backend.agents.report_agent import ReportAgent
from backend.agents.scenario_agent import ScenarioAgent
from backend.agents.test_case_agent import TestCaseAgent
from backend.agents.requirement_analyst_agent import RequirementAnalystAgent
from backend.agents.feature_inventory_agent import FeatureInventoryAgent
from backend.agents.backlog_creation_agent import BacklogCreationAgent
from backend.agents.jira_sync_agent import JiraSyncAgent
from backend.agents.qa_story_analyzer_agent import QAStoryAnalyzerAgent
from backend.agents.execution_analysis_agent import ExecutionAnalysisAgent
from backend.agents.bug_reporting_agent import BugReportingAgent
from backend.agents.self_healing_agent import SelfHealingAgent
from backend.agents.knowledge_retrieval_agent import STAGE_NODES, KnowledgeRetrievalAgent
from backend.models.requirement import Requirement
from backend.models.state import WorkflowState

try:
    # Configure LangSmith environment variables before importing
    import os
    from backend.config.settings import get_settings
    
    settings = get_settings()
    if settings.langsmith.tracing_enabled:
        os.environ['LANGSMITH_TRACING'] = 'true'
        os.environ['LANGCHAIN_TRACING_V2'] = 'true'  # Alternative variable name used by LangChain
        if settings.langsmith.api_key:
            os.environ['LANGSMITH_API_KEY'] = settings.langsmith.api_key
            os.environ['LANGCHAIN_API_KEY'] = settings.langsmith.api_key  # Alternative variable name
        if settings.langsmith.endpoint:
            os.environ['LANGSMITH_ENDPOINT'] = settings.langsmith.endpoint
            os.environ['LANGCHAIN_ENDPOINT'] = settings.langsmith.endpoint  # Alternative variable name
        if settings.langsmith.project_name:
            os.environ['LANGSMITH_PROJECT'] = settings.langsmith.project_name
            os.environ['LANGCHAIN_PROJECT'] = settings.langsmith.project_name  # Alternative variable name
    
    from langsmith import traceable
except ImportError:
    # Graceful degradation if langsmith is not installed
    def traceable(*args, **kwargs):
        if len(args) == 1 and callable(args[0]):
            return args[0]
        return lambda f: f


# ==========================================================
# Graph Builder with Dependency Injection
# ==========================================================

def build_graph(
    scenario_agent: BaseAgent | None = None,
    test_case_agent: BaseAgent | None = None,
    evaluation_agent: BaseAgent | None = None,
    human_approval_agent: BaseAgent | None = None,
    playwright_agent: BaseAgent | None = None,
    execution_agent: BaseAgent | None = None,
    report_agent: BaseAgent | None = None,
    requirement_analyst_agent: BaseAgent | None = None,
    feature_inventory_agent: BaseAgent | None = None,
    backlog_creation_agent: BaseAgent | None = None,
    jira_sync_agent: BaseAgent | None = None,
    qa_story_analyzer_agent: BaseAgent | None = None,
    execution_analysis_agent: BaseAgent | None = None,
    bug_reporting_agent: BaseAgent | None = None,
    knowledge_retrieval_agents: dict[str, BaseAgent] | None = None,
    self_healing_agent: BaseAgent | None = None,
):
    """
    Build LangGraph workflow with optional agent dependency injection.
    
    Args:
        scenario_agent: Optional ScenarioAgent instance (defaults to real agent)
        test_case_agent: Optional TestCaseAgent instance
        evaluation_agent: Optional EvaluationAgent instance
        human_approval_agent: Optional HumanApprovalAgent instance
        playwright_agent: Optional PlaywrightAgent instance
        execution_agent: Optional ExecutionAgent instance
        report_agent: Optional ReportAgent instance
        requirement_analyst_agent: Optional RequirementAnalystAgent instance
        feature_inventory_agent: Optional FeatureInventoryAgent instance
        backlog_creation_agent: Optional BacklogCreationAgent instance
        jira_sync_agent: Optional JiraSyncAgent instance
        qa_story_analyzer_agent: Optional QAStoryAnalyzerAgent instance
        execution_analysis_agent: Optional ExecutionAnalysisAgent instance
        bug_reporting_agent: Optional BugReportingAgent instance
        knowledge_retrieval_agents: Optional retrieval agents keyed by stage
            ("requirements", "scenarios", "test_cases", "analysis"); missing
            stages get a real KnowledgeRetrievalAgent (spec 0006)
        self_healing_agent: Optional SelfHealingAgent instance (spec 0011)
        
    Returns:
        Compiled LangGraph CompiledGraph ready for invocation
        
    Usage:
        # Production usage with real agents:
        graph = build_graph()
        
        # Testing usage with mocked agents:
        mock_scenario = MockScenarioAgent()
        graph = build_graph(scenario_agent=mock_scenario)
    """
    # Initialize agents with defaults if not provided
    supervisor_agent = SupervisorAgent()
    scenario_agent = scenario_agent or ScenarioAgent()
    test_case_agent = test_case_agent or TestCaseAgent()
    evaluation_agent = evaluation_agent or EvaluationAgent()
    human_approval_agent = human_approval_agent or HumanApprovalAgent()
    playwright_agent = playwright_agent or PlaywrightAgent()
    execution_agent = execution_agent or ExecutionAgent()
    report_agent = report_agent or ReportAgent()
    requirement_analyst_agent = requirement_analyst_agent or RequirementAnalystAgent()
    feature_inventory_agent = feature_inventory_agent or FeatureInventoryAgent()
    backlog_creation_agent = backlog_creation_agent or BacklogCreationAgent()
    jira_sync_agent = jira_sync_agent or JiraSyncAgent()
    qa_story_analyzer_agent = qa_story_analyzer_agent or QAStoryAnalyzerAgent()
    execution_analysis_agent = execution_analysis_agent or ExecutionAnalysisAgent()
    bug_reporting_agent = bug_reporting_agent or BugReportingAgent()
    self_healing_agent = self_healing_agent or SelfHealingAgent()
    # Spec 0006: one retrieval agent per stage. Real ones share one knowledge
    # service and one retrieval LLM service, both built on first use.
    injected_retrieval = knowledge_retrieval_agents or {}
    unknown_stages = set(injected_retrieval) - set(STAGE_NODES)
    if unknown_stages:
        raise ValueError(f"Unknown retrieval stages: {sorted(unknown_stages)}")
    shared_with: KnowledgeRetrievalAgent | None = None

    def _retrieval_agent(stage: str) -> BaseAgent:
        nonlocal shared_with
        if stage in injected_retrieval:
            return injected_retrieval[stage]
        agent = KnowledgeRetrievalAgent(stage=stage, share_with=shared_with)
        shared_with = shared_with or agent
        return agent

    retrieval_agents = {stage: _retrieval_agent(stage) for stage in STAGE_NODES}

    @traceable(name="SupervisorAgent")
    def _supervisor_node(state: WorkflowState) -> WorkflowState:
        return supervisor_agent.run(state)

    @traceable(name="ScenarioAgent")
    def _scenario_node(state: WorkflowState) -> WorkflowState:
        return scenario_agent.run(state)

    @traceable(name="HumanApprovalAgent")
    def _human_approval_node_1(state: WorkflowState) -> WorkflowState:
        return human_approval_agent.run(state)

    @traceable(name="TestCaseAgent")
    def _test_case_node(state: WorkflowState) -> WorkflowState:
        return test_case_agent.run(state)

    @traceable(name="EvaluationAgent")
    def _evaluation_node(state: WorkflowState) -> WorkflowState:
        return evaluation_agent.run(state)

    @traceable(name="HumanApprovalAgent")
    def _human_approval_node_2(state: WorkflowState) -> WorkflowState:
        return human_approval_agent.run(state)

    @traceable(name="PlaywrightAgent")
    def _playwright_node(state: WorkflowState) -> WorkflowState:
        return playwright_agent.run(state)

    @traceable(name="ExecutionAgent")
    def _execution_node(state: WorkflowState) -> WorkflowState:
        return execution_agent.run(state)

    @traceable(name="ReportAgent")
    def _report_node(state: WorkflowState) -> WorkflowState:
        return report_agent.run(state)

    @traceable(name="RequirementAnalystAgent")
    def _requirement_analyst_node(state: WorkflowState) -> WorkflowState:
        return requirement_analyst_agent.run(state)

    @traceable(name="FeatureInventoryAgent")
    def _feature_inventory_node(state: WorkflowState) -> WorkflowState:
        return feature_inventory_agent.run(state)

    @traceable(name="BacklogCreationAgent")
    def _backlog_creation_node(state: WorkflowState) -> WorkflowState:
        return backlog_creation_agent.run(state)

    @traceable(name="QAStoryAnalyzerAgent")
    def _qa_story_analyzer_node(state: WorkflowState) -> WorkflowState:
        return qa_story_analyzer_agent.run(state)

    @traceable(name="JiraSyncAgent")
    def _jira_sync_node(state: WorkflowState, config: dict | None = None) -> WorkflowState:
        return jira_sync_agent.run(state, config=config)

    @traceable(name="ExecutionAnalysisAgent")
    def _execution_analysis_node(state: WorkflowState) -> WorkflowState:
        return execution_analysis_agent.run(state)

    @traceable(name="SelfHealingAgent")
    def _self_healing_node(state: WorkflowState) -> WorkflowState:
        return self_healing_agent.run(state)

    @traceable(name="BugReportingAgent")
    def _bug_reporting_node(state: WorkflowState) -> WorkflowState:
        return bug_reporting_agent.run(state)

    def _retrieval_node_for(stage: str):
        agent = retrieval_agents[stage]

        @traceable(name=f"KnowledgeRetrievalAgent[{stage}]")
        def _node(state: WorkflowState) -> WorkflowState:
            return agent.run(state)

        return _node

    builder = StateGraph(WorkflowState)
    
    # Nodes
    builder.add_node("supervisor_agent", _supervisor_node)
    builder.add_node("scenario_agent", _scenario_node)
    builder.add_node("human_approval_agent_1", _human_approval_node_1)
    builder.add_node("test_case_agent", _test_case_node)
    builder.add_node("evaluation_agent", _evaluation_node)
    builder.add_node("human_approval_agent_2", _human_approval_node_2)
    builder.add_node("playwright_agent", _playwright_node)
    builder.add_node("execution_agent", _execution_node)
    builder.add_node("report_agent", _report_node)
    builder.add_node("requirement_analyst_agent", _requirement_analyst_node)
    builder.add_node("feature_inventory_agent", _feature_inventory_node)
    builder.add_node("backlog_creation_agent", _backlog_creation_node)
    builder.add_node("qa_story_analyzer_agent", _qa_story_analyzer_node)
    builder.add_node("jira_sync_agent", _jira_sync_node)
    builder.add_node("execution_analysis_agent", _execution_analysis_node)
    builder.add_node("bug_reporting_agent", _bug_reporting_node)
    builder.add_node("self_healing_agent", _self_healing_node)
    for stage, node_name in STAGE_NODES.items():
        builder.add_node(node_name, _retrieval_node_for(stage))

    # Routing Map
    routing_map = {
        "scenario_agent": "scenario_agent",
        "human_approval_agent_1": "human_approval_agent_1",
        "test_case_agent": "test_case_agent",
        "evaluation_agent": "evaluation_agent",
        "human_approval_agent_2": "human_approval_agent_2",
        "playwright_agent": "playwright_agent",
        "execution_agent": "execution_agent",
        "report_agent": "report_agent",
        "requirement_analyst_agent": "requirement_analyst_agent",
        "feature_inventory_agent": "feature_inventory_agent",
        "backlog_creation_agent": "backlog_creation_agent",
        "qa_story_analyzer_agent": "qa_story_analyzer_agent",
        "jira_sync_agent": "jira_sync_agent",
        "execution_analysis_agent": "execution_analysis_agent",
        "bug_reporting_agent": "bug_reporting_agent",
        "self_healing_agent": "self_healing_agent",
        **{node_name: node_name for node_name in STAGE_NODES.values()},
        "end": END,
    }

    # Edges
    builder.add_edge(START, "supervisor_agent")
    
    builder.add_conditional_edges(
        "supervisor_agent",
        supervisor_agent.route_start,
        routing_map
    )
    
    builder.add_conditional_edges(
        "requirement_analyst_agent",
        supervisor_agent.route_after_requirement_analyst,
        routing_map
    )
    
    builder.add_edge("feature_inventory_agent", END)
    
    builder.add_conditional_edges(
        "backlog_creation_agent",
        supervisor_agent.route_after_backlog_creation,
        routing_map
    )
    # Spec 0006, AC-17: these two were fixed edges; the router sends them
    # through retrieval when RAG_WORKFLOW_RETRIEVAL_ENABLED is on.
    builder.add_conditional_edges(
        "qa_story_analyzer_agent",
        supervisor_agent.route_after_qa_story_analyzer,
        routing_map
    )
    builder.add_edge("scenario_agent", "human_approval_agent_1")
    
    builder.add_conditional_edges(
        "human_approval_agent_1",
        supervisor_agent.route_after_human_approval_1,
        routing_map
    )
    
    builder.add_edge("test_case_agent", "evaluation_agent")
    builder.add_edge("evaluation_agent", "human_approval_agent_2")
    
    builder.add_conditional_edges(
        "human_approval_agent_2",
        supervisor_agent.route_after_human_approval_2,
        routing_map
    )
    
    builder.add_conditional_edges(
        "playwright_agent",
        supervisor_agent.route_after_playwright,
        routing_map
    )
    
    builder.add_conditional_edges(
        "execution_agent",
        supervisor_agent.route_after_execution,
        routing_map
    )
    # Spec 0011: was a fixed edge to bug_reporting_agent.
    builder.add_conditional_edges(
        "execution_analysis_agent",
        supervisor_agent.route_after_execution_analysis,
        routing_map
    )
    builder.add_conditional_edges(
        "self_healing_agent",
        supervisor_agent.route_after_self_healing,
        routing_map
    )
    builder.add_edge("bug_reporting_agent", "report_agent")
    builder.add_edge("report_agent", END)
    builder.add_edge("jira_sync_agent", END)

    # Each retrieval node feeds exactly one consumer.
    builder.add_edge("retrieval_requirements", "requirement_analyst_agent")
    builder.add_edge("retrieval_scenarios", "scenario_agent")
    builder.add_edge("retrieval_test_cases", "test_case_agent")
    builder.add_edge("retrieval_analysis", "execution_analysis_agent")

    return builder.compile()


# ==========================================================
# Default Production Graph
# ==========================================================

# Default real graph - used by run_workflow() and API layer
graph = build_graph()


def run_workflow(requirement: Requirement, graph_instance=None, project_id: UUID | None = None) -> WorkflowState:
    """
    Execute complete workflow for a requirement.
    
    Entry point for workflow execution. Used by API endpoints and integration tests.
    
    Args:
        requirement: Requirement object to process
        graph_instance: Optional graph instance (for testing with mocked agents)
        project_id: The project the requirement belongs to. The retrieval stage
            (spec 0006) searches only this project's knowledge and skips without it.
        
    Returns:
        Final workflow state with all generated artifacts
        
    Usage:
        # Production usage:
        from backend.graph.workflow import run_workflow
        from backend.models.requirement import Requirement
        
        req = Requirement(title="User Login", description="...")
        final_state = run_workflow(req)
        
        # Testing usage:
        mock_graph = build_graph(scenario_agent=MockScenarioAgent())
        final_state = run_workflow(req, graph_instance=mock_graph)
    """
    graph_instance = graph_instance or graph

    initial_state = WorkflowState(project_id=project_id, requirement=requirement)
    initial_state.add_log("Requirement received")

    # In E2E tests / full pipeline, we start without a specific operation config,
    # which the supervisor defaults to routing linearly from start to end.
    raw_result = graph_instance.invoke(initial_state)
    final_state = WorkflowState(**raw_result)

    final_state.add_log("Workflow finished")
    return final_state