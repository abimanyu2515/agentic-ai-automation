"""
KnowledgeRetrievalAgent

Purpose:
    Searches the project's own knowledge base before an LLM backed agent runs,
    grades what comes back, and rewrites the query when the results are weak
    (spec 0006, AC-17 to AC-20).

Workflow Position:
    One node per stage, each with a fixed edge to its consumer:
        retrieval_requirements -> requirement_analyst_agent
        retrieval_scenarios    -> scenario_agent
        retrieval_test_cases   -> test_case_agent
        retrieval_analysis     -> execution_analysis_agent (defect_management_agent reuses it)

Inputs:
    - state.project_id, state.requirement
    - state.execution_results and state.generated_test_cases (analysis stage)

Outputs (owned only by this agent):
    - retrieval_status ("ok", "empty", "skipped"), retrieval_query,
      retrieval_attempts, retrieval_sources, retrieved_context

Never fails the workflow: every problem ends in retrieval_status "skipped"
with empty context and a logged reason (AC-20).

Tracing (AC-24): every search, grade, and rewrite records a LangSmith span
when tracing is on. Each span carries stage, attempt, query, source_types,
returned_source_refs, and relevant_count:
    - search:  the refs the search returned; relevant_count is the running
               total of relevant chunks before this search
    - grade:   the refs the grader kept; relevant_count is how many it kept
    - rewrite: the refs kept so far; relevant_count is the total that was too
               low and triggered the rewrite
With LangSmith off or not installed, nothing is sent and nothing fails.
"""

from __future__ import annotations

import logging
from uuid import UUID

from pydantic import BaseModel, Field

from backend.agents.base import BaseAgent
from backend.config.settings import get_settings
from backend.models.state import WorkflowState
from backend.utils.prompts import load_prompt

try:
    from langsmith import traceable
    from langsmith.run_helpers import get_current_run_tree
except ImportError:
    # Same graceful fallback graph/workflow.py uses when langsmith is not installed.
    def traceable(*args, **kwargs):
        if len(args) == 1 and callable(args[0]):
            return args[0]
        return lambda f: f

    def get_current_run_tree():
        return None

logger = logging.getLogger(__name__)

AGENT_NAME = "knowledge_retrieval_agent"

# AC-18: the source types each stage searches.
STAGE_SOURCES: dict[str, tuple[str, ...]] = {
    "requirements": ("document", "jira_story"),
    "scenarios": ("document", "jira_story"),
    "test_cases": ("document", "jira_story", "test_case", "locator"),
    "analysis": ("execution_failure", "defect", "test_case", "locator"),
}

# The graph node name for each stage, and the agent each one feeds (spec 0006, Graph wiring).
STAGE_NODES: dict[str, str] = {
    "requirements": "retrieval_requirements",
    "scenarios": "retrieval_scenarios",
    "test_cases": "retrieval_test_cases",
    "analysis": "retrieval_analysis",
}

MAX_ANALYSIS_FAILURES = 5
# Keeps a whole uploaded requirement from becoming one huge search request.
MAX_QUERY_CHARS = 2000
MAX_REWRITE_CHARS = 500
FAILED_STATUSES = {"failed", "error"}


class _GradeResponse(BaseModel):
    relevant: list[str] = Field(default_factory=list)


def _span_inputs(inputs: dict) -> dict:
    """Keep the agent instance and the chunk bodies out of the span inputs."""
    return {key: value for key, value in inputs.items() if key not in ("self", "items", "project")}


def _span(name: str, run_type: str):
    """A traceable decorator for one retrieval step. Older langsmith releases
    have no process_inputs; tracing must never break the stage, so fall back."""
    try:
        return traceable(name=name, run_type=run_type, process_inputs=_span_inputs)
    except TypeError:
        return traceable(name=name, run_type=run_type)


