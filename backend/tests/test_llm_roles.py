"""
Role based LLM routing (spec 0003): role config parsing, which role each
caller uses, and the shared role pool (rotation, cap, pacing, 429 cooldown,
max wait). No live provider: completion is patched with fakes, and the pool
gets a fake clock wherever timing matters.
"""

import threading
import time
from unittest.mock import Mock, patch

import httpx
import pytest
from litellm.exceptions import RateLimitError
from pydantic import BaseModel, ValidationError

from backend.config.settings import LLMConfig, LLMRoleConfig, Settings, _build_llm_roles
from backend.services import llm as llm_module
from backend.services.llm import LLMRateLimitTimeout, LLMService, reset_role_pools

_ROLE_ENV_KEYS = [
    f"{role}_LLM_{name}"
    for role in ("GENERATION", "ANALYSIS")
    for name in (
        "PROVIDER", "MODELS", "MODEL", "API_KEY", "API_BASE", "MAX_CONCURRENT",
        "MIN_INTERVAL_SECONDS", "COOLDOWN_SECONDS", "MAX_WAIT_SECONDS",
    )
]


class _Response:
    def __init__(self, content: str):
        self.choices = [Mock(message=Mock(content=content), finish_reason="stop")]


class _Value(BaseModel):
    value: str


class FakeClock:
    """Pool clock whose wait() simply jumps time forward."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def wait(self, cond, timeout: float) -> None:
        self.now += timeout


def _rate_limit(retry_after: str | None = None, message: str = "quota exceeded") -> RateLimitError:
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    response = httpx.Response(429, headers=headers, request=httpx.Request("POST", "http://llm.test"))
    return RateLimitError(message=message, llm_provider="gemini", model="m", response=response)


def _settings(**role_overrides) -> Settings:
    generation = {"provider": "gemini", "models": ["A", "B"], "api_key": "g-key", "max_concurrent": 2}
    analysis = {"provider": "nvidia", "models": ["N"], "api_key": "n-key", "max_concurrent": 2}
    generation.update(role_overrides.pop("generation", {}))
    analysis.update(role_overrides.pop("analysis", {}))
    return Settings(
        llm=LLMConfig(provider="nvidia", model="legacy", api_key="legacy-key"),
        llm_roles={"generation": LLMRoleConfig(**generation), "analysis": LLMRoleConfig(**analysis)},
    )


@pytest.fixture
def role_settings():
    """Patch llm.py's settings; call the returned function to swap them later."""
    current = {"settings": _settings()}

    def use(settings: Settings) -> None:
        current["settings"] = settings

    with patch.object(llm_module, "get_settings", lambda: current["settings"]):
        reset_role_pools()
        yield use
    reset_role_pools()


@pytest.fixture
def clear_role_env(monkeypatch):
    for key in _ROLE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


# Config parsing (AC-9)

def test_models_list_is_trimmed_deduplicated_and_blanks_dropped(clear_role_env):
    clear_role_env.setenv("GENERATION_LLM_MODELS", " a, ,b,a ")
    roles = _build_llm_roles(LLMConfig(provider="gemini", model="x"))
    assert roles["generation"].models == ["a", "b"]


def test_only_llm_keys_send_both_roles_to_the_legacy_provider(clear_role_env):
    legacy = LLMConfig(provider="nvidia", model="legacy", api_key="k", api_base="https://nv.test/v1")
    roles = _build_llm_roles(legacy)
    for role in roles.values():
        assert (role.provider, role.models, role.api_key, role.api_base) == ("nvidia", ["legacy"], "k", "https://nv.test/v1")
        assert (role.max_concurrent, role.cooldown_seconds, role.max_wait_seconds) == (2, 60, 180)
    assert roles["analysis"].min_interval_seconds == 0


def test_blank_role_values_count_as_unset(clear_role_env):
    clear_role_env.setenv("ANALYSIS_LLM_MODEL", "  ")
    clear_role_env.setenv("ANALYSIS_LLM_API_KEY", "")
    roles = _build_llm_roles(LLMConfig(provider="nvidia", model="legacy", api_key="k"))
    assert roles["analysis"].models == ["legacy"]
    assert roles["analysis"].api_key == "k"


def test_api_base_is_not_inherited_by_a_different_provider(clear_role_env):
    clear_role_env.setenv("GENERATION_LLM_PROVIDER", "gemini")
    legacy = LLMConfig(provider="nvidia", model="legacy", api_base="https://integrate.api.nvidia.com/v1")
    roles = _build_llm_roles(legacy)
    assert roles["generation"].api_base is None
    assert roles["analysis"].api_base == "https://integrate.api.nvidia.com/v1"


