"""
The only module allowed to import litellm. Agents call LLMService, never
litellm directly - that's what keeps ScenarioAgent's code identical
whether it's talking to OpenAI, Ollama, or Groq.

structured_generate() forwards **kwargs straight to litellm.completion(),
which is deliberate: it means tests can pass mock_response=... through
the real public method and exercise this exact code path, with no
monkeypatching and no real API key. See test_scenario_agent.py.

Role routing (spec 0003): LLMService() uses the "analysis" role and
LLMService(role="generation") the "generation" role. Every role call goes
through that role's shared _RolePool, which rotates models, caps concurrent
calls, paces calls per model and cools a model down after a 429.
LLMService(config=...) skips all of that.

Token usage (Token Utilization page): every completion attempt, successful
or not, is reported to token_usage_service.record_llm_call with its tokens,
latency and the agent from LLMService(agent=...) or the llm_context. Recording
never raises and never blocks the call.
"""

import logging
import re
import threading
import time
from typing import Callable, TypeVar

import litellm
from litellm import completion
from litellm.exceptions import RateLimitError
from pydantic import BaseModel

from backend.config.settings import LLM_ROLES, LLMConfig, LLMRoleConfig, get_settings
from backend.services.token_usage_service import record_llm_call

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Providers whose litellm model string needs a prefix beyond the bare model
# name. OpenAI needs none ("gpt-4o-mini" as-is); others route through a
# provider/ prefix. Extend this as you actually add providers - don't
# pre-populate ones you're not using yet.
_PROVIDER_PREFIXES: dict[str, str] = {
    "openai": "",
    "ollama": "ollama/",
    "groq": "groq/",
    "nvidia": "openai/",
    "anthropic": "anthropic/",
    "gemini": "gemini/",
}

_DEFAULT_API_BASES: dict[str, str] = {
    "nvidia": "https://integrate.api.nvidia.com/v1",
}

# How each provider is told "respond with JSON". These are NOT
# interchangeable: OpenAI (and OpenAI-compatible providers like Groq) use
# response_format={"type": "json_object"}; Ollama's own JSON mode is a
# different parameter (format="json") per litellm's Ollama docs. Passing
# the wrong one for a provider doesn't error - it just gets ignored, and
# you silently lose JSON enforcement while still passing tests that don't
# actually hit that provider. Add a case here before using a new provider,
# don't assume the OpenAI-style kwarg works everywhere.
_JSON_MODE_KWARGS: dict[str, dict] = {
    "ollama": {"format": "json"},
    "nvidia": {"response_format": {"type": "json_object"}},
}
_DEFAULT_JSON_MODE_KWARGS = {"response_format": {"type": "json_object"}}

DEFAULT_STRUCTURED_SYSTEM_PROMPT = (
    "You respond with a single valid JSON object and nothing else - "
    "no markdown fences, no commentary before or after it."
)

# Bounds a single LLM HTTP call so a misconfigured provider/key cannot hang
# the API worker (and the UI spinner) forever. Callers may override per call.
DEFAULT_LLM_TIMEOUT = 60

# Cloud providers that require an API key. Ollama is local and needs api_base instead.
_CLOUD_PROVIDERS_REQUIRING_KEY = {"openai", "groq", "nvidia", "anthropic", "gemini"}


def model_string_for(provider: str, model: str) -> str:
    """The litellm model string for a provider and bare model name."""
    return f"{_PROVIDER_PREFIXES.get(provider, '')}{model}"


def lookup_litellm_price(model: str) -> tuple[float, float] | None:
    """(input, output) USD per token from litellm's price map, or None if unknown.
    Lives here so llm.py stays the only module that imports litellm."""
    info = (getattr(litellm, "model_cost", None) or {}).get(model)
    if not info:
        return None
    per_in = info.get("input_cost_per_token")
    per_out = info.get("output_cost_per_token")
    if per_in is None and per_out is None:
        return None
    return float(per_in or 0.0), float(per_out or 0.0)


