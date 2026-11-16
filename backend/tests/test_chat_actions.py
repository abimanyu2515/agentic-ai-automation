"""Chat assistant actions, slice 3 (spec 0005): propose, confirm, cancel,
status, expiry, audit, and restart recovery for `generate_scenarios`.

Every test uses a scripted LLM, a temporary JSON chat store, a fake project
repository, a fake workflow service, and a runner that does the work in the
calling thread. No live provider, database, or browser is needed (AC-26)."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.config.settings import ChatConfig
from backend.repository.chat_repository import JsonChatRepository
from backend.schemas.chat_dto import ChatActionConfirm
from backend.services.chat_actions import (
    CHANGED_MESSAGE,
    ONE_ACTION_RESULT,
    PROPOSED_RESULT,
    RESTART_ERROR,
    ChatActionExecutor,
    propose_tool_definitions,
)
from backend.services.chat_service import (
    ChatConflict,
    ChatForbidden,
    ChatInvalid,
    ChatNotFound,
    ChatService,
    allowed_action_types,
)
from backend.services.llm import ChatChunk, ChatToolCall

ALL_ACTIONS = frozenset({"generate_scenarios"})


class ScriptedLLM:
    def __init__(self, steps):
        self.steps = list(steps)
        self.calls: list[dict] = []

    def stream_chat(self, messages, tools=None, tool_choice=None, **kwargs):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        step = self.steps.pop(0) if self.steps else ["(no more steps)"]
        for item in step:
            yield ChatChunk(text=item) if isinstance(item, str) else ChatChunk(tool_call=item)


class FakeToolbox:
    def __init__(self):
        self.runs = []

    def definitions(self):
        return [{"type": "function", "function": {"name": "list_scenarios", "parameters": {}}}]

    def run(self, project, name, arguments):
        self.runs.append(name)
        return json.dumps({"tool": name})


class FakeRepo:
    def __init__(self, project, requirement, scenarios):
        self.project = project
        self.requirement = requirement
        self.scenarios = {s.id: s for s in scenarios}
        self.deleted: list = []

    def get_requirement(self, requirement_id):
        return self.requirement if requirement_id == self.requirement.id else None

    def get_requirements(self, project_id):
        live = project_id == self.project.id and not getattr(self.requirement, "is_deleted", False)
        return [self.requirement] if live else []

    def get_scenarios(self, requirement_id):
        return [s for s in self.scenarios.values() if s.requirement_id == requirement_id and not s.is_deleted]

    def get_scenario(self, scenario_id):
        return self.scenarios.get(scenario_id)

    def delete_scenario(self, scenario_id, deleted_by=None):
        scenario = self.scenarios.get(scenario_id)
        if scenario is None or scenario.is_deleted:
            return False
        scenario.is_deleted = True
        self.deleted.append((scenario_id, deleted_by))
        return True


class FakeWorkflow:
    def __init__(self, repo, fail=False):
        self.repo = repo
        self.fail = fail
        self.calls = []

    def generate_scenarios(self, project_id, requirement_id, count, mode="append"):
        self.calls.append((project_id, requirement_id, count, mode))
        if self.fail:
            raise RuntimeError("LLM provider down")
        created = [
            SimpleNamespace(id=uuid4(), requirement_id=requirement_id, scenario_name=f"New {i}", is_deleted=False)
            for i in range(count)
        ]
        for scenario in created:
            self.repo.scenarios[scenario.id] = scenario
        return created


def _parse(frames):
    events = []
    for frame in frames:
        lines = dict(line.split(": ", 1) for line in frame.strip().splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def _propose(index, requirement_id, **arguments):
    return ChatToolCall(
        id=f"p{index}",
        name="propose_generate_scenarios",
        arguments={"requirement_id": str(requirement_id), **arguments},
    )


@pytest.fixture
def world(tmp_path):
    requirement = SimpleNamespace(id=uuid4(), title="Login works")
    project = SimpleNamespace(id=uuid4(), name="Shop", requirements=[requirement.id])
    old = [
        SimpleNamespace(id=uuid4(), requirement_id=requirement.id, scenario_name=f"Old {i}", is_deleted=False)
        for i in range(2)
    ]
    repo = FakeRepo(project, requirement, old)
    workflow = FakeWorkflow(repo)
    audits: list[tuple[str, dict]] = []
    chats = JsonChatRepository(tmp_path / "chat_store.json")
    settings = SimpleNamespace(chat=ChatConfig())
    user_id = uuid4()

    def audit(event, *, user_id, project_id, action):
        audits.append((event, {"user_id": user_id, "project_id": project_id, **action}))

    def service(steps=(), runner=None, **overrides):
        executor = ChatActionExecutor(
            project_repository=repo, workflow_service=workflow, audit=audit,
            runner=runner or (lambda work: work()),
        )
        kwargs = dict(
            llm_service=ScriptedLLM(steps), chat_repository=chats, project_repository=repo, settings=settings,
            toolbox=FakeToolbox(), action_executor=executor, system_prompt="sys", guide="",
        )
        kwargs.update(overrides)
        return ChatService(**kwargs)

    def propose_card(svc=None, **arguments):
        """Run one turn that proposes generate_scenarios; returns (service, assistant message)."""
        svc = svc or service([["Setting that up.", _propose(1, requirement.id, **arguments)]])
        turn = svc.prepare_turn(project, state.user_id, "Generate scenarios for login", allowed_actions=ALL_ACTIONS)
        events = _parse(svc.stream_turn(turn))
        message_id = next(data["message_id"] for name, data in events if name == "action")
        return svc, chats.get_message(message_id), turn

    state = SimpleNamespace(
        project=project, requirement=requirement, old=old, repo=repo, workflow=workflow, audits=audits,
        chats=chats, user_id=user_id, service=service, propose_card=propose_card,
    )
    return state


def allow_all(module, action):
    return True


# ------------------------------------------------------------------ propose


def test_propose_tools_are_offered_only_for_permitted_actions(world):
    assert allowed_action_types(lambda module, action: (module, action) == ("Scenarios", "create")) == frozenset({
        "generate_scenarios", "duplicate_scenario",
    })
    assert allowed_action_types(lambda module, action: False) == frozenset()

    svc = world.service([["Hi"]])
    turn = svc.prepare_turn(world.project, world.user_id, "hello", allowed_actions=frozenset())
    _parse(svc.stream_turn(turn))
    names = [t["function"]["name"] for t in svc.llm.calls[0]["tools"]]
    assert "propose_generate_scenarios" not in names

    svc = world.service([["Hi"]])
    turn = svc.prepare_turn(world.project, world.user_id, "hello", allowed_actions=ALL_ACTIONS)
    _parse(svc.stream_turn(turn))
    names = [t["function"]["name"] for t in svc.llm.calls[0]["tools"]]
    assert "propose_generate_scenarios" in names


def test_action_catalog_exposes_only_the_specified_action_tools():
    from backend.services.chat_actions import ACTION_CATALOG

    assert set(ACTION_CATALOG) == {
        "generate_scenarios", "generate_test_cases", "generate_script", "run_test_case", "run_batch",
        "create_requirement", "update_requirement", "delete_requirement", "update_scenario",
        "duplicate_scenario", "delete_scenario", "update_test_case", "review_test_case", "generate_test_data",
        "delete_test_case",
        "delete_execution", "delete_failed_executions",
    }


def test_action_catalog_rejects_empty_edits_and_oversized_batches(world):
    from backend.services.chat_actions import ActionInvalid

    svc = world.service()
    with pytest.raises(ActionInvalid, match="at least one field"):
        svc.actions.propose(world.project, "update_requirement", {
            "requirement_id": str(world.requirement.id), "changes": {},
        })
    with pytest.raises(ActionInvalid, match="at most 50"):
        svc.actions.propose(world.project, "run_batch", {"test_case_ids": [str(uuid4())] * 51})


def test_propose_schema_hides_server_filled_params():
    (tool,) = propose_tool_definitions(ALL_ACTIONS)
    properties = tool["function"]["parameters"]["properties"]
    assert set(properties) == {"requirement_id", "count", "mode", "summary"}
    assert properties["count"]["maximum"] == 10


def test_propose_schemas_are_self_contained_so_nested_objects_arrive_as_objects():
    # Regression: an unresolved {"$ref": "#/$defs/RequirementChanges"} made the
    # nvidia chat model send `changes` as a JSON string, so edit cards never
    # appeared. Every propose schema must carry its nested objects inline.
    from backend.services.chat_actions import ACTION_CATALOG

    tools = propose_tool_definitions(set(ACTION_CATALOG))

    for tool in tools:
        assert "$ref" not in json.dumps(tool), tool["function"]["name"]
        assert "$defs" not in tool["function"]["parameters"], tool["function"]["name"]
    by_name = {tool["function"]["name"]: tool["function"]["parameters"] for tool in tools}
    changes = by_name["propose_update_requirement"]["properties"]["changes"]
    assert changes["type"] == "object"
    assert {"title", "description", "priority", "business_domain"} <= set(changes["properties"])
    scenario_priority = by_name["propose_update_scenario"]["properties"]["changes"]["properties"]["priority"]
    assert {"enum": ["low", "medium", "high"], "title": "Priority", "type": "string"} in scenario_priority["anyOf"]


def test_a_valid_proposal_saves_a_card_and_ends_the_turn(world):
    svc, message, turn = world.propose_card(count=2, summary="  Two   more login scenarios  ")

    assert len(svc.llm.calls) == 1, "a proposal ends the turn without another LLM call"
    action = message.action
    assert action["status"] == "proposed"
    assert action["type"] == "generate_scenarios"
    assert action["params"] == {"requirement_id": str(world.requirement.id), "count": 2, "mode": "append"}
    assert action["targets"] == [{"type": "requirement", "id": str(world.requirement.id), "name": "Login works"}]
    assert action["danger"] == "change"
    assert action["permission"] == {"module": "Scenarios", "action": "create"}
    assert action["affects"] == ["scenarios"]
    assert action["summary"] == "Two more login scenarios"
    assert message.content == "Setting that up."
    assert world.workflow.calls == [], "proposing never runs the action"

    rows = world.chats.recent_messages_for_llm(turn.thread.id, 20)
    tool_rows = [r for r in rows if r.role == "tool"]
    assert [r.tool_result for r in tool_rows] == [PROPOSED_RESULT]

    # The history replayed next turn pairs the call with its result and notes the card.
    history = svc.history_for_llm(turn.thread.id)
    roles = [m["role"] for m in history]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert history[1]["tool_calls"][0]["id"] == history[2]["tool_call_id"]
    assert history[3]["content"].endswith("[action generate_scenarios on Login works: proposed]")


def test_only_the_first_propose_call_of_a_step_becomes_a_card(world):
    read = ChatToolCall(id="r1", name="list_scenarios", arguments={})
    steps = [[_propose(1, world.requirement.id), read, _propose(2, world.requirement.id, count=5)]]
    svc = world.service(steps)
    turn = svc.prepare_turn(world.project, world.user_id, "go", allowed_actions=ALL_ACTIONS)
    events = _parse(svc.stream_turn(turn))

    assert [name for name, _ in events].count("action") == 1
    assert svc.toolbox.runs == ["list_scenarios"], "read calls in the same step still run"
    results = {r.tool_call_id: r.tool_result for r in world.chats.recent_messages_for_llm(turn.thread.id, 20) if r.role == "tool"}
    assert results["p1"] == PROPOSED_RESULT
    assert results["p2"] == ONE_ACTION_RESULT
    card = next(data["action"] for name, data in events if name == "action")
    assert card["params"]["count"] == 3


def test_an_invalid_proposal_is_an_error_the_model_can_correct(world):
    steps = [[_propose(1, uuid4())], ["That requirement is not in this project."]]
    svc = world.service(steps)
    turn = svc.prepare_turn(world.project, world.user_id, "go", allowed_actions=ALL_ACTIONS)
    events = _parse(svc.stream_turn(turn))

    assert "action" not in [name for name, _ in events]
    assert len(svc.llm.calls) == 2
    tool_result = svc.llm.calls[1]["messages"][-1]["content"]
    assert "not found in this project" in tool_result


def test_a_propose_tool_the_user_may_not_use_is_not_run(world):
    steps = [[_propose(1, world.requirement.id)], ["ok"]]
    svc = world.service(steps)
    turn = svc.prepare_turn(world.project, world.user_id, "go", allowed_actions=frozenset())
    events = _parse(svc.stream_turn(turn))
    assert "action" not in [name for name, _ in events]
    assert svc.toolbox.runs == ["propose_generate_scenarios"], "handled as an unknown tool by the toolbox"


# ------------------------------------------------------------------ confirm


def test_confirm_runs_the_saved_params_and_finishes(world):
    svc, message, _ = world.propose_card(count=2)

    action = svc.confirm_action(world.project, world.user_id, message.id, None, allow_all)

    assert world.workflow.calls == [(world.project.id, world.requirement.id, 2, "append")]
    assert action["status"] == "succeeded"
    assert action["confirmed_by"] == str(world.user_id)
    assert action["result"] == {"summary": "Created 2 scenarios", "link_view": "scenarios", "link_id": str(world.requirement.id)}
    assert world.chats.get_message(message.id).action["status"] == "succeeded"
    assert [event for event, _ in world.audits] == ["confirmed", "succeeded"]
    assert all(entry["project_id"] == world.project.id for _, entry in world.audits)

    with pytest.raises(ChatConflict):
        svc.confirm_action(world.project, world.user_id, message.id, None, allow_all)
    assert len(world.workflow.calls) == 1


def test_confirm_body_carries_only_confirm_text():
    body = ChatActionConfirm.model_validate({"confirm_text": "DELETE", "params": {"count": 10}})
    assert body.model_dump() == {"confirm_text": "DELETE"}


def test_status_is_running_while_the_worker_runs(world):
    pending = []
    svc = world.service([["", _propose(1, world.requirement.id)]], runner=pending.append)
    _, message, _ = world.propose_card(svc=svc)

    action = svc.confirm_action(world.project, world.user_id, message.id, None, allow_all)
    assert action["status"] == "running"
    assert svc.get_action(world.project, world.user_id, message.id)["status"] == "running"

    pending.pop()()
    assert svc.get_action(world.project, world.user_id, message.id)["status"] == "succeeded"


def test_confirm_without_permission_is_forbidden(world):
    svc, message, _ = world.propose_card()
    with pytest.raises(ChatForbidden) as error:
        svc.confirm_action(world.project, world.user_id, message.id, None, lambda module, action: False)
    assert error.value.status_code == 403
    assert str(error.value) == "You no longer have permission"
    assert world.chats.get_message(message.id).action["status"] == "proposed"
    assert world.workflow.calls == []


def test_confirm_rechecks_the_target(world):
    svc, message, _ = world.propose_card()
    # A soft deleted requirement stays in the project's own list, so the
    # recheck must not rely on that list.
    world.requirement.is_deleted = True
    with pytest.raises(ChatInvalid) as error:
        svc.confirm_action(world.project, world.user_id, message.id, None, allow_all)
    assert str(error.value) == CHANGED_MESSAGE
    assert world.chats.get_message(message.id).action["status"] == "proposed"


def test_only_the_owner_can_see_or_act_on_a_card(world):
    svc, message, _ = world.propose_card()
    stranger = uuid4()
    for call in (
        lambda: svc.confirm_action(world.project, stranger, message.id, None, allow_all),
        lambda: svc.cancel_action(world.project, stranger, message.id),
        lambda: svc.get_action(world.project, stranger, message.id),
    ):
        with pytest.raises(ChatNotFound):
            call()
    other_project = SimpleNamespace(id=uuid4(), requirements=world.project.requirements)
    with pytest.raises(ChatNotFound):
        svc.get_action(other_project, world.user_id, message.id)


# ------------------------------------------------------------- cancel, expiry


def test_cancel_then_confirm_conflicts(world):
    svc, message, _ = world.propose_card()
    assert svc.cancel_action(world.project, world.user_id, message.id)["status"] == "cancelled"
    with pytest.raises(ChatConflict):
        svc.confirm_action(world.project, world.user_id, message.id, None, allow_all)
    with pytest.raises(ChatConflict):
        svc.cancel_action(world.project, world.user_id, message.id)
    assert [event for event, _ in world.audits] == ["cancelled"]
    assert world.workflow.calls == []


def test_a_new_message_expires_the_card(world):
    svc, message, turn = world.propose_card()
    svc.prepare_turn(world.project, world.user_id, "actually, never mind", thread_id=turn.thread.id)

    assert world.chats.get_message(message.id).action["status"] == "expired"
    with pytest.raises(ChatConflict) as error:
        svc.confirm_action(world.project, world.user_id, message.id, None, allow_all)
    assert str(error.value) == "Expired, ask again"


# -------------------------------------------------------------------- replace


def test_replace_is_a_delete_that_needs_typed_confirmation(world):
    svc, message, _ = world.propose_card(mode="replace", count=1)
    action = message.action
    assert action["danger"] == "delete"
    assert action["params"]["replace_ids"] == [str(s.id) for s in world.old]
    assert [t["name"] for t in action["targets"]] == ["Login works", "Old 0", "Old 1"]

    for text in (None, "delete", "DELETE "):
        with pytest.raises(ChatInvalid):
            svc.confirm_action(world.project, world.user_id, message.id, text, allow_all)

    done = svc.confirm_action(world.project, world.user_id, message.id, "DELETE", allow_all)
    assert done["status"] == "succeeded"
    assert done["result"]["summary"] == "Created 1 scenario, replaced 2"
    assert world.repo.deleted == [(s.id, world.user_id) for s in world.old]


def test_replace_cannot_smuggle_its_own_delete_list(world):
    victim = uuid4()
    svc, message, _ = world.propose_card(mode="replace", replace_ids=[str(victim)])
    assert str(victim) not in message.action["params"]["replace_ids"]


def test_failed_generation_leaves_the_old_scenarios(world):
    world.workflow.fail = True
    svc, message, _ = world.propose_card(mode="replace")

    action = svc.confirm_action(world.project, world.user_id, message.id, "DELETE", allow_all)

    assert action["status"] == "failed"
    assert "LLM provider down" in action["error"]
    assert world.repo.deleted == []
    assert all(not s.is_deleted for s in world.old)
    assert [event for event, _ in world.audits] == ["confirmed", "failed"]


def test_a_replaced_scenario_deleted_since_the_proposal_fails_the_recheck(world):
    svc, message, _ = world.propose_card(mode="replace")
    world.old[0].is_deleted = True
    with pytest.raises(ChatInvalid):
        svc.confirm_action(world.project, world.user_id, message.id, "DELETE", allow_all)


# ------------------------------------------------------------ edge lifecycles


def test_work_whose_thread_was_deleted_still_writes_its_audit(world):
    pending = []
    svc = world.service([["", _propose(1, world.requirement.id)]], runner=pending.append)
    _, message, turn = world.propose_card(svc=svc)
    svc.confirm_action(world.project, world.user_id, message.id, None, allow_all)

    world.chats.delete_thread(turn.thread.id)
    pending.pop()()

    assert [event for event, _ in world.audits] == ["confirmed", "succeeded"]


def test_restart_recovery_fails_inline_work_and_marks_cut_off_replies(world):
    svc = world.service([["", _propose(1, world.requirement.id)]], runner=lambda work: None)
    _, message, turn = world.propose_card(svc=svc)
    svc.confirm_action(world.project, world.user_id, message.id, None, allow_all)
    assert world.chats.get_message(message.id).action["status"] == "running"
    cut_off = world.chats.append_message(turn.thread.id, "assistant", "")

    assert svc.recover_after_restart() == 1

    action = world.chats.get_message(message.id).action
    assert action["status"] == "failed"
    assert action["error"] == RESTART_ERROR
    assert world.chats.get_message(cut_off.id).interrupted is True
    assert world.audits[-1][0] == "failed"


# ------------------------------------------------------------------- routes


@pytest.fixture
def client(world, monkeypatch):
    from fastapi.testclient import TestClient

    import backend.main as main

    world.repo.get_project = lambda project_id: world.project if project_id == world.project.id else None
    monkeypatch.setattr(main, "project_service", world.repo)
    allowed = {"value": True}
    monkeypatch.setattr(main, "check_permission", lambda db, user, project_id, module, action: allowed["value"])
    http = TestClient(main.app)
    user_id = http.get("/api/v1/auth/me").json().get("id")
    return http, main, allowed, user_id


def test_routes_confirm_cancel_and_status(client, world, monkeypatch):
    from uuid import UUID

    http, main, allowed, user_id = client
    world.user_id = UUID(user_id)
    svc, message, _ = world.propose_card()
    monkeypatch.setattr(main, "chat_service", svc)
    base = f"/api/v1/projects/{world.project.id}/chat/messages/{message.id}/action"

    first = http.get(base)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "proposed"
    allowed["value"] = False
    denied = http.post(f"{base}/confirm", json={})
    assert denied.status_code == 403 and denied.json()["detail"] == "You no longer have permission"

    allowed["value"] = True
    confirmed = http.post(f"{base}/confirm", json={"params": {"count": 9}})
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "succeeded"
    assert world.workflow.calls[0][2] == 3, "browser params are ignored"
    assert http.post(f"{base}/confirm", json={}).status_code == 409
    assert http.post(f"{base}/cancel").status_code == 409
    assert http.get(f"/api/v1/projects/{world.project.id}/chat/messages/{uuid4()}/action").status_code == 404


def test_message_route_offers_only_permitted_actions(client, world, monkeypatch):
    http, main, allowed, _ = client
    svc = world.service([["Hi"]])
    monkeypatch.setattr(main, "chat_service", svc)
    allowed["value"] = False
    response = http.post(f"/api/v1/projects/{world.project.id}/chat/messages", json={"content": "generate"})
    assert response.status_code == 200
    names = [t["function"]["name"] for t in svc.llm.calls[0]["tools"]]
    assert not any(name.startswith("propose_") for name in names)
