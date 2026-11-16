"""Chat repository contract (spec 0005, AC-25): the JSON store and the
PostgreSQL store pass the same tests. The SQL store runs against the
configured SQLAlchemy engine (SQLite under the JSON provider)."""

import threading
from datetime import timedelta
from uuid import uuid4

import pytest

from backend.models.chat import utc_now
from backend.repository.chat_repository import InvalidCursorError, JsonChatRepository


@pytest.fixture
def json_store(tmp_path):
    return JsonChatRepository(tmp_path / "chat_store.json"), uuid4(), uuid4(), uuid4()


@pytest.fixture
def sql_store():
    from backend.database.db import Base, SessionLocal, engine
    from backend.database.db_models import ProjectDB, UserDB
    from backend.repository.postgres_chat_repository import PostgresChatRepository

    Base.metadata.create_all(engine)
    db = SessionLocal()
    projects = [
        ProjectDB(name=f"chat-{uuid4()}", description="", target_url=f"https://{uuid4().hex}.example.test")
        for _ in range(2)
    ]
    user = UserDB(email=f"chat-{uuid4().hex}@example.test", password_hash="x", role="Tester")
    db.add_all([*projects, user])
    db.commit()
    ids = (projects[0].id, projects[1].id, user.id)
    db.close()
    yield (PostgresChatRepository(), *ids)
    db = SessionLocal()
    db.query(ProjectDB).filter(ProjectDB.id.in_(ids[:2])).delete(synchronize_session=False)
    db.query(UserDB).filter(UserDB.id == ids[2]).delete(synchronize_session=False)
    db.commit()
    db.close()


@pytest.fixture(params=["json", "sql"])
def store(request):
    return request.getfixturevalue(f"{request.param}_store")


def test_thread_round_trip_and_ownership_fields(store):
    repo, project_id, _, user_id = store
    thread = repo.create_thread(project_id, user_id, "Why did login fail")
    loaded = repo.get_thread(thread.id)
    assert (loaded.project_id, loaded.user_id, loaded.title) == (project_id, user_id, "Why did login fail")
    assert repo.get_thread(uuid4()) is None


def test_append_assigns_seq_and_bumps_activity(store):
    repo, project_id, _, user_id = store
    thread = repo.create_thread(project_id, user_id, "t")
    first = repo.append_message(thread.id, "user", "hi", view_context={"view": "dashboard"})
    second = repo.append_message(thread.id, "assistant", "")
    assert (first.seq, second.seq) == (1, 2)
    assert repo.get_thread(thread.id).last_activity_at >= second.created_at - timedelta(seconds=1)
    assert repo.get_message(first.id).view_context == {"view": "dashboard"}


def test_update_content_and_interrupted(store):
    repo, project_id, _, user_id = store
    thread = repo.create_thread(project_id, user_id, "t")
    message = repo.append_message(thread.id, "assistant", "")
    assert repo.update_message_content(message.id, "partial", interrupted=True)
    saved = repo.get_message(message.id)
    assert (saved.content, saved.interrupted) == ("partial", True)


def test_delete_messages_after_removes_hidden_rows_and_keeps_sequence_prefix(store):
    repo, project_id, _, user_id = store
    thread = repo.create_thread(project_id, user_id, "t")
    first = repo.append_message(thread.id, "user", "question")
    repo.append_message(thread.id, "assistant", "", tool_calls=[{"id": "c1", "name": "lookup", "arguments": {}}])
    repo.append_message(thread.id, "tool", "", tool_name="lookup", tool_call_id="c1", tool_result="{}")
    repo.append_message(thread.id, "assistant", "partial", interrupted=True)

    assert repo.delete_messages_after(thread.id, first.seq) == 3
    rows = repo.recent_messages_for_llm(thread.id, 10)
    assert [(row.seq, row.role, row.content) for row in rows] == [(first.seq, "user", "question")]


def test_list_messages_hides_tool_rows_and_pages_by_seq(store):
    repo, project_id, _, user_id = store
    thread = repo.create_thread(project_id, user_id, "t")
    repo.append_message(thread.id, "user", "q1")
    repo.append_message(thread.id, "assistant", "", tool_calls=[{"id": "c1", "name": "x", "arguments": {}}])
    repo.append_message(thread.id, "tool", "", tool_name="x", tool_call_id="c1", tool_result="{}")
    repo.append_message(thread.id, "assistant", "a1")
    repo.append_message(thread.id, "user", "q2")

    items, has_more = repo.list_messages(thread.id)
    assert [m.content for m in items] == ["q1", "a1", "q2"]
    assert has_more is False

    items, has_more = repo.list_messages(thread.id, limit=2)
    assert [m.content for m in items] == ["a1", "q2"] and has_more is True
    items, has_more = repo.list_messages(thread.id, before_seq=items[0].seq, limit=2)
    assert [m.content for m in items] == ["q1"] and has_more is False

    everything = repo.recent_messages_for_llm(thread.id, 10)
    assert [m.role for m in everything] == ["user", "assistant", "tool", "assistant", "user"]
    assert [m.seq for m in repo.recent_messages_for_llm(thread.id, 2)] == [4, 5]


