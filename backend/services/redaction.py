"""
Secret redaction shared by bug reports and accessibility reports (spec 0010, spec 0011 AC-5).

`secret_patterns` builds one case insensitive pattern for each project vault
credential and each test data value of a test case. `clean` replaces every
match with the redaction marker. A vault that cannot be read is logged and
skipped, so redaction still covers the test data values.
"""

from __future__ import annotations

import logging
import re
from uuid import UUID

from backend.services.object_repository_harvest import REDACTED, secret_values, values_from_test_data

logger = logging.getLogger(__name__)

_VALUE_ATTRIBUTE = re.compile(r"""\svalue\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", re.IGNORECASE)


def secret_patterns(project_id: UUID, test_case=None, vault=None) -> list[re.Pattern]:
    values: list = []
    try:
        if vault is None:
            from backend.services.vault_service import VaultService

            vault = VaultService()
        values.extend(vault.get_credentials(str(project_id)))
    except Exception:
        logger.warning("Redaction could not load vault credentials: project_id=%s", project_id)
    if test_case is not None:
        values.extend(values_from_test_data(test_case))
    return [re.compile(re.escape(v), re.IGNORECASE) for v in secret_values(values)]


def clean(text, patterns) -> str:
    text = "" if text is None else str(text)
    for pattern in patterns:
        text = pattern.sub(REDACTED, text)
    return text


def clean_html(snippet, patterns, limit: int = 500) -> str:
    """An HTML snippet with every `value` attribute dropped, secrets redacted, and a length cap."""
    return clean(_VALUE_ATTRIBUTE.sub("", "" if snippet is None else str(snippet)), patterns)[:limit]
