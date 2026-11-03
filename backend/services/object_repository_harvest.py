"""
Object repository harvest helpers (spec 0004, AC-16 to AC-21).

The shared extractor source, the cleaning rules reconnaissance and harvest
both apply before fingerprints are computed (redaction and the token rule),
snapshot parsing, and the Python runner's `harvest.json` transport.
ExecutionService.apply_object_repository_harvest is the only caller that
ingests harvested snapshots.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from backend.models.object_repository import ELEMENT_TYPES, LOCATOR_STRATEGIES

logger = logging.getLogger(__name__)

EXTRACTOR_PATH = Path(__file__).parent.parent / "playwrightt" / "lib" / "object-harvest.js"
HARVEST_FILE = "harvest.json"
MAX_SNAPSHOT_ELEMENTS = 300
REDACTED = "[redacted]"
MIN_SECRET_LENGTH = 3
# 32 or more characters of only hex or base64 characters look like a token.
_TOKEN = re.compile(r"[A-Za-z0-9+/=]{32,}")
_TEXT_FIELDS = ("label", "placeholder")
_STABLE_ATTRIBUTES = ("data-testid", "id", "name")


@lru_cache(maxsize=1)
def extractor_source() -> str:
    """The extractor's function expression, without its header comment."""
    lines = EXTRACTOR_PATH.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if not line.startswith("//"))
    return "\n".join(lines[start:]).strip()


# ------------------------------------------------------------------ cleaning

def secret_values(values: list[Any]) -> list[str]:
    """Distinct values worth redacting, longest first (shorter than 3 is ignored)."""
    secrets = {str(value).strip() for value in values if value is not None and not isinstance(value, bool)}
    return sorted((s for s in secrets if len(s) >= MIN_SECRET_LENGTH), key=len, reverse=True)


def values_from_test_data(test_case) -> list[Any]:
    if test_case is None:
        return []
    values = []
    for data_set in [getattr(test_case, "test_data", None) or {}, *(getattr(test_case, "test_data_sets", None) or [])]:
        if isinstance(data_set, dict):
            values.extend(data_set.values())
    return values


def _redact(text: Any, patterns: list[re.Pattern]) -> str:
    text = "" if text is None else str(text)
    for pattern in patterns:
        text = pattern.sub(REDACTED, text)
    return text


def clean_elements(elements: list[dict], secrets: list[str]) -> list[dict]:
    """Redact secrets, drop token values and redacted locators (AC-20).

    Redaction is case insensitive and covers the label, placeholder, every
    attribute, and every locator value. A locator whose value was redacted or
    looks like a token is dropped, and an element left with no locator is
    dropped. Reconnaissance runs the same rules, so scan and run fingerprints
    come from the same cleaned values (AC-21).
    """
    patterns = [re.compile(re.escape(secret), re.IGNORECASE) for secret in secret_values(secrets)]
    cleaned = []
    for element in elements:
        item = dict(element)
        for field in _TEXT_FIELDS:
            item[field] = _redact(item.get(field), patterns)
        attributes = {key: _redact(value, patterns) for key, value in (item.get("attributes") or {}).items()}
        for key in _STABLE_ATTRIBUTES:
            if _TOKEN.search(attributes.get(key) or ""):
                attributes[key] = ""
        item["attributes"] = attributes
        locators = []
        for locator in item.get("locators") or []:
            value = _redact(locator.get("value"), patterns)
            options = dict(locator.get("options") or {})
            if "name" in options:
                options["name"] = _redact(options["name"], patterns)
            texts = [value, str(options.get("name") or "")]
            if any(REDACTED in text or _TOKEN.search(text) for text in texts):
                continue
            locators.append({**locator, "value": value, "options": options})
        if locators:
            item["locators"] = locators
            cleaned.append(item)
    return cleaned


# ----------------------------------------------------------------- snapshots

def normalize_path(url: Any) -> str:
    """Path only, trailing slash removed, case kept; the hash is ignored."""
    path = urlsplit(str(url or "")).path or "/"
    return path.rstrip("/") or "/"


def _valid_locator(locator: Any) -> bool:
    return (
        isinstance(locator, dict)
        and locator.get("strategy") in LOCATOR_STRATEGIES
        and isinstance(locator.get("value"), str)
        and isinstance(locator.get("options") or {}, dict)
        and isinstance(locator.get("match_count", 0), int)
    )


def _valid_element(element: Any) -> dict | None:
    if not isinstance(element, dict) or element.get("element_type") not in ELEMENT_TYPES:
        return None
    if not isinstance(element.get("attributes") or {}, dict) or not isinstance(element.get("locators"), list):
        return None
    locators = [locator for locator in element["locators"] if _valid_locator(locator)]
    if not locators:
        return None
    return {**element, "attributes": dict(element.get("attributes") or {}), "locators": locators}


def parse_snapshots(snapshots: Any, max_pages: int) -> list[tuple[str, list[dict]]]:
    """Valid snapshots as (path, elements): the first per path, at most max_pages.

    The backend enforces the once per path rule and the page cap whatever the
    runner sent (AC-16). Malformed snapshots and elements are skipped.
    """
    if not isinstance(snapshots, list):
        if snapshots is not None:
            logger.warning("Skipping an object repository harvest that is not a list")
        return []
    pages: dict[str, list[dict]] = {}
    for snapshot in snapshots:
        if len(pages) >= max_pages:
            break
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("url"), str) or not isinstance(snapshot.get("elements"), list):
            logger.warning("Skipping a malformed object repository harvest snapshot")
            continue
        path = normalize_path(snapshot["url"])
        if path in pages:
            continue
        elements = [element for element in map(_valid_element, snapshot["elements"][:MAX_SNAPSHOT_ELEMENTS]) if element]
        skipped = min(len(snapshot["elements"]), MAX_SNAPSHOT_ELEMENTS) - len(elements)
        if skipped:
            logger.warning("Skipped %s malformed harvested elements on %s", skipped, path)
        pages[path] = elements
    return list(pages.items())


def read_harvest_file(run_dir: Path) -> list:
    """Snapshots the Python runner wrote to `harvest.json`; [] when missing or malformed."""
    path = Path(run_dir) / HARVEST_FILE
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Skipping an unreadable object repository harvest file: %s", path)
        return []
    if not isinstance(data, list):
        logger.warning("Skipping an object repository harvest file that is not a list: %s", path)
        return []
    return data
