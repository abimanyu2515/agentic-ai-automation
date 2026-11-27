"""
Standalone accessibility scans (spec 0011, AC-10, AC-11).

A user picks pages on the project page and starts a scan. `start` checks the
request, saves the report as `queued`, and writes the audit entry; `run` (in a
background thread) moves it to `running`, writes `scan-input.json`, runs the
fixed template spec through `PlaywrightRunner`, and hands the `a11y.json` it
wrote to `AccessibilityService.process`, which fills the same report in place.

Rules:
- Paths must be on the project target URL's origin, and at most
  `accessibility.max_pages` of them (422 otherwise).
- Only one scan per project can be queued or running (409). Postgres enforces
  it with a partial unique index; the JSON store with its process lock.
- `include_login` needs a project marked as a safe test environment (409),
  because it submits real credentials.
- A scan still queued or running after `scan_stale_after_seconds` is marked
  `failed` with `error=timeout`, on every read of scan reports and at startup.
- The gate settings come from the project when the scan starts.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlsplit
from uuid import UUID

from backend.models.accessibility import (
    GATE_REASON_SCAN_ERROR,
    AccessibilityReport,
    AccessibilityReportStatus,
    AccessibilitySource,
    GateStatus,
)
from backend.repository.accessibility_repository import ActiveScanExists
from backend.services.accessibility_service import AccessibilityService, read_a11y_file

logger = logging.getLogger(__name__)

SCAN_INPUT_FILE = "scan-input.json"
ERROR_TIMEOUT = "timeout"
ERROR_LOGIN_FAILED = "login_failed"
# Time for the browser to start, log in, and close, on top of the axe budget
# (which can be spent twice: the public pass and the signed in pass).
_SCAN_TIMEOUT_MARGIN_SECONDS = 120
_SCAN_SESSION_MODULE = Path(__file__).parent.parent / "playwrightt" / "lib" / "a11y-standalone-scan"

_TEMPLATE = """\
import {{ test }} from '@playwright/test';
import {{ runStandaloneScan }} from {module};

