"""
LangSmith tracing module - isolated SDK imports.

This module provides centralized LangSmith tracing for the Enterprise AI Test Automation Platform.
ALL LangSmith SDK imports MUST stay in this file only.

Architecture:
- Template Method Pattern: BaseAgent.run() wraps _run() with tracing
- Context Manager API: Manual trace wrapping for workflows
- Singleton Pattern: Single tracer instance via get_tracer()
- Graceful Degradation: No-op when disabled or on errors

Usage:
    from backend.services.langsmith_tracer import get_tracer
    
    tracer = get_tracer()
    if tracer.enabled:
        # Use tracing features
        pass
"""

import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from backend.config.settings import LangSmithConfig, get_settings

logger = logging.getLogger(__name__)


class LangSmithTracer:
    """Centralized LangSmith tracing manager with isolated SDK access.
    
    This class manages all interactions with the LangSmith SDK. It provides:
    - Configuration-based initialization
    - Context manager API for manual trace wrapping
    - LangChain callback integration for automatic LLM metrics
    - Metadata extraction from workflow state
    - Graceful error handling
    
    Design Principles:
    - Zero overhead when disabled (no imports, no HTTP calls)
    - Fail gracefully (tracing errors don't break workflows)
    - Real metrics only (no fabricated data)
    - Isolated SDK imports (all LangSmith imports in this file)
    """
    
    def __init__(self, config: LangSmithConfig):
        """Initialize tracer from configuration.
        
        Args:
            config: LangSmith configuration from settings
            
        Side Effects:
            - Sets self.enabled based on configuration and initialization success
            - Initializes LangSmith Client if tracing is enabled
            - Logs initialization status and any errors
        """
        self.config = config
        self.enabled = False
        self.client = None
        
        # Early exit if tracing is disabled in configuration
        if not config.tracing_enabled:
            logger.debug("LangSmith tracing disabled in configuration")
            return
        
        # Validate required configuration
        if not config.api_key:
            logger.warning(
                "LANGSMITH_API_KEY not set. LangSmith tracing disabled. "
                "Set LANGSMITH_TRACING=false to suppress this warning."
            )
            return
        
        # Initialize LangSmith client (implementation in later tasks)
        try:
            self._initialize_client()
            self.enabled = True
            logger.info(
                f"LangSmith tracing enabled "
                f"(project={config.project_name}, endpoint={config.endpoint})"
            )
        except Exception as e:
            logger.error(
                f"Failed to initialize LangSmith client: {e}. "
                "Tracing will be disabled for this session.",
                exc_info=True
            )
    
    @property
    def is_enabled(self) -> bool:
        """Check if tracing is currently enabled.
        
        Returns:
            True if tracing is enabled and initialized successfully, False otherwise
        """
        return self.enabled
    
    def _initialize_client(self) -> None:
        """Initialize the LangSmith client.
        
        This method will be implemented in a later task to:
        - Import LangSmith SDK (only when tracing is enabled)
        - Create and configure Client instance
        - Validate connection to LangSmith API
        
        Raises:
            Exception: If client initialization fails
        """
        # Import LangSmith SDK (only when needed)
        try:
            from langsmith import Client
            
            self.client = Client(
                api_key=self.config.api_key,
                api_url=self.config.endpoint
            )
            logger.debug("LangSmith Client initialized successfully")
        except ImportError as e:
            raise Exception(f"LangSmith SDK not installed: {e}")
        except Exception as e:
            raise Exception(f"Failed to create LangSmith Client: {e}")
    
    def attach_langchain_callback(self, llm):
        """Attach LangSmith callback to LangChain LLM for automatic metrics capture.
        
        When attached, the callback automatically captures:
        - Prompts and completions
        - Token usage (input/output/total)
        - Model name and parameters
        - Latency measurements
        - Error messages and stack traces
        
        This uses the LangChain → LangSmith integration, which means:
        - NO manual metric extraction needed
        - Metrics come directly from LLM provider responses
        - NO fabricated or estimated data
        
        Args:
            llm: LangChain LLM instance (e.g., ChatOpenAI, ChatAnthropic)
            
        Returns:
            The same LLM instance (with callback attached if successful)
            
        Side Effects:
            - Appends LangSmith callback to llm.callbacks list
            - Logs warning if callback attachment fails
        """
        if not self.enabled:
            return llm
        try:
            from langchain_core.callbacks.manager import LangChainTracer
            tracer = LangChainTracer(project_name=self.config.project_name, client=self.client)
            if not getattr(llm, "callbacks", None):
                llm.callbacks = []
            llm.callbacks.append(tracer)
        except Exception as e:
            logger.warning(f"Failed to attach LangChain tracer callback: {e}")
        return llm
    
    def extract_metadata(self, state) -> dict[str, Any]:
        """Extract correlation IDs and metadata from workflow state.
        
        Extracts all available metadata for trace enrichment:
        - Execution identifiers (execution_id, project_id, requirement_id)
        - Scenario and test case correlation (scenario_id, testcase_id)
        - Counts and status (scenario_count, approved_scenario_count, etc.)
        - LLM configuration (model_name, llm_provider)
        
        Args:
            state: WorkflowState instance containing execution context
            
        Returns:
            Dictionary of metadata key-value pairs
            
        Note:
            Only includes keys for available data. Missing data is omitted,
            not filled with placeholder values.
        """
        metadata = {}
        
        # Extract requirement information
        if state.requirement:
            metadata["requirement_id"] = str(state.requirement.id)
            metadata["requirement_title"] = state.requirement.title
            
            # Note: The Requirement model doesn't have a project_id field in the current schema
            # This would need to be added if project correlation is required
            # For now, we omit project_id as it's not directly available in state
        
        # Extract execution_id from execution results
        if state.execution_results:
            # Get execution_id from the first result (all results share the same execution context)
            metadata["execution_id"] = str(state.execution_results[0].id)
        
        # Extract scenario information
        if state.generated_scenarios:
            metadata["scenario_count"] = len(state.generated_scenarios)
            
            # Extract approved scenario count
            if state.selected_scenario_ids:
                metadata["approved_scenario_count"] = len(state.selected_scenario_ids)
        
        # Extract test case information
        if state.generated_test_cases:
            metadata["testcase_count"] = len(state.generated_test_cases)
            
            # Extract approved test case count
            # Approved test cases are those with APPROVED status or in human_approved list
            from backend.models.test_case import EvaluationStatus
            approved_count = sum(
                1 for tc in state.generated_test_cases
                if tc.evaluation_status == EvaluationStatus.APPROVED or tc.id in state.human_approved_test_case_ids
            )
            metadata["approved_testcase_count"] = approved_count
        
        # Extract LLM configuration
        llm_config = get_settings().llm
        metadata["model_name"] = llm_config.model
        metadata["llm_provider"] = llm_config.provider
        
        return metadata
    
    @contextmanager
    def create_trace_context(self, name: str, metadata: dict):
        """Create a trace context for manual tracing.
        
        This context manager is used by:
        - BaseAgent.run() template method
        - run_workflow() manual wrapper
        
        The context manager:
        - Creates a trace with the given name and metadata
        - Yields a context object with trace_id and exception recording
        - Automatically ends the trace on exit
        - Records exceptions if they occur
        - Operates as no-op when tracing is disabled
        
        Args:
            name: Trace name (e.g., "Scenario Agent", "run_workflow")
            metadata: Dictionary of metadata to attach to the trace
            
        Yields:
            TraceContext object with:
                - trace_id: Current trace ID (or None if disabled)
                - record_exception(e): Method to record exceptions
                
        Example:
            with tracer.create_trace_context("agent_name", metadata) as ctx:
                try:
                    result = perform_work()
                    return result
                except Exception as e:
                    ctx.record_exception(e)
                    raise
        """
        # Return no-op context when disabled
        if not self.enabled:
            yield _NoOpTraceContext()
            return
        
        # Import LangSmith SDK components (only when enabled)
        try:
            from langsmith.run_trees import RunTree
        except ImportError:
            logger.warning(
                "LangSmith SDK not available. "
                "Tracing context will operate in no-op mode."
            )
            yield _NoOpTraceContext()
            return
        
        # Create a run tree for this trace
        run_tree = None
        trace_id = None
        
        try:
            # Create a new run tree (root trace)
            run_tree = RunTree(
                name=name,
                run_type="chain",
                project_name=self.config.project_name,
                metadata=metadata,
                client=self.client
            )
            
            # Post the run to LangSmith (creates the trace)
            run_tree.post()
            
            trace_id = str(run_tree.id) if run_tree.id else None
            logger.debug(f"Created trace context: {name} (trace_id={trace_id})")
            
            # Yield context with trace_id and exception recording capability
            yield _TraceContext(trace_id=trace_id, run_tree=run_tree)
            
            # On successful exit, end the trace
            run_tree.end()
            run_tree.patch()
                
        except Exception as e:
            # Tracing error - log but don't propagate
            # Workflow should continue even if tracing fails
            logger.error(
                f"Error in trace context for '{name}': {e}",
                exc_info=True
            )
            
            # Try to end the run tree if it was created
            if run_tree:
                try:
                    run_tree.end(error=str(e))
                    run_tree.patch()
                except Exception:
                    pass  # Ignore errors during cleanup
            
            # Yield no-op context so caller can continue
            yield _NoOpTraceContext()
    
    def persist_trace(
        self,
        trace_id: str,
        workflow_name: str,
        metadata: dict,
        status: str,
        duration_ms: int | None = None
    ) -> None:
        """Persist trace metadata (no-op)."""
        pass


