"""
LocatorHealingService (spec 0011).

Heals object repository elements whose every stored locator failed in a run:

* targets come from the run's `[OBJREPO]` markers (`find_heal_targets`),
* candidates come only from the failed run's `harvest.json`, redacted the same
  way harvest ingest redacts them,
* candidates are scored with fixed rules first; the LLM is asked only when the
  answer is unclear, and can only pick one of the candidates,
* a confident match is applied in one `ObjectRepositoryStore.mutate`, a less
  certain one is saved as a suggestion, and a heal whose rerun fails is put
  back and turned into a suggestion. When the healed key resolved but the test
  still failed for an automation reason, the heal waits for the script repair
  (stage 2, `ScriptRepairService`) and that rerun decides it.

Heals change locators only here, inside `mutate`, and never touch a stored
test case script. Every public method logs and swallows its errors (AC-15).
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import UUID

from pydantic import BaseModel

from backend.models.execution_result import ExecutionResult, ExecutionStatus
from backend.models.object_repository import (
    HEAL_DECIDED_KEEP,
    MAX_LOCATORS,
    LocatorHeal,
    ObjectElement,
    ObjectPage,
    ProjectObjectRepository,
)
from backend.repository.object_repository_store import (
    ObjectRepositoryIntegrityError,
    ObjectRepositoryUnit,
    get_object_repository_store,
)
from backend.services import object_repository_harvest as harvest
from backend.services.object_repository_service import (
    ObjectRepositoryConflict,
    ObjectRepositoryError,
    ObjectRepositoryNotFound,
    ObjectRepositoryService,
    _replace_locators,
    pattern_matches,
)

logger = logging.getLogger(__name__)

_FAILED = (ExecutionStatus.FAILED.value, ExecutionStatus.ERROR.value)
ENVIRONMENT_ISSUE = "Test Environment Issue"
_CSS_ID = re.compile(r"^#([A-Za-z_][\w-]*)$")
_CSS_NAME = re.compile(r"""^\[name=(['"])(.+)\1\]$""")
_EVIDENCE_FILES = ("report.json", harvest.HARVEST_FILE)

Audit = Callable[[UUID, str, dict], None]
RouteAudit = Callable[[str, dict], None]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _status(result: ExecutionResult) -> str:
    return getattr(result.status, "value", result.status)


def _norm(text) -> str:
    return " ".join(str(text or "").lower().split())


def _settings():
    from backend.config.settings import get_settings

    return get_settings().object_repository


class HealSelectionResponse(BaseModel):
    """The LLM's pick among the candidates (AC-6). It never returns a locator."""

    candidate_index: int | None = None
    confidence: float = 0.0
    reason: str = ""


# ------------------------------------------------------------------ targets

def latest_results(results: list[ExecutionResult]) -> dict[UUID, ExecutionResult]:
    latest: dict[UUID, ExecutionResult] = {}
    for result in results:
        latest[result.test_case_id] = result
    return latest


def find_heal_targets(state) -> dict[str, list[ExecutionResult]]:
    """Failing keys and the failed results that failed them (AC-1).

    A target is a key with an `ok: false` marker (any attempt) in the latest
    result of a failed or errored test case that is not an environment issue.
    Passed results never produce targets. A key failing in several test
    cases is one target. Pure function of the state.
    """
    targets: dict[str, list[ExecutionResult]] = {}
    for result in latest_results(state.execution_results).values():
        if _status(result) not in _FAILED or result.failure_category == ENVIRONMENT_ISSUE:
            continue
        lines = state.run_marker_lines.get(str(result.id)) or []
        for marker in ObjectRepositoryService.parse_markers(lines):
            if marker["ok"]:
                continue
            bucket = targets.setdefault(marker["key"], [])
            if result not in bucket:
                bucket.append(result)
    return targets


def pending_verification(state) -> list[dict]:
    """Applied heals from this run that a rerun has not been checked against yet."""
    return [heal for heal in state.heal_results if heal.get("status") == "applied" and not heal.get("verified")]


# ------------------------------------------------------------------ scoring

def _expected_signals(element: ObjectElement) -> dict:
    """Signals read from the stored locators through the AC-4 strategy table."""
    signals = {"test_id": "", "role": "", "name": "", "label": "", "placeholder": "", "id": "", "attr_name": ""}
    for locator in sorted(element.locators, key=lambda loc: loc.position):
        value = locator.value
        if locator.strategy == "test_id":
            signals["test_id"] = signals["test_id"] or _norm(value)
        elif locator.strategy == "role":
            signals["role"] = signals["role"] or _norm(value)
            signals["name"] = signals["name"] or _norm((locator.options or {}).get("name"))
        elif locator.strategy == "label":
            signals["label"] = signals["label"] or _norm(value)
        elif locator.strategy == "placeholder":
            signals["placeholder"] = signals["placeholder"] or _norm(value)
        elif locator.strategy == "text":
            signals["name"] = signals["name"] or _norm(value)
        elif locator.strategy == "css":
            css_id = _CSS_ID.match(value.strip())
            css_name = _CSS_NAME.match(value.strip())
            if css_id:
                signals["id"] = signals["id"] or _norm(css_id.group(1))
            elif css_name:
                signals["attr_name"] = signals["attr_name"] or _norm(css_name.group(2))
    return signals


def _candidate_signals(candidate: dict) -> dict:
    """Signals read from a harvested candidate's locators and attributes."""
    attributes = candidate.get("attributes") or {}
    by_strategy: dict[str, dict] = {}
    for locator in candidate.get("locators") or []:
        by_strategy.setdefault(locator.get("strategy"), locator)
    role_locator = by_strategy.get("role") or {}
    return {
        "test_id": _norm(attributes.get("data-testid") or (by_strategy.get("test_id") or {}).get("value")),
        "role": _norm(candidate.get("role") or role_locator.get("value")),
        "name": _norm(
            (role_locator.get("options") or {}).get("name")
            or (by_strategy.get("text") or {}).get("value")
            or candidate.get("label")
        ),
        "label": _norm((by_strategy.get("label") or {}).get("value") or candidate.get("label")),
        "placeholder": _norm(candidate.get("placeholder") or (by_strategy.get("placeholder") or {}).get("value")),
        "id": _norm(attributes.get("id")),
        "attr_name": _norm(attributes.get("name")),
    }


def _equal(a: str, b: str) -> bool:
    return bool(a) and a == b


def score_candidate(element: ObjectElement, candidate: dict) -> tuple[float, bool]:
    """(score, strong) for one candidate (AC-4). strong = test id or role match, for ties."""
    fingerprint = candidate.get("fingerprint")
    if fingerprint and element.fingerprint and fingerprint == element.fingerprint:
        return 1.0, True
    expected = _expected_signals(element)
    actual = _candidate_signals(candidate)
    score = 0.0
    test_id = _equal(expected["test_id"], actual["test_id"])
    role = _equal(expected["role"], actual["role"])
    if test_id:
        score += 0.35
    if role:
        score += 0.15
    if _equal(expected["id"], actual["id"]) or _equal(expected["attr_name"], actual["attr_name"]):
        score += 0.15
    texts_expected = [t for t in (expected["name"], expected["label"]) if t]
    texts_actual = [t for t in (actual["name"], actual["label"]) if t]
    if texts_expected and texts_actual:
        ratio = max(difflib.SequenceMatcher(None, a, b).ratio() for a in texts_expected for b in texts_actual)
        score += 0.25 * ratio
    if _equal(expected["placeholder"], actual["placeholder"]):
        score += 0.10
    if candidate.get("element_type") != element.element_type:
        score -= 0.30
    return max(0.0, min(1.0, round(score, 4))), test_id or role


@dataclass
class Scored:
    candidate: dict
    score: float
    strong: bool
    order: int


def rank_candidates(element: ObjectElement, page: ObjectPage, candidates: list[dict]) -> list[Scored]:
    """Score, exclude other elements' fingerprints, and rank (AC-4).

    A candidate whose fingerprint belongs to another element on the page is
    excluded. Soft deleted elements count too, because they keep their
    fingerprint and the page fingerprint index would reject the move.
    """
    others = {e.fingerprint for e in page.elements if e.id != element.id and e.fingerprint}
    ranked = []
    for order, candidate in enumerate(candidates):
        if candidate.get("fingerprint") and candidate["fingerprint"] in others:
            continue
        score, strong = score_candidate(element, candidate)
        ranked.append(Scored(candidate, score, strong, order))
    ranked.sort(key=lambda item: (-item.score, not item.strong, item.order))
    return ranked


# ------------------------------------------------------------------ plan

@dataclass
class HealPlan:
    """One target's decision, computed outside `mutate` and applied inside it."""

    key: str
    element_id: UUID
    status: str  # "applied" or "suggested"
    method: str
    confidence: float
    reason: str
    candidate: dict
    new_locators: list[dict]
    candidate_fingerprint: str | None
    locators_version: int
    execution_id: UUID | None
    test_case_ids: list[UUID] = field(default_factory=list)
    heal_id: UUID | None = None
    applied: bool = False


def _locator_dicts(element: ObjectElement) -> list[dict]:
    return [
        {"strategy": loc.strategy, "value": loc.value, "options": dict(loc.options or {})}
        for loc in sorted(element.locators, key=lambda loc: loc.position)
    ]


def _merge_locators(element: ObjectElement, new_locators: list[dict]) -> list[dict]:
    """Candidate locators first, old ones after as backups, at most five (AC-7).

    The old locators with the highest failure_count drop off first.
    """
    signature = lambda loc: (loc["strategy"], loc["value"], json.dumps(loc.get("options") or {}, sort_keys=True))
    taken = {signature(loc) for loc in new_locators}
    old = [
        (loc, data)
        for loc, data in zip(sorted(element.locators, key=lambda l: l.position), _locator_dicts(element))
        if signature(data) not in taken
    ]
    room = max(0, MAX_LOCATORS - len(new_locators))
    if len(old) > room:
        drop = sorted(range(len(old)), key=lambda i: (-old[i][0].failure_count, -i))[: len(old) - room]
        old = [pair for i, pair in enumerate(old) if i not in set(drop)]
    return (new_locators + [data for _, data in old])[:MAX_LOCATORS]


def _main_locator(locators: list[dict]) -> str | None:
    if not locators:
        return None
    return f"{locators[0]['strategy']}={locators[0]['value']}"


def trim_heals(element: ObjectElement) -> None:
    """Retention (AC-16): every suggestion, the latest applied or accepted heal,
    and the last 10 other decided heals; the oldest are trimmed first."""
    keep = {heal.id for heal in element.heals if heal.status == "suggested"}
    active = [heal for heal in element.heals if heal.status in ("applied", "accepted")]
    if active:
        keep.add(max(active, key=lambda heal: heal.created_at).id)
    decided = sorted(
        (heal for heal in element.heals if heal.id not in keep),
        key=lambda heal: heal.created_at,
    )
    keep.update(heal.id for heal in decided[-HEAL_DECIDED_KEEP:])
    element.heals = [heal for heal in element.heals if heal.id in keep]


def _replace_suggestion(element: ObjectElement, now: datetime) -> None:
    """Mark an older pending suggestion rejected, so at most one stays pending (AC-8)."""
    for heal in element.heals:
        if heal.status == "suggested":
            heal.status = "rejected"
            heal.reason = "replaced by a newer suggestion"
            heal.decided_by = None
            heal.decided_at = now


def default_audit(project_id: UUID, action: str, changes: dict) -> None:
    """One audit entry with no user, for automatic heal changes (AC-13)."""
    try:
        from backend.database.db import SessionLocal
        from backend.services.audit_service import log_audit

        session = SessionLocal()
        try:
            log_audit(session, None, project_id, action, "OBJECT_REPOSITORY", changes)
        finally:
            session.close()
    except Exception:
        logger.warning("Could not write the heal audit entry: project_id=%s action=%s", project_id, action)


class LocatorHealingService:
    def __init__(
        self,
        project_repo=None,
        store=None,
        llm_service=None,
        execution_service=None,
        artifacts_root: Path | None = None,
        audit: Audit | None = None,
        settings=None,
    ):
        self._project_repo = project_repo
        self._store = store
        self._llm_service = llm_service
        self._execution_service = execution_service
        self._artifacts_root = artifacts_root
        self.audit = audit or default_audit
        self._settings = settings

    # ----------------------------------------------------------- wiring

    @property
    def project_repo(self):
        if self._project_repo is not None:
            return self._project_repo
        from backend.repository.project_repository import get_project_repository

        return get_project_repository()

    @property
    def store(self):
        return self._store or get_object_repository_store(self.project_repo)

    @property
    def settings(self):
        return self._settings or _settings().healing

    @property
    def artifacts_root(self) -> Path:
        if self._artifacts_root is not None:
            return Path(self._artifacts_root)
        from backend.services.playwright_runner import ARTIFACTS_ROOT

        return ARTIFACTS_ROOT

    @property
    def execution_service(self):
        if self._execution_service is None:
            from backend.services.execution_service import ExecutionService

            self._execution_service = ExecutionService()
        return self._execution_service

    @property
    def llm_service(self):
        if self._llm_service is None:
            from backend.services.llm import LLMService

            self._llm_service = LLMService(role="analysis", agent="self_healing_agent")
        return self._llm_service

    # ----------------------------------------------------------- heal phase

    def heal(self, state, attempt: int) -> list[dict]:
        """Run the heal phase for every target (AC-1 to AC-8). Never raises.

        Returns heal_results entries for the heals it saved. Updates the
        failed results' artifact paths when a heal is applied (AC-3).
        """
        try:
            return self._heal(state, attempt)
        except Exception:
            logger.exception("Self healing failed: project_id=%s", state.project_id)
            return []

    def _heal(self, state, attempt: int) -> list[dict]:
        project_id = state.project_id
        if project_id is None or self.store is None:
            return []
        targets = find_heal_targets(state)
        healed_before = {heal["key"] for heal in state.heal_results}
        targets = {key: results for key, results in targets.items() if key not in healed_before}
        if not targets:
            return []

        # 1. Failure counts land on the locators that actually failed.
        failed_results = list({r.id: r for results in targets.values() for r in results}.values())
        for result in failed_results:
            if str(result.id) in state.run_marker_lines:
                self.execution_service.apply_object_repository_markers(
                    project_id, result.id, state.run_marker_lines[str(result.id)]
                )
                state.markers_applied_result_ids.append(str(result.id))

        # 2. The version each heal is checked against, read after the markers.
        repository = self.store.load(project_id)

        # 3. Score and decide.
        plans: list[HealPlan] = []
        for key, results in targets.items():
            try:
                plan = self._plan(project_id, repository, key, results)
            except Exception:
                logger.exception("Self healing could not decide a heal for %s", key)
                continue
            if plan is not None:
                plans.append(plan)
        if not plans:
            return []

        # 4. Apply in one mutate.
        saved = self._save_plans(project_id, plans)
        heal_results = []
        for plan in saved:
            heal_results.append({
                "heal_id": str(plan.heal_id),
                "key": plan.key,
                "status": "applied" if plan.applied else "suggested",
                "confidence": plan.confidence,
                "method": plan.method,
                "test_case_ids": [str(t) for t in plan.test_case_ids],
                "locators_version": plan.locators_version,
                "old_main_locator": plan.candidate.get("_old_main"),
                "new_main_locator": _main_locator(plan.new_locators),
            })
            if plan.applied:
                self.audit(project_id, "heal_applied", {
                    "entity": "object_locator_heal", "id": str(plan.heal_id), "key": plan.key,
                    "before": {"main_locator": plan.candidate.get("_old_main")},
                    "after": {"main_locator": _main_locator(plan.new_locators)},
                })
        if any(plan.applied for plan in saved):
            applied_cases = {t for plan in saved if plan.applied for t in plan.test_case_ids}
            for result in failed_results:
                if result.test_case_id in applied_cases:
                    self.keep_evidence(result, attempt)
        return heal_results

    def _plan(self, project_id: UUID, repository: ProjectObjectRepository, key: str, results: list[ExecutionResult]) -> HealPlan | None:
        element = repository.element_by_key(key)
        if element is None:
            logger.info("Self healing skipped an unknown key: %s", key)
            return None
        page = repository.find_page(element.page_id)
        candidates = self._candidates(project_id, page, results)
        if not candidates:
            logger.info("no harvest evidence for %s", key)
            return None
        ranked = rank_candidates(element, page, candidates)
        if not ranked:
            logger.info("no harvest evidence for %s", key)
            return None

        settings = self.settings
        top = ranked[0]
        margin = top.score - ranked[1].score if len(ranked) > 1 else top.score
        status = method = None
        chosen, confidence, reason = top, top.score, ""

        if top.score >= settings.auto_apply_threshold and margin >= settings.min_margin:
            status, method = "applied", "deterministic"
            reason = f"Deterministic match from the run's harvest (score {top.score:.2f}, lead {margin:.2f})."
        elif top.score >= settings.suggest_threshold:
            if settings.llm_enabled:
                picked = self._ask_llm(element, key, ranked[: settings.max_candidates_for_llm])
                if picked is None:
                    status, method = "suggested", "deterministic"
                    reason = f"The LLM call failed; best deterministic match (score {top.score:.2f})."
                else:
                    index, llm_confidence, llm_reason = picked
                    if index is None:
                        return None
                    chosen = ranked[index]
                    confidence = round(min(llm_confidence, chosen.score + 0.2), 4)
                    method, reason = "llm", llm_reason or "Picked by the LLM among the harvested candidates."
                    if confidence >= settings.auto_apply_threshold:
                        status = "applied"
                    elif confidence >= settings.suggest_threshold:
                        status = "suggested"
                    else:
                        return None
            else:
                status, method = "suggested", "deterministic"
                reason = f"Unclear deterministic match (score {top.score:.2f}, lead {margin:.2f})."
        else:
            logger.info("Self healing found no match for %s (best score %.2f)", key, top.score)
            return None

        if status == "applied" and element.manually_edited:
            status = "suggested"
            reason += " Not applied automatically because a tester edited this element."

        new_locators, _ = ObjectRepositoryService.order_recon_locators(chosen.candidate.get("locators") or [])
        if not new_locators:
            return None
        candidate = dict(chosen.candidate)
        candidate["_old_main"] = _main_locator(_locator_dicts(element))
        return HealPlan(
            key=key,
            element_id=element.id,
            status=status,
            method=method,
            confidence=max(0.0, min(1.0, confidence)),
            reason=reason.strip(),
            candidate=candidate,
            new_locators=new_locators,
            candidate_fingerprint=chosen.candidate.get("fingerprint"),
            locators_version=element.locators_version,
            execution_id=results[0].id,
            test_case_ids=list(dict.fromkeys(r.test_case_id for r in results)),
        )

    def _candidates(self, project_id: UUID, page: ObjectPage, results: list[ExecutionResult]) -> list[dict]:
        """Redacted, fingerprinted candidates from the failed runs' harvests (AC-2)."""
        candidates: list[dict] = []
        seen: set[str] = set()
        max_pages = _settings().harvest_max_pages
        for result in results:
            snapshots = harvest.read_harvest_file(self.artifacts_root / str(result.test_case_id))
            secrets = self.execution_service.harvest_secrets(project_id, result.test_case_id)
            for path, elements in harvest.parse_snapshots(snapshots, max_pages):
                if not pattern_matches(page.url_pattern, path):
                    continue
                for element in harvest.clean_elements(elements, secrets):
                    fingerprint = ObjectRepositoryService.fingerprint(element)
                    if fingerprint:
                        if fingerprint in seen:
                            continue
                        seen.add(fingerprint)
                    candidates.append({**element, "fingerprint": fingerprint})
        return candidates

    # ----------------------------------------------------------- LLM

    def _ask_llm(self, element: ObjectElement, key: str, ranked: list[Scored]) -> tuple[int | None, float, str] | None:
        """(index, confidence, reason), or None when the call failed (AC-6)."""
        from backend.utils.prompts import load_prompt

        stored = {
            "key": key,
            "description": element.description or "",
            "element_type": element.element_type,
            "locators": _locator_dicts(element),
        }
        listed = [
            {
                "index": index,
                "locators": [
                    {"strategy": loc.get("strategy"), "value": loc.get("value"), "options": loc.get("options") or {}}
                    for loc in item.candidate.get("locators") or []
                ],
                "role": item.candidate.get("role") or "",
                "label": item.candidate.get("label") or "",
                "score": item.score,
            }
            for index, item in enumerate(ranked)
        ]
        try:
            prompt = load_prompt(
                "self_healing_prompt.txt",
                element=json.dumps(stored, ensure_ascii=False),
                candidates=json.dumps(listed, ensure_ascii=False),
            )
            response = self.llm_service.structured_generate(user=prompt, response_model=HealSelectionResponse)
        except Exception as exc:
            logger.warning("Self healing LLM call failed for %s: %s", key, type(exc).__name__)
            return None
        index = response.candidate_index
        if index is not None and not 0 <= index < len(ranked):
            index = None
        confidence = max(0.0, min(1.0, float(response.confidence or 0.0)))
        return index, confidence, (response.reason or "")[:500]

    # ----------------------------------------------------------- save

    def _save_plans(self, project_id: UUID, plans: list[HealPlan]) -> list[HealPlan]:
        def change(unit: ObjectRepositoryUnit, with_suggestions: bool) -> list[HealPlan]:
            saved = []
            now = _now()
            for plan in plans:
                found = unit.repository.find_element(plan.element_id)
                if found is None or found[0].is_deleted or found[1].is_deleted:
                    continue
                element = found[1]
                status = plan.status
                if status == "applied" and element.locators_version != plan.locators_version:
                    status = "suggested"
                    plan.reason += " The element changed before the heal was applied, so it is a suggestion."
                if status == "suggested" and not with_suggestions:
                    continue
                old_locators = _locator_dicts(element)
                heal = LocatorHeal(
                    element_id=element.id,
                    project_id=project_id,
                    execution_id=plan.execution_id,
                    test_case_id=plan.test_case_ids[0] if plan.test_case_ids else None,
                    status=status,
                    method=plan.method,
                    confidence=plan.confidence,
                    old_locators=old_locators,
                    new_locators=plan.new_locators,
                    old_fingerprint=element.fingerprint,
                    candidate_fingerprint=plan.candidate_fingerprint,
                    reason=plan.reason,
                    evidence_source="harvest",
                    locators_version=plan.locators_version,
                    created_at=now,
                )
                if status == "applied":
                    _replace_locators(element, _merge_locators(element, plan.new_locators))
                    element.fingerprint = plan.candidate_fingerprint
                    element.health = "needs_review"
                    element.locators_version += 1
                    element.updated_at = now
                    heal.locators_version = element.locators_version
                else:
                    _replace_suggestion(element, now)
                element.heals.append(heal)
                trim_heals(element)
                plan.heal_id = heal.id
                plan.applied = status == "applied"
                plan.status = status
                plan.locators_version = heal.locators_version
                saved.append(plan)
            return saved

        store = self.store
        try:
            return store.mutate(project_id, lambda unit: change(unit, True))
        except ObjectRepositoryIntegrityError:
            # Another run saved a suggestion for one of these elements first
            # (AC-8). The store already rolled back and closed its session.
            logger.warning("A concurrent heal suggestion won; dropping this run's suggestions: project_id=%s", project_id)
            for plan in plans:
                plan.heal_id, plan.applied = None, False
            return store.mutate(project_id, lambda unit: change(unit, False))

    # ----------------------------------------------------------- evidence

    def keep_evidence(self, result: ExecutionResult, attempt: int) -> None:
        """Copy the failed run's evidence before the rerun overwrites it (AC-3)."""
        try:
            run_dir = self.artifacts_root / str(result.test_case_id)
            heal_dir = run_dir / f"heal-{attempt}"
            heal_dir.mkdir(parents=True, exist_ok=True)
            for name in _EVIDENCE_FILES:
                source = run_dir / name
                if source.is_file():
                    shutil.copy2(source, heal_dir / name)
            screenshots = run_dir / "screenshots"
            if screenshots.is_dir():
                shutil.copytree(screenshots, heal_dir / "screenshots", dirs_exist_ok=True)

            def copy(path: str | None) -> str | None:
                if not path:
                    return path
                source = Path(path)
                if not source.is_file():
                    return path
                try:
                    relative = source.resolve().relative_to(run_dir.resolve())
                except ValueError:
                    relative = Path(source.name)
                target = heal_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                return str(target)

            if result.trace_path:
                context = Path(result.trace_path).parent / "error-context.md"
                if context.is_file():
                    copy(str(context))
            result.trace_path = copy(result.trace_path)
            result.video_path = copy(result.video_path)
            result.screenshot_path = copy(result.screenshot_path)
        except Exception:
            logger.exception("Keeping heal evidence failed: test_case_id=%s", result.test_case_id)

    # ----------------------------------------------------------- verify

    def verify(self, state, defer: Callable[[dict], bool] | None = None) -> list[str]:
        """Check each applied heal against its stage 1 rerun (AC-10). Never raises.

        A heal stays applied when every rerun passed. A heal whose key resolved
        in the rerun while the test still failed waits for stage 2 when `defer`
        says the script repair will run (`verified = "pending_script_repair"`).
        Otherwise the old locators and fingerprint go back and the heal becomes
        a suggestion. Returns the keys that were put back.
        """
        try:
            return self._verify(state, defer)
        except Exception:
            logger.exception("Self healing verify failed: project_id=%s", state.project_id)
            for heal in pending_verification(state):
                heal["verified"] = "error"
            return []

    @staticmethod
    def key_resolved(state, heal: dict) -> bool:
        """The healed key was looked up in every rerun and never failed (AC-10)."""
        latest = {str(k): v for k, v in latest_results(state.execution_results).items()}
        seen = False
        for test_case_id in heal.get("test_case_ids", []):
            result = latest.get(str(test_case_id))
            if result is None:
                return False
            lines = state.run_marker_lines.get(str(result.id)) or []
            for marker in ObjectRepositoryService.parse_markers(lines):
                if marker["key"] != heal.get("key"):
                    continue
                if not marker["ok"]:
                    return False
                seen = True
        return seen

    def _verify(self, state, defer: Callable[[dict], bool] | None = None) -> list[str]:
        pending = pending_verification(state)
        if not pending:
            return []
        latest = {str(k): v for k, v in latest_results(state.execution_results).items()}
        failed: list[dict] = []
        for heal in pending:
            outcomes = [latest.get(str(t)) for t in heal.get("test_case_ids", [])]
            passed = bool(outcomes) and all(
                r is not None and _status(r) == ExecutionStatus.PASSED.value and r.heal_rerun_of is not None
                for r in outcomes
            )
            if passed:
                heal["verified"] = "passed"
            elif defer is not None and self.key_resolved(state, heal) and defer(heal):
                heal["verified"] = "pending_script_repair"
            else:
                heal["verified"] = "failed"
                failed.append(heal)
        return self._revert(state, failed)

    def finish_pending(self, state, test_case_ids: list[str], passed: bool) -> list[str]:
        """Decide heals that waited for stage 2 on these test cases (AC-10). Never raises.

        Kept when the stage 2 rerun passed, put back as suggestions otherwise.
        Returns the keys that were put back.
        """
        ids = {str(t) for t in test_case_ids}
        waiting = [
            heal for heal in state.heal_results
            if heal.get("verified") == "pending_script_repair"
            and ids & {str(t) for t in heal.get("test_case_ids", [])}
        ]
        for heal in waiting:
            heal["verified"] = "passed" if passed else "failed"
        if passed or not waiting:
            return []
        try:
            return self._revert(state, waiting)
        except Exception:
            logger.exception("Self healing revert after script repair failed: project_id=%s", state.project_id)
            return []

    def _revert(self, state, failed: list[dict]) -> list[str]:
        if not failed or state.project_id is None or self.store is None:
            return []

        def change(unit: ObjectRepositoryUnit) -> list[str]:
            reverted = []
            now = _now()
            for entry in failed:
                heal_id = UUID(entry["heal_id"])
                for page in unit.repository.pages:
                    for element in page.elements:
                        heal = next((h for h in element.heals if h.id == heal_id), None)
                        if heal is None:
                            continue
                        if element.locators_version != heal.locators_version:
                            logger.warning(
                                "Heal rerun failed but %s changed meanwhile; leaving its locators alone", entry["key"]
                            )
                        else:
                            _replace_locators(element, [dict(loc) for loc in heal.old_locators])
                            element.fingerprint = heal.old_fingerprint
                            element.locators_version += 1
                            element.updated_at = now
                        _replace_suggestion(element, now)
                        heal.status = "suggested"
                        heal.reason = (heal.reason + " The rerun still failed, so the heal was put back.").strip()
                        heal.locators_version = element.locators_version
                        entry["status"] = "suggested"
                        entry["locators_version"] = element.locators_version
                        reverted.append(entry["key"])
            return reverted

        reverted = self.store.mutate(state.project_id, change)
        for entry in failed:
            if entry["key"] in reverted:
                self.audit(state.project_id, "heal_reverted", {
                    "entity": "object_locator_heal", "id": entry["heal_id"], "key": entry["key"],
                    "before": {"main_locator": entry.get("new_main_locator")},
                    "after": {"main_locator": entry.get("old_main_locator")},
                    "reason": "rerun failed",
                })
        return reverted

    # ----------------------------------------------------------- tester decisions (AC-12, AC-13)
    #
    # Unlike the run phases above, these raise ObjectRepositoryError subclasses
    # so the routes can answer 404 or 409. A raise inside `mutate` saves nothing.

    def _require_store(self, project_id: UUID):
        if not self.project_repo.get_project(project_id):
            raise ObjectRepositoryNotFound("Project not found")
        store = self.store
        if store is None:
            raise ObjectRepositoryError("The object repository is not available for this repository provider")
        return store

    @staticmethod
    def _find_heal(unit_repository: ProjectObjectRepository, project_id: UUID, heal_id: UUID):
        """The heal with its page and element. 404 for an unknown heal, one in
        another project, or one whose element or page is soft deleted."""
        for page in unit_repository.pages:
            for element in page.elements:
                for heal in element.heals:
                    if heal.id != heal_id:
                        continue
                    if heal.project_id != project_id or page.is_deleted or element.is_deleted:
                        break
                    return page, element, heal
        raise ObjectRepositoryNotFound("Heal not found")

    @staticmethod
    def _require_status(heal: LocatorHeal, allowed: tuple[str, ...]) -> None:
        if heal.status not in allowed:
            raise ObjectRepositoryConflict(f"heal is {heal.status}")

    @staticmethod
    def _require_version(element: ObjectElement, heal: LocatorHeal) -> None:
        if element.locators_version != heal.locators_version:
            raise ObjectRepositoryConflict("element changed, review again")

    def list_heals(self, project_id: UUID, status: str | None = None) -> list[tuple[ObjectPage, LocatorHeal]]:
        """The project's heals on active elements, newest first (no pagination;
        retention bounds the list)."""
        store = self._require_store(project_id)
        repository = store.load(project_id)
        heals = [
            (page, heal)
            for page in repository.active_pages()
            for element in page.active_elements()
            for heal in element.heals
            if heal.project_id == project_id and (status is None or heal.status == status)
        ]
        heals.sort(key=lambda pair: pair[1].created_at, reverse=True)
        return heals

    def _decide(self, project_id: UUID, heal_id: UUID, decide, action: str, audit: RouteAudit | None):
        store = self._require_store(project_id)
        captured: dict = {}

        def change(unit: ObjectRepositoryUnit):
            page, element, heal = self._find_heal(unit.repository, project_id, heal_id)
            captured["before"] = _main_locator(_locator_dicts(element))
            decide(element, heal, _now())
            trim_heals(element)
            return page, element, heal

        page, element, heal = store.mutate(project_id, change)
        key = f"{page.name}.{element.name}"
        if audit:
            try:
                audit(action, {
                    "entity": "object_locator_heal", "id": str(heal.id), "key": key,
                    "before": {"main_locator": captured["before"]},
                    "after": {"main_locator": _main_locator(_locator_dicts(element))},
                })
            except Exception:
                logger.warning("Could not write the heal audit entry: heal_id=%s action=%s", heal.id, action)
        return page, element, heal

    def accept_heal(self, project_id: UUID, heal_id: UUID, decided_by: UUID | None, audit: RouteAudit | None = None):
        """Apply a suggestion with the AC-7 apply steps, checked against the
        heal's recorded version. Keeps manually_edited and reruns nothing."""

        def decide(element: ObjectElement, heal: LocatorHeal, now: datetime) -> None:
            self._require_status(heal, ("suggested",))
            self._require_version(element, heal)
            # The revert source is what the element held right before this accept.
            heal.old_locators = _locator_dicts(element)
            heal.old_fingerprint = element.fingerprint
            _replace_locators(element, _merge_locators(element, [dict(loc) for loc in heal.new_locators]))
            element.fingerprint = heal.candidate_fingerprint
            element.health = "needs_review"
            element.locators_version += 1
            element.updated_at = now
            heal.status = "accepted"
            heal.locators_version = element.locators_version
            heal.decided_by, heal.decided_at = decided_by, now

        return self._decide(project_id, heal_id, decide, "heal_accepted", audit)

    def reject_heal(self, project_id: UUID, heal_id: UUID, decided_by: UUID | None, audit: RouteAudit | None = None):
        def decide(element: ObjectElement, heal: LocatorHeal, now: datetime) -> None:
            self._require_status(heal, ("suggested",))
            heal.status = "rejected"
            heal.decided_by, heal.decided_at = decided_by, now

        return self._decide(project_id, heal_id, decide, "heal_rejected", audit)

    def revert_heal(self, project_id: UUID, heal_id: UUID, decided_by: UUID | None, audit: RouteAudit | None = None):
        """Put back the locators and fingerprint an applied or accepted heal replaced."""

        def decide(element: ObjectElement, heal: LocatorHeal, now: datetime) -> None:
            self._require_status(heal, ("applied", "accepted"))
            self._require_version(element, heal)
            _replace_locators(element, [dict(loc) for loc in heal.old_locators])
            element.fingerprint = heal.old_fingerprint
            element.locators_version += 1
            element.updated_at = now
            heal.status = "reverted"
            heal.locators_version = element.locators_version
            heal.decided_by, heal.decided_at = decided_by, now

        return self._decide(project_id, heal_id, decide, "heal_reverted", audit)
