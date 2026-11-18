"""Indexing the rest of the project, slice 3 (spec 0006, AC-14 to AC-16).

Test cases, Jira stories, failed executions, and object repository pages
become KnowledgeEntry rows, against the InMemoryRetriever and a fake clock.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.models.execution_result import ExecutionResult, ExecutionStatus
from backend.models.knowledge import ENTRY_FAILED, ENTRY_INDEXED, KnowledgeEntry
from backend.models.object_repository import ObjectElement, ObjectLocator, ObjectPage, ProjectObjectRepository
from backend.models.scenario import Scenario
from backend.models.test_case import EvaluationStatus, TestCase
from backend.scripts.reindex_project_knowledge import reindex
from backend.services import knowledge_indexer as indexer_module
from backend.services.knowledge_indexer import KnowledgeIndexer
from backend.services.knowledge_sources import list_project_sources, read_locator_page, read_source
from backend.services.retrieval import RetrieverError
from backend.services.retrieval.fake import NullRetriever
from backend.tests.test_knowledge_base import FakeClock, kb, settings  # noqa: F401  (pytest fixtures)


def _story(kb, project, key="SHOP-1", criteria=("Members get 10 percent off",)):
    req = kb.repo.create_requirement(
        project_id=project.id, title=f"Checkout discount {key}", description="Members pay less at checkout.",
        priority="medium", business_domain="shop", requirement_id=key,
    )
    req = req.model_copy(update={"jira_issue_key": key, "acceptance_criteria": list(criteria)})
    kb.repo.save_requirement(req)
    return req


def _test_case(kb, requirement, approved=True, title="Member discount applies"):
    scenario = Scenario(requirement_id=requirement.id, scenario_name="Discount", description="d", approved=True)
    kb.repo.save_scenarios([scenario])
    case = TestCase(
        scenario_id=scenario.id, title=title, steps=["Sign in as a member", "Open checkout"],
        expected_result="A 10 percent discount is shown",
        evaluation_status=EvaluationStatus.APPROVED if approved else EvaluationStatus.NEEDS_REVIEW,
    )
    kb.repo.save_test_cases([case])
    return case


def _search_types(kb, project, query):
    return {(i["source_type"], i["source_ref"]) for i in kb.service.search(project, query, top_k=10)}


# ------------------------------------------------------------ AC-14: test cases


def test_an_approved_test_case_is_indexed_and_searchable(kb):
    project = kb.project()
    case = _test_case(kb, _story(kb, project))
    kb.indexer.enqueue_source(project.id, "test_case", case.id)
    kb.indexer.run_pending()

    entry = kb.repo.get_knowledge_entry(project.id, "test_case", str(case.id))
    assert entry.status == ENTRY_INDEXED and entry.remote_doc_id and entry.title == case.title
    items = kb.service.search(kb.repo.get_project(project.id), "member discount checkout", source_types=["test_case"])
    assert items[0]["source_ref"] == str(case.id) and items[0]["title"] == case.title


def test_a_test_case_that_is_not_approved_is_not_indexed(kb):
    project = kb.project()
    case = _test_case(kb, _story(kb, project), approved=False)
    kb.indexer.enqueue_source(project.id, "test_case", case.id)
    kb.indexer.run_pending()
    assert kb.repo.get_knowledge_entry(project.id, "test_case", str(case.id)) is None


def test_same_content_is_sent_once_and_an_edit_replaces_the_remote_document(kb):
    project = kb.project()
    case = _test_case(kb, _story(kb, project))
    uploads = []
    original = kb.retriever.upload_text
    kb.retriever.upload_text = lambda p, text, name: uploads.append(name) or original(p, text, name)

    kb.indexer.enqueue_source(project.id, "test_case", case.id)
    kb.indexer.enqueue_source(project.id, "test_case", case.id)
    kb.indexer.run_pending()
    assert len(uploads) == 1 and uploads[0] == f"test_case__{case.id}.txt"
    first = kb.repo.get_knowledge_entry(project.id, "test_case", str(case.id))

    kb.repo.update_test_case(case.model_copy(update={"expected_result": "A 15 percent discount is shown"}))
    kb.indexer.enqueue_source(project.id, "test_case", case.id)
    kb.indexer.run_pending()
    second = kb.repo.get_knowledge_entry(project.id, "test_case", str(case.id))
    assert len(uploads) == 2
    assert second.remote_doc_id != first.remote_doc_id and second.id == first.id
    dataset = kb.retriever.datasets[kb.repo.get_project(project.id).rag_dataset_id]
    assert first.remote_doc_id not in dataset and second.remote_doc_id in dataset


def test_a_deleted_test_case_leaves_the_index(kb):
    project = kb.project()
    case = _test_case(kb, _story(kb, project))
    kb.indexer.enqueue_source(project.id, "test_case", case.id)
    kb.indexer.run_pending()
    remote = kb.repo.get_knowledge_entry(project.id, "test_case", str(case.id)).remote_doc_id

    kb.repo.delete_test_case(case.id)
    kb.indexer.enqueue_source_delete(project.id, "test_case", case.id)
    kb.indexer.run_pending()
    assert kb.repo.get_knowledge_entry(project.id, "test_case", str(case.id)) is None
    assert remote not in kb.retriever.datasets[kb.repo.get_project(project.id).rag_dataset_id]


def test_a_test_case_of_another_project_is_never_indexed_here(kb):
    a, b = kb.project("A", "https://a.example.com"), kb.project("B", "https://b.example.com")
    case = _test_case(kb, _story(kb, a))
    kb.indexer.enqueue_source(b.id, "test_case", case.id)
    kb.indexer.run_pending()
    assert kb.repo.get_knowledge_entry(b.id, "test_case", str(case.id)) is None


# ------------------------------------------------------------ AC-14: stories, failures, locators


def test_a_jira_story_indexes_key_summary_and_acceptance_criteria(kb):
    project = kb.project()
    _story(kb, project, "SHOP-7")
    source = read_source(kb.repo, project, "jira_story", "SHOP-7")
    assert "SHOP-7" in source.text and "Members get 10 percent off" in source.text
    kb.indexer.enqueue_source(project.id, "jira_story", "SHOP-7")
    kb.indexer.run_pending()
    assert ("jira_story", "SHOP-7") in _search_types(kb, kb.repo.get_project(project.id), "members percent off")


def test_only_failed_executions_are_indexed_with_their_analysis(kb):
    project = kb.project()
    case = _test_case(kb, _story(kb, project))
    failed = ExecutionResult(
        test_case_id=case.id, status=ExecutionStatus.FAILED, error_message="Discount label missing",
        failure_category="product_bug", root_cause_summary="The discount banner never renders for members",
    )
    passed = ExecutionResult(test_case_id=case.id, status=ExecutionStatus.PASSED)
    kb.repo.save_execution_result(project.id, failed)
    kb.repo.save_execution_result(project.id, passed)

    source = read_source(kb.repo, project, "execution_failure", str(failed.id))
    assert source.title == f"{case.title} failed"
    assert "Discount label missing" in source.text and "never renders" in source.text
    assert read_source(kb.repo, project, "execution_failure", str(passed.id)) is None

    kb.repo.delete_execution(failed.id)
    assert read_source(kb.repo, project, "execution_failure", str(failed.id)) is None


def test_a_ui_run_failure_holds_its_title_and_error_but_no_analysis_lines(kb):
    # covers: AC-14 (task 12b, value "entry source text"): UI and batch runs are not analysed.
    project = kb.project()
    case = _test_case(kb, _story(kb, project))
    failed = ExecutionResult(test_case_id=case.id, status=ExecutionStatus.ERROR, error_message="  Timed out on Pay  ")
    kb.repo.save_execution_result(project.id, failed)

    source = read_source(kb.repo, project, "execution_failure", str(failed.id))

    assert source.title == f"{case.title} failed"
    assert source.text.splitlines() == [
        f"Failed execution of: {case.title}",
        "Status: error",
        "Error: Timed out on Pay",
    ]


def test_a_failed_run_saved_again_with_the_same_content_is_uploaded_once(kb):
    # covers: AC-14 (task 12b): the workspace may post the same completed webhook twice.
    project = kb.project()
    case = _test_case(kb, _story(kb, project))
    failed = ExecutionResult(test_case_id=case.id, status=ExecutionStatus.FAILED, error_message="Pay button missing")
    uploads = []
    original = kb.retriever.upload_text
    kb.retriever.upload_text = lambda p, text, name: uploads.append(name) or original(p, text, name)

    kb.repo.save_execution_result(project.id, failed)
    kb.indexer.enqueue_source(project.id, "execution_failure", failed.id)
    kb.indexer.run_pending()
    first = kb.repo.get_knowledge_entry(project.id, "execution_failure", str(failed.id))
    kb.repo.save_execution_result(project.id, failed.model_copy())
    kb.indexer.enqueue_source(project.id, "execution_failure", failed.id)
    kb.indexer.run_pending()
    second = kb.repo.get_knowledge_entry(project.id, "execution_failure", str(failed.id))

    assert uploads == [f"execution_failure__{failed.id}.txt"]
    assert second.id == first.id and second.remote_doc_id == first.remote_doc_id
    assert second.status == ENTRY_INDEXED


def test_a_locator_page_lists_its_elements_and_locators():
    project = SimpleNamespace(id=uuid4())
    page = ObjectPage(project_id=project.id, name="Checkout", url_pattern="/checkout")
    page.elements = [
        ObjectElement(page_id=page.id, name="payButton", element_type="button",
                      locators=[ObjectLocator(position=0, strategy="role", value="button[name=Pay]")]),
        ObjectElement(page_id=page.id, name="oldLink", element_type="link", is_deleted=True),
    ]
    service = SimpleNamespace(load=lambda project_id: ProjectObjectRepository(project_id=project_id, pages=[page]))

    source = read_locator_page(project, str(page.id), service)
    assert source.title == "Checkout"
    assert "Checkout.payButton (button)" in source.text and "role=button[name=Pay]" in source.text
    assert "oldLink" not in source.text
    page.is_deleted = True
    assert read_locator_page(project, str(page.id), service) is None


# ------------------------------------------------------------ AC-15: hooks and failures


def test_the_hook_never_raises(monkeypatch):
    def broken():
        raise RuntimeError("indexer down")

    monkeypatch.setattr(indexer_module, "get_knowledge_indexer", broken)
    indexer_module.queue_knowledge_source(uuid4(), "test_case", uuid4())  # no exception


def test_upload_failures_are_retried_then_recorded_as_failed(kb):
    project = kb.project()
    case = _test_case(kb, _story(kb, project))

    def always_down(p, text, name):
        raise RetrieverError("RAGFlow unavailable", transient=True)

    kb.retriever.upload_text = always_down
    kb.indexer.enqueue_source(project.id, "test_case", case.id)
    for delay in [0, *kb.settings.rag.index_retry_delays]:
        kb.clock.advance(delay)
        kb.indexer.run_pending()
    entry = kb.repo.get_knowledge_entry(project.id, "test_case", str(case.id))
    assert entry.status == ENTRY_FAILED and "RAGFlow unavailable" in entry.error
    assert kb.indexer.pending() == 0


def test_a_failed_reindex_keeps_the_previous_remote_document_searchable(kb):
    """covers: AC-4, AC-14 (seen live in /check verify: edit while RAGFlow is down)."""
    project = kb.project()
    case = _test_case(kb, _story(kb, project))
    kb.indexer.enqueue_source(project.id, "test_case", case.id)
    kb.indexer.run_pending()
    before = kb.repo.get_knowledge_entry(project.id, "test_case", str(case.id))

    def always_down(p, text, name):
        raise RetrieverError("RAGFlow unavailable", transient=True)

    kb.retriever.upload_text = always_down
    kb.repo.update_test_case(case.model_copy(update={"expected_result": "A 15 percent discount is shown"}))
    kb.indexer.enqueue_source(project.id, "test_case", case.id)
    for delay in [0, *kb.settings.rag.index_retry_delays]:
        kb.clock.advance(delay)
        kb.indexer.run_pending()

    after = kb.repo.get_knowledge_entry(project.id, "test_case", str(case.id))
    assert after.status == ENTRY_FAILED and "RAGFlow unavailable" in after.error
    assert after.id == before.id and after.remote_doc_id == before.remote_doc_id
    dataset = kb.retriever.datasets[kb.repo.get_project(project.id).rag_dataset_id]
    assert before.remote_doc_id in dataset
    found = _search_types(kb, kb.repo.get_project(project.id), "member discount checkout")
    assert ("test_case", str(case.id)) in found


def test_a_failed_entry_is_uploaded_again_once_ragflow_is_back(kb):
    """covers: AC-4, AC-14, AC-16. The failed entry already stores the new
    content hash, so the unchanged check must not skip it."""
    project = kb.project()
    case = _test_case(kb, _story(kb, project))
    kb.indexer.enqueue_source(project.id, "test_case", case.id)
    kb.indexer.run_pending()
    before = kb.repo.get_knowledge_entry(project.id, "test_case", str(case.id))

    working_upload = kb.retriever.upload_text

    def always_down(p, text, name):
        raise RetrieverError("RAGFlow unavailable", transient=True)

    kb.retriever.upload_text = always_down
    kb.repo.update_test_case(case.model_copy(update={"expected_result": "A 15 percent discount is shown"}))
    kb.indexer.enqueue_source(project.id, "test_case", case.id)
    for delay in [0, *kb.settings.rag.index_retry_delays]:
        kb.clock.advance(delay)
        kb.indexer.run_pending()

    kb.retriever.upload_text = working_upload
    kb.indexer.enqueue_source(project.id, "test_case", case.id)
    kb.indexer.run_pending()

    after = kb.repo.get_knowledge_entry(project.id, "test_case", str(case.id))
    assert after.status == ENTRY_INDEXED and after.error is None
    assert after.remote_doc_id != before.remote_doc_id
    dataset = kb.retriever.datasets[kb.repo.get_project(project.id).rag_dataset_id]
    assert before.remote_doc_id not in dataset and after.remote_doc_id in dataset
    assert ("test_case", str(case.id)) in _search_types(kb, kb.repo.get_project(project.id), "15 percent discount")


def test_status_counts_entries_by_source_type_and_status(kb):
    project = kb.project()
    case = _test_case(kb, _story(kb, project, "SHOP-2"))
    kb.indexer.enqueue_source(project.id, "test_case", case.id)
    kb.indexer.enqueue_source(project.id, "jira_story", "SHOP-2")
    kb.indexer.run_pending()
    status = kb.service.status(project)
    assert status["entries"] == {"test_case": {"indexed": 1}, "jira_story": {"indexed": 1}}
    assert status["last_indexed_at"] is not None


def test_nothing_is_indexed_while_rag_is_disabled(kb):
    project = kb.project()
    case = _test_case(kb, _story(kb, project))
    disabled = KnowledgeIndexer(retriever=NullRetriever(), repository=kb.repo, settings=kb.settings, clock=kb.clock)
    disabled.enqueue_source(project.id, "test_case", case.id)
    disabled.run_pending()
    assert kb.repo.list_knowledge_entries(project.id) == []
    assert disabled.stats["skipped"] == 1


# ------------------------------------------------------------ AC-16: reindex script


def test_reindex_indexes_every_source_skips_unchanged_and_removes_orphans(kb, monkeypatch):
    project = kb.project()
    case = _test_case(kb, _story(kb, project, "SHOP-3"))
    _test_case(kb, _story(kb, project, "SHOP-4"), approved=False, title="Draft case")
    orphan = KnowledgeEntry(project_id=project.id, source_type="test_case", source_ref=str(uuid4()),
                            title="gone", remote_doc_id="r-gone", content_hash="x")
    kb.repo.save_knowledge_entry(orphan)
    empty_pages = SimpleNamespace(load=lambda project_id: ProjectObjectRepository(project_id=project_id))
    monkeypatch.setattr(
        "backend.scripts.reindex_project_knowledge.list_project_sources",
        lambda repo, proj: list_project_sources(repo, proj, empty_pages),
    )

    lines = []
    counts = reindex([project.id], indexer=kb.indexer, repo=kb.repo, out=lines.append)
    assert counts == {"indexed": 3, "skipped": 0, "failed": 0, "removed": 1}
    refs = {(e.source_type, e.source_ref) for e in kb.repo.list_knowledge_entries(project.id)}
    assert refs == {("test_case", str(case.id)), ("jira_story", "SHOP-3"), ("jira_story", "SHOP-4")}

    kb.indexer.stats.clear()
    counts = reindex([project.id], indexer=kb.indexer, repo=kb.repo, out=lines.append)
    assert counts == {"indexed": 0, "skipped": 3, "failed": 0, "removed": 0}
    assert lines[-1].startswith("Indexed 0, skipped (unchanged) 3")


# ------------------------------------------------------------ AC-16: the command line


@pytest.fixture
def cli(monkeypatch):
    """main() with RAG on and reindex recorded, so no store or RAGFlow is touched."""
    import backend.scripts.reindex_project_knowledge as script

    seen = {"targets": [], "counts": {"indexed": 1, "skipped": 0, "failed": 0, "removed": 0}}
    monkeypatch.setattr(script, "get_retriever", lambda: SimpleNamespace(is_active=lambda: True))

    def fake_reindex(project_ids):
        seen["targets"].append(project_ids)
        return seen["counts"]

    monkeypatch.setattr(script, "reindex", fake_reindex)
    return script, seen


def test_cli_reindexes_one_project(cli):
    # covers: AC-16
    script, seen = cli
    project_id = uuid4()
    assert script.main(["--project", str(project_id)]) == 0
    assert seen["targets"] == [[project_id]]


def test_cli_all_reindexes_every_project(cli):
    # covers: AC-16
    script, seen = cli
    assert script.main(["--all"]) == 0
    assert seen["targets"] == [None]


def test_cli_exits_1_when_any_item_failed(cli):
    # covers: AC-16
    script, seen = cli
    seen["counts"] = {"indexed": 2, "skipped": 0, "failed": 1, "removed": 0}
    assert script.main(["--all"]) == 1


def test_cli_exits_1_and_says_so_while_the_knowledge_base_is_disabled(cli, monkeypatch, capsys):
    # covers: AC-16, AC-8
    script, seen = cli
    monkeypatch.setattr(script, "get_retriever", lambda: NullRetriever())
    assert script.main(["--all"]) == 1
    assert "knowledge base is disabled" in capsys.readouterr().err
    assert seen["targets"] == []


@pytest.mark.parametrize("argv", [[], ["--all", "--project", str(uuid4())], ["--project", "not-a-uuid"]])
def test_cli_needs_exactly_one_valid_target(cli, argv):
    # covers: AC-16
    script, seen = cli
    with pytest.raises(SystemExit) as stopped:
        script.main(argv)
    assert stopped.value.code == 2
    assert seen["targets"] == []


def test_reindex_stops_on_an_unknown_project(kb):
    # covers: AC-16
    with pytest.raises(SystemExit, match="not found"):
        reindex([uuid4()], indexer=kb.indexer, repo=kb.repo, out=lambda line: None)
