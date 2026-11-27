"""
Accessibility report processing (spec 0011).

`AccessibilityService.process` turns the raw axe-core results of one run
(`a11y.json`, or the workspace webhook's `a11y` field) into a stored
`AccessibilityReport`: normalize, merge by rule and element, redact, add the
axe fallback text, compute the gate, and upsert on `execution_id`. Graph runs
call it through `AccessibilityAgent`; batch runs and the workspace webhook call
it directly.

A scan or processing error never changes a run's functional result: `process`
never raises, and the worst case is a saved report with `status=failed`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from backend.models.accessibility import (
    GATE_REASON_DISABLED,
    GATE_REASON_SCAN_ERROR,
    IMPACT_ORDER,
    AccessibilityEnrichment,
    AccessibilityFinding,
    AccessibilityPage,
    AccessibilityReport,
    AccessibilityReportStatus,
    AccessibilitySource,
    FindingKind,
    GateMode,
    GateStatus,
    empty_counts,
)
from backend.services.redaction import clean, clean_html, secret_patterns
from backend.utils.prompts import load_prompt

logger = logging.getLogger(__name__)

A11Y_FILE_NAME = "a11y.json"
DEFAULT_GATE_MODE = GateMode.WARN
DEFAULT_BLOCK_SEVERITY = "serious"
BLOCK_SEVERITIES = ("critical", "serious")
# Reason for a run that has no report at all (AC-9: it counts as NOT_SCANNED).
GATE_REASON_NO_REPORT = "no_report"
# Spec 0012, AC-12, AC-13: a backend batch with no saved run or report yet,
# still running or already ended.
GATE_REASON_BATCH_RUNNING = "batch_running"
GATE_REASON_BATCH_NO_RUNS = "batch_no_runs"
LLM_AGENT_NAME = "accessibility_agent"
PROMPT_PATH = "agents/accessibility/roles_and_responsibilities.md"
# Caps on the LLM's text, so a runaway answer never bloats a stored report.
_AI_TEXT_LIMIT = 2000

_WCAG_TAG = re.compile(r"^wcag(\d)(\d)(\d{1,2})$")
# Repeated rows of a table or a list differ only by these indexes.
_ROW_INDEX = re.compile(r"\b(tr|li):nth-child\(\d+\)", re.IGNORECASE)
# Worst first, for the batch gate (AC-9).
_GATE_RANK = {GateStatus.BLOCKED: 0, GateStatus.WARN: 1, GateStatus.NOT_SCANNED: 2, GateStatus.PASS: 3}
# Element screenshots (AC-7, AC-12) sit in this folder inside the run folder.
# The scan session names them; only a plain file name is ever accepted back.
SCREENSHOT_DIR = "a11y-shots"
_SHOT_NAME = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,119}\.png$")


def safe_screenshot_name(name) -> str | None:
    """A screenshot name with no path in it, or None. Rejects slashes, backslashes and `..`."""
    if not isinstance(name, str) or ".." in name or not _SHOT_NAME.match(name):
        return None
    return name


def report_dir(report: AccessibilityReport) -> Path:
    """The run folder a report's files live in: the test case id for runs (the
    runner's run id), `scan_<report id>` for standalone scans."""
    from backend.services.playwright_runner import ARTIFACTS_ROOT

    if report.source == AccessibilitySource.SCAN or report.test_case_id is None:
        return ARTIFACTS_ROOT / f"scan_{report.id}"
    return ARTIFACTS_ROOT / str(report.test_case_id)


def screenshot_file(report: AccessibilityReport, name) -> Path | None:
    """The element screenshot file for `name`, only when it is a safe name, it
    stays inside the report's screenshot folder, and the file exists."""
    safe = safe_screenshot_name(name)
    if safe is None:
        return None
    folder = (report_dir(report) / SCREENSHOT_DIR).resolve()
    path = (folder / safe).resolve()
    if path.parent != folder or not path.is_file():
        return None
    return path


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def read_a11y_file(run_dir: Path) -> dict | None:
    """The raw scan a runner wrote to its run directory; None when missing or unreadable."""
    path = Path(run_dir) / A11Y_FILE_NAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def wcag_criteria(tags) -> list[str]:
    """axe tags such as `wcag143` become WCAG criteria such as `1.4.3`."""
    out: list[str] = []
    for tag in tags or []:
        match = _WCAG_TAG.match(str(tag))
        if match:
            criterion = ".".join(match.groups())
            if criterion not in out:
                out.append(criterion)
    return out