@pytest.mark.parametrize("key, value", [
    ("GENERATION_LLM_MAX_CONCURRENT", "0"),
    ("GENERATION_LLM_MIN_INTERVAL_SECONDS", "-1"),
    ("ANALYSIS_LLM_COOLDOWN_SECONDS", "-5"),
    ("ANALYSIS_LLM_MAX_WAIT_SECONDS", "0"),
])
def test_invalid_limits_fail_at_settings_load(clear_role_env, key, value):
    clear_role_env.setenv(key, value)
    with pytest.raises(ValidationError):
        _build_llm_roles(LLMConfig())


# Which role each caller uses (AC-1, AC-2, AC-12)

def test_test_data_agent_defaults_to_the_generation_provider(role_settings):
    from backend.agents.test_data_agent import TestDataAgent

    agent = TestDataAgent()
    with patch.object(llm_module, "completion", return_value=_Response("hi")) as fake:
        agent.llm_service.generate(system="s", user="u")
    assert agent.llm_service.role == "generation"
    assert fake.call_args.kwargs["model"] == "gemini/A"
    assert fake.call_args.kwargs["api_key"] == "g-key"
    assert fake.call_args.kwargs["api_base"] is None


def test_playwright_agent_defaults_to_generation(role_settings):
    from backend.agents.playwright_agent import PlaywrightAgent

    assert PlaywrightAgent().llm_service.role == "generation"


@pytest.mark.parametrize("module, name", [
    ("requirement_analyst_agent", "RequirementAnalystAgent"),
    ("backlog_creation_agent", "BacklogCreationAgent"),
    ("qa_story_analyzer_agent", "QAStoryAnalyzerAgent"),
    ("scenario_agent", "ScenarioAgent"),
    ("test_case_agent", "TestCaseAgent"),
    ("evaluation_agent", "EvaluationAgent"),
    ("automation_orchestrator_agent", "QAAutomationOrchestratorAgent"),
    ("execution_analysis_agent", "ExecutionAnalysisAgent"),
    ("defect_management_agent", "DefectManagementAgent"),
])
def test_analysis_agents_default_to_analysis(role_settings, module, name):
    import importlib

    agent_class = getattr(importlib.import_module(f"backend.agents.{module}"), name)
    assert agent_class().llm_service.role == "analysis"


def test_execution_analyzer_defaults_to_analysis(role_settings):
    from backend.services.execution_analyzer import ExecutionAnalyzer

    assert ExecutionAnalyzer().llm_service.role == "analysis"


def test_bare_service_is_analysis_and_explicit_config_skips_roles(role_settings):
    assert LLMService().role == "analysis"
    explicit = LLMService(config=LLMConfig(provider="openai", model="gpt", api_key="k"))
    assert explicit.role is None
    with patch.object(llm_module, "completion", return_value=_Response("hi")) as fake:
        explicit.generate(system="s", user="u")
    assert "num_retries" not in fake.call_args.kwargs
    assert "metadata" not in fake.call_args.kwargs


def test_role_and_config_together_or_unknown_role_raise():
    with pytest.raises(ValueError):
        LLMService(config=LLMConfig(), role="generation")
    with pytest.raises(ValueError):
        LLMService(role="summarize")


# Pool behavior (AC-3 to AC-8, AC-11, AC-13)

def test_generation_rotates_across_instances_and_skips_cooling_models(role_settings):
    models = []

    def fake(**kwargs):
        models.append(kwargs["model"])
        return _Response("ok")

    with patch.object(llm_module, "completion", side_effect=fake):
        for _ in range(4):
            LLMService(role="generation").generate(system="s", user="u")
        assert models == ["gemini/A", "gemini/B", "gemini/A", "gemini/B"]

        llm_module._get_pool("generation").report_429("A", 300)
        models.clear()
        for _ in range(2):
            LLMService(role="generation").generate(system="s", user="u")
        assert models == ["gemini/B", "gemini/B"]


def test_pool_calls_disable_hidden_retries_and_carry_role_metadata(role_settings, caplog):
    with patch.object(llm_module, "completion", return_value=_Response("ok")) as fake, caplog.at_level("INFO"):
        LLMService(role="generation").generate(
            system="s", user="u", metadata={"run_name": "x", "tags": ["mine"]}, num_retries=5,
        )
    kwargs = fake.call_args.kwargs
    assert kwargs["num_retries"] == 0
    assert kwargs["metadata"] == {
        "run_name": "x", "llm_role": "generation", "llm_model": "A", "tags": ["mine", "generation", "A"],
    }
    assert any("role=generation model=A queue_wait=" in r.getMessage() for r in caplog.records)


