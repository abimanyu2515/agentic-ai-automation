"""Adding requirements manually or by file import must never replace existing ones."""

import io

import pandas as pd
import pytest

import backend.repository.project_repository as repo_module
from backend.repository.project_repository import JSONProjectRepository
from backend.services.project_service import ProjectService


@pytest.fixture
def service(tmp_path):
    original = repo_module._active_project_repo
    repo_module._active_project_repo = JSONProjectRepository(
        file_path=tmp_path / "requirements_store.json"
    )
    yield ProjectService()
    repo_module._active_project_repo = original


@pytest.fixture
def project(service):
    return service.create_project(
        "Requirements", "Requirement creation", target_url="https://app.example.test"
    )


def _add(service, project, title, requirement_id=None):
    return service.add_requirement(
        project_id=project.id,
        title=title,
        description=f"{title} description",
        priority="medium",
        business_domain="general",
        requirement_id=requirement_id,
        requirement_title=title,
    )


def test_manual_requirements_are_all_kept(service, project):
    first = _add(service, project, "Login")
    second = _add(service, project, "Logout")
    third = _add(service, project, "Search")

    requirements = service.get_requirements(project.id)

    assert {r.id for r in requirements} == {first.id, second.id, third.id}
    assert [r.requirement_id for r in (first, second, third)] == ["REQ-001", "REQ-002", "REQ-003"]


def test_importing_a_file_with_reused_ids_adds_new_requirements(service, project):
    first_import = [
        _add(service, project, "REQ-001: Login", "REQ-001"),
        _add(service, project, "REQ-002: Logout", "REQ-002"),
    ]
    second_import = [
        _add(service, project, "REQ-001: Checkout", "REQ-001"),
        _add(service, project, "REQ-002: Payment", "REQ-002"),
    ]

    requirements = service.get_requirements(project.id)

    assert len(requirements) == 4
    assert {r.title for r in requirements} == {
        "REQ-001: Login",
        "REQ-002: Logout",
        "REQ-003: Checkout",
        "REQ-004: Payment",
    }
    assert {r.id for r in first_import + second_import} == {r.id for r in requirements}


def test_explicit_id_collision_keeps_the_id_with_a_suffix(service, project):
    _add(service, project, "PAY-7: Card payment", "PAY-7")
    duplicate = _add(service, project, "PAY-7: Wallet payment", "PAY-7")

    assert duplicate.requirement_id == "PAY-7-2"
    assert duplicate.title == "PAY-7-2: Wallet payment"
    assert len(service.get_requirements(project.id)) == 2


def test_manual_and_imported_requirements_coexist(service, project):
    _add(service, project, "REQ-001: Imported login", "REQ-001")
    manual = _add(service, project, "Manual search")

    assert manual.requirement_id == "REQ-002"
    assert len(service.get_requirements(project.id)) == 2


def test_xlsx_rows_without_ids_each_become_a_requirement(service, project):
    frame = pd.DataFrame({
        "Requirement ID": [None, None, "REQ-010"],
        "Requirement": ["Users can log in", "Users can log out", "Users can search"],
    })
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False)

    for block in service.parse_requirements_from_file("reqs.xlsx", buffer.getvalue()):
        _add(service, project, block["title"], block["requirement_id"])

    requirements = service.get_requirements(project.id)
    assert len(requirements) == 3
    assert all(r.requirement_id and r.requirement_id.lower() != "nan" for r in requirements)
    assert len({r.requirement_id for r in requirements}) == 3
