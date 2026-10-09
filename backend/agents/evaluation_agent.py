"""
EvaluationAgent

Purpose:
    Quality assurance layer for AI-generated test cases. Scores relevance and completeness,
    detects duplicates, and routes test cases to auto-approval, human review, or rejection.

Responsibilities:
    - Detect duplicate test cases using Jaccard similarity
    - Score test case relevance and completeness via LLM
    - Mark irrelevant test cases as REJECTED
    - Route non-duplicate, relevant test cases to NEEDS_REVIEW
    - Verify scenario coverage (all scenarios have at least one test case)
    - Log evaluation summary to workflow state

Workflow Position:
    TestCaseAgent
        ↓
    EvaluationAgent
        ↓
    HumanApprovalAgent (test case review)
        ↓
    PlaywrightAgent

Inputs:
    - state.requirement: Requirement context for relevance scoring
    - state.generated_test_cases: Test cases from TestCaseAgent
    - state.generated_scenarios: Scenarios for coverage verification

Outputs:
    - test_case.evaluation_status: APPROVED, NEEDS_REVIEW, or REJECTED
    - test_case.confidence: Average of relevance and completeness scores (0.0-1.0)
    - test_case.evaluation_reason: Human-readable explanation
    - state.logs: Coverage summary and rejection counts

Duplicate Detection:
    Uses Jaccard similarity on combined title + expected_result text.
    Title-only comparison cannot distinguish:
        - "Login with valid password" vs "Login with invalid password"
    Adding expected_result separates them cleanly:
        - Real duplicates: ~0.90 similarity
        - Negation pairs: ~0.16 similarity
    
    Calibrated against real production data, not guessed.
    Threshold: settings.evaluation.duplicate_similarity_threshold (default 0.85)

Evaluation Outcomes:
    - REJECTED: Duplicate or relevance < relevance_rejection_threshold (0.3)
    - NEEDS_REVIEW: Relevant but confidence < evaluation_threshold
    - APPROVED: Currently unused - all non-rejected cases need human review

LLM Correlation:
    Uses test_case_number (1-based index) instead of title echo-back.
    Validates LLM returned exactly {1, 2, ..., N} with no gaps or extras.

Historical Context:
    This agent was added after production issues with auto-approved irrelevant test cases.
    Provides critical quality gate before expensive Playwright script generation.
"""

from pydantic import BaseModel, Field, field_validator

from backend.agents.base import BaseAgent
from backend.config.settings import get_settings
from backend.models.state import WorkflowState
from backend.models.test_case import EvaluationStatus, TestCase
from backend.services.llm import LLMService, LLMServiceError
from backend.utils.prompts import load_prompt


class _TestCaseEvaluation(BaseModel):
    test_case_number: int = Field(ge=1)
    relevance: float = Field(ge=0.0, le=1.0)
    completeness: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)

    @field_validator("reason", mode="before")
    @classmethod
    def _default_when_blank(cls, value):
        # Confirmed in a real live run: models sometimes leave this blank
        # specifically when both scores are high and there's nothing
        # critical to flag - not model malfunction, just nothing to say.
        # A blank reason on ONE item doesn't cast any doubt on the actual
        # relevance/completeness numbers for THIS item or the other two
        # in the same batch, so it shouldn't cost the whole batch its
        # results the way a number correlation mismatch legitimately
        # should. Substitute rather than fail.
        if isinstance(value, str) and not value.strip():
            return "No specific concerns noted."
        return value


class _EvaluationBatch(BaseModel):
    evaluations: list[_TestCaseEvaluation] = Field(min_length=1)


def _jaccard_similarity(a: str, b: str) -> float:
    words_a, words_b = set(a.lower().split()), set(b.lower().split())
    if not words_a and not words_b:
        return 1.0
    return len(words_a & words_b) / len(words_a | words_b)


def _test_case_text(tc: TestCase) -> str:
    return f"{tc.title} {tc.expected_result}"


