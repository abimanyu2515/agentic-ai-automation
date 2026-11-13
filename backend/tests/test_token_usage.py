"""
Token Utilization: LLM usage capture, pricing, storage, aggregation and API.
No live LLM: completion is patched to return canned responses with usage.
"""

import contextvars
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from backend.config.settings import LLMConfig
from backend.models.token_usage import TokenUsageFilters, TokenUsageRecord
from backend.repository.token_usage_store import JSONTokenUsageStore
from backend.schemas.token_usage_dto import ModelPriceOverride, TokenUsageSettings
from backend.services.llm import LLMService, LLMServiceError
from backend.services.llm_context import llm_call_context, llm_operation, run_in_context
from backend.services.token_pricing import price_usage, resolve_price

_CONFIG = LLMConfig(provider="openai", model="gpt-4o-mini", api_key="test-key")


def _response(content: str, prompt: int = 100, completion: int = 50):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion),
    )


class _Value(BaseModel):
    value: int


def _all(service) -> list[TokenUsageRecord]:
    return service.records(TokenUsageFilters())


def _record(**overrides) -> TokenUsageRecord:
    data = {
        "agent": "scenario_agent", "role": "analysis", "provider": "openai", "model": "gpt-4o-mini",
        "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
        "cost_usd": 0.01, "input_cost_usd": 0.004, "output_cost_usd": 0.006, "priced": True,
        "price_source": "litellm", "latency_ms": 200,
    }
    data.update(overrides)
    return TokenUsageRecord(**data)


# ── capture in LLMService ──────────────────────────────────────────────────

def test_generate_records_tokens_cost_and_agent(token_usage_service):
    service = LLMService(config=_CONFIG, agent="scenario_agent")
    with patch("backend.services.llm.completion", return_value=_response("hi", 1000, 500)):
        assert service.generate(system="s", user="u") == "hi"

    [record] = _all(token_usage_service)
    assert record.agent == "scenario_agent"
    assert record.provider == "openai" and record.model == "gpt-4o-mini"
    assert (record.prompt_tokens, record.completion_tokens, record.total_tokens) == (1000, 500, 1500)
    assert record.status == "success" and record.priced and record.price_source == "litellm"
    assert record.cost_usd == pytest.approx(record.input_cost_usd + record.output_cost_usd)
    assert record.cost_usd > 0


def test_each_structured_retry_is_recorded_with_its_attempt(token_usage_service):
    service = LLMService(config=_CONFIG, agent="test_case_agent")
    responses = [_response("not json"), _response('{"value": 3}')]
    with patch("backend.services.llm.completion", side_effect=responses), patch("time.sleep"):
        assert service.structured_generate(user="u", response_model=_Value, max_retries=2).value == 3

    records = _all(token_usage_service)
    assert [r.attempt for r in records] == [1, 2]
    assert all(r.status == "success" for r in records)


def test_failed_call_records_an_error_and_still_raises(token_usage_service):
    service = LLMService(config=_CONFIG, agent="report_agent")
    with patch("backend.services.llm.completion", side_effect=TimeoutError("slow")):
        with pytest.raises(LLMServiceError):
            service.generate(system="s", user="u")

    [record] = _all(token_usage_service)
    assert record.status == "error" and record.error_type == "TimeoutError"
    assert record.total_tokens == 0 and record.cost_usd == 0


def test_recorder_failure_never_breaks_the_llm_call(token_usage_service):
    service = LLMService(config=_CONFIG)
    with patch.object(token_usage_service.store, "add_many", side_effect=RuntimeError("disk full")), \
            patch("backend.services.llm.completion", return_value=_response("ok")):
        assert service.generate(system="s", user="u") == "ok"


def test_response_without_usage_records_zero_tokens(token_usage_service):
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])
    with patch("backend.services.llm.completion", return_value=response):
        LLMService(config=_CONFIG).generate(system="s", user="u")
    [record] = _all(token_usage_service)
    assert record.total_tokens == 0 and record.agent == "unattributed"


