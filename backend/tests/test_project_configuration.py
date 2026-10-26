"""
Tests for backend/services/project_configuration.py
"""

from uuid import uuid4

import pytest

from backend.services.project_configuration import (
    ProjectConfigurationError,
    normalize_target_url,
    normalize_framework,
    readiness_for_project,
    require_ready_project,
    SUPPORTED_FRAMEWORKS,
)


class MockProject:
    """Simple mock project for testing."""
    def __init__(self, id=None, target_url=None, framework=None, owner_id=None):
        self.id = id or uuid4()
        self.target_url = target_url
        self.framework = framework
        self.owner_id = owner_id


def test_normalize_target_url_strips_whitespace():
    assert normalize_target_url("  https://example.com  ") == "https://example.com"


def test_normalize_target_url_lowercases_scheme_and_host():
    assert normalize_target_url("HTTPS://EXAMPLE.COM") == "https://example.com"


def test_normalize_target_url_removes_default_ports():
    assert normalize_target_url("https://example.com:443") == "https://example.com"
    assert normalize_target_url("http://example.com:80") == "http://example.com"


def test_normalize_target_url_preserves_non_default_ports():
    assert normalize_target_url("https://example.com:8443") == "https://example.com:8443"
    assert normalize_target_url("http://example.com:8080") == "http://example.com:8080"


def test_normalize_target_url_removes_trailing_slash():
    assert normalize_target_url("https://example.com/") == "https://example.com"


def test_normalize_target_url_preserves_path():
    assert normalize_target_url("https://example.com/app") == "https://example.com/app"
    # Trailing slash on non-root path is preserved (only root "/" is removed)
    assert normalize_target_url("https://example.com/app/") == "https://example.com/app/"


def test_normalize_target_url_rejects_invalid_scheme():
    with pytest.raises(ProjectConfigurationError) as error:
        normalize_target_url("ftp://example.com")
    assert error.value.code == "PROJECT_INVALID_CONFIGURATION"
    assert error.value.invalid_fields == {"target_url": "invalid_url"}


def test_normalize_target_url_rejects_userinfo():
    with pytest.raises(ProjectConfigurationError) as error:
        normalize_target_url("https://user:pass@example.com")
    assert error.value.invalid_fields == {"target_url": "invalid_url"}


def test_normalize_target_url_rejects_query_string():
    with pytest.raises(ProjectConfigurationError) as error:
        normalize_target_url("https://example.com?token=value")
    assert error.value.invalid_fields == {"target_url": "invalid_url"}


def test_normalize_target_url_rejects_fragment():
    with pytest.raises(ProjectConfigurationError) as error:
        normalize_target_url("https://example.com#section")
    assert error.value.invalid_fields == {"target_url": "invalid_url"}


def test_normalize_target_url_rejects_missing_host():
    with pytest.raises(ProjectConfigurationError) as error:
        normalize_target_url("https://")
    assert error.value.invalid_fields == {"target_url": "invalid_url"}


def test_normalize_target_url_handles_ipv6():
    assert normalize_target_url("https://[::1]") == "https://[::1]"
    assert normalize_target_url("https://[::1]:8443") == "https://[::1]:8443"


def test_normalize_framework_lowercases():
    assert normalize_framework("PLAYWRIGHT") == "playwright"
    assert normalize_framework("  Selenium  ") == "selenium"


def test_normalize_framework_returns_none_for_empty():
    assert normalize_framework("") is None
    assert normalize_framework(None) is None


def test_readiness_for_project_missing_target_url():
    project = MockProject(target_url=None, framework="playwright")
    readiness = readiness_for_project(project)
    assert readiness["is_ready"] is False
    assert "target_url" in readiness["missing_fields"]


def test_readiness_for_project_missing_framework():
    project = MockProject(target_url="https://example.com", framework=None)
    readiness = readiness_for_project(project)
    assert readiness["is_ready"] is False
    assert "framework" in readiness["missing_fields"]


def test_readiness_for_project_invalid_framework():
    project = MockProject(target_url="https://example.com", framework="cypress")
    readiness = readiness_for_project(project)
    assert readiness["is_ready"] is False
    assert readiness["invalid_fields"]["framework"] == "unsupported_framework"


def test_readiness_for_project_ready_when_complete():
    project = MockProject(target_url="https://example.com", framework="playwright")
    readiness = readiness_for_project(project)
    assert readiness["is_ready"] is True
    assert readiness["missing_fields"] == []
    assert readiness["invalid_fields"] == {}
    assert readiness["target_url"] == "https://example.com"


def test_readiness_for_project_detects_duplicate_url_same_owner():
    project1 = MockProject(id=uuid4(), target_url="https://example.com", framework="playwright", owner_id=uuid4())
    project2 = MockProject(id=uuid4(), target_url="https://example.com", framework="playwright", owner_id=project1.owner_id)
    project2.id = uuid4()  # Different project, same owner

    readiness = readiness_for_project(project2, projects=[project1, project2])
    assert readiness["is_ready"] is False
    assert readiness["invalid_fields"]["target_url"] == "duplicate_url"


def test_readiness_for_project_allows_same_url_different_owners():
    owner1 = uuid4()
    owner2 = uuid4()
    project1 = MockProject(id=uuid4(), target_url="https://example.com", framework="playwright", owner_id=owner1)
    project2 = MockProject(id=uuid4(), target_url="https://example.com", framework="playwright", owner_id=owner2)

    readiness = readiness_for_project(project2, projects=[project1, project2])
    assert readiness["is_ready"] is True


def test_require_ready_project_returns_configuration():
    project = MockProject(id=uuid4(), target_url="https://example.com", framework="playwright")
    config = require_ready_project(project)
    assert config.project_id == project.id
    assert config.target_url == "https://example.com"
    assert config.framework == "playwright"
    assert config.credential_reference == str(project.id)


def test_require_ready_project_raises_on_unready():
    project = MockProject(target_url=None, framework="playwright")
    with pytest.raises(ProjectConfigurationError) as error:
        require_ready_project(project)
    assert error.value.code == "PROJECT_NOT_READY"
    assert "target_url" in error.value.missing_fields


def test_supported_frameworks_constant():
    assert SUPPORTED_FRAMEWORKS == ("playwright", "selenium", "cucumber")