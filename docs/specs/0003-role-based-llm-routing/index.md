# 0003. Route LLM calls by role with model rotation and rate limit protection

**Date**: 2026-11-02
**Status**: In Progress

## Summary

Today every agent sends its LLM calls to one model, so bursts of test data and script generation run into the provider's request limits. This spec splits LLM calls into two roles. The **generation** role (Gemini) handles test data and Playwright scripts, taking turns between two Gemini models, with at most two calls at a time and a pause after any rate limit error. The **analysis** role (NVIDIA) handles every other agent. Settings that a role leaves unset fall back to the existing `LLM_*` values, so an old `.env` still works.

## Requirements

**User stories**:
- As the platform operator, I want test data and script generation on Gemini and every other agent on NVIDIA, so that no single provider quota covers all the traffic.
- As the platform operator, I want generation calls to rotate between two Gemini models, queue beyond two at a time, and back off after a 429, so that bursts finish instead of failing.

**Acceptance criteria**:
- **AC-1**: When no service is injected, `TestDataAgent`, `PlaywrightAgent`, `generate_accurate_test_script` and `generate_data_driven_template` use the generation role. Their calls go to `GENERATION_LLM_PROVIDER`, using the models listed in `GENERATION_LLM_MODELS`.
- **AC-2**: When no service is injected, these callers use the analysis role (`ANALYSIS_LLM_*`): `RequirementAnalystAgent`, `BacklogCreationAgent`, `QAStoryAnalyzerAgent`, `ScenarioAgent`, `TestCaseAgent`, `EvaluationAgent`, `QAAutomationOrchestratorAgent`, `ExecutionAnalysisAgent`, `DefectManagementAgent`, `ReportAgent`, `ReportService` and `ExecutionAnalyzer`.
- **AC-3**: Generation calls rotate through `GENERATION_LLM_MODELS` in list order (1st, 2nd, 1st, ...). The rotation is shared across all agents, instances and threads in the process, and a model that is cooling down is skipped.
- **AC-4**: At most `GENERATION_LLM_MAX_CONCURRENT` generation calls are in flight at once, counted across all generation models. Any extra call waits, and starts when a slot frees up.
- **AC-5**: Two calls to the same model start at least `GENERATION_LLM_MIN_INTERVAL_SECONDS` apart. A value of 0 turns pacing off.
- **AC-6**: A 429 puts that model into cooldown. The cooldown lasts the number of seconds in the provider's `Retry-After` header when one is present, otherwise `GENERATION_LLM_COOLDOWN_SECONDS`. The call then moves to the next available model. The retry does not count against `structured_generate`'s format retries (`max_retries`).
- **AC-7**: When every model in a role is cooling down, the call waits for the first cooldown to end. If its total wait (for a slot, for pacing and for cooldowns) passes the role's max wait (180 seconds by default), it raises `LLMServiceError` naming the role, the models tried, and how long it waited.
- **AC-8**: The analysis role applies the same concurrency cap (`ANALYSIS_LLM_MAX_CONCURRENT`), 429 cooldown and max wait. It has one model, so a 429 means waiting, not switching.
- **AC-9**: Any role value left unset falls back to the matching `LLM_*` value (provider, model, API key, API base). A `.env` that holds only `LLM_*` keys sends both roles to that one provider and model, as today. A blank value counts as unset. No temperature is sent, which is today's behavior, so providers keep their default.
- **AC-10**: `GET /api/v1/settings` returns an `llm_roles` object with `generation` and `analysis`. Each shows its provider, models, max concurrent, min interval, cooldown and max wait. API keys are never returned. The usage endpoint reports both roles. Master Settings shows both roles read only.
- **AC-11**: Every call logs its role, the model chosen, how long it waited in the queue, and any 429. Each LangSmith trace is tagged with the role and the model.
- **AC-12**: An injected `llm_service` or an explicit `LLMService(config=...)` skips role routing, queueing and pacing. Tests stay on stubs or `mock_response` and never need a live provider, and the existing test suite passes.
- **AC-13**: Every completion sent through a pool passes `num_retries=0`, so LiteLLM never retries on its own and every 429 reaches the pool.