def _mark_duplicates(test_cases: list[TestCase], threshold: float) -> int:
    """Mutates evaluation_status/evaluation_reason in place for any test
    case that's a near-duplicate of an earlier one in the list. Keeps the
    first occurrence, rejects later ones. Returns how many were rejected."""
    rejected = 0
    for i in range(len(test_cases)):
        if test_cases[i].evaluation_status == EvaluationStatus.REJECTED:
            continue
        for j in range(i + 1, len(test_cases)):
            if test_cases[j].evaluation_status == EvaluationStatus.REJECTED:
                continue
            similarity = _jaccard_similarity(_test_case_text(test_cases[i]), _test_case_text(test_cases[j]))
            if similarity >= threshold:
                test_cases[j].evaluation_status = EvaluationStatus.REJECTED
                test_cases[j].evaluation_reason = f"Duplicate of '{test_cases[i].title}' (similarity {similarity:.2f})"
                rejected += 1
    return rejected


def _coverage_message(state: WorkflowState) -> str:
    covered_ids = {tc.scenario_id for tc in state.generated_test_cases}
    missing = [s for s in state.generated_scenarios if s.id not in covered_ids]
    if missing:
        names = ", ".join(s.scenario_name for s in missing)
        return f"coverage gap - no test case for: {names}"
    return f"coverage complete - all {len(state.generated_scenarios)} scenarios have at least one test case"


def _format_test_cases_block(test_cases: list[TestCase]) -> str:
    lines = []
    for i, tc in enumerate(test_cases, start=1):
        lines.append(
            f"{i}. Title: {tc.title}\n"
            f"   Preconditions: {tc.preconditions}\n"
            f"   Steps: {tc.steps}\n"
            f"   Expected result: {tc.expected_result}"
        )
    return "\n".join(lines)


class EvaluationAgent(BaseAgent):
    name = "Evaluation Agent"

    def __init__(self, llm_service: LLMService | None = None):
        self.llm_service = llm_service or LLMService()
        settings = get_settings()
        self.confidence_threshold = settings.workflow.evaluation_threshold
        self.duplicate_threshold = settings.evaluation.duplicate_similarity_threshold
        self.relevance_rejection_threshold = settings.evaluation.relevance_rejection_threshold

    def run(self, state: WorkflowState) -> WorkflowState:
        if state.requirement is None:
            raise ValueError(f"{self.name} requires state.requirement to be set")
        if not state.generated_test_cases:
            raise ValueError(f"{self.name} requires at least one generated test case")

        state.add_log(f"{self.name} started")
        state.add_log(f"{self.name}: {_coverage_message(state)}")

        duplicate_count = _mark_duplicates(state.generated_test_cases, self.duplicate_threshold)
        if duplicate_count:
            state.add_log(f"{self.name}: rejected {duplicate_count} duplicate test case(s)")

        candidates = [tc for tc in state.generated_test_cases if tc.evaluation_status != EvaluationStatus.REJECTED]
        if not candidates:
            state.add_log(f"{self.name} finished: 0 approved, 0 need review, {duplicate_count} rejected as duplicates")
            return state

        prompt = load_prompt(
            "evaluation_prompt.txt",
            title=state.requirement.title,
            description=state.requirement.description,
            test_cases=_format_test_cases_block(candidates),
        )

        batch = self.llm_service.structured_generate(
            user=prompt,
            response_model=_EvaluationBatch,
        )

        expected_numbers = set(range(1, len(candidates) + 1))
        valid_evaluations = [e for e in batch.evaluations if e.test_case_number in expected_numbers]
        returned_numbers = {e.test_case_number for e in valid_evaluations}
        if returned_numbers != expected_numbers:
            raise LLMServiceError(
                f"{self.name}: LLM returned evaluations for {sorted(returned_numbers)}, "
                f"expected exactly {sorted(expected_numbers)}"
            )

        review_count = 0
        irrelevant_count = 0
        for evaluation in valid_evaluations:
            test_case = candidates[evaluation.test_case_number - 1]
            confidence = (evaluation.relevance + evaluation.completeness) / 2
            test_case.confidence = confidence
            test_case.evaluation_reason = evaluation.reason

            if evaluation.relevance < self.relevance_rejection_threshold:
                test_case.evaluation_status = EvaluationStatus.REJECTED
                irrelevant_count += 1
            else:
                # EvaluationAgent never auto-approves. Non-duplicate, relevant test cases always need review.
                test_case.evaluation_status = EvaluationStatus.NEEDS_REVIEW
                review_count += 1

        state.add_log(
            f"{self.name} finished: {review_count} need review, "
            f"{duplicate_count + irrelevant_count} rejected ({duplicate_count} duplicates, {irrelevant_count} irrelevant)"
        )
        return state

