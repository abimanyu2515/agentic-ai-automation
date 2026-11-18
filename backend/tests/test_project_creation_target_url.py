from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
from uuid import UUID, uuid4

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


# ---------------------------------------------------------------------------
# Spec 0002 gap coverage: invalid URL shapes, ownership and permissions,
# readiness on read paths, and JSON concurrency.
# ---------------------------------------------------------------------------

import backend.dependencies.auth_dependencies as auth_module
from backend.dependencies.auth_dependencies import get_current_user, get_db
from backend.repository.project_repository import get_project_repository


def _valid_payload(**overrides):
    return {
        "name": "Spec 0002 Project",
        "description": "Gap coverage",
        "framework": "playwright",
        "target_url": "https://gap.example.com",
        **overrides,
    }


@pytest.fixture
def as_user(monkeypatch):
    """Authenticate as a plain (non Superadmin) user. `allowed` decides the
    Projects:create permission. Mocks only the auth and DB boundary."""

    def install(allowed=True):
        user = SimpleNamespace(id=uuid4(), role="Tester", email="tester@example.com", is_active=True)
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = lambda: None
        monkeypatch.setattr(auth_module, "check_permission", lambda *a, **k: allowed)
        monkeypatch.setattr(auth_module, "_resolve_role_name", lambda *a, **k: "Tester")
        return user

    yield install
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


# covers: AC-2, AC-3, AC-6
@pytest.mark.parametrize(
    "target_url",
    [
        "/relative/path",
        "app.example.com",
        "ftp://files.example.com",
        "https://user:pw@example.com",
        "https://example.com/page#section",
        "https://",
    ],
    ids=["relative", "no-scheme", "ftp", "userinfo", "fragment", "no-host"],
)
def test_create_rejects_invalid_url_shapes_and_creates_nothing(client, target_url):
    response = client.post("/api/v1/projects", json=_valid_payload(target_url=target_url))

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "PROJECT_INVALID_CONFIGURATION"
    assert body["invalid_fields"] == {"target_url": "invalid_url"}
    assert get_project_repository().list_projects() == []


# covers: AC-2, AC-6
def test_create_reports_missing_target_url_as_missing_field(client):
    payload = _valid_payload()
    del payload["target_url"]

    response = client.post("/api/v1/projects", json=payload)

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "PROJECT_INVALID_CONFIGURATION"
    assert "target_url" in body["missing_fields"]
    assert get_project_repository().list_projects() == []


# covers: AC-2
@pytest.mark.parametrize("target_url", ["", "   "], ids=["empty", "whitespace"])
def test_create_rejects_blank_target_url_and_creates_nothing(client, target_url):
    response = client.post("/api/v1/projects", json=_valid_payload(target_url=target_url))

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "PROJECT_INVALID_CONFIGURATION"
    assert "target_url" in body["missing_fields"] or "target_url" in body["invalid_fields"]
    assert get_project_repository().list_projects() == []


# covers: AC-2, AC-6 (regression: a spaces only URL used to create a project with no URL)
def test_create_reports_spaces_only_target_url_as_missing(client):
    response = client.post("/api/v1/projects", json=_valid_payload(target_url="   "))

    assert response.status_code == 422
    assert response.json()["missing_fields"] == ["target_url"]


# covers: AC-3 (regression: an out of range port used to return 500)
def test_create_rejects_out_of_range_port_as_validation_error(client):
    response = client.post(
        "/api/v1/projects", json=_valid_payload(target_url="https://example.com:99999")
    )

    assert response.status_code == 422
    assert response.json()["invalid_fields"] == {"target_url": "invalid_url"}
    assert get_project_repository().list_projects() == []


# covers: AC-3
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("http://Example.COM:80/", "http://example.com"),
        ("https://example.com:8443/app/", "https://example.com:8443/app/"),
        ("https://example.com/login?next=/home", "https://example.com/login?next=/home"),
    ],
)
def test_create_stores_the_normalized_url(client, raw, expected):
    response = client.post("/api/v1/projects", json=_valid_payload(target_url=raw))

    assert response.status_code == 201
    assert response.json()["target_url"] == expected


# covers: AC-1, AC-6
def test_create_rejects_unsupported_framework(client):
    response = client.post("/api/v1/projects", json=_valid_payload(framework="Selenium-RC"))

    assert response.status_code == 422
    assert response.json()["invalid_fields"] == {"framework": "unsupported_framework"}
    assert get_project_repository().list_projects() == []


# covers: AC-1
def test_create_accepts_framework_case_insensitively(client):
    response = client.post("/api/v1/projects", json=_valid_payload(framework="  PLAYWRIGHT "))

    assert response.status_code == 201
    assert response.json()["framework"] == "playwright"