## Decision

**Chosen option**: Option 1: Role aware `LLMService` with a shared pool for each role

`LLMService` takes `role="generation" | "analysis"`. Each role resolves its own config, with fallback to `LLM_*`, and every call goes through that role's shared pool. The pool handles rotation, the concurrency cap, pacing, 429 cooldown and the max wait.

## Feature design

**Data model sketch** (configuration only, nothing persisted):

`LLMRoleConfig` (new, in `backend/config/settings.py`):
| Field | Type | Required | Generation source | Analysis source | Default when unset |
|---|---|---|---|---|---|
| `provider` | str | yes | `GENERATION_LLM_PROVIDER` | `ANALYSIS_LLM_PROVIDER` | `llm.provider` |
| `models` | list[str] | yes, at least 1 | `GENERATION_LLM_MODELS` (comma separated) | `ANALYSIS_LLM_MODEL` (a list of one) | `[llm.model]` |
| `api_key` | str or null | per provider | `GENERATION_LLM_API_KEY` | `ANALYSIS_LLM_API_KEY` | `llm.api_key` |
| `api_base` | str or null | no | `GENERATION_LLM_API_BASE` | `ANALYSIS_LLM_API_BASE` | `llm.api_base`, then the provider default |
| `max_concurrent` | int ≥ 1 | yes | `GENERATION_LLM_MAX_CONCURRENT` | `ANALYSIS_LLM_MAX_CONCURRENT` | 2 |
| `min_interval_seconds` | float ≥ 0 | yes | `GENERATION_LLM_MIN_INTERVAL_SECONDS` | always 0 (not read, shown as 0) | 0 |
| `cooldown_seconds` | float ≥ 0 | yes | `GENERATION_LLM_COOLDOWN_SECONDS` | `ANALYSIS_LLM_COOLDOWN_SECONDS` (optional) | 60 |
| `max_wait_seconds` | float > 0 | yes | `GENERATION_LLM_MAX_WAIT_SECONDS` (optional) | `ANALYSIS_LLM_MAX_WAIT_SECONDS` (optional) | 180 |

`Settings` gains `llm_roles: dict[str, LLMRoleConfig]` with the keys `generation` and `analysis`. The existing `settings.llm` stays, and acts as the fallback. No temperature is sent.

Parsing and validation rules:
- `GENERATION_LLM_MODELS`: split on commas, trim spaces, drop blanks, and drop duplicates (keeping the first). An empty result falls back to `[llm.model]`.
- Any blank string (`""`) counts as unset, keys included.
- `max_concurrent < 1`, a negative interval or cooldown, or `max_wait_seconds <= 0` fails at settings load with a pydantic validation error. Values are never silently clamped.

Pool state (in memory, module level in `llm.py`, one `RolePool` per role, keyed by role name only):
- A single `threading.Condition` guards all state. Never asyncio primitives, because callers run in `ThreadPoolExecutor` and `asyncio.to_thread` threads.
- The state it guards: the in-flight count, the rotation cursor, and for each model `next_allowed_at` (pacing) and `cooldown_until`.
- The pool reads its role config from `get_settings()` on every acquire. After a settings save, the limits change in place, and cooldowns and in-flight counts carry over, so the cap never doubles.

Per call config: for the chosen model, the service builds an `LLMConfig` (provider, that model, the key and base) and the existing helpers (`_model_string`, `_api_base`, `_json_mode_kwargs`, `_require_valid_config`) read it. On a role service, `service.config` returns the config for the role's first model, for compatibility.

