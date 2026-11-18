"""RetrievalEvalService: measures how well project search finds the right material (spec 0006, AC-25).

For each labelled case `{query, expected_source_refs, source_types?}` it runs
the same project search chat and the workflow use (`KnowledgeService.search`,
which maps chunks to `source_type`/`source_ref` for this project only) and scores:

- recall@k: the fraction of the case's expected refs found in the top k results
- reciprocal rank: 1 divided by the rank of the first expected ref in the
  top k, or 0 when none appears

The overall numbers are the means across cases (MRR is the mean reciprocal rank).
An expected ref may be written as `<source_ref>` or as `<source_type>:<source_ref>`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.services.knowledge_service import SEARCH_TOP_K_MAX, SOURCE_TYPES, KnowledgeService


@dataclass
class EvalCase:
    query: str
    expected_source_refs: list[str]
    source_types: list[str] | None = None

    @classmethod
    def from_dict(cls, data: dict, index: int = 0) -> "EvalCase":
        if not isinstance(data, dict):
            raise ValueError(f"case {index + 1}: expected an object")
        query = str(data.get("query") or "").strip()
        if not query:
            raise ValueError(f"case {index + 1}: query is required")
        expected = data.get("expected_source_refs")
        if not isinstance(expected, list) or not expected or not all(str(ref).strip() for ref in expected):
            raise ValueError(f"case {index + 1}: expected_source_refs must be a non empty list")
        source_types = data.get("source_types")
        if source_types is not None:
            if not isinstance(source_types, list):
                raise ValueError(f"case {index + 1}: source_types must be a list")
            unknown = set(source_types) - set(SOURCE_TYPES)
            if unknown:
                raise ValueError(f"case {index + 1}: unknown source_types: {', '.join(sorted(unknown))}")
        return cls(query=query, expected_source_refs=[str(ref).strip() for ref in expected], source_types=source_types)


@dataclass
class CaseResult:
    query: str
    expected_source_refs: list[str]
    retrieved_source_refs: list[str]
    found_source_refs: list[str]
    recall_at_k: float
    reciprocal_rank: float
    first_hit_rank: int | None = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class EvalReport:
    k: int
    recall_at_k: float
    mrr: float
    cases: list[CaseResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "k": self.k,
            "case_count": len(self.cases),
            "recall_at_k": self.recall_at_k,
            "mrr": self.mrr,
            "cases": [case.to_dict() for case in self.cases],
        }


def _matches(expected: str, item: dict) -> bool:
    ref = str(item.get("source_ref"))
    return expected == ref or expected == f"{item.get('source_type')}:{ref}"


def score_case(case: EvalCase, items: list[dict], k: int) -> CaseResult:
    """Pure scoring of one case against ranked search results."""
    top = items[:k]
    found = [ref for ref in case.expected_source_refs if any(_matches(ref, item) for item in top)]
    first_rank = next(
        (rank for rank, item in enumerate(top, start=1) if any(_matches(ref, item) for ref in case.expected_source_refs)),
        None,
    )
    expected_count = len(set(case.expected_source_refs))
    return CaseResult(
        query=case.query,
        expected_source_refs=list(case.expected_source_refs),
        retrieved_source_refs=[f"{item.get('source_type')}:{item.get('source_ref')}" for item in top],
        found_source_refs=found,
        recall_at_k=round(len(set(found)) / expected_count, 4) if expected_count else 0.0,
        reciprocal_rank=round(1.0 / first_rank, 4) if first_rank else 0.0,
        first_hit_rank=first_rank,
    )


class RetrievalEvalService:
    def __init__(self, repository=None):
        self._repository = repository

    def evaluate(self, retriever, project, cases, k: int) -> EvalReport:
        """recall@k and MRR, per case and overall. `cases` holds EvalCase objects
        or plain dicts. Search errors are raised: an eval that cannot search
        has no meaningful score."""
        k = int(k)
        if k < 1 or k > SEARCH_TOP_K_MAX:
            raise ValueError(f"k must be between 1 and {SEARCH_TOP_K_MAX}")
        parsed = [case if isinstance(case, EvalCase) else EvalCase.from_dict(case, i) for i, case in enumerate(cases or [])]
        if not parsed:
            raise ValueError("at least one case is required")

        search = KnowledgeService(repository=self._repository, retriever=retriever)
        results = [
            score_case(case, search.search(project, case.query, top_k=k, source_types=case.source_types), k)
            for case in parsed
        ]
        count = len(results)
        return EvalReport(
            k=k,
            recall_at_k=round(sum(r.recall_at_k for r in results) / count, 4),
            mrr=round(sum(r.reciprocal_rank for r in results) / count, 4),
            cases=results,
        )
