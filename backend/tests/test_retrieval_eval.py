"""Retrieval eval, slice 6 (spec 0006, AC-25).

RetrievalEvalService runs labelled cases through the real project search
(KnowledgeService over the InMemoryRetriever) and returns recall@k and MRR.
"""

import hashlib
import json
from pathlib import Path

import pytest

from backend.models.knowledge import KnowledgeEntry
from backend.scripts import eval_retrieval
from backend.services.retrieval_eval_service import EvalCase, RetrievalEvalService, score_case
from backend.tests.test_knowledge_base import kb, settings  # noqa: F401  (pytest fixtures)

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "retrieval_eval" / "corpus.json").read_text(encoding="utf-8"))


def _seed(kb):
    """A project whose dataset holds the fixture corpus, mapped to knowledge entries."""
    project = kb.project()
    dataset_id = kb.retriever.ensure_dataset(project)
    kb.repo.set_project_rag_dataset(project.id, dataset_id)
    project = kb.repo.get_project(project.id)
    for entry in FIXTURE["entries"]:
        result = kb.retriever.upload_text(project, entry["text"], f"{entry['source_type']}__{entry['source_ref']}.txt")
        kb.repo.save_knowledge_entry(
            KnowledgeEntry(
                project_id=project.id,
                source_type=entry["source_type"],
                source_ref=entry["source_ref"],
                title=entry["title"],
                remote_doc_id=result.remote_doc_id,
                content_hash=hashlib.sha256(entry["text"].encode()).hexdigest(),
            )
        )
    return project


def test_fixture_corpus_gives_the_expected_recall_and_mrr(kb):
    project = _seed(kb)
    report = RetrievalEvalService(repository=kb.repo).evaluate(kb.retriever, project, FIXTURE["cases"], k=3)

    per_case = [(case.recall_at_k, case.reciprocal_rank) for case in report.cases]
    assert per_case == [
        (1.0, 1.0),  # both reset refs in the top 3, first at rank 1
        (1.0, 1.0),  # the defect ranks first
        (1.0, 1.0),  # source_types keeps only stories
        (0.5, 1.0),  # the locator (bare ref form) is found, SHOP-1 is not
        (1.0, 0.5),  # SHOP-1 ranks second behind the defect
        (0.0, 0.0),  # nothing relevant comes back
    ]
    assert report.cases[4].first_hit_rank == 2
    assert report.cases[5].first_hit_rank is None
    assert report.cases[3].found_source_refs == ["page-login"]
    assert report.recall_at_k == 0.75
    assert report.mrr == 0.75
    assert report.to_dict()["case_count"] == 6


def test_search_never_crosses_projects(kb):
    _seed(kb)
    other = kb.project(name="B", url="https://b.example.com")
    dataset_id = kb.retriever.ensure_dataset(other)
    kb.repo.set_project_rag_dataset(other.id, dataset_id)
    other = kb.repo.get_project(other.id)

    report = RetrievalEvalService(repository=kb.repo).evaluate(kb.retriever, other, FIXTURE["cases"][:2], k=3)
    assert report.recall_at_k == 0.0 and report.mrr == 0.0


def test_score_case_counts_only_the_top_k():
    items = [
        {"source_type": "document", "source_ref": "d1"},
        {"source_type": "document", "source_ref": "d2"},
        {"source_type": "jira_story", "source_ref": "S-1"},
    ]
    case = EvalCase(query="q", expected_source_refs=["jira_story:S-1", "d2"])
    assert score_case(case, items, k=2).recall_at_k == 0.5
    assert score_case(case, items, k=2).reciprocal_rank == 0.5
    full = score_case(case, items, k=3)
    assert full.recall_at_k == 1.0 and full.reciprocal_rank == 0.5


def test_a_type_prefixed_ref_does_not_match_another_type():
    items = [{"source_type": "document", "source_ref": "S-1"}]
    case = EvalCase(query="q", expected_source_refs=["jira_story:S-1"])
    assert score_case(case, items, k=1).recall_at_k == 0.0


@pytest.mark.parametrize(
    "cases, k",
    [
        ([], 3),
        ([{"query": "", "expected_source_refs": ["a"]}], 3),
        ([{"query": "q", "expected_source_refs": []}], 3),
        ([{"query": "q", "expected_source_refs": ["a"], "source_types": ["nope"]}], 3),
        ([{"query": "q", "expected_source_refs": ["a"]}], 0),
        ([{"query": "q", "expected_source_refs": ["a"]}], 11),
    ],
)
def test_bad_input_is_rejected(kb, cases, k):
    project = _seed(kb)
    with pytest.raises(ValueError):
        RetrievalEvalService(repository=kb.repo).evaluate(kb.retriever, project, cases, k=k)


def test_script_prints_the_metrics(kb, tmp_path, monkeypatch):
    project = _seed(kb)
    cases_file = tmp_path / "cases.json"
    cases_file.write_text(json.dumps(FIXTURE["cases"]), encoding="utf-8")
    monkeypatch.setattr(eval_retrieval, "get_project_repository", lambda: kb.repo)
    monkeypatch.setattr(eval_retrieval, "get_retriever", lambda: kb.retriever)
    lines = []

    code = eval_retrieval.main(["--project", str(project.id), "--cases", str(cases_file), "--k", "3"], out=lines.append)
    assert code == 0
    assert "Overall recall@3: 0.7500" in lines[0] and "Overall MRR: 0.7500" in lines[0]

    lines.clear()
    code = eval_retrieval.main(["--project", str(project.id), "--cases", str(cases_file), "--k", "3", "--json"], out=lines.append)
    assert code == 0 and json.loads(lines[0])["mrr"] == 0.75


def test_script_refuses_a_missing_project_or_a_bad_cases_file(kb, tmp_path, monkeypatch):
    monkeypatch.setattr(eval_retrieval, "get_project_repository", lambda: kb.repo)
    monkeypatch.setattr(eval_retrieval, "get_retriever", lambda: kb.retriever)
    cases_file = tmp_path / "cases.json"
    cases_file.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    out = []
    assert eval_retrieval.main(["--project", "00000000-0000-0000-0000-000000000000", "--cases", str(cases_file)], out=out.append) == 2
    assert out[0].startswith("error:")
