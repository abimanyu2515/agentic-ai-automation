# 0003. Route LLM calls by role with model rotation and rate limit protection: rationale

Decision record for [index.md](index.md).

## Context

All LLM traffic goes through `LLMService` (`backend/services/llm.py`), which reads one config (`settings.llm`, from `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_API_BASE`). Seventeen places build their own `LLMService()` with that same config: 12 agents, 2 functions in `script_generator_agent`, 2 places in `ReportService`, and `ExecutionAnalyzer`. All of their calls count against a single provider quota.

The heaviest callers are also the ones that come in bursts:
- `TestDataAgent` makes up to 3 attempts per test case (`max_retries=1`, `timeout=240`).
- `PlaywrightAgent` and `TestCaseAgent` fan out across threads (`max_workers`).
- `script_generator_agent` retries when its output is invalid, calling the LLM through `asyncio.to_thread`.

On top of that, `structured_generate` retries up to 3 times with only a 1 second pause, even after a 429 (rate limited) response. It wraps the 429 into `LLMServiceError` and counts it as a normal attempt, so most retries land while the provider is still refusing requests. LiteLLM and the OpenAI client can also retry internally, which adds calls nobody sees.

The engineer has keys for two providers: Gemini (where each model has its own rate limit) and NVIDIA. They have already added the role variables to `.env`. Constraints from `AGENTS.md`:
- Settings load from `.env` first, then YAML.
- LLM dependencies must stay injectable, so tests never call a live provider.
- `LLMService` is the only module allowed to import `litellm`.

The app runs as a single uvicorn process. Nothing here touches persisted data, auth, or regulated data.

## Options considered

### Option 1: Role aware `LLMService` with a shared pool for each role (fix in place)

Add a `role` argument to `LLMService`. One module level pool per role holds the rotation counter, a slot limit (semaphore), per model pacing times and per model cooldowns. Agents only change which role they use by default.

**Pros**:
- Implements the exact behavior chosen: strict list order rotation, one shared cap of 2, a bounded wait.
- Fits the rule that `LLMService` is the only `litellm` importer. Injection and the existing tests are unchanged.

**Cons**:
- About 150 lines of new concurrency code, which needs careful tests.
- Limits apply per process, not across processes.

### Option 2: LiteLLM `Router`

LiteLLM's built in router, with both Gemini models registered as deployments, plus its cooldowns, retries and per deployment request limits.

**Pros**:
- Rate limit cooldowns and retries are already built and maintained upstream.

**Cons**:
- Its strategies (random shuffle, least busy, usage based) don't give strict list order rotation.
- Its concurrency limits apply to each deployment, not one shared cap of 2. It has no wait-for-a-slot-up-to-N-seconds queue.
- Routing decisions happen inside the library, so they're harder to log and test against `mock_response`.

### Option 3: LiteLLM proxy server

Run the LiteLLM proxy as a separate service, configured with routing and limits.

**Pros**:
- Limits are shared across processes and machines.

**Cons**:
- A new piece of infrastructure to deploy, secure and monitor, for a single process app.

### Option 4: Two configs only, no rotation or queue

Split the config into two roles and stop there.

**Pros**:
- The smallest possible change.

**Cons**:
- Generation bursts still hit one model with no queue and no back off. The original problem stays for the busiest calls.

## Rationale

The engineer chose behaviors that LiteLLM's Router doesn't offer as is: strict rotation, one cap shared by both models, and a wait that is bounded rather than an immediate failure. Building them into `LLMService` keeps every LLM decision in the one module `AGENTS.md` already makes responsible for `litellm`. Injection keeps working, so the existing stub based tests don't change.

The app runs as one process, so in memory state is enough. A proxy (Option 3) only pays off once there are several workers. Option 4 leaves the busiest calls unprotected, and those are where the drain happens.

Fixing the 429 retry belongs in this change. The immediate retries in `structured_generate`, plus LiteLLM's hidden retries, waste calls exactly when the quota is exhausted, and rotation can't help while each 429 is retried straight away.

Temperature stays unsent (the provider default). Sending 0.2 would change every agent's output, and lower variety in TestDataAgent's 20 distinct datasets, which would mean more retries.