**Call flow** (`generate` and `structured_generate`, for a role service; `generate` gains the same 429 loop it lacks today):
1. Set one `deadline = now + max_wait_seconds` for the whole method call (all attempts and failovers). It is never reset. Time spent inside `completion` doesn't count toward it, only waiting does.
2. `acquire(deadline)`, under the `Condition`. Wait until `in_flight < max_concurrent` **and** some model is ready (`cooldown_until <= now` and `next_allowed_at <= now`). Pick the first ready model at or after the cursor, so cooling models are skipped. Then set the cursor to the model after the one chosen, increment `in_flight`, and set `next_allowed_at = now + min_interval_seconds` for that model. While waiting, use `Condition.wait(timeout)`, with the timeout set to whichever comes first: the earliest model ready time or the deadline. If the deadline passes, raise (step 6).
3. Call `litellm.completion` with that model, `num_retries=0`, and LangSmith metadata. Wrap it in `try/finally` so `release()` always decrements `in_flight` and runs `notify_all()`.
4. Catch `litellm.RateLimitError` **before** any wrapping into `LLMServiceError`. Call `report_429(model, seconds)`, which sets `cooldown_until` and runs `notify_all()`. Log it, then go back to step 2 (the slot was already released in step 3). The 429 does not count against `max_retries`. The loop becomes a `while`, with a separate attempt counter.
5. Cooldown seconds come from, in order:
   - the `Retry-After` header on `exc.response`, if it holds whole seconds (case insensitive; an HTTP date is ignored);
   - otherwise Gemini's `retryDelay` ("30s"), or "retry in N s", parsed from the error message;
   - otherwise `cooldown_seconds`.
   The result is clamped to 0 to 3600.
6. When the deadline passes while waiting, raise `LLMServiceError("<role> LLM rate limited: models [...] unavailable after <N>s")`.
7. Other errors (including 503) and replies that don't match the schema keep today's handling: they count as attempts, up to `max_retries`, with the 1 second pause. Each retry goes back through step 2 under the same deadline. A 503 does not trigger a cooldown.

**API surface**:
| Interface | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| `LLMService(role=None, config=None)` | constructor | `LLMService()` = analysis role, with its pool. `LLMService(role="generation")` = generation role, with its pool. `LLMService(config=X)` = X only, no role, no pool (tests and ad hoc use, unchanged) | service | n/a | `ValueError` for an unknown role, or when `role` and `config` are both given |
| `LLMService.generate` / `structured_generate` | call | unchanged | unchanged | n/a | `LLMServiceError` (adds the rate limit timeout case) |
| `/api/v1/settings` | GET | none | adds `llm_roles.{generation,analysis}` with `{provider, models, max_concurrent, min_interval_seconds, cooldown_seconds, max_wait_seconds}`, no keys | existing | none new |
| `/api/v1/settings` | PUT | unchanged | an `llm_roles` key in the payload is ignored (role config is read only, from `.env`) | existing | none new |
| Usage endpoint (`main.py` around line 2531) | GET | unchanged | adds `llm_roles` with each role's `{provider, models}`; the old `model`/`provider` fields show the analysis role | existing | none new |

**Value sourcing**:
| Action | Value produced / used | Source |
|---|---|---|
| Choose the role | role | constructor default in each agent or service (AC-1, AC-2); `"analysis"` when not given |
| Choose the model | model string | role pool rotation over `LLMRoleConfig.models`, prefixed by `_PROVIDER_PREFIXES[provider]` |
| Wait for pacing | earliest start time | the model's `next_allowed_at` (derived from `min_interval_seconds`) |
| Cooldown length | seconds | the `Retry-After` header (whole seconds), then `retryDelay` or "retry in N s" in the error message, then `cooldown_seconds`; clamped to 0 to 3600 |
| Max wait | seconds | `max_wait_seconds`, one deadline per method call |
| API key and base | credentials | role env var, then `LLM_*`, then `_DEFAULT_API_BASES` for the base |
| Temperature | none sent | provider default (unchanged) |
| LangSmith tags | `metadata={"llm_role", "llm_model"}` plus `tags=[role, model]` | the role and the chosen model, merged into any `metadata` the caller passes, never a duplicate keyword |
| Queue wait in the log | seconds | total time spent inside `acquire`, including slot waits, pacing and cooldown waits |
| Settings response | role fields | `settings.llm_roles`, with `api_key` excluded |

**Key invariants**:
- No more than `max_concurrent` calls per role are in flight within the process.
- A model in cooldown is never called before `cooldown_until`.
- A 429 never counts against `max_retries`. Total time spent waiting never goes past `max_wait_seconds`.
- A slot is always released, including when the call raises (`try/finally`). Every release and every 429 report runs `notify_all()`, so no waiting call misses a slot that freed up or a cooldown that ended.
- Pool calls always pass `num_retries=0`.
- `max_workers` on agents stays unchanged. The pool is the only limiter, and extra threads simply wait in `acquire`.
- `litellm` is imported only in `backend/services/llm.py`.

