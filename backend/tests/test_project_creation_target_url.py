from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.repository.project_repository import JSONProjectRepository, ProjectURLConflictError


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolated_repository(tmp_path):
    import backend.repository.project_repository as repo_module

    previous = repo_module._active_project_repo
    repo_module._active_project_repo = JSONProjectRepository(tmp_path / "store.json")
    yield
    repo_module._active_project_repo = previous


def test_create_project_normalizes_url_and_reports_readiness(client):
    response = client.post(
        "/api/v1/projects",
        json={
            "name": "Configured Project",
            "description": "A configured project",
            "framework": "Playwright",
            "target_url": " HTTPS://Example.com:443/ ",
            "jira_project_key": "QA-1",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["target_url"] == "https://example.com"
    assert body["jira_project_key"] == "QA-1"
    assert body["readiness"]["is_ready"] is True
    assert body["readiness"]["missing_fields"] == []
    assert body["readiness"]["invalid_fields"] == {}
    assert "target_password" not in body


def test_create_project_rejects_forbidden_fields_with_structured_422(client):
    response = client.post(
        "/api/v1/projects",
        json={
            "name": "Forbidden Fields",
            "description": "Should not be created",
            "framework": "playwright",
            "target_url": "https://forbidden.example.com",
            "target_username": "user",
            "target_password": "secret",
            "owner_id": str(uuid4()),
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "PROJECT_INVALID_CONFIGURATION"
    assert body["invalid_fields"]["target_username"] == "field_not_permitted"
    assert body["invalid_fields"]["target_password"] == "field_not_permitted"
    assert body["invalid_fields"]["owner_id"] == "field_not_permitted"
    assert body["action"]["route"] == "#/settings?tab=project"


def test_create_project_rejects_invalid_url_with_configuration_detail(client):
    response = client.post(
        "/api/v1/projects",
        json={
            "name": "Invalid URL",
            "description": "Should not be created",
            "framework": "playwright",
            "target_url": "javascript:alert(1)",
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "PROJECT_INVALID_CONFIGURATION"
    assert body["invalid_fields"] == {"target_url": "invalid_url"}
    assert body["missing_fields"] == []


def test_create_project_rejects_duplicate_url_for_same_owner(client):
    payload = {
        "name": "First Project",
        "description": "First",
        "framework": "playwright",
        "target_url": "https://duplicate.example.com/",
    }
    first = client.post("/api/v1/projects", json=payload)
    assert first.status_code == 201

    second = client.post(
        "/api/v1/projects",
        json={**payload, "name": "Second Project"},
    )

    assert second.status_code == 409
    assert second.json() == {
        "code": "PROJECT_URL_CONFLICT",
        "message": "A project with this target URL already exists for the current owner.",
        "invalid_fields": {"target_url": "duplicate_url"},
        "conflicting_project_id": first.json()["id"],
    }


def test_json_repository_allows_same_url_for_different_owners():
    with TemporaryDirectory() as directory:
        repository = JSONProjectRepository(Path(directory) / "store.json")
        first_owner = uuid4()
        second_owner = uuid4()

        first = repository.create_project(
            "First",
            "First",
            framework="playwright",
            target_url="https://shared.example.com/",
            owner_id=first_owner,
            created_by=first_owner,
        )
        second = repository.create_project(
            "Second",
            "Second",
            framework="playwright",
            target_url="https://shared.example.com",
            owner_id=second_owner,
            created_by=second_owner,
        )

    assert first.target_url == second.target_url == "https://shared.example.com"
    assert first.owner_id != second.owner_id


def test_json_repository_rejects_duplicate_url_for_same_owner():
    with TemporaryDirectory() as directory:
        repository = JSONProjectRepository(Path(directory) / "store.json")
        owner_id = uuid4()
        repository.create_project(
            "First",
            "First",
            framework="playwright",
            target_url="https://same-owner.example.com",
            owner_id=owner_id,
            created_by=owner_id,
        )

        with pytest.raises(ProjectURLConflictError):
            repository.create_project(
                "Second",
                "Second",
                framework="playwright",
                target_url="https://same-owner.example.com/",
                owner_id=owner_id,
                created_by=owner_id,
            )
