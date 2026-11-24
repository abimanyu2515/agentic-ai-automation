"""
Log evidence collector for the log analyser (ExecutionAnalysisAgent).

Reads the text evidence a failed Playwright run leaves on disk and condenses it
into a bounded `LogEvidence` the analysis prompt can carry:

- report.json        full error (message, stack, snippet, location), all errors,
                     and the test's stdout and stderr
- error-context.md   Playwright's failure summary and page snapshot
- trace.zip          test steps up to the failure, the failing step, console
                     errors and warnings, and failed network requests
- result.timeline    timeline entries marked as errors

It never raises. A missing or unreadable source leaves its section empty and
adds a note. Only files under the Playwright artifacts root are read.

Safety: network entries keep only method, URL without query string, and status
(no headers, cookies or bodies). Every section is passed through credential and
test data redaction, then capped in size.
"""

from __future__ import annotations

import json
import logging
import re
import zipfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field

from backend.models.execution_result import ExecutionResult
from backend.services.object_repository_harvest import REDACTED, secret_values

logger = logging.getLogger(__name__)

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
_YAML_BLOCK = re.compile(r"```yaml\s*\n(.*?)```", re.DOTALL)

MAX_SECTION_CHARS = 2500
MAX_PAGE_SNAPSHOT_CHARS = 2000
MAX_ACTIONS = 15
MAX_CONSOLE_ENTRIES = 15
MAX_NETWORK_ENTRIES = 15
MAX_TIMELINE_ENTRIES = 10

# Test runner step categories that are plumbing, not test actions.
_SKIPPED_STEP_METHODS = {"hook", "fixture", "test.attach"}
_CONSOLE_LEVELS = {"error", "warning", "assert"}


class LogEvidence(BaseModel):
    """Bounded, redacted text evidence for one failed run."""

    error_details: str = ""
    page_snapshot: str = ""
    actions: list[str] = Field(default_factory=list)
    failing_action: str = ""
    console_errors: list[str] = Field(default_factory=list)
    network_failures: list[str] = Field(default_factory=list)
    timeline_errors: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not any((
            self.error_details, self.page_snapshot, self.actions, self.failing_action,
            self.console_errors, self.network_failures, self.timeline_errors,
        ))


def _default_artifacts_root() -> Path:
    from backend.services.playwright_runner import ARTIFACTS_ROOT

    return Path(ARTIFACTS_ROOT)


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text or "")


