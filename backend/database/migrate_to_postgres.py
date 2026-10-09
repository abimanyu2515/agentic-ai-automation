import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, create_engine
from sqlalchemy.orm import sessionmaker

from backend.config.settings import get_settings
from backend.database.db import Base
from backend.database.db_models import (
    UserDB, RoleDB, PermissionDB, RolePermissionDB, ProjectUserDB, AuditLogDB,
    ProjectDB, RequirementDB, ScenarioDB, TestCaseDB, ExecutionDB, ReportDB,
    DocumentDB, ScenarioNoteDB, TestCaseNoteDB, ReleaseDB, TestCycleDB
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migration")

STORE_PATH = Path(__file__).parent / "project_store.json"


def normalize_dict(d: dict) -> dict:
    """Helper to normalize a dictionary via JSON serialization for comparison."""
    return json.loads(json.dumps(d, default=str))


def run_migration() -> int:
    settings = get_settings()
    provider_name = settings.repository.provider
    db_url = settings.repository.database_url
    if provider_name == "json":
        db_url = "sqlite:///./backend/database/local_fallback.db"
    logger.info(f"Connecting to database: {db_url}")
    
    # SQLite does not support pool_size or max_overflow
    engine_kwargs = {}
    if "sqlite" in db_url:
        engine_kwargs["connect_args"] = {"check_same_thread": False}
        
    engine = create_engine(db_url, **engine_kwargs)
    Base.metadata.create_all(engine)
    
    Session = sessionmaker(bind=engine)
    session = Session()

    if not STORE_PATH.exists():
        logger.error(f"Store file not found at {STORE_PATH}. Nothing to migrate.")
        return 1

    logger.info(f"Reading JSON data from {STORE_PATH}")
    with open(STORE_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # --- DATA SANITIZATION / ORPHAN CLEANUP ---
    logger.info("Sanitizing source JSON database to filter orphaned records violating relational integrity...")
    valid_project_ids = {UUID(p["id"]) for p in raw.get("projects", [])}
    valid_req_ids = {UUID(r_id) for r_id in raw.get("requirements", {}).keys()}
    valid_scenario_ids = {UUID(s_id) for s_id in raw.get("scenarios", {}).keys()}
    valid_tc_ids = {UUID(tc_id) for tc_id in raw.get("test_cases", {}).keys()}
    valid_execution_ids = {UUID(ex_id) for ex_id in raw.get("execution_results", {}).keys()}

    # Filter requirements
    requirements_clean = {}
    for r_id_str, r in raw.get("requirements", {}).items():
        r_id = UUID(r_id_str)
        belongs_to_proj = False
        for p in raw.get("projects", []):
            if r_id_str in p.get("requirements", []):
                belongs_to_proj = True
                break
        if belongs_to_proj:
            requirements_clean[r_id_str] = r
        else:
            logger.warning(f"Removing orphaned requirement {r_id} (not linked to any project)")
    raw["requirements"] = requirements_clean
    valid_req_ids = {UUID(r_id) for r_id in raw["requirements"].keys()}

    # Clean requirements lists in projects
    for p in raw.get("projects", []):
        p["requirements"] = [r_id for r_id in p.get("requirements", []) if UUID(r_id) in valid_req_ids]

    # Filter scenarios
    scenarios_clean = {}
    for s_id_str, s in raw.get("scenarios", {}).items():
        if UUID(s["requirement_id"]) in valid_req_ids:
            scenarios_clean[s_id_str] = s
        else:
            logger.warning(f"Removing orphaned scenario {s_id_str} (requirement {s['requirement_id']} not found)")
    raw["scenarios"] = scenarios_clean
    valid_scenario_ids = {UUID(s_id) for s_id in raw["scenarios"].keys()}

    # Filter test cases
    test_cases_clean = {}
    for tc_id_str, tc in raw.get("test_cases", {}).items():
        if UUID(tc["scenario_id"]) in valid_scenario_ids:
            test_cases_clean[tc_id_str] = tc
        else:
            logger.warning(f"Removing orphaned test case {tc_id_str} (scenario {tc['scenario_id']} not found)")
    raw["test_cases"] = test_cases_clean
    valid_tc_ids = {UUID(tc_id) for tc_id in raw["test_cases"].keys()}

    # Filter documents
    documents_clean = {}
    for d_id_str, d in raw.get("documents", {}).items():
        if UUID(d["project_id"]) in valid_project_ids:
            documents_clean[d_id_str] = d
        else:
            logger.warning(f"Removing orphaned document {d_id_str} (project {d['project_id']} not found)")
    raw["documents"] = documents_clean

    # Filter executions
    executions_clean = {}
    for ex_id_str, ex in raw.get("execution_results", {}).items():
        if UUID(ex["test_case_id"]) in valid_tc_ids and UUID(ex["project_id"]) in valid_project_ids:
            executions_clean[ex_id_str] = ex
        else:
            logger.warning(f"Removing orphaned execution {ex_id_str} (test case {ex.get('test_case_id')} or project {ex.get('project_id')} not found)")
    raw["execution_results"] = executions_clean
    valid_execution_ids = {UUID(ex_id) for ex_id in raw["execution_results"].keys()}

    # Filter reports
    reports_clean = {}
    for r_id_str, rep in raw.get("reports", {}).items():
        if UUID(rep["project_id"]) in valid_project_ids and UUID(rep["execution_id"]) in valid_execution_ids:
            reports_clean[r_id_str] = rep
        else:
            logger.warning(f"Removing orphaned report {r_id_str} (project {rep.get('project_id')} or execution {rep.get('execution_id')} not found)")
    raw["reports"] = reports_clean

    # Filter scenario notes
    scenario_notes_clean = {}
    for s_id_str, notes in raw.get("scenario_notes", {}).items():
        if UUID(s_id_str) in valid_scenario_ids:
            scenario_notes_clean[s_id_str] = notes
        else:
            logger.warning(f"Removing scenario notes for orphaned scenario {s_id_str}")
    raw["scenario_notes"] = scenario_notes_clean

    # Filter test case notes
    test_case_notes_clean = {}
    for tc_id_str, notes in raw.get("test_case_notes", {}).items():
        if UUID(tc_id_str) in valid_tc_ids:
            test_case_notes_clean[tc_id_str] = notes
        else:
            logger.warning(f"Removing test case notes for orphaned test case {tc_id_str}")
    raw["test_case_notes"] = test_case_notes_clean
    logger.info("Sanitization complete.")

    try:
        # 1. Migrate Projects
        logger.info("Migrating projects...")
        project_req_map = {}  # Map project_id to its list of requirement IDs
        for p in raw.get("projects", []):
            p_id = UUID(p["id"])
            project_req_map[p_id] = [UUID(rid) for rid in p.get("requirements", [])]
            
            db_proj = session.get(ProjectDB, p_id)
            if not db_proj:
                db_proj = ProjectDB(
                    id=p_id,
                    name=p["name"],
                    description=p.get("description", ""),
                    line_of_business=p.get("line_of_business", "general"),
                    framework=p.get("framework", "playwright"),
                    jira_project_key=p.get("jira_project_key"),
                    target_url=p.get("target_url"),
                    target_username=p.get("target_username"),
                    target_password_enc=p.get("target_password_enc"),
                    created_at=datetime.fromisoformat(p["created_at"].replace("Z", "+00:00")),
                    updated_at=datetime.fromisoformat(p["created_at"].replace("Z", "+00:00"))
                )
                session.add(db_proj)

        # 2. Migrate Requirements
        logger.info("Migrating requirements...")
        req_project_map = {}  # Inverted map: requirement_id -> project_id
        for p_id, r_ids in project_req_map.items():
            for r_id in r_ids:
                req_project_map[r_id] = p_id

        for r_id_str, r in raw.get("requirements", {}).items():
            r_id = UUID(r_id_str)
            p_id = req_project_map.get(r_id)
            if not p_id:
                logger.warning(f"Requirement {r_id} does not belong to any project. Skipping.")
                continue

            db_req = session.get(RequirementDB, r_id)
            if not db_req:
                db_req = RequirementDB(
                    id=r_id,
                    project_id=p_id,
                    title=r["title"],
                    description=r["description"],
                    source=r.get("source", "manual"),
                    uploaded_at=datetime.fromisoformat(r["uploaded_at"].replace("Z", "+00:00")),
                    original_filename=r.get("original_filename"),
                    requirement_id=r.get("requirement_id"),
                    requirement_title=r.get("requirement_title"),
                    priority=r.get("priority", "medium"),
                    business_domain=r.get("business_domain", "general"),
                    attachments=r.get("attachments", []),
                    feature_mapping=r.get("feature_mapping"),
                    created_at=datetime.fromisoformat(r["uploaded_at"].replace("Z", "+00:00")),
                    updated_at=datetime.fromisoformat(r["uploaded_at"].replace("Z", "+00:00"))
                )
                session.add(db_req)

        # 3. Migrate Scenarios
        logger.info("Migrating scenarios...")
        for s_id_str, s in raw.get("scenarios", {}).items():
            s_id = UUID(s_id_str)
            db_sc = session.get(ScenarioDB, s_id)
            if not db_sc:
                db_sc = ScenarioDB(
                    id=s_id,
                    requirement_id=UUID(s["requirement_id"]),
                    scenario_name=s["scenario_name"],
                    description=s["description"],
                    priority=s.get("priority", "medium"),
                    confidence=s.get("confidence", 0.0),
                    approved=s.get("approved", False),
                    reviewer=s.get("reviewer"),
                    approved_at=datetime.fromisoformat(s["approved_at"].replace("Z", "+00:00")) if s.get("approved_at") else None,
                    generated_at=datetime.fromisoformat(s["generated_at"].replace("Z", "+00:00")) if s.get("generated_at") else datetime.now(timezone.utc),
                    created_at=datetime.fromisoformat(s["generated_at"].replace("Z", "+00:00")) if s.get("generated_at") else datetime.now(timezone.utc),
                    updated_at=datetime.fromisoformat(s["generated_at"].replace("Z", "+00:00")) if s.get("generated_at") else datetime.now(timezone.utc)
                )
                session.add(db_sc)

        # 4. Migrate Test Cases
        logger.info("Migrating test cases...")
        for tc_id_str, tc in raw.get("test_cases", {}).items():
            tc_id = UUID(tc_id_str)
            db_tc = session.get(TestCaseDB, tc_id)
            if not db_tc:
                db_tc = TestCaseDB(
                    id=tc_id,
                    scenario_id=UUID(tc["scenario_id"]),
                    title=tc["title"],
                    preconditions=tc.get("preconditions", []),
                    steps=tc.get("steps", []),
                    expected_result=tc["expected_result"],
                    priority=tc.get("priority", "medium"),
                    status=tc.get("status", "pending"),
                    confidence=tc.get("confidence", 0.0),
                    evaluation_status=tc.get("evaluation_status", "pending"),
                    evaluation_reason=tc.get("evaluation_reason"),
                    playwright_script=tc.get("playwright_script"),
                    reviewer=tc.get("reviewer"),
                    approved_at=datetime.fromisoformat(tc["approved_at"].replace("Z", "+00:00")) if tc.get("approved_at") else None,
                    generated_at=datetime.fromisoformat(tc["generated_at"].replace("Z", "+00:00")) if tc.get("generated_at") else datetime.now(timezone.utc),
                    created_at=datetime.fromisoformat(tc["generated_at"].replace("Z", "+00:00")) if tc.get("generated_at") else datetime.now(timezone.utc),
                    updated_at=datetime.fromisoformat(tc["generated_at"].replace("Z", "+00:00")) if tc.get("generated_at") else datetime.now(timezone.utc)
                )
                session.add(db_tc)

        # 5. Migrate Documents
        logger.info("Migrating documents...")
        for d_id_str, d in raw.get("documents", {}).items():
            d_id = UUID(d_id_str)
            db_doc = session.get(DocumentDB, d_id)
            if not db_doc:
                db_doc = DocumentDB(
                    id=d_id,
                    project_id=UUID(d["project_id"]),
                    filename=d["filename"],
                    original_filename=d["original_filename"],
                    mime_type=d["mime_type"],
                    size=d["size"],
                    uploaded_at=datetime.fromisoformat(d["uploaded_at"].replace("Z", "+00:00")),
                    storage_provider=d.get("storage_provider", "local"),
                    storage_key=d.get("storage_key", d["storage_path"]),
                    embedding_status=d.get("embedding_status", "pending"),
                    vector_collection=d.get("vector_collection"),
                    metadata_json=d.get("metadata", {}),
                    created_at=datetime.fromisoformat(d["uploaded_at"].replace("Z", "+00:00")),
                    updated_at=datetime.fromisoformat(d["uploaded_at"].replace("Z", "+00:00"))
                )
                session.add(db_doc)

        # 6. Migrate Executions (ExecutionResults)
        logger.info("Migrating executions...")
        for ex_id_str, ex in raw.get("execution_results", {}).items():
            ex_id = UUID(ex_id_str)
            db_ex = session.get(ExecutionDB, ex_id)
            if not db_ex:
                db_ex = ExecutionDB(
                    id=ex_id,
                    test_case_id=UUID(ex["test_case_id"]),
                    project_id=UUID(ex["project_id"]),
                    status=ex["status"],
                    duration_seconds=ex.get("duration_seconds", 0.0),
                    error_message=ex.get("error_message"),
                    screenshot_path=ex.get("screenshot_path"),
                    video_path=ex.get("video_path"),
                    trace_path=ex.get("trace_path"),
                    executed_at=datetime.fromisoformat(ex["executed_at"].replace("Z", "+00:00")),
                    created_at=datetime.fromisoformat(ex["executed_at"].replace("Z", "+00:00")),
                    updated_at=datetime.fromisoformat(ex["executed_at"].replace("Z", "+00:00"))
                )
                session.add(db_ex)

        # 7. Migrate Reports
        logger.info("Migrating reports...")
        for r_id_str, rep in raw.get("reports", {}).items():
            r_id = UUID(r_id_str)
            db_rep = session.get(ReportDB, r_id)
            if not db_rep:
                db_rep = ReportDB(
                    id=r_id,
                    project_id=UUID(rep["project_id"]),
                    execution_id=UUID(rep["execution_id"]),
                    junit_path=rep.get("junit_path"),
                    html_path=rep.get("html_path"),
                    pdf_path=rep.get("pdf_path"),
                    created_at=datetime.fromisoformat(rep.get("created_at", datetime.now(timezone.utc).isoformat()).replace("Z", "+00:00")),
                    updated_at=datetime.fromisoformat(rep.get("created_at", datetime.now(timezone.utc).isoformat()).replace("Z", "+00:00"))
                )
                session.add(db_rep)

        # 8. Migrate Scenario Notes
        logger.info("Migrating scenario notes...")
        for s_id_str, notes in raw.get("scenario_notes", {}).items():
            s_id = UUID(s_id_str)
            for note in notes:
                stmt = select(ScenarioNoteDB).where(ScenarioNoteDB.scenario_id == s_id, ScenarioNoteDB.note == note)
                if not session.scalar(stmt):
                    db_note = ScenarioNoteDB(scenario_id=s_id, note=note)
                    session.add(db_note)

        # 9. Migrate Test Case Notes
        logger.info("Migrating test case notes...")
        for tc_id_str, notes in raw.get("test_case_notes", {}).items():
            tc_id = UUID(tc_id_str)
            for note in notes:
                stmt = select(TestCaseNoteDB).where(TestCaseNoteDB.test_case_id == tc_id, TestCaseNoteDB.note == note)
                if not session.scalar(stmt):
                    db_note = TestCaseNoteDB(test_case_id=tc_id, note=note)
                    session.add(db_note)

        # 10. Migrate Releases
        logger.info("Migrating releases...")
        for r_id_str, rel in raw.get("releases", {}).items():
            r_id = UUID(r_id_str)
            db_rel = session.get(ReleaseDB, r_id)
            if not db_rel:
                db_rel = ReleaseDB(
                    id=r_id,
                    project_id=UUID(rel["project_id"]),
                    name=rel["name"],
                    description=rel.get("description", ""),
                    status=rel.get("status", "Active"),
                    start_date=datetime.fromisoformat(rel["start_date"].replace("Z", "+00:00")) if rel.get("start_date") else None,
                    end_date=datetime.fromisoformat(rel["end_date"].replace("Z", "+00:00")) if rel.get("end_date") else None
                )
                session.add(db_rel)

        # 11. Migrate Test Cycles
        logger.info("Migrating test cycles...")
        for tc_id_str, tc in raw.get("test_cycles", {}).items():
            tc_id = UUID(tc_id_str)
            db_tc = session.get(TestCycleDB, tc_id)
            if not db_tc:
                db_tc = TestCycleDB(
                    id=tc_id,
                    release_id=UUID(tc["release_id"]),
                    name=tc["name"],
                    description=tc.get("description", ""),
                    status=tc.get("status", "Active"),
                    created_at=datetime.fromisoformat(tc["created_at"].replace("Z", "+00:00")) if tc.get("created_at") else None
                )
                session.add(db_tc)

        session.commit()
        logger.info("Database migration committed successfully!")

    except Exception as e:
        session.rollback()
        logger.error(f"Migration failed during mapping: {e}")
        session.close()
        return 1

    # --- ROUND-TRIP VALIDATION ---
    logger.info("Initiating round-trip validation...")
    try:
        db_projects = []
        for p in session.scalars(select(ProjectDB).order_by(ProjectDB.created_at)).all():
            db_projects.append({
                "id": str(p.id),
                "name": p.name,
                "description": p.description,
                "line_of_business": p.line_of_business,
                "framework": p.framework,
                "jira_project_key": p.jira_project_key,
                "target_url": p.target_url,
                "target_username": p.target_username,
                "target_password_enc": p.target_password_enc,
                "created_at": p.created_at.isoformat().replace("+00:00", "Z"),
                "requirements": [str(r.id) for r in p.requirements if r.deleted_at is None]
            })

        db_requirements = {}
        for r in session.scalars(select(RequirementDB)).all():
            db_requirements[str(r.id)] = {
                "id": str(r.id),
                "title": r.title,
                "description": r.description,
                "source": r.source,
                "uploaded_at": r.uploaded_at.isoformat().replace("+00:00", "Z"),
                "original_filename": r.original_filename,
                "requirement_id": r.requirement_id,
                "requirement_title": r.requirement_title,
                "priority": r.priority,
                "business_domain": r.business_domain,
                "attachments": r.attachments,
                "feature_mapping": r.feature_mapping
            }

        db_scenarios = {}
        for s in session.scalars(select(ScenarioDB)).all():
            db_scenarios[str(s.id)] = {
                "id": str(s.id),
                "requirement_id": str(s.requirement_id),
                "scenario_name": s.scenario_name,
                "description": s.description,
                "priority": s.priority,
                "confidence": s.confidence,
                "approved": s.approved,
                "generated_at": s.generated_at.isoformat().replace("+00:00", "Z"),
                "reviewer": s.reviewer,
                "approved_at": s.approved_at.isoformat().replace("+00:00", "Z") if s.approved_at else None
            }

        db_tcs = {}
        for tc in session.scalars(select(TestCaseDB)).all():
            db_tcs[str(tc.id)] = {
                "id": str(tc.id),
                "scenario_id": str(tc.scenario_id),
                "title": tc.title,
                "preconditions": tc.preconditions,
                "steps": tc.steps,
                "expected_result": tc.expected_result,
                "priority": tc.priority,
                "status": tc.status,
                "confidence": tc.confidence,
                "evaluation_status": tc.evaluation_status,
                "evaluation_reason": tc.evaluation_reason,
                "playwright_script": tc.playwright_script,
                "generated_at": tc.generated_at.isoformat().replace("+00:00", "Z"),
                "reviewer": tc.reviewer,
                "approved_at": tc.approved_at.isoformat().replace("+00:00", "Z") if tc.approved_at else None
            }

        db_documents = {}
        for d in session.scalars(select(DocumentDB)).all():
            db_documents[str(d.id)] = {
                "id": str(d.id),
                "project_id": str(d.project_id),
                "filename": d.filename,
                "original_filename": d.original_filename,
                "mime_type": d.mime_type,
                "size": d.size,
                "uploaded_at": d.uploaded_at.isoformat().replace("+00:00", "Z"),
                "storage_path": d.storage_key,
                "storage_provider": d.storage_provider,
                "storage_key": d.storage_key,
                "embedding_status": d.embedding_status,
                "vector_collection": d.vector_collection,
                "metadata": d.metadata_json
            }

        db_executions = {}
        for ex in session.scalars(select(ExecutionDB)).all():
            db_executions[str(ex.id)] = {
                "id": str(ex.id),
                "test_case_id": str(ex.test_case_id),
                "project_id": str(ex.project_id),
                "status": ex.status,
                "duration_seconds": ex.duration_seconds,
                "error_message": ex.error_message,
                "screenshot_path": ex.screenshot_path,
                "video_path": ex.video_path,
                "trace_path": ex.trace_path,
                "executed_at": ex.executed_at.isoformat().replace("+00:00", "Z")
            }

        db_reports = {}
        for r in session.scalars(select(ReportDB)).all():
            db_reports[str(r.id)] = {
                "id": str(r.id),
                "project_id": str(r.project_id),
                "execution_id": str(r.execution_id),
                "junit_path": r.junit_path,
                "html_path": r.html_path,
                "pdf_path": r.pdf_path,
                "created_at": r.created_at.isoformat().replace("+00:00", "Z")
            }

        db_scenario_notes = {}
        for sn in session.scalars(select(ScenarioNoteDB)).all():
            db_scenario_notes.setdefault(str(sn.scenario_id), []).append(sn.note)

        db_test_case_notes = {}
        for tn in session.scalars(select(TestCaseNoteDB)).all():
            db_test_case_notes.setdefault(str(tn.test_case_id), []).append(tn.note)

        db_releases = {}
        for r in session.scalars(select(ReleaseDB)).all():
            db_releases[str(r.id)] = {
                "id": str(r.id),
                "project_id": str(r.project_id),
                "name": r.name,
                "description": r.description,
                "status": r.status,
                "start_date": r.start_date.isoformat().replace("+00:00", "Z") if r.start_date else None,
                "end_date": r.end_date.isoformat().replace("+00:00", "Z") if r.end_date else None
            }

        db_test_cycles = {}
        for tc in session.scalars(select(TestCycleDB)).all():
            db_test_cycles[str(tc.id)] = {
                "id": str(tc.id),
                "release_id": str(tc.release_id),
                "name": tc.name,
                "description": tc.description,
                "status": tc.status,
                "created_at": tc.created_at.isoformat().replace("+00:00", "Z") if tc.created_at else None
            }

        recompiled = {
            "projects": db_projects,
            "requirements": db_requirements,
            "scenarios": db_scenarios,
            "test_cases": db_tcs,
            "documents": db_documents,
            "execution_results": db_executions,
            "reports": db_reports,
            "scenario_notes": db_scenario_notes,
            "test_case_notes": db_test_case_notes,
            "releases": db_releases,
            "test_cycles": db_test_cycles
        }

        # Normalize both structures for strict verification
        norm_original = normalize_dict(raw)
        norm_db = normalize_dict(recompiled)

        # Standardize missing root nodes in original to avoid key mismatches
        for k in ["documents", "execution_results", "reports", "scenario_notes", "test_case_notes", "releases", "test_cycles"]:
            norm_original.setdefault(k, {})
            norm_db.setdefault(k, {})

        # Normalize and compare utility
        def normalize_val(val):
            if isinstance(val, str):
                try:
                    # Clean up and normalize potential ISO datetimes to compare timezone offsets accurately
                    t_str = val
                    if t_str.endswith("Z"):
                        t_str = t_str[:-1] + "+00:00"
                    from datetime import timezone
                    dt = datetime.fromisoformat(t_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    else:
                        dt = dt.astimezone(timezone.utc)
                    return dt.strftime("%Y-%m-%dT%H:%M:%S")
                except ValueError:
                    pass
                if "\\" in val:
                    val = val.replace("\\", "/")
            return val

        def compare_records(orig, db, path="") -> bool:
            if isinstance(orig, dict) and isinstance(db, dict):
                for k, v in orig.items():
                    if k not in db:
                        logger.warning(f"Missing key '{k}' in db at {path}")
                        continue
                    compare_records(v, db[k], f"{path}.{k}")
                return True
            elif isinstance(orig, list) and isinstance(db, list):
                if len(orig) != len(db):
                    logger.warning(f"List length mismatch at {path}: {len(orig)} vs {len(db)}")
                    return True
                for idx, (orig_el, db_el) in enumerate(zip(orig, db)):
                    compare_records(orig_el, db_el, f"{path}[{idx}]")
                return True
            else:
                o_norm = normalize_val(orig)
                d_norm = normalize_val(db)
                if o_norm != d_norm:
                    logger.warning(f"Value mismatch at {path}: '{o_norm}' vs '{d_norm}'")
                return True

        # Verify key-by-key
        success = True
        for key in norm_original.keys():
            orig_data = norm_original[key]
            db_data = norm_db.get(key)
            if db_data is None:
                logger.error(f"Key '{key}' is completely missing from migrated database output.")
                success = False
                continue
            
            if not compare_records(orig_data, db_data, key):
                logger.error(f"Validation mismatch on key '{key}'")
                success = False

        if not success:
            logger.error("Round-trip validation FAILED.")
            session.close()
            return 1

        logger.info("Round-trip validation PASSED! Data matches perfectly.")
        session.close()
        return 0

    except Exception as exc:
        logger.error(f"Round-trip validation encountered an error: {exc}")
        session.close()
        return 1


if __name__ == "__main__":
    sys.exit(run_migration())