def test_disabled_recording_writes_nothing(token_usage_service, monkeypatch):
    from backend.config.settings import get_settings

    monkeypatch.setattr(get_settings().token_usage, "enabled", False)
    with patch("backend.services.llm.completion", return_value=_response("ok")):
        LLMService(config=_CONFIG).generate(system="s", user="u")
    assert _all(token_usage_service) == []


# ── attribution context ────────────────────────────────────────────────────

def test_context_supplies_project_run_and_overrides_agent(token_usage_service):
    project_id = uuid4()
    service = LLMService(config=_CONFIG, agent="playwright_agent")
    with llm_call_context(project_id=project_id, operation="generate_script", agent="script_generator_agent"):
        with patch("backend.services.llm.completion", return_value=_response("ok")):
            service.generate(system="s", user="u")
    [record] = _all(token_usage_service)
    assert record.project_id == project_id
    assert record.operation == "generate_script"
    assert record.agent == "script_generator_agent"


def test_llm_operation_decorator_reads_ids_from_arguments(token_usage_service):
    project_id, requirement_id = uuid4(), uuid4()

    class Workflow:
        @llm_operation("generate_scenarios")
        def run(self, project_id, requirement_id):
            with patch("backend.services.llm.completion", return_value=_response("ok")):
                LLMService(config=_CONFIG, agent="scenario_agent").generate(system="s", user="u")

    Workflow().run(project_id, requirement_id=requirement_id)
    [record] = _all(token_usage_service)
    assert record.project_id == project_id and record.requirement_id == requirement_id
    assert record.operation == "generate_scenarios" and record.run_id is not None


def test_run_in_context_carries_attribution_into_thread_pools(token_usage_service):
    project_id = uuid4()

    def call(_):
        LLMService(config=_CONFIG, agent="test_case_agent").generate(system="s", user="u")

    with patch("backend.services.llm.completion", return_value=_response("ok")):
        with llm_call_context(project_id=project_id):
            with ThreadPoolExecutor(max_workers=3) as executor:
                list(executor.map(run_in_context(call), range(3)))
            # Without run_in_context the worker threads lose the context.
            with ThreadPoolExecutor(max_workers=1) as executor:
                list(executor.map(call, range(1)))

    records = _all(token_usage_service)
    assert [r.project_id for r in records].count(project_id) == 3
    assert [r.project_id for r in records].count(None) == 1


def test_middleware_attributes_project_from_url():
    from backend.services.llm_context import LLMContextMiddleware, current_llm_context

    seen = {}

    async def inner(scope, receive, send):
        seen.update(current_llm_context())

    import asyncio

    project_id = uuid4()
    middleware = LLMContextMiddleware(inner)
    scope = {"type": "http", "method": "POST", "path": f"/api/v1/projects/{project_id}/requirements/{uuid4()}/scenarios"}
    asyncio.run(middleware(scope, None, None))
    assert seen["project_id"] == str(project_id)
    assert seen["operation"] == "POST /projects/{id}/requirements/{id}/scenarios"


# ── pricing ────────────────────────────────────────────────────────────────

def test_override_wins_over_litellm():
    price = resolve_price(
        provider="openai", model="gpt-4o-mini", model_string="gpt-4o-mini",
        overrides={"gpt-4o-mini": {"input_per_1m": 1.0, "output_per_1m": 2.0}},
    )
    assert price.source == "override"
    priced = price_usage(price, 1_000_000, 500_000)
    assert priced.cost_usd == pytest.approx(2.0)


def test_unknown_model_is_unpriced():
    price = resolve_price(provider="nvidia", model="acme/not-a-model", model_string="openai/acme/not-a-model")
    assert price.source == "none" and not price.priced
    assert price_usage(price, 1000, 1000).cost_usd == 0


def test_ui_override_is_applied_to_new_records(token_usage_service):
    token_usage_service.update_settings(TokenUsageSettings(
        pricing_overrides={"gpt-4o-mini": ModelPriceOverride(input_per_1m=10, output_per_1m=20)}
    ))
    with patch("backend.services.llm.completion", return_value=_response("ok", 1_000_000, 1_000_000)):
        LLMService(config=_CONFIG).generate(system="s", user="u")
    [record] = _all(token_usage_service)
    assert record.price_source == "override" and record.cost_usd == pytest.approx(30.0)