def _cap(text: str, limit: int = MAX_SECTION_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


def _url_without_query(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<invalid url>"
    return urlunsplit((parts.scheme, parts.hostname or parts.netloc, parts.path, "", ""))


class LogEvidenceCollector:
    """Collects `LogEvidence` from a run's artifacts. Inject `artifacts_root` in tests."""

    def __init__(self, artifacts_root: Path | str | None = None):
        self._artifacts_root = Path(artifacts_root) if artifacts_root else None

    @property
    def artifacts_root(self) -> Path:
        if self._artifacts_root is None:
            self._artifacts_root = _default_artifacts_root()
        return self._artifacts_root

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def collect(self, result: ExecutionResult, secrets: list | None = None) -> LogEvidence:
        evidence = LogEvidence()
        try:
            trace = self._safe_path(result.trace_path, evidence, "trace")
            report = self._find_report(trace)
            if report is not None:
                self._read_report(report, evidence)
            elif trace is not None:
                evidence.notes.append("report.json not found next to the trace.")
            context_file = trace.parent / "error-context.md" if trace is not None else None
            if context_file is not None and context_file.is_file():
                self._read_error_context(context_file, evidence)
            if trace is not None and trace.is_file():
                self._read_trace(trace, evidence)
            elif result.trace_path:
                evidence.notes.append("Trace file is missing.")
            self._read_timeline(result.timeline, evidence)
            if not evidence.error_details and result.error_message:
                evidence.error_details = _cap(_strip_ansi(result.error_message))
        except Exception as exc:  # never fail triage because of evidence
            logger.exception("Log evidence collection failed: execution_id=%s", result.id)
            evidence.notes.append(f"Evidence collection failed: {exc}")
            if not evidence.error_details and result.error_message:
                evidence.error_details = _cap(_strip_ansi(result.error_message))
        return self._redact(evidence, secrets or [])

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def _safe_path(self, raw: str | None, evidence: LogEvidence, label: str) -> Path | None:
        if not raw:
            return None
        try:
            root = self.artifacts_root.resolve()
            path = Path(raw)
            if not path.is_absolute():
                path = root / path
            path = path.resolve()
        except (OSError, ValueError):
            evidence.notes.append(f"The {label} path could not be resolved.")
            return None
        if path != root and root not in path.parents:
            evidence.notes.append(f"The {label} path is outside the artifacts folder and was ignored.")
            return None
        return path

    def _find_report(self, trace: Path | None) -> Path | None:
        """report.json sits at the run folder: <run>/test-results/<slug>/trace.zip."""
        if trace is None:
            return None
        root = self.artifacts_root.resolve()
        for folder in list(trace.parents)[:3]:
            if folder == root or root not in folder.parents:
                break
            candidate = folder / "report.json"
            if candidate.is_file():
                return candidate
        return None

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------

    def _read_report(self, report: Path, evidence: LogEvidence) -> None:
        try:
            data = json.loads(report.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError) as exc:
            evidence.notes.append(f"report.json could not be read: {exc}")
            return
        failing = self._first_failing_result(data)
        if failing is None:
            top_errors = [_strip_ansi(e.get("message", "")) for e in data.get("errors") or [] if isinstance(e, dict)]
            if top_errors:
                evidence.error_details = _cap("\n\n".join(top_errors))
            return
        title, run = failing
        parts = [f"Test: {title}"]
        error = run.get("error") or {}
        location = error.get("location") or run.get("errorLocation") or {}
        if location:
            parts.append(f"Location: {Path(str(location.get('file', ''))).name}:{location.get('line')}:{location.get('column')}")
        if error.get("message"):
            parts.append("Error:\n" + _strip_ansi(error["message"]).strip())
        if error.get("snippet"):
            parts.append("Snippet:\n" + _strip_ansi(error["snippet"]).strip())
        stack = _strip_ansi(error.get("stack") or "")
        stack_lines = [line for line in stack.splitlines() if line.strip().startswith("at ")]
        if stack_lines:
            parts.append("Stack:\n" + "\n".join(stack_lines[:5]))
        extra = [_strip_ansi(e.get("message", "")).strip() for e in (run.get("errors") or [])[1:] if isinstance(e, dict)]
        if extra:
            parts.append("Other errors:\n" + "\n---\n".join(extra[:3]))
        output = self._output_lines(run.get("stdout")) + self._output_lines(run.get("stderr"))
        output = [line for line in output if "[Timeline] Screenshot" not in line]
        if output:
            parts.append("Test output:\n" + "\n".join(output[-15:]))
        evidence.error_details = _cap("\n\n".join(parts))

    @staticmethod
    def _output_lines(entries) -> list[str]:
        lines: list[str] = []
        for entry in entries or []:
            text = entry.get("text") if isinstance(entry, dict) else str(entry)
            for line in _strip_ansi(text or "").splitlines():
                if line.strip():
                    lines.append(line.rstrip())
        return lines

    @staticmethod
    def _first_failing_result(data: dict):
        def walk(suite, path):
            for spec in suite.get("specs") or []:
                for test in spec.get("tests") or []:
                    for run in test.get("results") or []:
                        if run.get("status") not in ("passed", "skipped"):
                            return " > ".join([*path, spec.get("title", "")]).strip(" >"), run
            for child in suite.get("suites") or []:
                found = walk(child, path + ([child.get("title")] if child.get("title") else []))
                if found:
                    return found
            return None

        for suite in data.get("suites") or []:
            found = walk(suite, [])
            if found:
                return found
        return None

    def _read_error_context(self, path: Path, evidence: LogEvidence) -> None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            evidence.notes.append(f"error-context.md could not be read: {exc}")
            return
        # The page snapshot is the ```yaml aria tree. The rest (Playwright's own
        # "Instructions", error details, test source) is skipped: the error is
        # already in error_details and the instructions are not evidence.
        match = _YAML_BLOCK.search(text)
        if match:
            evidence.page_snapshot = _cap(_strip_ansi(match.group(1)), MAX_PAGE_SNAPSHOT_CHARS)
        else:
            evidence.notes.append("error-context.md has no page snapshot.")

    def _read_trace(self, trace: Path, evidence: LogEvidence) -> None:
        try:
            with zipfile.ZipFile(trace) as archive:
                names = archive.namelist()
                if "test.trace" in names:
                    self._read_test_steps(archive.read("test.trace"), evidence)
                for name in names:
                    if name.endswith("-trace.trace"):
                        self._read_console(archive.read(name), evidence)
                    elif name.endswith("-trace.network"):
                        self._read_network(archive.read(name), evidence)
        except (OSError, zipfile.BadZipFile, KeyError) as exc:
            evidence.notes.append(f"Trace could not be read: {exc}")

    @staticmethod
    def _events(raw: bytes):
        for line in raw.decode("utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                yield event

    def _read_test_steps(self, raw: bytes, evidence: LogEvidence) -> None:
        titles: dict[str, str] = {}
        order: list[str] = []
        failing_id = None
        for event in self._events(raw):
            kind = event.get("type")
            if kind == "before" and event.get("method") not in _SKIPPED_STEP_METHODS:
                call_id = event.get("callId")
                title = event.get("title") or event.get("method") or ""
                if call_id and title and title not in ("Screenshot", "Wait for timeout", "Evaluate"):
                    titles[call_id] = title
                    order.append(call_id)
            elif kind == "after" and event.get("error") and failing_id is None:
                if event.get("callId") in titles:
                    failing_id = event.get("callId")
        if failing_id:
            evidence.failing_action = titles[failing_id]
            order = order[: order.index(failing_id) + 1]
        evidence.actions = [titles[c] for c in order][-MAX_ACTIONS:]

    def _read_console(self, raw: bytes, evidence: LogEvidence) -> None:
        for event in self._events(raw):
            if event.get("type") != "console":
                continue
            level = str(event.get("messageType") or event.get("params", {}).get("type") or "").lower()
            if level not in _CONSOLE_LEVELS:
                continue
            text = event.get("text") or event.get("params", {}).get("text") or ""
            entry = f"[{level}] {_cap(str(text), 300)}"
            if entry not in evidence.console_errors:
                evidence.console_errors.append(entry)
        evidence.console_errors = evidence.console_errors[:MAX_CONSOLE_ENTRIES]

    def _read_network(self, raw: bytes, evidence: LogEvidence) -> None:
        for event in self._events(raw):
            if event.get("type") != "resource-snapshot":
                continue
            snapshot = event.get("snapshot") or {}
            request = snapshot.get("request") or {}
            response = snapshot.get("response") or {}
            status = response.get("status")
            failure = response.get("_failureText") or snapshot.get("_failureText")
            failed = failure or (isinstance(status, int) and (status >= 400 or status <= 0))
            if not failed:
                continue
            url = _url_without_query(str(request.get("url", "")))
            label = f"{request.get('method', 'GET')} {url} -> {status if isinstance(status, int) and status > 0 else 'failed'}"
            if failure:
                label += f" ({_cap(str(failure), 120)})"
            if label not in evidence.network_failures:
                evidence.network_failures.append(label)
        evidence.network_failures = evidence.network_failures[:MAX_NETWORK_ENTRIES]

    @staticmethod
    def _read_timeline(timeline, evidence: LogEvidence) -> None:
        for entry in timeline or []:
            if isinstance(entry, dict) and entry.get("type") == "error":
                text = entry.get("details") or entry.get("event") or ""
                if text:
                    evidence.timeline_errors.append(_cap(_strip_ansi(str(text)), 300))
        evidence.timeline_errors = evidence.timeline_errors[:MAX_TIMELINE_ENTRIES]

    # ------------------------------------------------------------------
    # Redaction
    # ------------------------------------------------------------------

    @staticmethod
    def _redact(evidence: LogEvidence, secrets: list) -> LogEvidence:
        patterns = [re.compile(re.escape(value), re.IGNORECASE) for value in secret_values(secrets)]
        if not patterns:
            return evidence

        def clean(text: str) -> str:
            for pattern in patterns:
                text = pattern.sub(REDACTED, text)
            return text

        return evidence.model_copy(update={
            "error_details": clean(evidence.error_details),
            "page_snapshot": clean(evidence.page_snapshot),
            "actions": [clean(a) for a in evidence.actions],
            "failing_action": clean(evidence.failing_action),
            "console_errors": [clean(c) for c in evidence.console_errors],
            "network_failures": [clean(n) for n in evidence.network_failures],
            "timeline_errors": [clean(t) for t in evidence.timeline_errors],
        })