class _TraceContext:
    """Trace context returned by create_trace_context().
    
    Provides access to the current trace and exception recording.
    """
    
    def __init__(self, trace_id: str | None, run_tree=None):
        """Initialize trace context.
        
        Args:
            trace_id: LangSmith trace ID
            run_tree: LangSmith run tree object (for exception recording)
        """
        self.trace_id = trace_id
        self._run_tree = run_tree
    
    def record_exception(self, exception: Exception) -> None:
        """Record an exception in the current trace.
        
        Args:
            exception: Exception to record
            
        Side Effects:
            - Updates trace in LangSmith with error information
            - Logs error if recording fails
        """
        if not self._run_tree:
            return
        
        try:
            # Record the exception in the run tree
            self._run_tree.end(error=str(exception))
            # Patch the run tree to update LangSmith
            self._run_tree.patch()
            logger.debug(f"Recorded exception in trace {self.trace_id}: {exception}")
        except Exception as e:
            logger.error(f"Failed to record exception in trace: {e}", exc_info=True)


class _NoOpTraceContext:
    """No-op trace context when tracing is disabled.
    
    Provides the same interface as _TraceContext but does nothing.
    This eliminates the need for tracing checks at call sites.
    """
    
    trace_id = None
    
    def record_exception(self, exception: Exception) -> None:
        """No-op exception recording.
        
        Args:
            exception: Exception (ignored)
        """
        pass


# Singleton instance
_tracer: LangSmithTracer | None = None


def get_tracer() -> LangSmithTracer:
    """Get or create singleton tracer instance.
    
    This is the primary entry point for accessing LangSmith tracing.
    The tracer is initialized on first call and reused thereafter.
    
    Returns:
        Singleton LangSmithTracer instance
        
    Example:
        from backend.services.langsmith_tracer import get_tracer
        
        tracer = get_tracer()
        if tracer.enabled:
            with tracer.create_trace_context("my_operation", metadata) as ctx:
                result = do_work()
    """
    global _tracer
    if _tracer is None:
        settings = get_settings()
        _tracer = LangSmithTracer(settings.langsmith)
    return _tracer