# covers: AC-8
def test_forbidden_credential_fields_never_reach_the_vault(client, monkeypatch):
    from backend.services import vault_service

    calls = []
    monkeypatch.setattr(vault_service.VaultService, "set_credentials", lambda *a, **k: calls.append(a))

    response = client.post(
        "/api/v1/projects",
        json=_valid_payload(target_username="alice", target_password="hunter2"),
    )

    assert response.status_code == 422
    assert "hunter2" not in response.text
    assert calls == []
    assert get_project_repository().list_projects() == []


# covers: AC-8
def test_successful_create_does_not_store_credentials(client, monkeypatch):
    from backend.services import vault_service

    calls = []
    monkeypatch.setattr(vault_service.VaultService, "set_credentials", lambda *a, **k: calls.append(a))

    response = client.post("/api/v1/projects", json=_valid_payload())

    assert response.status_code == 201
    assert calls == []


# covers: AC-7
def test_owner_and_creator_come_from_the_authenticated_user(client, as_user):
    user = as_user(allowed=True)

    response = client.post("/api/v1/projects", json=_valid_payload())

    assert response.status_code == 201
    stored = get_project_repository().get_project(UUID(response.json()["id"]))
    assert stored.owner_id == user.id
    assert stored.created_by == user.id


# covers: AC-7
def test_create_without_projects_create_permission_returns_403_and_writes_nothing(client, as_user):
    as_user(allowed=False)

    response = client.post("/api/v1/projects", json=_valid_payload())

    assert response.status_code == 403
    assert get_project_repository().list_projects() == []


# covers: AC-7
def test_create_without_authentication_returns_401_and_writes_nothing(client, monkeypatch):
    monkeypatch.setattr(auth_module, "is_running_tests", lambda: False)

    response = client.post("/api/v1/projects", json=_valid_payload())

    assert response.status_code == 401
    assert get_project_repository().list_projects() == []


# covers: AC-5
def test_same_url_is_accepted_for_two_owners_through_the_api(client, as_user):
    as_user(allowed=True)
    first = client.post("/api/v1/projects", json=_valid_payload(name="Owner A"))
    as_user(allowed=True)  # a second user with a fresh id

    second = client.post("/api/v1/projects", json=_valid_payload(name="Owner B"))

    assert first.status_code == 201
    assert second.status_code == 201


# covers: AC-4
def test_list_and_detail_include_readiness_without_credentials(client):
    created = client.post("/api/v1/projects", json=_valid_payload()).json()

    listed = client.get("/api/v1/projects").json()
    detail = client.get(f"/api/v1/projects/{created['id']}").json()

    listed_project = next(p for p in listed if p["id"] == created["id"])
    for body in (listed_project, detail):
        assert body["target_url"] == "https://gap.example.com"
        assert body["readiness"]["is_ready"] is True
        assert "target_password" not in body
        assert not body.get("target_username")


# Regression: the list route loaded every project once per project (N squared),
# so with a few hundred projects it took over a minute and the SPA never
# registered its route listener. It must load the project list exactly once.
def test_list_route_loads_projects_once(client, monkeypatch):
    import backend.main as main_module

    for index in range(3):
        client.post("/api/v1/projects", json=_valid_payload(name=f"Listed {index}", target_url=f"https://list{index}.example.com"))

    calls = []
    original = main_module.project_service.list_projects

    def counting_list_projects(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(main_module.project_service, "list_projects", counting_list_projects)

    listed = client.get("/api/v1/projects")

    assert listed.status_code == 200
    assert len(listed.json()) == 3
    assert all(project["readiness"]["is_ready"] for project in listed.json())
    assert len(calls) == 1


# covers: AC-4
def test_project_without_credentials_is_ready(client):
    response = client.post("/api/v1/projects", json=_valid_payload())

    readiness = response.json()["readiness"]
    assert readiness["is_ready"] is True
    assert readiness["missing_fields"] == []


# covers: AC-4
def test_jira_key_is_retained_on_read(client):
    created = client.post("/api/v1/projects", json=_valid_payload(jira_project_key="QA")).json()

    detail = client.get(f"/api/v1/projects/{created['id']}").json()

    assert detail["jira_project_key"] == "QA"


# covers: AC-5
def test_json_repository_concurrent_duplicates_yield_one_project_and_one_conflict(tmp_path):
    repository = JSONProjectRepository(tmp_path / "concurrent.json")
    owner_id = uuid4()
    barrier = threading.Barrier(2)
    outcomes = []

    def create(name):
        barrier.wait()
        try:
            repository.create_project(
                name, name, framework="playwright",
                target_url="https://race.example.com",
                owner_id=owner_id, created_by=owner_id,
            )
            outcomes.append("created")
        except ProjectURLConflictError:
            outcomes.append("conflict")

    threads = [threading.Thread(target=create, args=(f"Racer {i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["conflict", "created"]
    assert len(repository.list_projects()) == 1
