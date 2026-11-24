"""
Log analysis service: the log analyser behind ExecutionAnalysisAgent.

One place triages a finished run, so the LangGraph single-run path, the batch
path and the webhook path all get the same analysis:

1. Collect bounded, redacted text evidence from the run's artifacts
   (LogEvidenceCollector).
2. Summarize earlier runs of the same test case (run_history).
3. Ask the `analysis` LLM for a structured triage, then apply deterministic
   rules: the category must be one of the three known values, and a flaky
   history downgrades a low-confidence "Product Bug".
4. Write the triage fields onto the ExecutionResult. The caller saves it.

`analyze` never raises. An LLM or evidence failure falls back to
"Test Environment Issue" with suggest_retry=True, as the agent did before.
"""

from __future__ import annotations

import logging
from uuid import UUID

from pydantic import BaseModel, Field

from backend.agents.knowledge_context import with_project_knowledge
from backend.models.execution_result import ExecutionResult, ExecutionStatus
from backend.services import run_history
from backend.services.log_evidence_collector import LogEvidence, LogEvidenceCollector
from backend.utils.prompts import load_prompt

logger = logging.getLogger(__name__)

PRODUCT_BUG = "Product Bug"
ENVIRONMENT_ISSUE = "Test Environment Issue"
FLAKY_ISSUE = "Flaky Test / Automation Issue"
FAILURE_CATEGORIES = (PRODUCT_BUG, ENVIRONMENT_ISSUE, FLAKY_ISSUE)

# A "Product Bug" below this confidence on a flaky history is downgraded.
FLAKY_OVERRIDE_CONFIDENCE = 0.7
MAX_EVIDENCE_ITEMS = 8


class LogAnalysisResponse(BaseModel):
    """Structured triage the analysis LLM returns for one failed run."""

    failure_category: str = Field(description="One of: Product Bug, Test Environment Issue, Flaky Test / Automation Issue")
    root_cause_summary: str = Field(description="One to two sentence plain-language summary of the root cause")
    failing_step: str = Field(default="", description="The test step or action where the run failed")
    trace_analysis: str = Field(description="The specific error, file and line or step taken from the logs")
    evidence: list[str] = Field(default_factory=list, description="Exact lines quoted from the input that support the category")
    screenshot_findings: str = Field(default="", description="What the page snapshot shows at failure, or 'No page snapshot available'")
    frontend_error_detected: bool = Field(default=False, description="True if console errors, failed network calls or broken UI were found")
    frontend_error_details: str = Field(default="", description="The frontend error found, or 'None identified'")
    confidence: float = Field(default=0.5, description="Confidence in failure_category, from 0.0 to 1.0")
    suggest_retry: bool = Field(description="True if the test should be retried")
    retest_pending_candidate: bool = Field(description="True if this failure is a bug candidate awaiting retest")


