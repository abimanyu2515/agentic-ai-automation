# Verify: Role based LLM routing · spec 0003 · updated 2026-11-02
_Steps derived from spec 0003 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

## UI / manual
- [x] Start the app (`uvicorn backend.main:app --reload --port 8000`), open Master Settings, General Settings → an "LLM Roles (read only)" section shows Generation (gemini, both Gemini models, max concurrent 2, min interval 4s, cooldown 60s, max wait 180s) and Analysis (nvidia, the Nemotron model, max concurrent 2, min interval 0s) → AC-10
- [x] Save the General Settings form → the role section is unchanged, and `backend/config/override.yaml` has no `llm_roles` key → AC-10
- [ ] Run a requirement through test data generation on a real project → the server log shows `LLM call role=generation model=gemini-3.5-flash-lite`, then `model=gemini-3.1-flash-lite`, alternating → AC-1, AC-3, AC-11
- [ ] In the same run, scenario, test case and evaluation steps log `role=analysis model=nvidia/nemotron-3-ultra-550b-a55b` → AC-2, AC-11
- [ ] With LangSmith tracing on, open a generation trace → its metadata carries `llm_role` and `llm_model`, and its tags include the role and the model → AC-11
- [ ] Generate test data for several test cases at once → the log never shows more than 2 generation calls in flight, and later calls report a `queue_wait` above 0 → AC-4

## Commands
- [x] `python -m pytest -q backend/tests/test_llm_roles.py` → all pass → AC-1 to AC-13
- [x] `python -m pytest -q` → no failures beyond the 27 that already failed before this change (`test_api.py::test_scenario_replacement_and_rejection_cascades` can fail on a Windows file lock; rerun it) → AC-12
- [x] `curl -s localhost:8000/api/v1/settings` (with auth if required) → `llm_roles.generation` and `llm_roles.analysis` are present, and neither `api_key` nor `api_base` appears under `llm_roles` → AC-10
- [x] `curl -s localhost:8000/api/v1/projects/<id>/traces` → `llm_roles` lists both providers and models, and `model`/`provider` show the analysis role → AC-10

## Value sourcing
- [x] Role choice: build `TestDataAgent()` and `ScenarioAgent()` with no service → roles `generation` and `analysis` → AC-1, AC-2
- [x] Model string: generation calls go to `gemini/<model>`, analysis to `openai/nvidia/<model>` (the `_PROVIDER_PREFIXES` entry) → AC-1, AC-2
- [ ] Pacing: set `GENERATION_LLM_MODELS` to one model and `GENERATION_LLM_MIN_INTERVAL_SECONDS=10` → two calls start at least 10s apart; set it to 0 → no gap → AC-5
- [x] Cooldown length: a 429 with `Retry-After: 30` cools that model 30s; one with `retryDelay "15s"` in the message cools it 15s; one with neither uses `GENERATION_LLM_COOLDOWN_SECONDS`; `Retry-After: 99999` is clamped to 3600 → AC-6
- [x] Max wait: set `GENERATION_LLM_MAX_WAIT_SECONDS=20` and force 429s on every model → the call fails with `generation LLM rate limited: models [...] unavailable after ~20s`, and does not retry again → AC-7
- [x] Analysis 429: the single analysis model returns 429 once → the call waits out the cooldown and succeeds, with no switch → AC-8
- [x] Key and base fallback: remove every `GENERATION_LLM_*` and `ANALYSIS_LLM_*` line → both roles use `LLM_PROVIDER`/`LLM_MODEL`/`LLM_API_KEY`/`LLM_API_BASE` as before → AC-9
- [x] Base URL across providers: with `LLM_PROVIDER=nvidia`, `LLM_API_BASE` set and `GENERATION_LLM_API_BASE` unset → the Gemini role has no `api_base` (calls do not go to the NVIDIA host) → AC-1, AC-9
- [x] Blank values: set `ANALYSIS_LLM_MODEL=` (blank) → analysis falls back to `LLM_MODEL` → AC-9
- [x] Invalid limits: set `GENERATION_LLM_MAX_CONCURRENT=0` → settings fail to load with a validation error → AC-9
- [x] Hidden retries: every role call passes `num_retries=0` to `completion`; an explicit `LLMService(config=...)` call does not → AC-12, AC-13
- [x] Settings reload: save settings while calls are in flight → the cap is not doubled and an active cooldown stays → AC-4, AC-6

## Acceptance-criteria coverage
- AC-1 … role choice, model string, UI generation log, base URL across providers
- AC-2 … role choice, model string, UI analysis log
- AC-3 … UI alternating models; `test_generation_rotates_across_instances_and_skips_cooling_models`
- AC-4 … UI queue wait; settings reload; `test_cap_holds_the_third_call_until_a_slot_frees`
- AC-5 … pacing; `test_calls_to_one_model_are_paced`
- AC-6 … cooldown length; `test_429_fails_over_without_using_retries_and_honors_retry_after`
- AC-7 … max wait; `test_every_model_limited_raises_after_max_wait`
- AC-8 … analysis 429; `test_analysis_role_waits_out_a_429_on_its_single_model`
- AC-9 … fallback, blank values, invalid limits, base URL across providers
- AC-10 … Master Settings, settings and traces curls
- AC-11 … UI logs and LangSmith trace
- AC-12 … full suite, hidden retries, `test_bare_service_is_analysis_and_explicit_config_skips_roles`
- AC-13 … hidden retries; `test_pool_calls_disable_hidden_retries_and_carry_role_metadata`