def normalize_selector(target) -> str:
    """Joins nested frame and shadow DOM parts with ` >> `, and strips row indexes."""
    parts: list[str] = []

    def walk(value):
        if isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
        elif value is not None:
            parts.append(str(value).strip())

    walk(target)
    return _ROW_INDEX.sub(r"\1", " >> ".join(p for p in parts if p))


def finding_signature(rule_id: str, selector: str) -> str:
    return hashlib.sha256(f"a11y:{rule_id}:{selector}".encode("utf-8")).hexdigest()


def _impact_rank(finding: AccessibilityFinding) -> int:
    if finding.kind == FindingKind.NEEDS_REVIEW:
        return len(IMPACT_ORDER)
    return IMPACT_ORDER.index(finding.impact) if finding.impact in IMPACT_ORDER else len(IMPACT_ORDER) - 1


def _at_or_above(impact: str | None, threshold: str) -> bool:
    if impact not in IMPACT_ORDER:
        return False
    return IMPACT_ORDER.index(impact) <= IMPACT_ORDER.index(threshold)


def _snapshot_value(snapshot: dict | None, key: str):
    return (snapshot or {}).get(key)


def _truthy(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("false", "0", "no", "off", "")


class AccessibilityService:
    def __init__(self, repo=None, project_repo=None, vault=None, settings=None, llm_service=None):
        self._repo = repo
        self._project_repo = project_repo
        self._vault = vault
        self._settings = settings
        self._llm_service = llm_service

    # ------------------------------------------------------------ dependencies

    @property
    def repo(self):
        if self._repo is None:
            from backend.repository.accessibility_repository import get_accessibility_repository

            self._repo = get_accessibility_repository()
        return self._repo

    @property
    def project_repo(self):
        if self._project_repo is None:
            from backend.repository.project_repository import get_project_repository

            self._project_repo = get_project_repository()
        return self._project_repo

    @property
    def llm_service(self):
        if self._llm_service is None:
            from backend.services.llm import LLMService

            self._llm_service = LLMService(role="analysis", agent=LLM_AGENT_NAME)
        return self._llm_service

    @property
    def config(self):
        if self._settings is None:
            from backend.config.settings import get_settings

            self._settings = get_settings()
        return self._settings.accessibility

    # ------------------------------------------------------------ settings

    def scan_enabled(self, project_config: dict | None) -> bool:
        """AC-1: the global switch AND the project's `a11y_enabled` from the run snapshot."""
        try:
            enabled = self.config.enabled
        except Exception:
            return False
        return enabled and _truthy(_snapshot_value(project_config, "a11y_enabled"), True)

    @staticmethod
    def gate_settings(project_config: dict | None) -> tuple[GateMode, str]:
        """AC-8: mode and threshold saved in the run snapshot when it started."""
        try:
            mode = GateMode(_snapshot_value(project_config, "a11y_gate_mode") or DEFAULT_GATE_MODE)
        except ValueError:
            mode = DEFAULT_GATE_MODE
        severity = _snapshot_value(project_config, "a11y_block_severity") or DEFAULT_BLOCK_SEVERITY
        if severity not in BLOCK_SEVERITIES:
            severity = DEFAULT_BLOCK_SEVERITY
        return mode, severity

    # ------------------------------------------------------------ processing

    def process(
        self,
        project_id: UUID,
        execution_result=None,
        raw: dict | None = None,
        *,
        project_config: dict | None = None,
        source: AccessibilitySource = AccessibilitySource.EXECUTION,
        enabled: bool | None = None,
        base: AccessibilityReport | None = None,
        enrich: bool = True,
    ) -> AccessibilityReport:
        """Builds, gates, and saves the report of one run. Never raises.

        `base` is a standalone scan's queued report (AC-10): the result keeps its
        id, creator, creation time, and login status, so it replaces that row.
        `enrich=False` saves the axe text only; call `enrich_saved` later, so a
        caller such as the workspace webhook does not wait on the LLM."""
        snapshot = project_config
        if snapshot is None and execution_result is not None:
            snapshot = getattr(execution_result, "project_config_snapshot", None)
        mode, severity = self.gate_settings(snapshot)
        report = AccessibilityReport(project_id=project_id, source=source, gate_mode=mode)
        if base is not None:
            report.id = base.id
            report.created_at = base.created_at
            report.created_by = base.created_by
            report.login_status = base.login_status
        if execution_result is not None:
            report.execution_id = execution_result.id
            report.test_case_id = execution_result.test_case_id
            # Spec 0012, AC-6: the run's own batch id, never its test cycle.
            batch = getattr(execution_result, "batch_id", None)
            report.batch_id = str(batch) if batch else None

        try:
            report.wcag_tags = list(self.config.wcag_tags)
            if enabled is None:
                enabled = self.scan_enabled(snapshot)
            if not enabled:
                report.gate_status, report.gate_reason = GateStatus.NOT_SCANNED, GATE_REASON_DISABLED
            elif not isinstance(raw, dict):
                self._mark_failed(report)
            else:
                patterns = self._patterns(report.project_id, execution_result)
                self._fill(report, raw, patterns)
                if enrich:
                    self._enrich(report, patterns)
                if raw.get("status") == "failed":
                    self._mark_failed(report)
                else:
                    report.gate_status = self.compute_gate(report.findings, mode, severity)
        except Exception:
            logger.exception("Accessibility report processing failed: project_id=%s", project_id)
            report.findings, report.counts = [], empty_counts()
            self._mark_failed(report)

        report.finished_at = _utc_now()
        return self._upsert(report)

    @staticmethod
    def _mark_failed(report: AccessibilityReport) -> None:
        report.status = AccessibilityReportStatus.FAILED
        report.error = GATE_REASON_SCAN_ERROR
        report.gate_status = GateStatus.NOT_SCANNED
        report.gate_reason = GATE_REASON_SCAN_ERROR

    def _fill(self, report: AccessibilityReport, raw: dict, patterns: list[re.Pattern]) -> None:
        config = self.config
        report.truncated = bool(raw.get("truncated"))
        report.pages = [
            AccessibilityPage(
                url=str(page.get("url") or ""),
                title=clean(page.get("title"), patterns)[:200],
                scanned_at=page.get("scanned_at") or None,
                requires_login=bool(page.get("requires_login")),
                error=page.get("error") or None,
            )
            for page in raw.get("pages") or []
            if isinstance(page, dict) and page.get("url")
        ]

        merged: dict[tuple[str, str], AccessibilityFinding] = {}
        for page in raw.get("results") or []:
            if not isinstance(page, dict):
                continue
            url = str(page.get("url") or "")
            for kind, key in ((FindingKind.VIOLATION, "violations"), (FindingKind.NEEDS_REVIEW, "incomplete")):
                for rule in page.get(key) or []:
                    self._merge_rule(merged, kind, rule, url, patterns, config)

        findings = sorted(merged.values(), key=lambda f: (_impact_rank(f), f.rule_id, f.selector))
        report.findings = findings
        counts = empty_counts()
        for finding in findings:
            if finding.kind == FindingKind.NEEDS_REVIEW:
                counts["needs_review"] += 1
            elif finding.impact in counts:
                counts[finding.impact] += 1
        report.counts = counts

    @staticmethod
    def _merge_rule(merged, kind, rule, url, patterns, config) -> None:
        if not isinstance(rule, dict) or not rule.get("id"):
            return
        rule_id = str(rule["id"])
        for node in (rule.get("nodes") or [])[: config.max_nodes_per_rule]:
            if not isinstance(node, dict):
                continue
            selector = clean(normalize_selector(node.get("target")), patterns)
            signature = finding_signature(rule_id, selector)
            finding = merged.get((kind.value, signature))
            if finding is None:
                summary = clean(node.get("failureSummary"), patterns)
                finding = AccessibilityFinding(
                    signature=signature,
                    kind=kind,
                    rule_id=rule_id,
                    impact=node.get("impact") or rule.get("impact") or None,
                    wcag_criteria=wcag_criteria(rule.get("tags")),
                    help=clean(rule.get("help"), patterns),
                    help_url=str(rule.get("helpUrl") or ""),
                    selector=selector,
                    html_snippet=clean_html(node.get("html"), patterns),
                    failure_summary=summary,
                    # AC-4 fallback; the LLM enrichment replaces these for the top findings.
                    ai_explanation=clean(rule.get("help") or rule.get("description"), patterns),
                    ai_fix=summary,
                    ai_source="axe_fallback",
                )
                merged[(kind.value, signature)] = finding
            if finding.screenshot_path is None and kind == FindingKind.VIOLATION:
                finding.screenshot_path = safe_screenshot_name(node.get("screenshot"))
            finding.occurrences += 1
            if url and url not in finding.pages and len(finding.pages) < config.max_pages_per_finding:
                finding.pages.append(url)

    # ------------------------------------------------------------ AI guidance

    def enrich_saved(self, report: AccessibilityReport, execution_result=None) -> AccessibilityReport:
        """The deferred half of `process(enrich=False)`: explains a saved report's
        top findings and saves it again. The gate does not depend on this text,
        so it is unchanged. Never raises."""
        if report is None or report.status != AccessibilityReportStatus.SUCCEEDED or not report.findings:
            return report
        try:
            self._enrich(report, self._patterns(report.project_id, execution_result))
        except Exception:
            logger.exception("Deferred accessibility enrichment failed: execution_id=%s", report.execution_id)
            return report
        if any(f.ai_source == "llm" for f in report.findings):
            return self._upsert(report)
        return report

    def _enrich(self, report: AccessibilityReport, patterns: list[re.Pattern]) -> None:
        """AC-4: one structured LLM call explains the top findings, highest impact
        first. Findings past the cap, and every finding when the call fails, keep
        the axe fallback text `_merge_rule` set. Never raises."""
        limit = self.config.max_llm_findings
        # `_fill` sorts findings by impact (critical first, needs review last).
        top = report.findings[:limit] if limit > 0 else []
        if not top:
            return
        try:
            prompt = load_prompt(PROMPT_PATH, findings=json.dumps(
                [self._llm_input(i, f) for i, f in enumerate(top)], indent=2, ensure_ascii=False
            ))
            response = self.llm_service.structured_generate(
                user=prompt,
                response_model=AccessibilityEnrichment,
                max_retries=1,
                timeout=int(self.config.llm_timeout_seconds),
            )
        except Exception as exc:
            logger.warning(
                "Accessibility enrichment failed, using axe text: project_id=%s execution_id=%s error=%s",
                report.project_id, report.execution_id, type(exc).__name__,
            )
            return

        for item in response.items:
            if not 0 <= item.id < len(top):
                continue
            finding = top[item.id]
            explanation = clean(item.explanation, patterns).strip()[:_AI_TEXT_LIMIT]
            fix = clean(item.fix, patterns).strip()[:_AI_TEXT_LIMIT]
            review = clean(item.review_guidance, patterns).strip()[:_AI_TEXT_LIMIT]
            if finding.kind == FindingKind.NEEDS_REVIEW:
                # The UI shows `ai_fix` as "What a person must check" for these.
                fix = f"{review}\n\nIf it fails: {fix}" if review and fix else (review or fix)
            if not explanation:
                continue
            finding.ai_explanation = explanation
            if fix:
                finding.ai_fix = fix
            finding.ai_source = "llm"

    @staticmethod
    def _llm_input(index: int, finding: AccessibilityFinding) -> dict:
        """Only redacted fields reach the LLM (AC-5): `_merge_rule` cleaned them."""
        return {
            "id": index,
            "kind": finding.kind.value,
            "rule_id": finding.rule_id,
            "impact": finding.impact,
            "wcag_criteria": finding.wcag_criteria,
            "help": finding.help,
            "selector": finding.selector,
            "html_snippet": finding.html_snippet,
            "failure_summary": finding.failure_summary,
            "page_count": len(finding.pages),
            "occurrences": finding.occurrences,
        }

    def _patterns(self, project_id: UUID, execution_result) -> list[re.Pattern]:
        test_case = None
        if execution_result is not None:
            try:
                test_case = self.project_repo.get_test_case(execution_result.test_case_id)
            except Exception:
                test_case = None
        return secret_patterns(project_id, test_case, self._vault)

    @staticmethod
    def compute_gate(findings, mode: GateMode, severity: str) -> GateStatus:
        """AC-8: only violations at or above the threshold count."""
        if GateMode(mode) == GateMode.OFF:
            return GateStatus.PASS
        hit = any(
            f.kind == FindingKind.VIOLATION and _at_or_above(f.impact, severity)
            for f in findings
        )
        if not hit:
            return GateStatus.PASS
        return GateStatus.BLOCKED if GateMode(mode) == GateMode.BLOCK else GateStatus.WARN

    def _upsert(self, report: AccessibilityReport) -> AccessibilityReport:
        """One report per execution: a retried run or webhook replaces it (AC-15)."""
        try:
            if report.execution_id is not None:
                existing = self.repo.get_by_execution(report.execution_id)
                if existing is not None:
                    report.id = existing.id
                    report.created_at = existing.created_at
            self.repo.save(report)
        except Exception:
            logger.exception("Accessibility report could not be saved: execution_id=%s", report.execution_id)
        return report

    def relink_execution(self, old_id: UUID, new_id: UUID) -> None:
        """A run's id can be replaced after the graph finishes (queued executions keep
        the id the client was given). Point the report at the final id."""
        if old_id == new_id:
            return
        try:
            report = self.repo.get_by_execution(old_id)
            # The final id already having a report would break one report per run; keep it.
            if report is None or self.repo.get_by_execution(new_id) is not None:
                return
            report.execution_id = new_id
            self.repo.save(report)
        except Exception:
            logger.exception("Accessibility report could not be relinked: %s -> %s", old_id, new_id)

    # ------------------------------------------------------------ reads

    def report_for_execution(self, project_id: UUID, execution_id: UUID) -> AccessibilityReport | None:
        report = self.repo.get_by_execution(execution_id)
        return report if report is not None and report.project_id == project_id else None

    def report(self, project_id: UUID, report_id: UUID) -> AccessibilityReport | None:
        """A report of this project, or None (AC-15: another project's is a 404)."""
        report = self.repo.get(report_id)
        return report if report is not None and report.project_id == project_id else None

    def screenshot(self, project_id: UUID, report_id: UUID, name: str) -> Path | None:
        """An element screenshot of one of the report's findings (AC-12). Only a
        name a finding records is served, and only from the report's folder."""
        report = self.report(project_id, report_id)
        if report is None or not any(f.screenshot_path == name for f in report.findings):
            return None
        return screenshot_file(report, name)

    def gate_for_execution(self, project_id: UUID, execution_id: UUID) -> dict:
        report = self.report_for_execution(project_id, execution_id)
        if report is None:
            return {
                "gate_status": GateStatus.NOT_SCANNED.value,
                "gate_reason": GATE_REASON_NO_REPORT,
                "gate_mode": None,
                "runs": [{"execution_id": str(execution_id), "gate_status": GateStatus.NOT_SCANNED.value}],
            }
        return {
            "gate_status": report.gate_status.value,
            "gate_reason": report.gate_reason,
            "gate_mode": report.gate_mode.value,
            "runs": [{"execution_id": str(execution_id), "gate_status": report.gate_status.value}],
        }

    def gate_for_batch(
        self, project_id: UUID, batch_id: str, execution_ids: list[UUID], log_state: str | None = None,
    ) -> dict | None:
        """AC-9: the worst status among the batch's runs. A run with no report counts
        as NOT_SCANNED, and a batch with no reports is NOT_SCANNED.

        Spec 0012, AC-12, AC-13: with no run and no report, the backend batch log
        decides. `running` and `ended` give NOT_SCANNED answers; no log gives None.
        `in_progress` is the caller's to set."""
        reports = {r.execution_id: r for r in self.repo.list_by_batch(project_id, batch_id) if r.execution_id}
        run_ids = list(dict.fromkeys([*execution_ids, *reports.keys()]))
        if not run_ids:
            reason = {"running": GATE_REASON_BATCH_RUNNING, "ended": GATE_REASON_BATCH_NO_RUNS}.get(log_state)
            if reason is None:
                return None
            return {"gate_status": GateStatus.NOT_SCANNED.value, "gate_reason": reason,
                    "gate_mode": None, "runs": []}
        runs = []
        for execution_id in run_ids:
            report = reports.get(execution_id)
            status = report.gate_status if report else GateStatus.NOT_SCANNED
            runs.append({"execution_id": str(execution_id), "gate_status": status.value})
        if not reports:
            return {"gate_status": GateStatus.NOT_SCANNED.value, "gate_reason": GATE_REASON_NO_REPORT,
                    "gate_mode": None, "runs": runs}
        worst = min((GateStatus(r["gate_status"]) for r in runs), key=lambda s: _GATE_RANK[s])
        worst_report = next((r for r in reports.values() if r.gate_status == worst), None)
        modes = {r.gate_mode.value for r in reports.values()}
        return {
            "gate_status": worst.value,
            "gate_reason": worst_report.gate_reason if worst_report else GATE_REASON_NO_REPORT,
            "gate_mode": modes.pop() if len(modes) == 1 else "mixed",
            "runs": runs,
        }