def _normalize_category(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    for category in FAILURE_CATEGORIES:
        if text == category.lower():
            return category
    if "product" in text or text == "bug":
        return PRODUCT_BUG
    if "environment" in text or "infra" in text:
        return ENVIRONMENT_ISSUE
    if "flaky" in text or "automation" in text:
        return FLAKY_ISSUE
    return None


def _block(items: list[str] | str, empty: str) -> str:
    """Evidence goes inside XML-like tags; keep it from closing them early."""
    text = "\n".join(f"- {item}" for item in items) if isinstance(items, list) else items
    text = (text or "").strip() or empty
    return text.replace("</", "< /")


class LogAnalysisService:
    """Analyse failed runs. Inject `llm_service`, `repo` and `collector` in tests."""

    def __init__(self, llm_service=None, repo=None, collector: LogEvidenceCollector | None = None, vault=None):
        self._llm_service = llm_service
        self._repo = repo
        self.collector = collector or LogEvidenceCollector()
        self._vault = vault

    @property
    def llm_service(self):
        if self._llm_service is None:
            from backend.services.llm import LLMService

            self._llm_service = LLMService(role="analysis", agent="execution_analysis_agent")
        return self._llm_service

    @property
    def repo(self):
        if self._repo is None:
            from backend.repository.project_repository import get_project_repository

            self._repo = get_project_repository()
        return self._repo

    # ------------------------------------------------------------------

    def analyze(
        self,
        project_id: UUID | None,
        result: ExecutionResult,
        test_case=None,
        retrieved_context: list[dict] | None = None,
    ) -> ExecutionResult:
        """Fill the triage fields of `result` in place and return it."""
        if getattr(result.status, "value", result.status) in (ExecutionStatus.PASSED.value, ExecutionStatus.SKIPPED.value):
            self._mark_not_failed(result)
            return result

        evidence = self.collector.collect(result, self._secrets(project_id, test_case))
        history = (
            run_history.summarize(self.repo, project_id, result) if project_id is not None else run_history.RunHistory()
        )
        result.flaky_score = history.flaky_score
        result.run_history_summary = history.model_dump()
        result.failing_step = evidence.failing_action or None

        try:
            prompt = with_project_knowledge(self._prompt(result, test_case, evidence, history), retrieved_context)
            analysis = self.llm_service.structured_generate(user=prompt, response_model=LogAnalysisResponse)
            self._apply(result, analysis, evidence, history)
        except Exception as exc:
            logger.warning("Log analysis LLM call failed: execution_id=%s error=%s", result.id, exc)
            self._apply_fallback(result, evidence, exc)
        return result

    # ------------------------------------------------------------------

    def _secrets(self, project_id: UUID | None, test_case) -> list:
        secrets: list = []
        if project_id is not None:
            try:
                if self._vault is None:
                    from backend.services.vault_service import VaultService

                    self._vault = VaultService()
                secrets.extend(self._vault.get_credentials(str(project_id)))
            except Exception:
                logger.warning("Log analysis could not load vault credentials: project_id=%s", project_id)
        try:
            from backend.services.object_repository_harvest import values_from_test_data

            secrets.extend(values_from_test_data(test_case))
        except Exception:
            pass
        return secrets

    @staticmethod
    def _prompt(result: ExecutionResult, test_case, evidence: LogEvidence, history: run_history.RunHistory) -> str:
        steps = "\n".join(f"- {s}" for s in (getattr(test_case, "steps", None) or [])) or "N/A"
        error_details = evidence.error_details
        if evidence.notes:
            error_details = f"{error_details}\n\nCollector notes: {'; '.join(evidence.notes)}"
        actions = list(evidence.actions)
        if evidence.failing_action:
            actions.append(f"FAILED AT: {evidence.failing_action}")
        return load_prompt(
            "execution_analysis_prompt.txt",
            test_title=_block(getattr(test_case, "title", None) or "Unknown Test Case", "Unknown Test Case"),
            steps=_block(steps, "N/A"),
            expected_result=_block(getattr(test_case, "expected_result", None) or "N/A", "N/A"),
            run_status=getattr(result.status, "value", str(result.status)),
            error_details=_block(error_details, result.error_message or "No error details captured."),
            actions=_block(actions, "No step trace available."),
            console_errors=_block(evidence.console_errors, "None captured."),
            network_failures=_block(evidence.network_failures, "None captured."),
            timeline_errors=_block(evidence.timeline_errors, "None captured."),
            page_snapshot=_block(evidence.page_snapshot, "No page snapshot available."),
            run_history=_block(history.describe(), "No earlier runs of this test case."),
        )

    @staticmethod
    def _mark_not_failed(result: ExecutionResult) -> None:
        result.failure_category = "None"
        result.root_cause_summary = "Execution passed successfully."
        result.trace_analysis = "N/A"
        result.screenshot_findings = "N/A"
        result.suggest_retry = False
        result.retest_pending_candidate = False

    @staticmethod
    def _apply(result: ExecutionResult, analysis: LogAnalysisResponse, evidence: LogEvidence, history) -> None:
        confidence = max(0.0, min(1.0, float(analysis.confidence)))
        category = _normalize_category(analysis.failure_category)
        summary = analysis.root_cause_summary.strip()
        if category is None:
            category = ENVIRONMENT_ISSUE
            summary = f"{summary} (Unrecognized category '{analysis.failure_category}' was set to {ENVIRONMENT_ISSUE}.)".strip()
            confidence = min(confidence, 0.3)

        suggest_retry = bool(analysis.suggest_retry)
        bug_candidate = bool(analysis.retest_pending_candidate)
        if (
            category == PRODUCT_BUG
            and history.flaky_score >= run_history.FLAKY_SCORE_THRESHOLD
            and confidence < FLAKY_OVERRIDE_CONFIDENCE
        ):
            category = FLAKY_ISSUE
            suggest_retry = True
            bug_candidate = False
            summary = (
                f"{summary} Reclassified from Product Bug: this test's recent history is flaky "
                f"(flaky score {history.flaky_score:.2f}) and the analysis confidence was {confidence:.2f}."
            ).strip()
        if category != PRODUCT_BUG:
            bug_candidate = False

        result.failure_category = category
        result.root_cause_summary = summary
        result.trace_analysis = analysis.trace_analysis
        result.screenshot_findings = analysis.screenshot_findings or None
        result.suggest_retry = suggest_retry
        result.retest_pending_candidate = bug_candidate
        result.failing_step = (analysis.failing_step or "").strip() or evidence.failing_action or None
        result.evidence = [line.strip() for line in analysis.evidence if line and line.strip()][:MAX_EVIDENCE_ITEMS]
        result.confidence = round(confidence, 2)
        result.frontend_error_detected = bool(analysis.frontend_error_detected)
        result.frontend_error_details = analysis.frontend_error_details or None

    @staticmethod
    def _apply_fallback(result: ExecutionResult, evidence: LogEvidence, exc: Exception) -> None:
        failure_msg = result.error_message or "Unknown failure"
        result.failure_category = ENVIRONMENT_ISSUE
        result.root_cause_summary = f"Analysis agent failed: {exc}. Execution error: {failure_msg}"
        result.trace_analysis = evidence.error_details or failure_msg
        result.screenshot_findings = "Unknown"
        result.suggest_retry = True
        result.retest_pending_candidate = False
        result.confidence = 0.0
        result.evidence = [line for line in (evidence.console_errors + evidence.network_failures)][:MAX_EVIDENCE_ITEMS]
        result.frontend_error_detected = bool(evidence.console_errors or evidence.network_failures)
        result.frontend_error_details = None