def _usage_counts(response) -> tuple[int, int, int | None]:
    """(prompt, completion, total) tokens from a litellm response; zeros when absent."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return 0, 0, None

    def read(name: str) -> int:
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    total = read("total_tokens")
    return read("prompt_tokens"), read("completion_tokens"), total or None


class LLMServiceError(Exception):
    """Raised when a completion call fails or returns something that
    doesn't validate against the expected schema. Agents catch this, not
    litellm's own exception types - callers shouldn't need to know which
    provider SDK is underneath."""


class LLMRateLimitTimeout(LLMServiceError):
    """Every model of a role stayed unavailable past the role's max wait.
    Not retried by structured_generate: the wait budget is already spent."""


# A 429 cooldown is clamped to this range whatever the provider asks for.
_MAX_COOLDOWN_SECONDS = 3600.0


class _PoolWaitExceeded(Exception):
    pass


class _RolePool:
    """Shared state for one role, across every LLMService instance and thread.

    One threading.Condition guards everything (callers run in thread pools
    and asyncio.to_thread, so never asyncio primitives). Limits come from
    get_settings() on every acquire, so a settings reload changes them in
    place while in-flight counts and cooldowns carry over.
    """

    def __init__(
        self,
        role: str,
        clock: Callable[[], float] = time.monotonic,
        wait: Callable[[threading.Condition, float], None] | None = None,
    ):
        self.role = role
        self._clock = clock
        self._wait = wait or (lambda cond, timeout: cond.wait(timeout))
        self._cond = threading.Condition()
        self._in_flight = 0
        self._cursor = 0
        self._next_allowed_at: dict[str, float] = {}
        self._cooldown_until: dict[str, float] = {}

    def config(self) -> LLMRoleConfig:
        return get_settings().llm_roles[self.role]

    def now(self) -> float:
        return self._clock()

    def _ready_at(self, model: str) -> float:
        return max(self._next_allowed_at.get(model, 0.0), self._cooldown_until.get(model, 0.0))

    def acquire(self, deadline: float) -> tuple[str, LLMRoleConfig]:
        """Block until a slot is free and some model is ready, then take both.
        Picks the first ready model at or after the cursor (strict list order,
        skipping models that are cooling down or pacing)."""
        with self._cond:
            while True:
                config = self.config()
                models = config.models
                now = self._clock()
                wake_at = deadline
                if self._in_flight < config.max_concurrent:
                    start = self._cursor % len(models)
                    for offset in range(len(models)):
                        index = (start + offset) % len(models)
                        model = models[index]
                        if self._ready_at(model) <= now:
                            self._cursor = index + 1
                            self._in_flight += 1
                            self._next_allowed_at[model] = now + config.min_interval_seconds
                            return model, config
                    wake_at = min(min(self._ready_at(m) for m in models), deadline)
                # A full pool wakes on release(); a cooling pool wakes when the
                # earliest model is ready. Either way, never past the deadline.
                if now >= deadline:
                    raise _PoolWaitExceeded()
                self._wait(self._cond, max(wake_at - now, 0.0))

    def release(self) -> None:
        with self._cond:
            self._in_flight = max(self._in_flight - 1, 0)
            self._cond.notify_all()

    def report_429(self, model: str, seconds: float) -> None:
        with self._cond:
            until = self._clock() + seconds
            self._cooldown_until[model] = max(self._cooldown_until.get(model, 0.0), until)
            self._cond.notify_all()


_pools: dict[str, _RolePool] = {}
_pools_lock = threading.Lock()


def _get_pool(role: str) -> _RolePool:
    with _pools_lock:
        if role not in _pools:
            _pools[role] = _RolePool(role)
        return _pools[role]


def reset_role_pools(**pool_kwargs) -> None:
    """Drop all pool state. Tests use this, optionally passing clock= and wait=."""
    with _pools_lock:
        _pools.clear()
        for role in LLM_ROLES:
            _pools[role] = _RolePool(role, **pool_kwargs)


def _cooldown_seconds(exc: Exception, default: float) -> float:
    """Retry-After header (whole seconds), then Gemini's retryDelay or
    "retry in N s" in the message, then the role default; clamped to 0..3600."""
    seconds: float | None = None
    response = getattr(exc, "response", None)
    for headers in (getattr(exc, "headers", None), getattr(response, "headers", None)):
        if not headers:
            continue
        for key, value in headers.items():
            if str(key).lower() == "retry-after" and str(value).strip().isdigit():
                seconds = float(str(value).strip())
                break
        if seconds is not None:
            break
    if seconds is None:
        message = str(exc)
        match = re.search(r"retryDelay\W*(\d+(?:\.\d+)?)s", message) or re.search(
            r"retry in (\d+(?:\.\d+)?)\s*s", message, re.IGNORECASE
        )
        seconds = float(match.group(1)) if match else default
    return min(max(seconds, 0.0), _MAX_COOLDOWN_SECONDS)


class _WaitBudget:
    """One max wait for a whole generate/structured_generate call: time spent
    waiting in acquire counts, time spent inside completion does not."""

    def __init__(self, max_wait_seconds: float):
        self.remaining = max_wait_seconds
        self.waited = 0.0


class LLMService:
    def __init__(self, config: LLMConfig | None = None, role: str | None = None, agent: str | None = None):
        # Who the token usage of this service is attributed to (Token Utilization page).
        self.agent = agent
        if config is not None and role is not None:
            raise ValueError("Pass either config or role to LLMService, not both")
        if role is not None and role not in LLM_ROLES:
            raise ValueError(f"Unknown LLM role {role!r}; expected one of {LLM_ROLES}")

        # An explicit config skips role routing, queueing and pacing (AC-12).
        self.role = None if config is not None else (role or "analysis")
        if config is not None:
            self.config = config
        else:
            role_config = get_settings().llm_roles[self.role]
            self.config = self._config_for(role_config, role_config.models[0])

        # Enable LangSmith tracing for LiteLLM if configured
        self._init_langsmith_tracing()

    @staticmethod
    def _config_for(role_config: LLMRoleConfig, model: str) -> LLMConfig:
        return LLMConfig(
            provider=role_config.provider,
            model=model,
            api_key=role_config.api_key,
            api_base=role_config.api_base,
        )
    
    def _init_langsmith_tracing(self) -> None:
        """Initialize LangSmith tracing for LiteLLM if enabled.
        
        This integrates LiteLLM with LangSmith using the official integration.
        When enabled, all LLM calls will be traced to LangSmith automatically.
        
        Error handling: Any tracing setup failure is logged but never breaks LLM functionality.
        """
        try:
            settings = get_settings()
            if not settings.langsmith.tracing_enabled:
                return
            
            import litellm
            
            # Enable LangSmith callback (official LiteLLM integration)
            if "langsmith" not in (litellm.callbacks or []):
                litellm.callbacks = (litellm.callbacks or []) + ["langsmith"]
                logger.info("LiteLLM LangSmith tracing enabled")
                
        except Exception as e:
            # Tracing failure should never break LLM functionality
            logger.warning(f"Failed to enable LiteLLM LangSmith tracing: {e}")


    def _model_string(self, config: LLMConfig | None = None) -> str:
        config = config or self.config
        prefix = _PROVIDER_PREFIXES.get(config.provider, "")
        return f"{prefix}{config.model}"

    def _api_base(self, config: LLMConfig | None = None) -> str | None:
        config = config or self.config
        return config.api_base or _DEFAULT_API_BASES.get(config.provider)

    def _json_mode_kwargs(self, config: LLMConfig | None = None) -> dict:
        config = config or self.config
        return _JSON_MODE_KWARGS.get(config.provider, _DEFAULT_JSON_MODE_KWARGS)

    def _require_valid_config(self) -> None:
        """Fail fast on misconfigured provider instead of hanging in litellm retries."""
        config = self.config
        if self.role is not None:
            role_config = get_settings().llm_roles[self.role]
            config = self._config_for(role_config, role_config.models[0])
        key_var = f"{self.role.upper()}_LLM_API_KEY or LLM_API_KEY" if self.role else "LLM_API_KEY"
        base_var = f"{self.role.upper()}_LLM_API_BASE or LLM_API_BASE" if self.role else "LLM_API_BASE"
        provider = (config.provider or "").lower()
        if provider in _CLOUD_PROVIDERS_REQUIRING_KEY and not config.api_key:
            raise LLMServiceError(
                f"LLM misconfigured ({provider}/{config.model}): missing API key. "
                f"Set {key_var} or configure it in Master Settings > General Settings."
            )
        if provider == "ollama" and not config.api_base:
            raise LLMServiceError(
                "LLM misconfigured (ollama): missing api_base. "
                f"Set {base_var} (e.g. http://localhost:11434) or switch provider."
            )

    def _record_usage(
        self,
        config: LLMConfig,
        started: float,
        *,
        response=None,
        status: str = "success",
        error: Exception | None = None,
        attempt: int = 1,
    ) -> None:
        prompt_tokens, completion_tokens, total_tokens = (
            _usage_counts(response) if response is not None else (0, 0, None)
        )
        record_llm_call(
            agent=self.agent,
            role=self.role,
            provider=config.provider,
            model=config.model,
            model_string=self._model_string(config),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=int((time.perf_counter() - started) * 1000),
            status=status,
            error_type=type(error).__name__ if error is not None else None,
            attempt=attempt,
        )

    def _complete(
        self,
        *,
        messages: list[dict],
        timeout: int,
        json_mode: bool,
        budget: _WaitBudget | None,
        attempt: int = 1,
        **kwargs,
    ):
        """One completion. A role call goes through its pool and recovers from
        429s by cooling the model down and moving on, without using up the
        caller's retries. Any other failure is wrapped into LLMServiceError.
        Every attempt is reported for token usage."""
        if self.role is None:
            started = time.perf_counter()
            try:
                response = completion(
                    model=self._model_string(),
                    messages=messages,
                    api_base=self._api_base(),
                    api_key=self.config.api_key,
                    timeout=timeout,
                    **(self._json_mode_kwargs() if json_mode else {}),
                    **kwargs,
                )
            except Exception as exc:
                self._record_usage(self.config, started, status="error", error=exc, attempt=attempt)
                raise LLMServiceError(f"LLM call failed ({self.config.provider}/{self.config.model}): {exc}") from exc
            self._record_usage(self.config, started, response=response, attempt=attempt)
            return response

        pool = _get_pool(self.role)
        caller_metadata = dict(kwargs.pop("metadata", None) or {})
        caller_tags = list(caller_metadata.get("tags") or [])
        kwargs.pop("num_retries", None)
        while True:
            start = pool.now()
            try:
                model, role_config = pool.acquire(start + budget.remaining)
            except _PoolWaitExceeded:
                budget.waited += pool.now() - start
                models = get_settings().llm_roles[self.role].models
                raise LLMRateLimitTimeout(
                    f"{self.role} LLM rate limited: models {models} unavailable "
                    f"after {budget.waited:.0f}s"
                ) from None
            waited = pool.now() - start
            budget.waited += waited
            budget.remaining = max(budget.remaining - waited, 0.0)

            config = self._config_for(role_config, model)
            logger.info(
                "LLM call role=%s model=%s queue_wait=%.2fs", self.role, model, waited
            )
            metadata = {
                **caller_metadata,
                "llm_role": self.role,
                "llm_model": model,
                "tags": [*caller_tags, self.role, model],
            }
            started = time.perf_counter()
            try:
                response = completion(
                    model=self._model_string(config),
                    messages=messages,
                    api_base=self._api_base(config),
                    api_key=config.api_key,
                    timeout=timeout,
                    num_retries=0,
                    metadata=metadata,
                    **(self._json_mode_kwargs(config) if json_mode else {}),
                    **kwargs,
                )
                self._record_usage(config, started, response=response, attempt=attempt)
                return response
            except RateLimitError as exc:
                self._record_usage(config, started, status="rate_limited", error=exc, attempt=attempt)
                seconds = _cooldown_seconds(exc, role_config.cooldown_seconds)
                pool.report_429(model, seconds)
                logger.warning(
                    "LLM 429 role=%s model=%s cooldown=%.0fs; trying the next available model",
                    self.role, model, seconds,
                )
            except Exception as exc:
                self._record_usage(config, started, status="error", error=exc, attempt=attempt)
                raise LLMServiceError(f"LLM call failed ({config.provider}/{config.model}): {exc}") from exc
            finally:
                pool.release()

    def _new_budget(self) -> _WaitBudget | None:
        if self.role is None:
            return None
        return _WaitBudget(get_settings().llm_roles[self.role].max_wait_seconds)

    def generate(self, *, system: str, user: str, timeout: int = 30, **kwargs) -> str:
        """Plain text completion. Returns the raw response content."""
        self._require_valid_config()
        response = self._complete(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            timeout=timeout,
            json_mode=False,
            budget=self._new_budget(),
            **kwargs,
        )
        return response.choices[0].message.content

    def structured_generate(
        self,
        *,
        user: str,
        response_model: type[T],
        system: str = DEFAULT_STRUCTURED_SYSTEM_PROMPT,
        max_retries: int = 3,
        timeout: int = 30,
        **kwargs,
    ) -> T:
        """Completion constrained to JSON, validated into response_model.

        response_model must describe a JSON *object* at the top level -
        OpenAI's json_object mode rejects a bare top-level array. Wrap
        list output in a small model with one field, e.g. {"items": [...]}.
        """
        import json
        import time

        self._require_valid_config()

        last_exception = None
        last_content = ""
        # One max wait for every attempt of this call; a 429 is handled inside
        # _complete and never counts as one of the max_retries attempts.
        budget = self._new_budget()

        for attempt in range(max_retries):
            try:
                schema_json = json.dumps(response_model.model_json_schema(), indent=2)
                user_prompt = f"{user}\n\nYou MUST respond with a JSON object that adheres strictly to this JSON Schema:\n{schema_json}\n\nDo not include any extra keys, markdown tags, or explanations outside the JSON object."

                response = self._complete(
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user_prompt}],
                    timeout=timeout,
                    json_mode=True,
                    budget=budget,
                    attempt=attempt + 1,
                    **kwargs,
                )

                choice = response.choices[0]
                content = getattr(choice.message, "content", None)
                if not isinstance(content, str) or not content.strip():
                    finish_reason = getattr(choice, "finish_reason", None)
                    raise LLMServiceError(
                        "LLM returned empty response content "
                        f"(finish_reason={finish_reason!r}); the selected model may have "
                        "used its output-token budget before producing a final answer"
                    )
                content = content.strip()
                last_content = content
                # Strip markdown fences if present
                if content.startswith("```"):
                    lines = content.splitlines()
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    content = "\n".join(lines).strip()
                # Strip any non-JSON prefix before the first '{' or '['
                brace_pos = content.find("{")
                bracket_pos = content.find("[")
                first_pos = brace_pos if brace_pos >= 0 else bracket_pos
                if first_pos is not None and first_pos >= 0:
                    content = content[first_pos:]
                if content and content[-1] not in ("}", "]"):
                    # Trim trailing non-JSON characters
                    last_brace = content.rfind("}")
                    last_bracket = content.rfind("]")
                    last_pos = last_brace if last_brace >= last_bracket else last_bracket
                    if last_pos >= 0:
                        content = content[:last_pos + 1]
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError:
                    pass  # let model_validate_json handle the error
                else:
                    if isinstance(parsed, dict):
                        if isinstance(parsed.get("test_cases"), list):
                            normalized_test_cases = []
                            for tc in parsed["test_cases"]:
                                if isinstance(tc, str):
                                    for _ in range(3):
                                        try:
                                            decoded_tc = json.loads(tc)
                                        except json.JSONDecodeError:
                                            break
                                        if isinstance(decoded_tc, dict):
                                            tc = decoded_tc
                                            break
                                        if not isinstance(decoded_tc, str):
                                            break
                                        tc = decoded_tc

                                if isinstance(tc, dict):
                                    if isinstance(tc.get("steps"), str):
                                        tc["steps"] = [tc["steps"]]
                                    elif "steps" not in tc:
                                        tc["steps"] = ["Execute test case"]
                                    if "expected_result" not in tc:
                                        tc["expected_result"] = "Test completed as expected"
                                    if "preconditions" not in tc:
                                        tc["preconditions"] = []
                                    if "test_data" not in tc:
                                        tc["test_data"] = {}
                                normalized_test_cases.append(tc)
                            parsed["test_cases"] = normalized_test_cases

                        if isinstance(parsed.get("scenarios"), list):
                            priority_map = {"critical": "high", "crit": "high", "critical high": "high", "highest": "high",
                                            "lowest": "low", "lowest priority": "low"}
                            for sc in parsed["scenarios"]:
                                if not isinstance(sc, dict):
                                    continue
                                p = sc.get("priority")
                                if isinstance(p, str) and p.lower().strip() in priority_map:
                                    sc["priority"] = priority_map[p.lower().strip()]
                                if "tags" not in sc:
                                    sc["tags"] = []
                    content = json.dumps(parsed)

                return response_model.model_validate_json(content)
            except LLMRateLimitTimeout:
                raise
            except Exception as exc:
                last_exception = exc
                retry_message = (
                    "Retrying..."
                    if attempt < max_retries - 1
                    else "No attempts remain."
                )
                logger.warning(
                    "structured_generate attempt %s failed: %s. %s",
                    attempt + 1,
                    exc,
                    retry_message,
                )
                if attempt < max_retries - 1:
                    time.sleep(1)
        
        # If we reach here, all attempts failed
        logger.warning(
            "structured_generate raw output (first 2000 chars): %s", last_content[:2000]
        )
        if isinstance(last_exception, LLMServiceError):
            if str(last_exception).startswith("LLM call failed"):
                raise LLMServiceError(
                    f"{last_exception} after {max_retries} attempt(s), "
                    f"with a {timeout}s timeout per attempt"
                ) from last_exception
            raise LLMServiceError(
                f"{last_exception} after {max_retries} attempt(s)"
            ) from last_exception
        raise LLMServiceError(
            f"LLM response did not match {response_model.__name__} after {max_retries} attempts: {last_exception}\nRaw content: {last_content}"
        ) from last_exception