def test_cap_holds_the_third_call_until_a_slot_frees(role_settings):
    release_first = threading.Event()
    started = []
    lock = threading.Lock()

    def fake(**kwargs):
        with lock:
            started.append(kwargs["model"])
            index = len(started)
        if index <= 2:
            release_first.wait(5)
        return _Response("ok")

    with patch.object(llm_module, "completion", side_effect=fake):
        threads = [
            threading.Thread(target=LLMService(role="generation").generate, kwargs={"system": "s", "user": "u"})
            for _ in range(3)
        ]
        for thread in threads:
            thread.start()
        time.sleep(0.3)
        assert len(started) == 2
        release_first.set()
        for thread in threads:
            thread.join(5)
    assert len(started) == 3


def test_calls_to_one_model_are_paced(role_settings):
    role_settings(_settings(generation={"models": ["A"], "min_interval_seconds": 10}))
    clock = FakeClock()
    reset_role_pools(clock=clock, wait=clock.wait)
    starts = []

    def fake(**kwargs):
        starts.append(clock.now)
        return _Response("ok")

    with patch.object(llm_module, "completion", side_effect=fake):
        LLMService(role="generation").generate(system="s", user="u")
        LLMService(role="generation").generate(system="s", user="u")
    assert starts[1] - starts[0] >= 10


def test_429_fails_over_without_using_retries_and_honors_retry_after(role_settings):
    clock = FakeClock()
    reset_role_pools(clock=clock, wait=clock.wait)
    calls = []

    def fake(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "gemini/A":
            raise _rate_limit(retry_after="30")
        return _Response('{"value": "ok"}')

    with patch.object(llm_module, "completion", side_effect=fake):
        result = LLMService(role="generation").structured_generate(user="u", response_model=_Value, max_retries=1)
        assert result.value == "ok"
        assert calls == ["gemini/A", "gemini/B"]
        pool = llm_module._get_pool("generation")
        assert pool._cooldown_until["A"] == clock.now + 30


def test_every_model_limited_raises_after_max_wait(role_settings):
    role_settings(_settings(generation={"max_wait_seconds": 50, "cooldown_seconds": 20}))
    clock = FakeClock()
    reset_role_pools(clock=clock, wait=clock.wait)
    start = clock.now

    with patch.object(llm_module, "completion", side_effect=lambda **_: (_ for _ in ()).throw(_rate_limit())):
        with pytest.raises(LLMRateLimitTimeout, match=r"generation LLM rate limited: models \['A', 'B'\]"):
            LLMService(role="generation").structured_generate(user="u", response_model=_Value, max_retries=3)
    assert clock.now - start <= 50 + 1


def test_analysis_role_waits_out_a_429_on_its_single_model(role_settings):
    clock = FakeClock()
    reset_role_pools(clock=clock, wait=clock.wait)
    outcomes = iter([_rate_limit(message='{"retryDelay": "15s"}'), _Response("ok")])
    start = clock.now

    def fake(**kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    with patch.object(llm_module, "completion", side_effect=fake):
        assert LLMService().generate(system="s", user="u") == "ok"
    assert clock.now - start == pytest.approx(15)


def test_cooldown_seconds_sources_and_clamp():
    assert llm_module._cooldown_seconds(_rate_limit(retry_after="30"), 60) == 30
    assert llm_module._cooldown_seconds(_rate_limit(retry_after="Wed, 21 Oct 2026 07:28:00 GMT"), 60) == 60
    assert llm_module._cooldown_seconds(_rate_limit(message="Please retry in 12.5s."), 60) == 12.5
    assert llm_module._cooldown_seconds(_rate_limit(retry_after="99999"), 60) == 3600


def test_settings_reload_keeps_in_flight_count_and_cooldowns(role_settings):
    pool = llm_module._get_pool("generation")
    pool.acquire(pool.now() + 1)
    pool.report_429("A", 300)

    role_settings(_settings(generation={"max_concurrent": 1}))
    with pytest.raises(llm_module._PoolWaitExceeded):
        pool.acquire(pool.now() + 0.05)
    pool.release()
    model, _ = pool.acquire(pool.now() + 1)
    assert model == "B"


# Settings API (AC-10)

def test_settings_endpoint_returns_roles_without_keys():
    from fastapi.testclient import TestClient

    from backend.main import app

    body = TestClient(app).get("/api/v1/settings").json()
    assert set(body["llm_roles"]) == {"generation", "analysis"}
    for role in body["llm_roles"].values():
        assert set(role) == {
            "provider", "models", "max_concurrent", "min_interval_seconds", "cooldown_seconds", "max_wait_seconds",
        }
    assert "api_key" not in body["llm"]