# ── stores and aggregation ─────────────────────────────────────────────────

def test_json_store_appends_filters_and_skips_corrupt_lines(tmp_path):
    store = JSONTokenUsageStore(tmp_path / "usage.jsonl")
    old = _record(created_at=datetime.now(timezone.utc) - timedelta(days=10))
    new = _record(agent="test_data_agent", model="gemini-2.5-flash")
    store.add_many([old])
    store.add_many([new])
    with open(store.path, "a", encoding="utf-8") as handle:
        handle.write("{broken\n")

    assert [r.id for r in store.query(TokenUsageFilters())] == [old.id, new.id]
    recent = store.query(TokenUsageFilters(start=datetime.now(timezone.utc) - timedelta(days=1)))
    assert [r.id for r in recent] == [new.id]
    assert [r.id for r in store.query(TokenUsageFilters(agent="test_data_agent"))] == [new.id]


def test_breakdown_by_agent_with_shares_and_stage(token_usage_service):
    token_usage_service.store.add_many([
        _record(agent="scenario_agent", cost_usd=0.03),
        _record(agent="scenario_agent", cost_usd=0.03, status="error", attempt=2),
        _record(agent="test_data_agent", cost_usd=0.04, role="generation", model="gemini-2.5-flash"),
    ])
    rows = {r["key"]: r for r in token_usage_service.breakdown(TokenUsageFilters(), "agent")["rows"]}
    assert rows["scenario_agent"]["calls"] == 2
    assert rows["scenario_agent"]["errors"] == 1 and rows["scenario_agent"]["retries"] == 1
    assert rows["scenario_agent"]["cost_share_pct"] == pytest.approx(60.0)
    assert rows["scenario_agent"]["stage"] == "Scenarios"
    assert rows["test_data_agent"]["stage"] == "Test data"
    assert rows["test_data_agent"]["models"] == ["gemini-2.5-flash"]

    with pytest.raises(ValueError):
        token_usage_service.breakdown(TokenUsageFilters(), "nonsense")


def test_summary_compares_with_previous_period(token_usage_service):
    now = datetime.now(timezone.utc)
    token_usage_service.store.add_many([
        _record(created_at=now - timedelta(days=10), cost_usd=0.01),
        _record(created_at=now - timedelta(days=2), cost_usd=0.02),
        _record(created_at=now - timedelta(days=1), cost_usd=0.02, priced=False, model="mystery"),
    ])
    summary = token_usage_service.summary(TokenUsageFilters(start=now - timedelta(days=7), end=now))
    assert summary["calls"] == 2
    assert summary["previous"]["calls"] == 1
    assert summary["deltas"]["cost_usd"] == pytest.approx(300.0)
    assert summary["unpriced_models"] == ["mystery"]


def test_budget_warns_when_projection_exceeds_budget(token_usage_service):
    token_usage_service.update_settings(TokenUsageSettings(monthly_budget_usd=1.0, alert_threshold_pct=80))
    token_usage_service.store.add_many([_record(cost_usd=0.9)])
    budget = token_usage_service.budget_status()
    assert budget["month_to_date_usd"] == pytest.approx(0.9)
    assert budget["used_pct"] == pytest.approx(90.0)
    assert budget["state"] == "warning"

    token_usage_service.store.add_many([_record(cost_usd=0.2)])
    assert token_usage_service.budget_status()["state"] == "exceeded"


def test_timeseries_buckets_and_split(token_usage_service):
    now = datetime.now(timezone.utc)
    token_usage_service.store.add_many([
        _record(created_at=now - timedelta(days=2), agent="scenario_agent", total_tokens=10),
        _record(created_at=now, agent="test_data_agent", total_tokens=20),
    ])
    result = token_usage_service.timeseries(
        TokenUsageFilters(start=now - timedelta(days=3), end=now + timedelta(seconds=1)),
        bucket="day", split_by="agent",
    )
    assert len(result["labels"]) in (4, 5)
    series = {s["key"]: s for s in result["series"]}
    assert sum(series["scenario_agent"]["tokens"]) == 10
    assert sum(series["test_data_agent"]["tokens"]) == 20
    assert series["test_data_agent"]["tokens"][-1] == 20


