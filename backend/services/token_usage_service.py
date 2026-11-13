"""
Token Utilization: records every LLM completion attempt and aggregates them.

LLMService._complete calls `record_llm_call(...)` after each attempt. That
must never break or slow an LLM call, so recording only builds a record and
puts it on a queue; a daemon thread prices nothing heavy and writes batches
to the TokenUsageStore. Queries flush the queue first so the page always
shows the calls that just finished.

Settings saved from the UI (budget, alert threshold, price overrides) live in
backend/database/token_usage_settings.json and override token_usage.* config.
"""

import atexit
import csv
import io
import json
import logging
import queue
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from backend.config.settings import get_settings
from backend.models.token_usage import TokenUsageFilters, TokenUsageRecord
from backend.schemas.token_usage_dto import TokenUsageSettings
from backend.repository.token_usage_store import TokenUsageStore, get_token_usage_store
from backend.services.llm_context import current_llm_context
from backend.services.token_pricing import price_usage, resolve_price

logger = logging.getLogger(__name__)

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "database" / "token_usage_settings.json"

# Which pipeline stage each agent belongs to, for the page's stage labels.
AGENT_STAGES: dict[str, str] = {
    "requirement_analyst_agent": "Requirement analysis",
    "qa_story_analyzer_agent": "Requirement analysis",
    "backlog_creation_agent": "Backlog",
    "scenario_agent": "Scenarios",
    "test_case_agent": "Test cases",
    "evaluation_agent": "Test cases",
    "test_data_agent": "Test data",
    "automation_orchestrator_agent": "Scripts",
    "playwright_agent": "Scripts",
    "script_generator_agent": "Scripts",
    "execution_analysis_agent": "Analysis",
    "defect_management_agent": "Analysis",
    "execution_analyzer": "Analysis",
    "report_agent": "Reports",
    "report_service": "Reports",
}

GROUP_FIELDS = ("agent", "model", "role", "provider", "project_id", "operation", "status")
BUCKETS = {"hour": timedelta(hours=1), "day": timedelta(days=1), "week": timedelta(weeks=1)}
MAX_BUCKETS = 400
MAX_SERIES = 8


def stage_for(agent: str) -> str:
    return AGENT_STAGES.get(agent, "Other")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _floor(moment: datetime, bucket: str) -> datetime:
    moment = moment.astimezone(timezone.utc)
    if bucket == "hour":
        return moment.replace(minute=0, second=0, microsecond=0)
    day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    if bucket == "week":
        return day - timedelta(days=day.weekday())
    return day


class _Totals:
    __slots__ = ("calls", "prompt", "completion", "total", "cost", "latency", "errors", "retries", "unpriced")

    def __init__(self):
        self.calls = self.prompt = self.completion = self.total = 0
        self.cost = 0.0
        self.latency = 0
        self.errors = self.retries = self.unpriced = 0

    def add(self, r: TokenUsageRecord) -> None:
        self.calls += 1
        self.prompt += r.prompt_tokens
        self.completion += r.completion_tokens
        self.total += r.total_tokens
        self.cost += r.cost_usd
        self.latency += r.latency_ms
        if r.status != "success":
            self.errors += 1
        if r.attempt > 1:
            self.retries += 1
        if not r.priced and r.status == "success":
            self.unpriced += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt,
            "completion_tokens": self.completion,
            "total_tokens": self.total,
            "cost_usd": round(self.cost, 6),
            "avg_tokens_per_call": round(self.total / self.calls, 1) if self.calls else 0,
            "avg_cost_per_call": round(self.cost / self.calls, 6) if self.calls else 0,
            "avg_latency_ms": round(self.latency / self.calls) if self.calls else 0,
            "errors": self.errors,
            "error_rate": round(self.errors / self.calls, 4) if self.calls else 0,
            "retries": self.retries,
            "unpriced_calls": self.unpriced,
        }


def _totals(records: Iterable[TokenUsageRecord]) -> _Totals:
    totals = _Totals()
    for record in records:
        totals.add(record)
    return totals


