import pytest
from fastapi.testclient import TestClient
from uuid import uuid4
from backend.main import app
from backend.database.db import SessionLocal
from backend.database.db_models import UserDB, RoleDB, PermissionDB, RolePermissionDB
from backend.services.auth_service import hash_password

client = TestClient(app)

@pytest.fixture(scope="module")
def setup_db():
    db = SessionLocal()
    from backend.database.db_seeder import seed_database
    seed_database(db)
    yield db
    db.close()

def test_login_success(setup_db):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "dev@agenticai.com", "password": "devpassword"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["user"]["role"] == "Admin"
    assert data["user"]["email"] == "dev@agenticai.com"

def test_login_invalid_password(setup_db):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "dev@agenticai.com", "password": "wrongpassword"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"

def test_login_non_existent_user(setup_db):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "nonexistent@platform.ai", "password": "somepassword"}
    )
    assert response.status_code == 401

def test_token_refresh(setup_db):
    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "dev@platform.ai", "password": "devpassword"}
    )
    refresh_token = login_response.json()["refresh_token"]

    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token}
    )
    assert response.status_code == 200
    assert "access_token" in response.json()

def test_token_refresh_invalid(setup_db):
    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "invalid_refresh_token"}
    )
    assert response.status_code == 401

def test_admin_list_users_forbidden(setup_db):
    # Log in as a stakeholder who has restricted permissions
    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "stakeholder@platform.ai", "password": "stakeholderpassword"}
    )
    token = login_response.json()["access_token"]

    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/api/v1/admin/users", headers=headers)
    assert response.status_code == 403

def test_admin_list_users_success(setup_db):
    # Log in as Superadmin
    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "dev@platform.ai", "password": "devpassword"}
    )
    token = login_response.json()["access_token"]

    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/api/v1/admin/users", headers=headers)
    assert response.status_code == 200
    users = response.json()
    assert len(users) >= 5
    assert any(u["email"] == "dev@platform.ai" for u in users)

def test_admin_roles_and_permissions(setup_db):
    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "dev@platform.ai", "password": "devpassword"}
    )
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. List roles
    response = client.get("/api/v1/admin/roles", headers=headers)
    assert response.status_code == 200
    roles = response.json()
    assert any(r["name"] == "Tester" for r in roles)

    # 2. Get permissions matrix
    response = client.get("/api/v1/admin/permissions", headers=headers)
    assert response.status_code == 200
    matrix = response.json()
    assert "permissions" in matrix
    assert "role_permissions" in matrix

    # 3. Create a new custom role
    custom_role_name = f"CustomRole-{uuid4()}"
    response = client.post(
        "/api/v1/admin/roles",
        json={"name": custom_role_name},
        headers=headers
    )
    assert response.status_code == 200
    role_id = response.json()["role_id"]

    # Verify custom role appears in list
    response = client.get("/api/v1/admin/roles", headers=headers)
    assert any(r["id"] == role_id for r in response.json())

def test_audit_logs_retrieval(setup_db):
    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "dev@platform.ai", "password": "devpassword"}
    )
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/api/v1/admin/audit-logs", headers=headers)
    assert response.status_code == 200
    logs = response.json()
    assert len(logs) >= 1
    # First log should be the login action itself!
    assert any(l["action"] == "LOGIN" for l in logs)