def test_calls_paginate_and_sort(token_usage_service):
    token_usage_service.store.add_many([_record(cost_usd=c) for c in (0.1, 0.3, 0.2)])
    page = token_usage_service.calls(TokenUsageFilters(), page=1, page_size=2, sort="-cost_usd")
    assert page["total"] == 3
    assert [i["cost_usd"] for i in page["items"]] == [0.3, 0.2]
    with pytest.raises(ValueError):
        token_usage_service.calls(TokenUsageFilters(), sort="password")


# ── API ────────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from backend.dependencies.auth_dependencies import get_current_user, get_db
    from backend.main import app

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role="Superadmin", id=uuid4())
    app.dependency_overrides[get_db] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_summary_breakdown_calls_and_csv(client, token_usage_service):
    project_id = uuid4()
    token_usage_service.store.add_many([
        _record(project_id=project_id, agent="scenario_agent"),
        _record(agent="test_data_agent"),
    ])

    summary = client.get("/api/v1/token-usage/summary", params={"project_id": str(project_id)}).json()
    assert summary["calls"] == 1 and summary["total_tokens"] == 150

    breakdown = client.get("/api/v1/token-usage/breakdown", params={"group_by": "agent"}).json()
    assert {r["key"] for r in breakdown["rows"]} == {"scenario_agent", "test_data_agent"}
    assert client.get("/api/v1/token-usage/breakdown", params={"group_by": "bad"}).status_code == 400

    calls = client.get("/api/v1/token-usage/calls", params={"agent": "test_data_agent"}).json()
    assert calls["total"] == 1

    facets = client.get("/api/v1/token-usage/facets").json()
    assert facets["agent"] == ["scenario_agent", "test_data_agent"]

    ts = client.get("/api/v1/token-usage/timeseries", params={"bucket": "hour", "split_by": "model"})
    assert ts.status_code == 200

    csv = client.get("/api/v1/token-usage/export.csv")
    assert csv.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv.headers["content-disposition"]
    lines = csv.text.strip().splitlines()
    assert lines[0].startswith("id,created_at,agent") and len(lines) == 3


def test_api_date_filter_accepts_naive_iso(client, token_usage_service):
    token_usage_service.store.add_many([_record(created_at=datetime.now(timezone.utc) - timedelta(days=30))])
    since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    assert client.get("/api/v1/token-usage/summary", params={"from": since}).json()["calls"] == 0


def test_api_settings_round_trip_and_validation(client):
    payload = {
        "monthly_budget_usd": 25,
        "alert_threshold_pct": 75,
        "pricing_overrides": {"meta/llama-3.1-70b-instruct": {"input_per_1m": 0.5, "output_per_1m": 0.8}},
    }
    assert client.put("/api/v1/token-usage/settings", json=payload).status_code == 200
    saved = client.get("/api/v1/token-usage/settings").json()
    assert saved["monthly_budget_usd"] == 25
    assert saved["pricing_overrides"]["meta/llama-3.1-70b-instruct"]["input_per_1m"] == 0.5
    assert client.put("/api/v1/token-usage/settings", json={"alert_threshold_pct": 150}).status_code == 422

    pricing = client.get("/api/v1/token-usage/pricing").json()
    assert all({"model", "source", "input_per_1m"} <= set(row) for row in pricing)


def test_traces_route_reports_real_usage(client, token_usage_service):
    project_id = uuid4()
    token_usage_service.store.add_many([_record(project_id=project_id, prompt_tokens=7, completion_tokens=3, total_tokens=10)])
    body = client.get(f"/api/v1/projects/{project_id}/traces").json()
    assert body["input_tokens"] == 7 and body["output_tokens"] == 3 and body["total_calls"] == 1
    assert body["traces"][0]["agent"] == "scenario_agent"