def _pct_change(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


class TokenUsageService:
    def __init__(
        self,
        store: TokenUsageStore | None = None,
        settings_path: Path = SETTINGS_PATH,
        async_writes: bool = True,
    ):
        self._store = store
        self._store_lock = threading.Lock()
        self.settings_path = Path(settings_path)
        self.async_writes = async_writes
        self._queue: "queue.Queue[TokenUsageRecord | None]" = queue.Queue()
        self._writer: threading.Thread | None = None
        self._writer_lock = threading.Lock()
        self._settings_cache: TokenUsageSettings | None = None

    # ── store and settings ────────────────────────────────────────────────

    @property
    def store(self) -> TokenUsageStore:
        with self._store_lock:
            if self._store is None:
                self._store = get_token_usage_store()
            return self._store

    def get_settings(self) -> TokenUsageSettings:
        if self._settings_cache is not None:
            return self._settings_cache
        config = get_settings().token_usage
        data: dict[str, Any] = {
            "monthly_budget_usd": config.monthly_budget_usd,
            "alert_threshold_pct": config.alert_threshold_pct,
            "pricing_overrides": dict(config.pricing_overrides),
        }
        if self.settings_path.exists():
            try:
                saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
                overrides = {**data["pricing_overrides"], **(saved.pop("pricing_overrides", None) or {})}
                data.update(saved)
                data["pricing_overrides"] = overrides
            except Exception:
                logger.warning("Ignoring unreadable %s", self.settings_path)
        self._settings_cache = TokenUsageSettings.model_validate(data)
        return self._settings_cache

    def update_settings(self, settings: TokenUsageSettings) -> TokenUsageSettings:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.settings_path.with_suffix(".tmp")
        tmp.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(self.settings_path)
        self._settings_cache = None
        return self.get_settings()

    def _overrides(self) -> dict[str, dict[str, float]]:
        return {k: v.model_dump() for k, v in self.get_settings().pricing_overrides.items()}

    # ── recording ─────────────────────────────────────────────────────────

    def record(
        self,
        *,
        agent: str | None,
        role: str | None,
        provider: str,
        model: str,
        model_string: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int | None,
        latency_ms: int,
        status: str = "success",
        error_type: str | None = None,
        attempt: int = 1,
    ) -> TokenUsageRecord | None:
        """Build, price and queue one record. Never raises."""
        try:
            if not get_settings().token_usage.enabled:
                return None
            context = current_llm_context()
            price = resolve_price(
                provider=provider, model=model, model_string=model_string, overrides=self._overrides()
            )
            priced = price_usage(price, prompt_tokens, completion_tokens)
            record = TokenUsageRecord(
                agent=context.get("agent") or agent or "unattributed",
                operation=context.get("operation"),
                project_id=context.get("project_id"),
                requirement_id=context.get("requirement_id"),
                test_case_id=context.get("test_case_id"),
                run_id=context.get("run_id"),
                role=role,
                provider=provider,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens if total_tokens else prompt_tokens + completion_tokens,
                input_cost_usd=priced.input_cost_usd,
                output_cost_usd=priced.output_cost_usd,
                cost_usd=priced.cost_usd,
                priced=priced.priced,
                price_source=priced.source,
                latency_ms=latency_ms,
                status=status,
                error_type=error_type,
                attempt=attempt,
            )
            if self.async_writes:
                self._ensure_writer()
                self._queue.put(record)
            else:
                self.store.add_many([record])
            return record
        except Exception:
            logger.exception("Token usage recording failed; the LLM call is unaffected")
            return None

    def _ensure_writer(self) -> None:
        with self._writer_lock:
            if self._writer is None or not self._writer.is_alive():
                self._writer = threading.Thread(target=self._write_loop, name="token-usage-writer", daemon=True)
                self._writer.start()

    def _drain(self, first: TokenUsageRecord | None = None) -> list[TokenUsageRecord]:
        batch = [first] if first is not None else []
        while len(batch) < 500:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is not None:
                batch.append(item)
            self._queue.task_done()
        return batch

    def _write_batch(self, batch: list[TokenUsageRecord]) -> None:
        if not batch:
            return
        try:
            self.store.add_many(batch)
        except Exception:
            logger.exception("Could not persist %s token usage record(s)", len(batch))

    def _write_loop(self) -> None:
        while True:
            item = self._queue.get()
            try:
                batch = self._drain(item)
                self._write_batch(batch)
            finally:
                self._queue.task_done()

    def flush(self, timeout: float = 5.0) -> None:
        """Wait until queued records are written (bounded by timeout)."""
        if not self.async_writes:
            return
        deadline = _utcnow() + timedelta(seconds=timeout)
        while self._queue.unfinished_tasks and _utcnow() < deadline:
            if self._writer is None or not self._writer.is_alive():
                # No writer (e.g. interpreter shutdown): write inline.
                self._write_batch(self._drain())
                break
            threading.Event().wait(0.02)

    # ── queries ───────────────────────────────────────────────────────────

    def records(self, filters: TokenUsageFilters) -> list[TokenUsageRecord]:
        self.flush()
        return self.store.query(filters)

    def summary(self, filters: TokenUsageFilters) -> dict[str, Any]:
        records = self.records(filters)
        current = _totals(records)

        previous = None
        if filters.start and filters.end and filters.end > filters.start:
            span = filters.end - filters.start
            previous_filters = filters.model_copy(update={"start": filters.start - span, "end": filters.start})
            previous = _totals(self.records(previous_filters))

        deltas = {}
        if previous is not None:
            prev = previous.as_dict()
            cur = current.as_dict()
            for key in ("cost_usd", "total_tokens", "calls", "avg_cost_per_call", "error_rate", "avg_latency_ms"):
                deltas[key] = _pct_change(cur[key], prev[key])

        unpriced_models = sorted({r.model for r in records if not r.priced and r.status == "success"})
        return {
            **current.as_dict(),
            "previous": previous.as_dict() if previous is not None else None,
            "deltas": deltas,
            "unpriced_models": unpriced_models,
            "first_call_at": records[0].created_at if records else None,
            "last_call_at": records[-1].created_at if records else None,
            "budget": self.budget_status(filters.project_id),
        }

    def budget_status(self, project_id=None) -> dict[str, Any]:
        settings = self.get_settings()
        now = _utcnow()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        next_month = (month_start + timedelta(days=32)).replace(day=1)
        spend = _totals(
            self.store.query(TokenUsageFilters(start=month_start, end=next_month, project_id=project_id))
        ).cost
        elapsed = max((now - month_start).total_seconds(), 1.0)
        month_seconds = (next_month - month_start).total_seconds()
        projected = spend / elapsed * month_seconds
        budget = settings.monthly_budget_usd
        used_pct = round(spend / budget * 100, 1) if budget else None
        projected_pct = round(projected / budget * 100, 1) if budget else None
        if not budget:
            state = "none"
        elif spend >= budget:
            state = "exceeded"
        elif used_pct >= settings.alert_threshold_pct or projected >= budget:
            state = "warning"
        else:
            state = "ok"
        return {
            "monthly_budget_usd": budget,
            "alert_threshold_pct": settings.alert_threshold_pct,
            "month_to_date_usd": round(spend, 6),
            "projected_month_end_usd": round(projected, 6),
            "used_pct": used_pct,
            "projected_pct": projected_pct,
            "state": state,
            "month_start": month_start,
            "month_end": next_month,
        }

    def breakdown(self, filters: TokenUsageFilters, group_by: str = "agent") -> dict[str, Any]:
        if group_by not in GROUP_FIELDS:
            raise ValueError(f"group_by must be one of {GROUP_FIELDS}")
        records = self.records(filters)
        groups: dict[str, _Totals] = defaultdict(_Totals)
        models: dict[str, set[str]] = defaultdict(set)
        roles: dict[str, set[str]] = defaultdict(set)
        for record in records:
            key = getattr(record, group_by)
            key = str(key) if key is not None else "(none)"
            groups[key].add(record)
            models[key].add(record.model)
            if record.role:
                roles[key].add(record.role)
        grand_cost = sum(t.cost for t in groups.values())
        grand_tokens = sum(t.total for t in groups.values())
        rows = []
        for key, totals in groups.items():
            row = {
                "key": key,
                **totals.as_dict(),
                "cost_share_pct": round(totals.cost / grand_cost * 100, 2) if grand_cost else 0,
                "token_share_pct": round(totals.total / grand_tokens * 100, 2) if grand_tokens else 0,
                "models": sorted(models[key]),
                "roles": sorted(roles[key]),
            }
            if group_by == "agent":
                row["stage"] = stage_for(key)
            rows.append(row)
        rows.sort(key=lambda r: (r["cost_usd"], r["total_tokens"]), reverse=True)
        return {"group_by": group_by, "rows": rows}

    def timeseries(
        self, filters: TokenUsageFilters, bucket: str = "day", split_by: str | None = None
    ) -> dict[str, Any]:
        if bucket not in BUCKETS:
            raise ValueError(f"bucket must be one of {tuple(BUCKETS)}")
        if split_by and split_by not in GROUP_FIELDS:
            raise ValueError(f"split_by must be one of {GROUP_FIELDS}")
        records = self.records(filters)
        step = BUCKETS[bucket]

        start = filters.start or (records[0].created_at if records else _utcnow() - step * 6)
        end = filters.end or _utcnow()
        first = _floor(start, bucket)
        if (end - first) / step > MAX_BUCKETS:
            first = _floor(end - step * MAX_BUCKETS, bucket)
        labels: list[datetime] = []
        cursor = first
        while cursor < end and len(labels) < MAX_BUCKETS:
            labels.append(cursor)
            cursor += step
        if not labels:
            labels.append(first)
        index = {label: i for i, label in enumerate(labels)}

        def key_of(record: TokenUsageRecord) -> str:
            if not split_by:
                return "All"
            value = getattr(record, split_by)
            return str(value) if value is not None else "(none)"

        # Keep the biggest series by cost, fold the rest into "Other".
        cost_by_key: dict[str, float] = defaultdict(float)
        for record in records:
            cost_by_key[key_of(record)] += record.cost_usd + record.total_tokens * 1e-12
        top = [k for k, _ in sorted(cost_by_key.items(), key=lambda kv: kv[1], reverse=True)[:MAX_SERIES]]

        series: dict[str, dict[str, list]] = {}

        def series_for(key: str) -> dict[str, list]:
            if key not in series:
                n = len(labels)
                series[key] = {"tokens": [0] * n, "prompt_tokens": [0] * n, "completion_tokens": [0] * n,
                               "cost_usd": [0.0] * n, "calls": [0] * n}
            return series[key]

        for record in records:
            i = index.get(_floor(record.created_at, bucket))
            if i is None:
                continue
            key = key_of(record)
            target = series_for(key if key in top else "Other")
            target["tokens"][i] += record.total_tokens
            target["prompt_tokens"][i] += record.prompt_tokens
            target["completion_tokens"][i] += record.completion_tokens
            target["cost_usd"][i] += record.cost_usd
            target["calls"][i] += 1

        ordered = [k for k in top if k in series] + (["Other"] if "Other" in series else [])
        return {
            "bucket": bucket,
            "split_by": split_by,
            "labels": labels,
            "series": [
                {"key": key, **{k: ([round(v, 6) for v in vals] if k == "cost_usd" else vals)
                                for k, vals in series[key].items()}}
                for key in ordered
            ],
        }

    _SORTS = {"created_at", "cost_usd", "total_tokens", "latency_ms", "agent", "model"}

    def calls(
        self, filters: TokenUsageFilters, page: int = 1, page_size: int = 25, sort: str = "-created_at"
    ) -> dict[str, Any]:
        field = sort.lstrip("-")
        if field not in self._SORTS:
            raise ValueError(f"sort must be one of {sorted(self._SORTS)}, optionally prefixed with '-'")
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        records = self.records(filters)
        records.sort(key=lambda r: getattr(r, field), reverse=sort.startswith("-"))
        start = (page - 1) * page_size
        return {
            "page": page,
            "page_size": page_size,
            "total": len(records),
            "items": [
                {**r.model_dump(mode="json"), "stage": stage_for(r.agent)}
                for r in records[start:start + page_size]
            ],
        }

    def facets(self, filters: TokenUsageFilters) -> dict[str, list[str]]:
        records = self.records(filters)
        result: dict[str, list[str]] = {}
        for field in ("agent", "model", "role", "provider", "status", "operation", "project_id"):
            values = {getattr(r, field) for r in records}
            result[field] = sorted(str(v) for v in values if v is not None)
        return result

    def export_csv(self, filters: TokenUsageFilters) -> str:
        records = self.records(filters)
        columns = list(TokenUsageRecord.model_fields) + ["stage"]
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns)
        writer.writeheader()
        for record in records:
            writer.writerow({**record.model_dump(mode="json"), "stage": stage_for(record.agent)})
        return buffer.getvalue()

    def pricing(self, filters: TokenUsageFilters | None = None) -> list[dict[str, Any]]:
        """Effective price for every configured model and every model seen in usage."""
        settings = get_settings()
        from backend.services.llm import model_string_for

        seen: dict[tuple[str, str], set[str]] = defaultdict(set)
        for role, config in settings.llm_roles.items():
            for model in config.models:
                seen[(config.provider, model)].add(role)
        for record in self.records(filters or TokenUsageFilters()):
            seen[(record.provider, record.model)]
            if record.role:
                seen[(record.provider, record.model)].add(record.role)

        overrides = self._overrides()
        rows = []
        for (provider, model), roles in sorted(seen.items()):
            model_string = model_string_for(provider, model)
            price = resolve_price(provider=provider, model=model, model_string=model_string, overrides=overrides)
            rows.append({
                "provider": provider,
                "model": model,
                "model_string": model_string,
                "roles": sorted(roles),
                "input_per_1m": round(price.input_per_1m, 6),
                "output_per_1m": round(price.output_per_1m, 6),
                "source": price.source,
            })
        return rows


_service: TokenUsageService | None = None
_service_lock = threading.Lock()


def get_token_usage_service() -> TokenUsageService:
    global _service
    with _service_lock:
        if _service is None:
            _service = TokenUsageService()
            atexit.register(_service.flush)
        return _service


def set_token_usage_service(service: TokenUsageService | None) -> None:
    """Swap the process wide service. Tests use this with an in-memory store."""
    global _service
    with _service_lock:
        _service = service


def record_llm_call(**kwargs) -> None:
    """Entry point for LLMService. Never raises."""
    try:
        get_token_usage_service().record(**kwargs)
    except Exception:
        logger.exception("Token usage recording failed; the LLM call is unaffected")
