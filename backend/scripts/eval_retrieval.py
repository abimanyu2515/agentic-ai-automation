"""Measure live retrieval quality for one project (spec 0006, AC-25).

Runs labelled cases against the project's real knowledge base and prints
recall@k and MRR per case and overall.

Run with:
    python -m backend.scripts.eval_retrieval --project <project_id> --cases <file.json> [--k 5] [--json]

The cases file is a JSON list:
    [
      {"query": "How does password reset work?",
       "expected_source_refs": ["document:<document_id>", "jira_story:PROJ-12"],
       "source_types": ["document", "jira_story"]}
    ]
`source_types` is optional. An expected ref is `<source_ref>` or `<source_type>:<source_ref>`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

from backend.repository.project_repository import get_project_repository
from backend.services.knowledge_service import SEARCH_TOP_K_MAX
from backend.services.retrieval import get_retriever
from backend.services.retrieval_eval_service import RetrievalEvalService


def load_cases(path: Path) -> list:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("the cases file must hold a JSON list")
    return data


def format_report(report) -> str:
    lines = [f"Retrieval eval: {len(report.cases)} case(s), k={report.k}", ""]
    for index, case in enumerate(report.cases, start=1):
        rank = case.first_hit_rank if case.first_hit_rank else "none"
        lines.append(f"{index}. {case.query}")
        lines.append(f"   recall@{report.k}={case.recall_at_k:.2f}  reciprocal rank={case.reciprocal_rank:.2f}  first hit={rank}")
        missing = [ref for ref in case.expected_source_refs if ref not in case.found_source_refs]
        if missing:
            lines.append(f"   missing: {', '.join(missing)}")
    lines += ["", f"Overall recall@{report.k}: {report.recall_at_k:.4f}", f"Overall MRR: {report.mrr:.4f}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None, out=print) -> int:
    parser = argparse.ArgumentParser(description="Measure recall@k and MRR of a project's knowledge search.")
    parser.add_argument("--project", required=True, help="project ID")
    parser.add_argument("--cases", required=True, help="path to the JSON cases file")
    parser.add_argument("--k", type=int, default=5, help=f"how many results to score (1 to {SEARCH_TOP_K_MAX})")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = parser.parse_args(argv)

    try:
        project_id = UUID(args.project)
        cases = load_cases(Path(args.cases))
    except (ValueError, OSError) as exc:
        out(f"error: {exc}")
        return 2

    repo = get_project_repository()
    project = repo.get_project(project_id)
    if project is None:
        out(f"error: project {project_id} not found")
        return 2
    retriever = get_retriever()
    if not retriever.is_active():
        out("error: the knowledge base is disabled (RAG_ENABLED is false)")
        return 2

    try:
        report = RetrievalEvalService(repository=repo).evaluate(retriever, project, cases, args.k)
    except Exception as exc:
        out(f"error: {type(exc).__name__}: {exc}")
        return 1
    out(json.dumps(report.to_dict(), indent=2) if args.json else format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
