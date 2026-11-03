"""
The object repository "Scan now" job (spec 0004, AC-3, AC-12).

The registry is per process and in memory: a restart loses an in flight
scan's status, but the data it already saved stays. Only one scan per
project can be queued or running, and a scan still running after ten
minutes is marked failed.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable
from urllib.parse import urljoin
from uuid import UUID, uuid4

from backend.services.object_repository_service import (
    ObjectRepositoryConflict,
    ObjectRepositoryNotFound,
    ObjectRepositoryService,
    _settings,
)

logger = logging.getLogger(__name__)

STALE_AFTER_SECONDS = 600
_ACTIVE = ("queued", "running")


def _default_audit(project_id: UUID, user_id: UUID | None, changes: dict) -> None:
    from backend.database.db import SessionLocal
    from backend.services.audit_service import log_audit

    db = SessionLocal()
    try:
        log_audit(db, user_id, project_id, "SCAN", "OBJECT_REPOSITORY", changes)
    finally:
        db.close()


class ObjectRepositoryScanService:
    _scans: dict[str, dict] = {}
    _lock = threading.Lock()

    def __init__(
        self,
        object_repository: ObjectRepositoryService | None = None,
        inspector=None,
        audit: Callable[[UUID, UUID | None, dict], None] | None = None,
    ):
        self.object_repository = object_repository or ObjectRepositoryService()
        self.inspector = inspector
        self.audit = audit or _default_audit

    def _inspector(self):
        if self.inspector is not None:
            return self.inspector
        from backend.agents.application_reconnaissance_agent import ApplicationReconnaissanceAgent

        return ApplicationReconnaissanceAgent(repo=self.object_repository.project_repo)

    @classmethod
    def _expire(cls) -> None:
        """Mark stale scans failed and drop finished ones past their TTL."""
        now = time.time()
        ttl = _settings().scan_status_ttl_seconds
        for scan_id, scan in list(cls._scans.items()):
            if scan["status"] in _ACTIVE and now - scan["created_at"] > STALE_AFTER_SECONDS:
                scan.update(status="failed", error="The scan did not finish within 10 minutes", finished_at=now)
            if scan["status"] not in _ACTIVE and now - (scan.get("finished_at") or now) > ttl:
                del cls._scans[scan_id]

    def start(self, project_id: UUID, user_id: UUID | None = None) -> str:
        project = self.object_repository.project_repo.get_project(project_id)
        if not project:
            raise ObjectRepositoryNotFound("Project not found")
        if not project.is_safe_test_environment:
            raise ObjectRepositoryConflict("Scans run only for a project marked as a safe test environment")
        if not project.target_url:
            raise ObjectRepositoryConflict("The project has no target URL to scan")
        with self._lock:
            self._expire()
            if any(s["project_id"] == project_id and s["status"] in _ACTIVE for s in self._scans.values()):
                raise ObjectRepositoryConflict("A scan is already running for this project")
            scan_id = str(uuid4())
            self._scans[scan_id] = {
                "scan_id": scan_id,
                "project_id": project_id,
                "user_id": user_id,
                "status": "queued",
                "pages_scanned": 0,
                "added": 0,
                "updated": 0,
                "marked_missing": 0,
                "error": None,
                "created_at": time.time(),
                "finished_at": None,
            }
        return scan_id

    def status(self, project_id: UUID, scan_id: str) -> dict:
        with self._lock:
            self._expire()
            scan = self._scans.get(scan_id)
            if scan is None or scan["project_id"] != project_id:
                raise ObjectRepositoryNotFound("Scan not found")
            return {k: scan[k] for k in ("scan_id", "status", "pages_scanned", "added", "updated", "marked_missing", "error")}

    def scan_urls(self, project) -> list[str]:
        """The target URL, literal page patterns, and URLs named by test cases."""
        from backend.agents.application_reconnaissance_agent import ApplicationReconnaissanceAgent

        repo = self.object_repository.project_repo
        urls = [project.target_url]
        origin = ApplicationReconnaissanceAgent._safe_origin(project.target_url)
        for page in self.object_repository.load(project.id).active_pages():
            if "*" not in page.url_pattern:
                urls.append(urljoin(origin + "/", page.url_pattern.lstrip("/")))
        test_cases = repo.get_test_cases_for_project(project.id)
        urls.extend(ApplicationReconnaissanceAgent(repo=None)._derive_urls_from_test_cases(project.target_url, test_cases))
        return list(dict.fromkeys(urls))

    def run(self, scan_id: str) -> None:
        with self._lock:
            scan = self._scans.get(scan_id)
            if scan is None or scan["status"] != "queued":
                return
            scan["status"] = "running"
        project_id = scan["project_id"]
        try:
            project = self.object_repository.project_repo.get_project(project_id)
            evidence = self._inspector().inspect_urls(project_id, self.scan_urls(project))
            from backend.agents.application_reconnaissance_agent import ApplicationReconnaissanceAgent

            recon_pages = ApplicationReconnaissanceAgent.pop_object_evidence(evidence)
            totals = self.object_repository.ingest_evidence(project_id, recon_pages)
            with self._lock:
                if scan["status"] == "running":
                    scan.update(status="succeeded", **totals)
        except Exception as exc:
            logger.warning("Object repository scan failed: project_id=%s error=%s", project_id, type(exc).__name__)
            with self._lock:
                scan.update(status="failed", error=str(exc)[:300] or type(exc).__name__)
        finally:
            with self._lock:
                scan["finished_at"] = time.time()
        try:
            self.audit(project_id, scan.get("user_id"), {
                "entity": "object_repository_scan",
                "scan_id": scan_id,
                "status": scan["status"],
                "pages_scanned": scan["pages_scanned"],
                "added": scan["added"],
                "updated": scan["updated"],
                "marked_missing": scan["marked_missing"],
            })
        except Exception:
            logger.exception("Recording the scan audit entry failed: scan_id=%s", scan_id)
