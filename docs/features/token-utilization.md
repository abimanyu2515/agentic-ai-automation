# Token Utilization

The **Token Utilization** page (sidebar → *Token Utilization*) shows the real token usage and cost of every AI call the agents make. That covers requirement analysis, scenarios, test cases, evaluation, test data, scripts, execution analysis, defects and reports. It covers both configured LLM roles: `generation` (for example Gemini) and `analysis` (for example NVIDIA).

## What is recorded

Every completion **attempt** that goes through `LLMService` produces one `TokenUsageRecord`:

| Field | Meaning |
|---|---|
| `agent` | The agent that made the call, e.g. `scenario_agent`, `test_data_agent`, `script_generator_agent` |
| `operation`, `project_id`, `requirement_id`, `test_case_id`, `run_id` | Where the call came from (see *Attribution*) |
| `role`, `provider`, `model` | The LLM role and the model actually used (generation rotates models, so this is per call) |
| `prompt_tokens`, `completion_tokens`, `total_tokens` | Taken from the provider response (`response.usage`) |
| `input_cost_usd`, `output_cost_usd`, `cost_usd`, `priced`, `price_source` | Cost at the price in effect when the call was made |
| `latency_ms`, `status`, `error_type`, `attempt` | Call duration; `success`, `error` or `rate_limited`; retry number |

A retried `structured_generate` call produces one record per attempt, because each attempt is billed. Failed and rate limited attempts are recorded with zero tokens, so error rates are visible.

Recording never breaks or slows an LLM call. Records are queued and written by a background thread. Any recording error is logged and swallowed. Set `token_usage.enabled: false` in `backend/config/default_config.yaml` to turn recording off.

## Cost

Cost is computed per call in `backend/services/token_pricing.py`:

1. **Custom price.** An override saved on the page (*Model pricing* panel) or set in `token_usage.pricing_overrides` in config, in USD per 1M input and output tokens. The key can be the bare model name or the litellm model string.
2. **LiteLLM price map.** Used for models litellm knows. NVIDIA models are also looked up as `nvidia_nim/<model>`.
3. **Unpriced.** Cost 0 with `priced=false`. The page lists unpriced models and asks for a price instead of showing a misleading $0.

Changing a price affects new calls only. Past records keep the cost they were recorded with.

## Attribution

- **Agent:** each agent constructs `LLMService(role=..., agent="<agent_name>")`.
- **Project and operation:**
  - `LLMContextMiddleware` (in `backend/services/llm_context.py`) tags every `/api/v1` request with its route and the project ID from the URL.
  - `WorkflowService` operations add a clean operation name (`generate_scenarios`, `generate_test_data`, and so on), a `run_id`, and the requirement or test case ID through `@llm_operation`.
- **Threads:** context does not cross into `ThreadPoolExecutor` workers on its own. Wrap the callable with `run_in_context(fn)`, as `TestCaseAgent` and `PlaywrightAgent` do.

## Storage

| Repository provider | Store |
|---|---|
| `json` (default) | `backend/database/token_usage.jsonl`, append only, one record per line |
| `postgres` | `token_usage` table (Alembic revision `d4e9b2c7a1f3`) |

UI settings (budget, alert threshold, price overrides) are saved to `backend/database/token_usage_settings.json`. Both JSON files are git ignored.

## The page

- **Filters:**
  - Date presets: today, 7, 30 or 90 days, month to date, all time, or a custom range.
  - Project, agent, model, role and status.
  - The range, chart toggles and breakdown grouping are remembered in the browser.
- **KPIs:** total cost, total tokens (input/output), calls, average cost per call, error rate with retries, and average latency. Each shows its change against the previous period of the same length.
- **Monthly budget:** month to date spend and projected month end spend against a budget, with a warning above the alert threshold.
- **Usage over time:** cost, tokens or calls, either in total or stacked by agent, model or role, with an hourly, daily or weekly interval.
- **Cost by agent** and **tokens by model** (input vs output). Click a bar to filter the whole page.
- **Breakdown table:** grouped by agent (with pipeline stage), model, role, provider, project or operation. Sortable, and it shows share of spend.
- **Call log:** every attempt, paginated and sortable. Click a row for its run, operation, cost split and error.
- **Model pricing:** the effective price per model and its source, with editable custom prices.
- **Export CSV:** downloads the filtered records.

Access is controlled by the `TokenUsage` RBAC module: `view` to see the page, `edit` to change budget and prices.

The LangSmith view's token and cost figures (`GET /api/v1/projects/{id}/traces`) come from the same records.

API: see [Token Usage](../api.md#token-usage).
