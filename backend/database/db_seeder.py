from sqlalchemy.orm import Session
from backend.database.db_models import UserDB, RoleDB, PermissionDB, RolePermissionDB
from backend.services.auth_service import hash_password
from backend.config.settings import get_settings

DEFAULT_ROLES = ["Superadmin", "Admin", "Project Manager", "Tester", "Stakeholder"]

MODULES = ["Dashboard", "Requirements", "Scenarios", "TestCases", "Playwright", "Executions", "Reports", "Settings", "Users", "Roles", "Permissions", "Projects"]
ACTIONS = ["view", "create", "edit", "delete", "approve"]

def seed_database(db: Session) -> None:
    settings = get_settings()

    # 0. Migrate renamed roles using raw SQL to avoid ORM conflicts
    from sqlalchemy import text
    old_to_new = {"Super Admin": "Superadmin"}
    for old_name, new_name in old_to_new.items():
        old_role = db.query(RoleDB).filter(RoleDB.name == old_name).first()
        new_role = db.query(RoleDB).filter(RoleDB.name == new_name).first()
        if old_role and new_role:
            db.execute(text("UPDATE role_permissions SET role_id = :new_id WHERE role_id = :old_id AND permission_id NOT IN (SELECT permission_id FROM role_permissions WHERE role_id = :new_id)"), {"new_id": new_role.id, "old_id": old_role.id})
            db.execute(text("DELETE FROM role_permissions WHERE role_id = :old_id"), {"old_id": old_role.id})
            db.execute(text("UPDATE users SET role = :new_name WHERE role = :old_name"), {"new_name": new_name, "old_name": old_name})
            db.execute(text("DELETE FROM roles WHERE id = :old_id"), {"old_id": old_role.id})
            db.commit()
        elif old_role and not new_role:
            db.execute(text("UPDATE roles SET name = :new_name WHERE name = :old_name"), {"new_name": new_name, "old_name": old_name})
            db.execute(text("UPDATE users SET role = :new_name WHERE role = :old_name"), {"new_name": new_name, "old_name": old_name})
            db.commit()
    
    # 1. Seed Roles
    existing_roles = {r.name for r in db.query(RoleDB).all()}
    role_objects = {}
    for name in DEFAULT_ROLES:
        if name not in existing_roles:
            role = RoleDB(name=name)
            db.add(role)
            role_objects[name] = role
        else:
            role_objects[name] = db.query(RoleDB).filter(RoleDB.name == name).first()
    db.commit()

    # 2. Seed Permissions
    existing_perms = {(p.module, p.action) for p in db.query(PermissionDB).all()}
    perm_objects = {}
    for module in MODULES:
        for action in ACTIONS:
            if (module, action) not in existing_perms:
                perm = PermissionDB(module=module, action=action)
                db.add(perm)
                perm_objects[(module, action)] = perm
            else:
                perm_objects[(module, action)] = db.query(PermissionDB).filter(
                    PermissionDB.module == module, PermissionDB.action == action
                ).first()
    db.commit()

    # 3. Seed Role-Permissions Mapping
    all_perms = db.query(PermissionDB).all()
    perm_lookup = {(p.module, p.action): p.id for p in all_perms}

    def assign_perm(role_name, module, action):
        role_id = role_objects[role_name].id
        perm_id = perm_lookup.get((module, action))
        if perm_id:
            exists = db.query(RolePermissionDB).filter(
                RolePermissionDB.role_id == role_id,
                RolePermissionDB.permission_id == perm_id
            ).first()
            if not exists:
                rp = RolePermissionDB(role_id=role_id, permission_id=perm_id)
                db.add(rp)

    # Superadmin gets all permissions
    for module in MODULES:
        for action in ACTIONS:
            assign_perm("Superadmin", module, action)

    # Admin gets everything except Settings and Users modification
    for module in MODULES:
        if module not in ["Settings", "Users"]:
            for action in ACTIONS:
                assign_perm("Admin", module, action)
        else:
            assign_perm("Admin", module, "view")

    # Project Manager gets view, create, edit, delete, approve for business models
    for module in ["Dashboard", "Requirements", "Scenarios", "TestCases", "Playwright", "Executions", "Reports"]:
        for action in ACTIONS:
            assign_perm("Project Manager", module, action)
    assign_perm("Project Manager", "Projects", "create")
    assign_perm("Project Manager", "Settings", "view")

    # Tester gets view, create, edit for scenarios, testcases, executions, playwright, and view-only for dashboard, requirements, reports
    for module in ["Scenarios", "TestCases", "Playwright", "Executions"]:
        for action in ["view", "create", "edit"]:
            assign_perm("Tester", module, action)
    for module in ["Dashboard", "Requirements", "Reports"]:
        assign_perm("Tester", module, "view")

    # Stakeholder gets read-only view access to reports, executions, dashboard
    for module in ["Dashboard", "Requirements", "Executions", "Reports"]:
        assign_perm("Stakeholder", module, "view")

    db.commit()

    # 4. Seed Development Users
    if settings.app_env in ["development", "testing"]:
        dev_users = [
            {"email": "dev@agenticai.com", "name": "Admin", "pass": "devpassword", "role": "Admin"},
            {"email": "admin@agenticai.com", "name": "Superadmin", "pass": "admin123", "role": "Superadmin"},
            {"email": "pm@agenticai.com", "name": "Project Manager User", "pass": "pmpassword", "role": "Project Manager"},
            {"email": "tester@agenticai.com", "name": "Tester User", "pass": "testerpassword", "role": "Tester"},
            {"email": "stakeholder@agenticai.com", "name": "Stakeholder User", "pass": "stakeholderpassword", "role": "Stakeholder"}
        ]
        for du in dev_users:
            exists = db.query(UserDB).filter(UserDB.email == du["email"]).first()
            if not exists:
                user = UserDB(
                    email=du["email"],
                    password_hash=hash_password(du["pass"]),
                    full_name=du["name"],
                    role=du["role"],
                    is_active=True
                )
                db.add(user)
            else:
                # Keep the documented development accounts usable after an
                # existing database is reused between application restarts.
                exists.password_hash = hash_password(du["pass"])
                exists.role = du["role"]
                exists.full_name = du["name"]
                exists.is_active = True
        db.commit()
