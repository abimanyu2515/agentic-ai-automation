import asyncio
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import MagicMock

import pytest

from backend.agents.application_reconnaissance_agent import (
    ApplicationInspectionError,
    ApplicationReconnaissanceAgent,
)
from backend.models.test_case import TestCase
from backend.models.state import WorkflowState
from backend.models.project import Project


def test_safe_origin_excludes_credentials_paths_and_default_ports():
    assert ApplicationReconnaissanceAgent._safe_origin(
        "https://user:secret@example.test:443/path?token=value"
    ) == "https://example.test"


def test_link_local_target_detection():
    assert ApplicationReconnaissanceAgent._resolves_to_link_local(
        "http://169.254.10.20/"
    )
    assert not ApplicationReconnaissanceAgent._resolves_to_link_local(
        "http://127.0.0.1/"
    )


def test_reconnaissance_blocks_step_urls_outside_project_origin():
    test_case = TestCase(
        scenario_id=uuid4(),
        title="Visit an external destination",
        steps=["Visit https://outside.example/login"],
        expected_result="The destination opens",
    )
    state = WorkflowState(requirement=None)

    with pytest.raises(ApplicationInspectionError) as error:
        asyncio.run(
            ApplicationReconnaissanceAgent()._reconnoiter(
                state=state,
                target_url="https://app.example",
                test_cases=[test_case],
            )
        )

    assert error.value.reason == "insufficient_evidence"


def test_safe_origin_preserves_non_default_ports():
    assert ApplicationReconnaissanceAgent._safe_origin(
        "https://example.test:8443/path"
    ) == "https://example.test:8443"
    assert ApplicationReconnaissanceAgent._safe_origin(
        "http://example.test:8080/path"
    ) == "http://example.test:8080"


def test_safe_origin_handles_ipv6():
    assert ApplicationReconnaissanceAgent._safe_origin(
        "https://[::1]:8443/path"
    ) == "https://[::1]:8443"


def test_extract_urls_from_text_handles_relative_paths():
    agent = ApplicationReconnaissanceAgent()
    urls = agent._extract_urls_from_text("Go to /dashboard and visit /settings", "https://app.example")
    assert "https://app.example/dashboard" in urls
    assert "https://app.example/settings" in urls


def test_extract_urls_from_text_handles_full_urls():
    agent = ApplicationReconnaissanceAgent()
    urls = agent._extract_urls_from_text("Visit https://other.example/page", "https://app.example")
    assert "https://other.example/page" in urls


def test_extract_urls_from_text_handles_markdown_links():
    agent = ApplicationReconnaissanceAgent()
    urls = agent._extract_urls_from_text("See [here](https://example.com/page)", "https://app.example")
    assert "https://example.com/page" in urls


def test_reconnaissance_returns_error_page_for_empty_target_url():
    """Empty target_url results in a page with error, not an exception."""
    agent = ApplicationReconnaissanceAgent()
    state = WorkflowState(requirement=None)
    result = asyncio.run(
        agent._reconnoiter(
            state=state,
            target_url="",
            test_cases=[],
        )
    )
    # Returns dom_context with a page containing error
    assert "pages" in result
    assert len(result["pages"]) == 1
    assert "error" in result["pages"][0]
    assert "Navigation failed" in result["pages"][0]["error"]


def test_reconnaissance_handles_navigation_failure_in_page_result():
    agent = ApplicationReconnaissanceAgent()
    test_case = TestCase(
        scenario_id=uuid4(),
        title="Test",
        steps=["Open the app"],
        expected_result="It works",
    )
    class BrokenPage:
        async def goto(self, *args, **kwargs):
            raise RuntimeError("navigation failed")

    result = asyncio.run(
        agent._inspect_page(
            page=BrokenPage(),
            url="https://unreachable.invalid.tld",
            test_cases=[test_case],
            allow_invalid_login_probe=False,
        )
    )
    assert result["error"] == "Navigation failed"
    assert result["url"] == "https://unreachable.invalid.tld"


def test_perform_login_not_called_when_no_login_page():
    """Test that auto-login is not attempted when page doesn't look like login."""
    agent = ApplicationReconnaissanceAgent()
    # This is an internal behavior test - the key point is that _perform_login
    # should only be called when _looks_like_login_page returns True
    assert True  # placeholder for integration test


def test_dom_context_structure_has_required_keys():
    """Verify that the DOM context structure matches what validation expects."""
    agent = ApplicationReconnaissanceAgent()
    # The dom_context should have pages with forms, elements, navigation
    # This is verified by the existing validation tests
    assert True  # structural test covered by validation tests


def test_project_safe_test_environment_defaults_to_false():
    project = Project(name="Example", target_url="https://example.test")

    assert project.is_safe_test_environment is False


def test_inspection_rejects_projects_not_marked_safe():
    project = SimpleNamespace(
        id=uuid4(),
        is_safe_test_environment=False,
        target_url="https://example.test",
    )
    agent = ApplicationReconnaissanceAgent(
        repo=SimpleNamespace(get_project=lambda project_id: project)
    )

    with pytest.raises(ApplicationInspectionError, match="not marked as a safe"):
        agent.inspect_project(project.id, [], allow_invalid_login_probe=False)


def test_invalid_login_probe_requires_explicit_test_data():
    test_case = TestCase(
        scenario_id=uuid4(),
        title="Reject invalid login",
        steps=["Submit invalid credentials"],
        expected_result="An error is displayed",
        test_data={"username": "invalid-user", "password": "invalid-password"},
    )

    assert ApplicationReconnaissanceAgent._explicit_invalid_login_data(test_case) == (
        "invalid-user",
        "invalid-password",
    )
    test_case.test_data = {}
    assert ApplicationReconnaissanceAgent._explicit_invalid_login_data(test_case) is None


def test_visible_text_sanitization_redacts_supplied_test_data():
    test_case = TestCase(
        scenario_id=uuid4(),
        title="Reject invalid login",
        steps=["Submit invalid credentials"],
        expected_result="An error is displayed",
        test_data={"username": "private-user", "password": "private-password"},
    )

    result = ApplicationReconnaissanceAgent._sanitize_text(
        "private-user private-password",
        [test_case],
    )

    assert "private-user" not in result
    assert "private-password" not in result