def test_thread_list_is_per_user_and_project_newest_first_with_cursor(store):
    repo, project_id, other_project_id, user_id = store
    threads = []
    for index in range(3):
        thread = repo.create_thread(project_id, user_id, f"t{index}")
        repo.append_message(thread.id, "user", "x")
        threads.append(thread)
    repo.create_thread(other_project_id, user_id, "elsewhere")

    page, cursor = repo.list_threads(project_id, user_id, limit=2)
    assert [t.title for t in page] == ["t2", "t1"]
    assert cursor
    page, cursor = repo.list_threads(project_id, user_id, cursor=cursor, limit=2)
    assert [t.title for t in page] == ["t0"] and cursor is None
    assert repo.list_threads(project_id, uuid4())[0] == []
    with pytest.raises(InvalidCursorError):
        repo.list_threads(project_id, user_id, cursor="not-a-cursor")


def test_delete_purge_and_project_cleanup(store):
    repo, project_id, other_project_id, user_id = store
    keep = repo.create_thread(project_id, user_id, "keep")
    gone = repo.create_thread(project_id, user_id, "gone")
    repo.append_message(gone.id, "user", "x")
    assert repo.delete_thread(gone.id) and repo.get_thread(gone.id) is None
    assert repo.list_messages(gone.id) == ([], False)

    future = utc_now() + timedelta(days=1)
    assert repo.purge_idle(future, project_id=project_id, user_id=user_id, skip_thread_ids={keep.id}) == 0
    assert repo.purge_idle(future, project_id=project_id, user_id=user_id) == 1

    repo.create_thread(other_project_id, user_id, "a")
    repo.create_thread(other_project_id, user_id, "b")
    assert repo.delete_threads_for_project(other_project_id) == 2


def test_action_status_changes_only_through_compare_and_set(store):
    repo, project_id, _, user_id = store
    thread = repo.create_thread(project_id, user_id, "t")
    message = repo.append_message(thread.id, "assistant", "", action={"type": "generate_scenarios", "status": "proposed"})

    results = []

    def confirm():
        results.append(repo.compare_and_set_action_status(message.id, {"proposed"}, "running", {"confirmed_by": "u"}))

    workers = [threading.Thread(target=confirm) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    assert winners[0]["status"] == "running" and winners[0]["confirmed_by"] == "u"

    other = repo.append_message(thread.id, "assistant", "", action={"type": "x", "status": "proposed"})
    assert repo.expire_proposed_actions(thread.id) == 1
    assert repo.get_message(other.id).action["status"] == "expired"
    assert repo.get_message(message.id).action["status"] == "running"


def test_set_action_active_actions_and_restart_recovery_rows(store):
    repo, project_id, _, user_id = store
    thread = repo.create_thread(project_id, user_id, "t")
    reply = repo.append_message(thread.id, "assistant", "Here is the plan")
    action = {"type": "generate_scenarios", "status": "proposed"}

    assert repo.set_message_action(reply.id, action) is True
    # A message keeps its first action; later changes go through compare and set.
    assert repo.set_message_action(reply.id, {"type": "other", "status": "proposed"}) is False
    assert repo.get_message(reply.id).action["type"] == "generate_scenarios"
    assert repo.list_active_actions() == [] or all(m.id != reply.id for m, _ in repo.list_active_actions())

    repo.compare_and_set_action_status(reply.id, {"proposed"}, "running")
    active = [(m.id, p) for m, p in repo.list_active_actions() if m.thread_id == thread.id]
    assert active == [(reply.id, project_id)]

    cut_off = repo.append_message(thread.id, "assistant", "")
    hidden = repo.append_message(thread.id, "assistant", "", tool_calls=[{"id": "c", "name": "x", "arguments": {}}])
    assert repo.mark_empty_replies_interrupted() >= 1
    assert repo.get_message(cut_off.id).interrupted is True
    assert repo.get_message(hidden.id).interrupted is False
    assert repo.get_message(reply.id).interrupted is False