test('accessibility scan', async ({{ browser }}) => {{
  await runStandaloneScan({{
    browser,
    runDir: __dirname,
    username: process.env.TARGET_USERNAME,
    password: process.env.TARGET_PASSWORD,
  }});
}});
"""


class AccessibilityScanError(Exception):
    status_code = 400

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class AccessibilityScanNotFound(AccessibilityScanError):
    status_code = 404


class AccessibilityScanConflict(AccessibilityScanError):
    status_code = 409


class AccessibilityScanInvalid(AccessibilityScanError):
    status_code = 422


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower() if parts.scheme and parts.netloc else ""


def _path_key(url: str) -> str:
    """The dedupe key: the path, ignoring the query string and hash (as the browser side does)."""
    return urlsplit(url).path.rstrip("/") or "/"


def _default_audit(project_id: UUID, user_id: UUID | None, changes: dict) -> None:
    from backend.database.db import SessionLocal
    from backend.services.audit_service import log_audit

    db = SessionLocal()
    try:
        log_audit(db, user_id, project_id, "SCAN", "ACCESSIBILITY", changes)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class AccessibilityScanService:
    def __init__(
        self,
        accessibility_service: AccessibilityService | None = None,
        project_repo=None,
        repo=None,
        runner_factory: Callable | None = None,
        bug_report_service=None,
        audit: Callable[[UUID, UUID | None, dict], None] | None = None,
        settings=None,
        launcher: Callable[[Callable[[], None]], None] | None = None,
    ):
        self._accessibility_service = accessibility_service
        self._project_repo = project_repo
        self._repo = repo
        self._runner_factory = runner_factory
        self._bug_report_service = bug_report_service
        self.audit = audit or _default_audit
        self._settings = settings
        self._launcher = launcher

    # ------------------------------------------------------------ dependencies

    @property
    def accessibility_service(self) -> AccessibilityService:
        if self._accessibility_service is None:
            self._accessibility_service = AccessibilityService(repo=self._repo, project_repo=self._project_repo)
        return self._accessibility_service

    @property
    def repo(self):
        return self._repo or self.accessibility_service.repo

    @property
    def project_repo(self):
        return self._project_repo or self.accessibility_service.project_repo

    @property
    def config(self):
        if self._settings is None:
            from backend.config.settings import get_settings

            self._settings = get_settings()
        return self._settings.accessibility

    @property
    def bug_report_service(self):
        if self._bug_report_service is None:
            from backend.services.bug_report_service import BugReportService

            self._bug_report_service = BugReportService()
        return self._bug_report_service

    def _runner(self):
        if self._runner_factory is not None:
            return self._runner_factory()
        from backend.services.playwright_runner import PlaywrightRunner

        # Two axe passes at most (public, then signed in), plus browser and login time.
        timeout = 2 * self.config.page_budget_ms // 1000 + _SCAN_TIMEOUT_MARGIN_SECONDS
        # The template scans on its own; the per test execution scan hook stays off.
        return PlaywrightRunner(timeout_seconds=timeout, a11y_scan=False)

    def _project(self, project_id: UUID):
        project = self.project_repo.get_project(project_id)
        if project is None:
            raise AccessibilityScanNotFound("Project not found")
        return project

    @staticmethod
    def _snapshot(project) -> dict:
        from backend.services.project_configuration import ProjectConfigurationError, capture_project_snapshot

        try:
            return capture_project_snapshot(project)
        except ProjectConfigurationError as exc:
            raise AccessibilityScanConflict(
                "The project needs a valid target URL before it can be scanned"
            ) from exc

    # ------------------------------------------------------------ candidate pages

    def scan_pages(self, project_id: UUID) -> list[dict]:
        """The checklist of the scan dialog (AC-10): the target URL, literal object
        repository page patterns, and test case URLs. Wildcard patterns are listed
        as skipped. Each item is `{url, source, skipped_reason?}`."""
        project = self._project(project_id)
        target_url = self._snapshot(project)["target_url"]
        origin = _origin(target_url)
        pages: list[dict] = [{"url": target_url, "source": "target_url"}]

        try:
            from backend.services.object_repository_service import ObjectRepositoryService

            for page in ObjectRepositoryService(project_repo=self.project_repo).load(project_id).active_pages():
                pattern = page.url_pattern
                if "*" in pattern:
                    pages.append({"url": pattern, "source": "object_repository", "skipped_reason": "wildcard"})
                else:
                    pages.append({"url": urljoin(origin + "/", pattern.lstrip("/")), "source": "object_repository"})
        except Exception:
            logger.warning("Object repository pages could not be listed for the scan: project_id=%s", project_id)

        try:
            from backend.agents.application_reconnaissance_agent import ApplicationReconnaissanceAgent

            test_cases = self.project_repo.get_test_cases_for_project(project_id)
            for url in ApplicationReconnaissanceAgent(repo=None)._derive_urls_from_test_cases(target_url, test_cases):
                pages.append({"url": url, "source": "test_case"})
        except Exception:
            logger.warning("Test case pages could not be listed for the scan: project_id=%s", project_id)

        # One entry per path; the first source named wins. Off origin URLs never appear.
        seen: set[str] = set()
        out: list[dict] = []
        for page in pages:
            if page.get("skipped_reason"):
                key = "skip:" + page["url"]
            elif _origin(page["url"]) != origin:
                continue
            else:
                key = _path_key(page["url"])
            if key not in seen:
                seen.add(key)
                out.append(page)
        return out

    def resolve_paths(self, target_url: str, paths: list[str]) -> list[str]:
        """Absolute URLs on the target origin, deduped by path. An empty list means
        the target URL only. Off origin or over the cap is a 422 (AC-10)."""
        origin = _origin(target_url)
        urls: list[str] = []
        seen: set[str] = set()
        for raw in paths or []:
            value = str(raw or "").strip()
            if not value:
                continue
            url = value if urlsplit(value).scheme else urljoin(origin + "/", value.lstrip("/"))
            if urlsplit(url).scheme not in ("http", "https") or _origin(url) != origin:
                raise AccessibilityScanInvalid(f"Only pages on {origin} can be scanned: {value[:200]}")
            key = _path_key(url)
            if key not in seen:
                seen.add(key)
                urls.append(url.split("#", 1)[0])
        if not urls:
            urls = [target_url]
        if len(urls) > self.config.max_pages:
            raise AccessibilityScanInvalid(f"A scan can cover at most {self.config.max_pages} pages")
        return urls

    # ------------------------------------------------------------ start and run

    def start(
        self,
        project_id: UUID,
        paths: list[str],
        include_login: bool = False,
        user_id: UUID | None = None,
    ) -> AccessibilityReport:
        """Checks the request and saves the queued report. Call `launch` next."""
        # `accessibility.enabled` (A11Y_ENABLED) is the kill switch for all
        # scanning. The project's `a11y_enabled` covers test runs only, so a
        # scan a person starts by hand ignores it.
        if not self.config.enabled:
            raise AccessibilityScanConflict("Accessibility scanning is turned off on this server")
        project = self._project(project_id)
        if include_login and not getattr(project, "is_safe_test_environment", False):
            raise AccessibilityScanConflict(
                "Login scans run only for a project marked as a safe test environment"
            )
        snapshot = self._snapshot(project)
        urls = self.resolve_paths(snapshot["target_url"], paths)
        self.expire_stale(project_id)

        mode, _ = self.accessibility_service.gate_settings(snapshot)
        report = AccessibilityReport(
            project_id=project_id,
            source=AccessibilitySource.SCAN,
            status=AccessibilityReportStatus.QUEUED,
            gate_mode=mode,
            login_status="failed" if include_login else "not_requested",
            wcag_tags=list(self.config.wcag_tags),
            created_by=user_id,
        )
        try:
            self.repo.save_new_scan(report)
        except ActiveScanExists as exc:
            raise AccessibilityScanConflict("An accessibility scan is already running for this project") from exc

        try:
            self.audit(project_id, user_id, {
                "entity": "accessibility_scan",
                "report_id": str(report.id),
                "pages": len(urls),
                "include_login": bool(include_login),
            })
        except Exception:
            logger.exception("Recording the accessibility scan audit entry failed: report_id=%s", report.id)

        self._pending[report.id] = (urls, bool(include_login), snapshot)
        return report

    # Requests waiting for `launch`, keyed by report id. Per process, like the scan itself.
    _pending: dict[UUID, tuple[list[str], bool, dict]] = {}

    def launch(self, report_id: UUID) -> None:
        """Runs the scan in a background thread, in a copy of the caller's context."""
        from backend.services.llm_context import llm_call_context, run_in_context
        from backend.models.operation import WorkflowOperation

        def job():
            with llm_call_context(operation=WorkflowOperation.ACCESSIBILITY_SCAN.value):
                self.run(report_id)

        task = run_in_context(job)
        if self._launcher is not None:
            self._launcher(task)
        else:
            threading.Thread(target=task, name=f"a11y-scan-{report_id}", daemon=True).start()

    def run(self, report_id: UUID) -> AccessibilityReport | None:
        """Runs one queued scan to the end. Never raises."""
        pending = self._pending.pop(report_id, None)
        report = self.repo.get(report_id)
        if report is None or report.status != AccessibilityReportStatus.QUEUED or pending is None:
            return report
        urls, include_login, snapshot = pending
        report.status = AccessibilityReportStatus.RUNNING
        self.repo.save(report)

        raw = None
        try:
            from backend.services.playwright_runner import ARTIFACTS_ROOT

            run_id = f"scan_{report.id}"
            run_dir = ARTIFACTS_ROOT / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            config = self.config
            (run_dir / SCAN_INPUT_FILE).write_text(json.dumps({
                "urls": urls,
                "origin": _origin(snapshot["target_url"]),
                "target_url": snapshot["target_url"],
                "include_login": include_login,
                "wcag_tags": list(config.wcag_tags),
                "max_pages": config.max_pages,
                "page_budget_ms": config.page_budget_ms,
                "max_nodes_per_rule": config.max_nodes_per_rule,
                "max_element_screenshots": config.max_element_screenshots,
            }), encoding="utf-8")
            result = self._runner().run(
                self.template(run_dir),
                run_id=run_id,
                project_id=str(report.project_id),
                target_url=snapshot["target_url"],
            )
            raw = read_a11y_file(run_dir)
            if raw is None:
                logger.warning(
                    "Accessibility scan wrote no results: report_id=%s runner_status=%s",
                    report.id, getattr(result, "status", None),
                )
        except Exception:
            logger.exception("Accessibility scan run failed: report_id=%s", report.id)
            raw = None

        # A scan closed as timed out while it ran stays closed.
        current = self.repo.get(report_id)
        if current is None or current.status != AccessibilityReportStatus.RUNNING:
            return current

        if isinstance(raw, dict) and raw.get("login_status") in ("not_requested", "succeeded", "failed"):
            report.login_status = raw["login_status"]
        done = self.accessibility_service.process(
            report.project_id,
            None,
            raw,
            project_config=snapshot,
            source=AccessibilitySource.SCAN,
            enabled=True,
            base=report,
        )
        if include_login and done.login_status == "failed" and done.error is None:
            done.error = ERROR_LOGIN_FAILED
            self.repo.save(done)
        # Serious findings become bug drafts, deduped with run findings (AC-6).
        self.bug_report_service.draft_from_accessibility(report.project_id, done)
        return self.repo.get(report_id) or done

    @staticmethod
    def template(run_dir: Path) -> str:
        """The fixed spec every standalone scan runs; only the import path varies."""
        module = os.path.relpath(_SCAN_SESSION_MODULE, run_dir).replace("\\", "/")
        if not module.startswith("."):
            module = "./" + module
        return _TEMPLATE.format(module=json.dumps(module))

    # ------------------------------------------------------------ stale scans

    def expire_stale(self, project_id: UUID | None = None) -> int:
        """Marks scans queued or running past the stale limit as failed with
        `error=timeout` (AC-10). Called on reads and at startup. Never raises."""
        try:
            limit = _utc_now() - timedelta(seconds=self.config.scan_stale_after_seconds)
            closed = 0
            for report in self.repo.list_active_scans(project_id):
                created = report.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created > limit:
                    continue
                report.status = AccessibilityReportStatus.FAILED
                report.error = ERROR_TIMEOUT
                report.gate_status = GateStatus.NOT_SCANNED
                report.gate_reason = GATE_REASON_SCAN_ERROR
                report.finished_at = _utc_now()
                self.repo.save(report)
                self._pending.pop(report.id, None)
                closed += 1
            return closed
        except Exception:
            logger.exception("Closing stale accessibility scans failed")
            return 0
