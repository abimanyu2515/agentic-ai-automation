from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

SUPPORTED_FRAMEWORKS = ("playwright", "selenium", "cucumber")


class ProjectConfigurationError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        missing_fields: list[str] | None = None,
        invalid_fields: dict[str, str] | None = None,
        status_code: int = 422,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.missing_fields = missing_fields or []
        self.invalid_fields = invalid_fields or {}
        self.status_code = status_code

    def as_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "missing_fields": self.missing_fields,
            "invalid_fields": self.invalid_fields,
            "action": {
                "label": "Open Project Settings",
                "route": "#/settings?tab=project",
            },
        }


@dataclass(frozen=True)
class ProjectConfiguration:
    project_id: UUID
    target_url: str
    framework: str
    credential_reference: str


def redact_url_for_log(value: str | None) -> str | None:
    """Return a URL safe for diagnostic logs: scheme, host, port, and path only.

    Query text, fragments, and userinfo can carry tokens or credentials, so they
    never reach the logs (spec 0002, AC-9).
    """
    if value is None:
        return None
    try:
        parsed = urlsplit(value.strip())
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return "<invalid url>"
    if not parsed.scheme or not hostname:
        return "<invalid url>"
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        netloc = f"{netloc}:{port}"
    redacted = urlunsplit((parsed.scheme.lower(), netloc, parsed.path, "", ""))
    return f"{redacted}?<redacted>" if parsed.query else redacted


def normalize_target_url(value: str | None) -> str | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None

    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ProjectConfigurationError(
            "PROJECT_INVALID_CONFIGURATION",
            "Target URL must use http or https.",
            invalid_fields={"target_url": "invalid_url"},
        )
    if not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        raise ProjectConfigurationError(
            "PROJECT_INVALID_CONFIGURATION",
            "Target URL must be an absolute URL without credentials or fragments.",
            invalid_fields={"target_url": "invalid_url"},
        )
    try:
        hostname = parsed.hostname
    except ValueError as exc:
        raise ProjectConfigurationError(
            "PROJECT_INVALID_CONFIGURATION",
            "Target URL contains an invalid host.",
            invalid_fields={"target_url": "invalid_url"},
        ) from exc
    if not hostname:
        raise ProjectConfigurationError(
            "PROJECT_INVALID_CONFIGURATION",
            "Target URL must include a host.",
            invalid_fields={"target_url": "invalid_url"},
        )

    try:
        port = parsed.port
    except ValueError as exc:
        raise ProjectConfigurationError(
            "PROJECT_INVALID_CONFIGURATION",
            "Target URL contains an invalid port.",
            invalid_fields={"target_url": "invalid_url"},
        ) from exc
    scheme = parsed.scheme.lower()
    normalized_netloc = hostname.lower()
    if ":" in hostname and not hostname.startswith("["):
        normalized_netloc = f"[{normalized_netloc}]"
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        normalized_netloc = f"{normalized_netloc}:{port}"

    path = parsed.path or ""
    if path == "/":
        path = ""
    return urlunsplit((scheme, normalized_netloc, path, parsed.query, ""))


def normalize_framework(value: str | None) -> str | None:
    if value is None:
        return None
    framework = value.strip().lower()
    return framework or None


def readiness_for_project(project: Any, projects: list[Any] | None = None) -> dict[str, Any]:
    missing_fields: list[str] = []
    invalid_fields: dict[str, str] = {}

    raw_url = getattr(project, "target_url", None)
    normalized_url = None
    if not raw_url or not str(raw_url).strip():
        missing_fields.append("target_url")
    else:
        try:
            normalized_url = normalize_target_url(str(raw_url))
        except ProjectConfigurationError as exc:
            invalid_fields["target_url"] = exc.invalid_fields.get("target_url", "invalid_url")

    framework = normalize_framework(getattr(project, "framework", None))
    if not framework:
        missing_fields.append("framework")
    elif framework not in SUPPORTED_FRAMEWORKS:
        invalid_fields["framework"] = "unsupported_framework"

    if normalized_url and projects is not None:
        for other in projects:
            if getattr(other, "id", None) == getattr(project, "id", None):
                continue
            try:
                other_url = normalize_target_url(getattr(other, "target_url", None))
            except ProjectConfigurationError:
                continue
            if other_url == normalized_url:
                invalid_fields["target_url"] = "duplicate_url"
                break

    return {
        "is_ready": not missing_fields and not invalid_fields,
        "missing_fields": missing_fields,
        "invalid_fields": invalid_fields,
        "target_url": normalized_url,
    }


def require_ready_project(project: Any, projects: list[Any] | None = None) -> ProjectConfiguration:
    readiness = readiness_for_project(project, projects)
    if not readiness["is_ready"]:
        raise ProjectConfigurationError(
            "PROJECT_NOT_READY",
            "Project configuration is incomplete.",
            missing_fields=readiness["missing_fields"],
            invalid_fields=readiness["invalid_fields"],
        )
    return ProjectConfiguration(
        project_id=project.id,
        target_url=readiness["target_url"],
        framework=normalize_framework(project.framework) or "playwright",
        credential_reference=str(project.id),
    )


def capture_project_snapshot(project: Any, require_ready: bool = True) -> dict[str, str]:
    """Spec 0001, AC-3, AC-4, AC-8: check readiness when work is accepted and
    return the configuration the work must use until it ends. Raises
    ProjectConfigurationError("PROJECT_NOT_READY") for an incomplete project.
    Holds no secret: credentials are looked up by `credential_reference`.

    `require_ready=False` (spec 0011, workspace runs finalized after the fact)
    skips every readiness check and fills the same keys from the raw project,
    so a run that already happened still gets a snapshot. Every value stays a
    string."""
    if require_ready:
        configuration = require_ready_project(project)
        base = {
            "project_id": str(configuration.project_id),
            "target_url": configuration.target_url,
            "framework": configuration.framework,
            "credential_reference": configuration.credential_reference,
        }
    else:
        try:
            target_url = normalize_target_url(getattr(project, "target_url", None)) or ""
        except ProjectConfigurationError:
            target_url = ""
        # Credentials are always looked up by project id, as in the ready path.
        reference = getattr(project, "id", None)
        base = {
            "project_id": str(project.id),
            "target_url": target_url,
            "framework": normalize_framework(getattr(project, "framework", None)) or "",
            "credential_reference": str(reference) if reference else "",
        }
    return {
        **base,
        # Spec 0011, AC-8 and AC-13: the accessibility settings this run uses, so a
        # later settings change never rewrites its gate. Strings, like the rest.
        "a11y_enabled": "true" if getattr(project, "a11y_enabled", True) is not False else "false",
        "a11y_gate_mode": str(getattr(project, "a11y_gate_mode", None) or "warn"),
        "a11y_block_severity": str(getattr(project, "a11y_block_severity", None) or "serious"),
    }


def snapshot_for_project_context(context: dict[str, Any]) -> dict[str, Any]:
    snapshot = dict(context)
    if snapshot.get("project_id") is not None:
        snapshot["project_id"] = str(snapshot["project_id"])
    return snapshot