**Security model**: There are no new users or permissions. API keys stay in `.env`, and are never logged, returned by an API, or written to `override.yaml`.

**Configuration required** (already in the engineer's `.env`):
- `GENERATION_LLM_PROVIDER`, `GENERATION_LLM_API_KEY`, `GENERATION_LLM_MODELS`: the Gemini role.
- `GENERATION_LLM_MAX_CONCURRENT`, `GENERATION_LLM_MIN_INTERVAL_SECONDS`, `GENERATION_LLM_COOLDOWN_SECONDS`: the generation limits.
- `ANALYSIS_LLM_PROVIDER`, `ANALYSIS_LLM_API_KEY`, `ANALYSIS_LLM_MODEL`, `ANALYSIS_LLM_API_BASE` (optional), `ANALYSIS_LLM_MAX_CONCURRENT`: the NVIDIA role.
- Optional: `GENERATION_LLM_MAX_WAIT_SECONDS`, `ANALYSIS_LLM_MAX_WAIT_SECONDS`, `ANALYSIS_LLM_COOLDOWN_SECONDS`.

**Critical test scenarios**:
- Happy path: `TestDataAgent()` built with no injected service hits the generation provider and model, checked with `mock_response` and a patched config. Verifies **AC-1**, **AC-9**.
- Routing: each agent in the AC-2 list, built with no injected service, resolves to the analysis role. Verifies **AC-2**.
- Rotation: four calls from separate service instances go to models in the order A, B, A, B. With A in cooldown, the calls go to B, B. Verifies **AC-3**.
- Concurrency: three threads with a cap of 2, using a blocking fake completion. The third call starts only after one of the first two finishes. Verifies **AC-4**.
- Pacing: two calls to one model start at least `min_interval` apart, using an injected clock. Verifies **AC-5**.
- 429 failover: model A raises `RateLimitError` with `Retry-After: 30`. The same call succeeds on B, `max_retries` isn't used up, and A is skipped for 30 seconds. Verifies **AC-6**.
- Every model limited: both models return 429 until the deadline. The call raises `LLMServiceError` naming the role and models, within `max_wait` plus a small margin. Verifies **AC-7**.
- Analysis role: a single model returns 429 and then succeeds. The call waits for the cooldown and returns the result. Verifies **AC-8**.
- Settings API: the response contains both roles and contains no `api_key`. Verifies **AC-10**.
- Observability: a log record and the completion metadata carry the role and model. Caller supplied `metadata` is merged, not overwritten. Verifies **AC-11**.
- Injection: an existing stub based test suite passes unchanged, and `LLMService(role="generation", config=X)` raises `ValueError`. Verifies **AC-12**.
- Hidden retries: each pool call passes `num_retries=0` to `completion`. Verifies **AC-13**.
- Config parsing: `" a, ,b,a "` parses to `[a, b]`. A blank value falls back to `LLM_*`. `max_concurrent=0` fails at load. Verifies **AC-9**.
- Settings reload: after `get_settings.cache_clear()` with a new cap, in-flight calls still count toward the cap, and an active cooldown remains. Verifies **AC-4**, **AC-6**.

## Build plan

These tasks follow the Tracer Bullet order: one generation call working end to end first, then widened to the other callers.

1. Add `LLMRoleConfig` and `Settings.llm_roles` to `settings.py`, reading the env vars with fallback to `llm.*` and applying the parsing and validation rules from the data model sketch. Satisfies **AC-9**.
2. Thin thread: add the `role` argument to `LLMService`, resolving the role config and using its first model (no pool yet). Make `TestDataAgent` default to `LLMService(role="generation")`, and add a test proving it reaches the Gemini model string. Satisfies **AC-1**, **AC-9**, **AC-12**.
3. Point the remaining defaults at their roles: `PlaywrightAgent`, `generate_accurate_test_script` and `generate_data_driven_template` use generation. Every AC-2 caller passes `role="analysis"` explicitly. That includes the inline `LLMService()` in `report_agent.py`, both places in `report_service.py` (the one at around line 1230 is injected into `ExecutionAnalyzer`), and `ExecutionAnalyzer`'s own fallback. Callers that build agents without a service (`workflow_service.py`, `grounded_playwright_service.py`) need no change, because the agent default sets the role. Satisfies **AC-1**, **AC-2**.
4. Build `RolePool`: one `threading.Condition` with `acquire(deadline) -> model`, `release(model)` and `report_429(model, seconds)`, keyed by role, reading its limits on every acquire. Route `generate` and `structured_generate` through it with `num_retries=0`. An explicit `config=` bypasses it. Tests use an injected clock and wait function. Satisfies **AC-3**, **AC-4**, **AC-5**, **AC-12**, **AC-13**.
5. Add 429 handling: restructure both methods into a `while` loop with a separate attempt counter, catch `RateLimitError` before it gets wrapped, parse the cooldown (header, then message, then default, clamped), fail over without using up `max_retries`, and enforce the single deadline. This replaces the immediate retry on a 429. Satisfies **AC-6**, **AC-7**, **AC-8**.
6. Add observability: log role, model, wait time and 429s, and pass role and model metadata to `completion` so LangSmith tags them. Satisfies **AC-11**.
7. Update the settings and usage endpoints to return `llm_roles` without keys, make `PUT /api/v1/settings` ignore `llm_roles`, and add a read only role display to the Master Settings view in `frontend/` (the one that renders `GET /api/v1/settings`). Satisfies **AC-10**.
8. Write tests for every critical scenario. Add `.env.example` listing all role variables with placeholder values, and update the docs that describe LLM settings and agents. Satisfies **AC-1** through **AC-13**.

## Consequences

**Positive**:
- Test data and script bursts draw on two Gemini quotas instead of one shared quota, and the analysis agents stop competing with them.
- A 429 no longer uses up retries, so far fewer calls are wasted during rate limiting.
- LangSmith can show call counts per role and per model.

**Negative / tradeoffs**:
- Limits are per process. Running uvicorn with N workers multiplies the effective cap and pacing by N. That is acceptable for the current single process deployment, but must be revisited before scaling out.
- Waiting blocks a thread for up to 180 seconds. Many waiting calls can fill the default `asyncio.to_thread` pool and the server's worker thread pool (anyio defaults to 40), slowing unrelated requests during a burst.
- An exhausted *daily* quota looks like any other 429. Every call then waits the full 180 seconds before failing, and each TestDataAgent attempt waits again (up to about 9 minutes per test case).
- Two models produce slightly different styles of test data and scripts within one run.
- Concurrency code is harder to test. Tests need an injected clock and fake completions so they don't depend on timing.

**Neutral**:
- `settings.llm` stays as the fallback. The Master Settings edit form for `llm` still writes `override.yaml`, and `.env` values still win over it.
- No data migration. Rolling back means reverting the change; the extra env vars are then simply ignored.

## Follow-up

- [ ] Confirm the exact Gemini model IDs (`gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`) against Google AI Studio before the first live run.
- [ ] Set `GENERATION_LLM_MIN_INTERVAL_SECONDS` to 60 ÷ each model's per minute limit, taken from the Gemini quota page.
- [ ] Check that the chosen NVIDIA model honors `response_format: json_object`. If it doesn't, the existing fence stripping is the only safeguard.
- [ ] Check that LiteLLM maps the default `response_format: json_object` correctly for both Gemini models (`_JSON_MODE_KWARGS` has no `gemini` entry). Add one if needed.
- [ ] If daily quota exhaustion turns out to be common, consider detecting "per day" 429s and failing fast instead of waiting.
- [ ] If the app moves to multiple workers, revisit shared limits (LiteLLM proxy or Redis).
- [ ] `backend/services/langsmith_tracer.py` appears unused. Decide separately whether to remove it.

Decision record (context, options, rationale): [rationale.md](rationale.md). Verify steps: [verify.md](verify.md).
