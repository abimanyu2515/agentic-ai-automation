from sqlalchemy.orm import Session
from datetime import datetime, timezone
from backend.database.db_models import AuditLogDB
from uuid import UUID

def log_audit(
    db: Session,
    user_id: UUID | None,
    project_id: UUID | None,
    action: str,
    module: str,
    field_changes: dict = None
) -> None:
    """Log an audit entry in the database.
    
    Actions: CREATE, UPDATE, DELETE, APPROVE, REJECT, LOGIN, LOGOUT, EXECUTE, UPLOAD
    Modules: PROJECT, REQUIREMENT, SCENARIO, TESTCASE, PLAYWRIGHT, EXECUTION, REPORT, USER, ROLE, PERMISSION
    """
    try:
        log_entry = AuditLogDB(
            user_id=user_id,
            project_id=project_id,
            timestamp=datetime.now(timezone.utc),
            action=action.upper(),
            module=module.upper(),
            field_changes=field_changes or {}
        )
        db.add(log_entry)
        db.commit()
    except Exception as e:
        # Prevent audit logging failure from crashing the main transaction
        db.rollback()
        print(f"[AUDIT LOG ERROR] Failed to record audit log: {e}")