def _annotate(**metadata) -> None:
    """Attach AC-24 metadata to the current span. A no op when tracing is off."""
    try:
        run_tree = get_current_run_tree()
        if run_tree is not None:
            run_tree.add_metadata(metadata)
    except Exception as exc:  # tracing must never fail retrieval
        logger.debug("Could not attach retrieval span metadata: %s", exc)


def _refs(items: list[dict]) -> list[str]:
    return [f"{item.get('source_type')}:{item.get('source_ref')}" for item in items]


class _Skip(Exception):
    """Ends the stage with retrieval_status "skipped" and the given reason."""


class KnowledgeRetrievalAgent(BaseAgent):
    name = "Knowledge Retrieval Agent"

    def __init__(
        self,
        stage: str,
        knowledge_service=None,
        llm_service=None,
        repository=None,
        settings=None,
        share_with: "KnowledgeRetrievalAgent | None" = None,
    ):
        if stage not in STAGE_SOURCES:
            raise ValueError(f"Unknown retrieval stage {stage!r}; expected one of {tuple(STAGE_SOURCES)}")
        self.stage = stage
        self._knowledge_service = knowledge_service
        self._llm_service = llm_service
        self._repository = repository
        self._settings = settings
        # The graph's four stage agents share one knowledge service and one LLM service.
        self._share_with = share_with

    # Built on first use so building the graph never needs RAG or the retrieval role.
    @property
    def settings(self):
        return self._settings or get_settings()

    @property
    def repository(self):
        if self._repository is None:
            from backend.repository.project_repository import get_project_repository

            return get_project_repository()
        return self._repository

    @property
    def knowledge_service(self):
        if self._knowledge_service is None and self._share_with is not None:
            return self._share_with.knowledge_service
        if self._knowledge_service is None:
            from backend.services.knowledge_service import KnowledgeService

            self._knowledge_service = KnowledgeService()
        return self._knowledge_service

    @property
    def llm_service(self):
        if self._llm_service is None and self._share_with is not None:
            return self._share_with.llm_service
        if self._llm_service is None:
            from backend.services.llm import LLMService

            self._llm_service = LLMService(role="retrieval", agent=AGENT_NAME)
        return self._llm_service

    # ------------------------------------------------------------------ run

    def run(self, state: WorkflowState) -> WorkflowState:
        sources = list(STAGE_SOURCES[self.stage])
        state.add_log(f"{self.name} started (stage {self.stage})")
        state.retrieval_sources = sources
        state.retrieval_attempts = 0
        state.retrieval_query = None
        state.retrieved_context = []
        try:
            context, query, attempts = self._retrieve(state, sources)
        except _Skip as reason:
            return self._skip(state, str(reason))
        except Exception as exc:  # AC-20: never fail the workflow
            logger.warning("Retrieval stage %s failed: %s", self.stage, exc, exc_info=True)
            return self._skip(state, f"retrieval error: {type(exc).__name__}: {exc}")

        state.retrieval_query = query
        state.retrieval_attempts = attempts
        state.retrieved_context = context
        state.retrieval_status = "ok" if context else "empty"
        state.add_log(
            f"{self.name} finished (stage {self.stage}): {state.retrieval_status}, "
            f"{len(context)} chunks after {attempts} attempt(s)"
        )
        logger.info(
            "Retrieval stage %s project=%s status=%s chunks=%d attempts=%d",
            self.stage, state.project_id, state.retrieval_status, len(context), attempts,
        )
        return state

    def _skip(self, state: WorkflowState, reason: str) -> WorkflowState:
        state.retrieval_status = "skipped"
        state.retrieved_context = []
        state.add_log(f"{self.name} skipped (stage {self.stage}): {reason}")
        logger.info("Retrieval stage %s skipped for project %s: %s", self.stage, state.project_id, reason)
        return state

    # ------------------------------------------------------------- the loop

    def _retrieve(self, state: WorkflowState, sources: list[str]) -> tuple[list[dict], str, int]:
        from backend.services.knowledge_service import KnowledgeNotReady
        from backend.services.llm import LLMRateLimitTimeout, LLMServiceError
        from backend.services.retrieval import RetrieverError, rag_enabled

        settings = self.settings
        if not rag_enabled(settings):
            raise _Skip("RAG is disabled")
        if state.project_id is None:
            raise _Skip("state has no project_id")
        query = self.build_query(state)
        if not query:
            raise _Skip("the query is empty")
        project = self.repository.get_project(state.project_id)
        if project is None:
            raise _Skip("project not found")
        if not getattr(project, "rag_dataset_id", None):
            raise _Skip("the project has no knowledge dataset yet")

        rag = settings.rag
        top_k = rag.top_k
        max_attempts = rag.max_retrieval_attempts
        min_relevant = rag.min_relevant_chunks
        collected: dict[str, dict] = {}
        attempts = 0

        while True:
            attempts += 1
            try:
                items = self._search(
                    project, query, top_k=top_k, sources=sources, attempt=attempts, relevant_so_far=len(collected)
                )
            except KnowledgeNotReady as exc:
                raise _Skip(str(exc)) from exc
            except RetrieverError as exc:
                raise _Skip(f"knowledge search failed: {exc}") from exc

            fresh = [item for item in items if item["chunk_id"] not in collected]
            if fresh:
                try:
                    kept = self._grade(query, fresh, attempt=attempts, sources=sources)
                except LLMRateLimitTimeout as exc:
                    raise _Skip(f"the retrieval role is busy: {exc}") from exc
                except (LLMServiceError, ValueError) as exc:
                    # AC-20: unparseable grades keep the best few, marked ungraded.
                    logger.warning("Retrieval stage %s: grading failed: %s", self.stage, exc)
                    state.add_log(f"{self.name}: grading failed, keeping the top {min_relevant} chunks by score")
                    best = sorted(fresh, key=lambda item: item["score"], reverse=True)[:min_relevant]
                    kept = [{**item, "graded": False} for item in best]
                for item in kept:
                    collected[item["chunk_id"]] = item

            if len(collected) >= min_relevant or attempts >= max_attempts:
                break
            try:
                rewritten = self._rewrite(
                    query, attempt=attempts, sources=sources, kept_refs=_refs(list(collected.values()))
                )
            except LLMRateLimitTimeout as exc:
                raise _Skip(f"the retrieval role is busy: {exc}") from exc
            except LLMServiceError as exc:
                logger.warning("Retrieval stage %s: query rewrite failed: %s", self.stage, exc)
                state.add_log(f"{self.name}: query rewrite failed, keeping what was found")
                break
            if not rewritten or rewritten == query:
                break
            state.add_log(f"{self.name}: rewrote the query after {len(collected)} relevant chunk(s)")
            query = rewritten

        context = sorted(collected.values(), key=lambda item: item["score"], reverse=True)[:top_k]
        return context, query, attempts

    @_span("knowledge_retrieval.search", "retriever")
    def _search(self, project, query: str, top_k: int, sources: list[str], attempt: int = 1, relevant_so_far: int = 0) -> list[dict]:
        _annotate(stage=self.stage, attempt=attempt, query=query, source_types=list(sources))
        items = self.knowledge_service.search(project, query, top_k=top_k, source_types=sources)
        _annotate(returned_source_refs=_refs(items), relevant_count=relevant_so_far)
        return items

    @_span("knowledge_retrieval.grade", "llm")
    def _grade(self, query: str, items: list[dict], attempt: int = 1, sources: list[str] | None = None) -> list[dict]:
        _annotate(
            stage=self.stage, attempt=attempt, query=query,
            source_types=list(sources or STAGE_SOURCES[self.stage]), graded_count=len(items),
        )
        kept = self._grade_chunks(query, items)
        _annotate(returned_source_refs=_refs(kept), relevant_count=len(kept))
        return kept

    @_span("knowledge_retrieval.rewrite", "llm")
    def _rewrite(self, query: str, attempt: int = 1, sources: list[str] | None = None, kept_refs: list[str] | None = None) -> str:
        kept_refs = list(kept_refs or [])
        _annotate(
            stage=self.stage, attempt=attempt, query=query,
            source_types=list(sources or STAGE_SOURCES[self.stage]),
            returned_source_refs=kept_refs, relevant_count=len(kept_refs),
        )
        rewritten = self._rewrite_query(query)
        _annotate(rewritten_query=rewritten)
        return rewritten

    def _grade_chunks(self, query: str, items: list[dict]) -> list[dict]:
        """One batched call. Chunks get short ids so nothing a chunk says can
        name another chunk, and unknown ids in the answer are ignored."""
        aliases = {f"c{index}": item for index, item in enumerate(items, start=1)}
        blocks = "\n".join(
            f'<chunk id="{alias}" source="{_attr(item["source_type"])}:{_attr(item["source_ref"])}" '
            f'title="{_attr(item["title"])}">\n{_data(item["text"])}\n</chunk>'
            for alias, item in aliases.items()
        )
        prompt = load_prompt(
            "agents/knowledge_retrieval/grade.md",
            stage=self.stage,
            query=_data(query),
            chunks=blocks,
        )
        response = self.llm_service.structured_generate(
            user=prompt,
            response_model=_GradeResponse,
            max_retries=1,
            timeout=60,
        )
        relevant = {str(alias).strip() for alias in response.relevant}
        return [{**item, "graded": True} for alias, item in aliases.items() if alias in relevant]

    def _rewrite_query(self, query: str) -> str:
        prompt = load_prompt("agents/knowledge_retrieval/rewrite.md", stage=self.stage, query=_data(query))
        text = self.llm_service.generate(
            system="You rewrite search queries. Answer with plain text only.",
            user=prompt,
            timeout=60,
        )
        line = next((part.strip() for part in (text or "").splitlines() if part.strip()), "")
        return line.strip("\"'` ")[:MAX_REWRITE_CHARS]

    # ------------------------------------------------------------ queries

    def build_query(self, state: WorkflowState) -> str:
        """AC-18: requirement text for every stage but analysis, which uses the failures."""
        if self.stage == "analysis":
            return self._analysis_query(state)
        requirement = state.requirement
        if requirement is None:
            return ""
        parts = [requirement.title, requirement.description, *requirement.acceptance_criteria]
        return _join(parts)

    def _analysis_query(self, state: WorkflowState) -> str:
        failures = [
            result for result in state.execution_results
            if getattr(result.status, "value", result.status) in FAILED_STATUSES
        ][:MAX_ANALYSIS_FAILURES]
        if not failures:
            return ""
        titles = {case.id: case.title for case in state.generated_test_cases}
        parts = [state.requirement.title] if state.requirement else []
        for result in failures:
            title = titles.get(result.test_case_id) or self._test_case_title(result.test_case_id)
            parts.append(_join([title, result.error_message or ""], sep=": "))
        return _join(parts)

    def _test_case_title(self, test_case_id: UUID) -> str:
        try:
            case = self.repository.get_test_case(test_case_id)
        except Exception as exc:
            logger.warning("Retrieval: could not read test case %s: %s", test_case_id, exc)
            return ""
        return getattr(case, "title", "") or ""


def _join(parts, sep: str = "\n") -> str:
    text = sep.join(part.strip() for part in parts if part and part.strip())
    return text[:MAX_QUERY_CHARS].strip()


def _data(text: str) -> str:
    """Keep chunk text from closing its own delimiter."""
    return (text or "").replace("</chunk", "</ chunk").replace("</query", "</ query")


def _attr(value) -> str:
    return str(value or "").replace('"', "'").replace("\n", " ")[:200]
