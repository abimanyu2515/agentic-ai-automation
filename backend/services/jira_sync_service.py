import os
import time
import logging
import asyncio
from datetime import datetime, timezone
from uuid import UUID

from backend.config.settings import get_settings
from backend.services.jira_service import JiraService
from backend.repository.project_repository import get_project_repository
from backend.models.scenario import Scenario
from backend.models.execution_result import ExecutionResult, ExecutionStatus

logger = logging.getLogger("backend.services.jira_sync_service")


def get_jira_client() -> JiraService:
    return JiraService()


def log_sync_event(scenario_id: str, issue_key: str, operation: str, status: str, duration: float = 0.0, reason: str = "", retry_val: str = ""):
    """Prints a structured JIRA_SYNC log for auditing and troubleshooting."""
    parts = [
        f"[JIRA_SYNC]",
        f"Scenario: {scenario_id}",
        f"Issue: {issue_key or 'N/A'}",
        f"Operation: {operation}",
        f"Status: {status}"
    ]
    if duration > 0:
        parts.append(f"Duration: {duration:.2f}s")
    if reason:
        parts.append(f"Reason: {reason}")
    if retry_val:
        parts.append(f"Retry: {retry_val}")
    logger.info(" ".join(parts))


async def run_with_background_tasks(background_tasks, sync_func, *args, **kwargs):
    """Schedules a JIRA sync function via FastAPI BackgroundTasks immediately."""
    if background_tasks:
        background_tasks.add_task(sync_func, *args, **kwargs)
    else:
        # Fallback to run async task directly if no background tasks provided
        asyncio.create_task(sync_func(*args, **kwargs))


def build_jira_description(scenario, latest_execution: ExecutionResult = None, open_bugs: list = None) -> str:
    """Builds a stable, structured JIRA description formatting for the Story issue."""
    details = (
        f"*Scenario Details*\n"
        f"Name: {scenario.scenario_name}\n"
        f"Requirement ID: {scenario.requirement_id}\n"
        f"Priority: {scenario.priority.value if hasattr(scenario.priority, 'value') else scenario.priority}\n\n"
    )
    
    desc = (
        f"*Description*\n"
        f"{scenario.description}\n\n"
    )
    
    confidence = (
        f"*AI Generated Confidence*\n"
        f"AI Generated Confidence: {scenario.confidence * 100:.0f}%\n\n"
    )
    
    status_str = "Pending"
    if latest_execution:
        status_str = latest_execution.status.value.upper() if hasattr(latest_execution.status, "value") else str(latest_execution.status).upper()
    elif scenario.last_jira_sync_status == "SYNC_PENDING":
        status_str = "Sync Pending"
    
    exec_status = (
        f"*Execution Status*\n"
        f"Status: {status_str}\n\n"
    )
    
    latest_details = "*Latest Execution*\n"
    if latest_execution:
        executed_time = latest_execution.executed_at.strftime('%Y-%m-%d %H:%M:%S UTC') if hasattr(latest_execution.executed_at, "strftime") else str(latest_execution.executed_at)
        latest_details += (
            f"Verdict: {latest_execution.status.value.upper() if hasattr(latest_execution.status, 'value') else str(latest_execution.status).upper()}\n"
            f"Timestamp: {executed_time}\n"
            f"Duration: {latest_execution.duration_seconds:.2f} seconds\n"
            f"Summary: {latest_execution.error_message or 'All steps passed.'}\n\n"
        )
    else:
        latest_details += "Verdict: N/A\nTimestamp: N/A\nDuration: N/A\n\n"
        
    bugs_text = "*Related Bugs*\n"
    if open_bugs:
        bugs_text += "\n".join(f"🐞 {b.get('key')} - {b.get('url')}" for b in open_bugs) + "\n\n"
    else:
        bugs_text += "No bugs reported.\n\n"
        
    return details + desc + confidence + exec_status + latest_details + bugs_text


async def sync_scenario_created(scenario_id: UUID):
    """Initializes Scenario JIRA Story creation."""
    start_time = time.time()
    repo = get_project_repository()
    scenario = repo.get_scenario(scenario_id)
    if not scenario:
        logger.error(f"Scenario {scenario_id} not found in DB.")
        return

    # Check settings for project-specific JIRA Project Key
    project_key = None
    # Find project context
    projects = repo.list_projects()
    project = next((p for p in projects if scenario.requirement_id in p.requirements), None)
    if project:
        project_key = getattr(project, "jira_project_key", None)
    if not project_key:
        settings = get_settings()
        project_key = settings.jira.project_key or "QA"

    jira_client = get_jira_client()
    issue_key = scenario.jira_issue_key
    issue_id = scenario.jira_issue_id
    issue_url = scenario.jira_issue_url

    try:
        # DB-first check: if missing from DB, query JIRA using JQL
        if not issue_key:
            jql = f"project = '{project_key}' AND labels = 'scenario_{scenario_id}'"
            existing = jira_client.search_issues(jql)
            if existing:
                issue = existing[0]
                issue_key = issue["key"]
                issue_id = issue.get("id")
                issue_url = f"{jira_client.config.base_url.rstrip('/')}/browse/{issue_key}"
                logger.info(f"Re-linked JIRA Story key '{issue_key}' from recovery search for scenario {scenario_id}.")

        if not issue_key:
            # Create JIRA Story
            summary = f"Scenario Story: {scenario.scenario_name}"
            desc = build_jira_description(scenario)
            
            # Check for AI Confidence custom field
            custom_fields = {}
            conf_field_id = jira_client.find_custom_field_by_name("AI Generated Confidence")
            if conf_field_id:
                custom_fields[conf_field_id] = float(scenario.confidence)

            # Build fields payload
            fields = {
                "project": {"key": project_key},
                "summary": summary,
                "description": desc,
                "issuetype": {"name": jira_client.config.default_issue_type or "Story"},
                "labels": ["platform_sync", f"scenario_{scenario_id}"]
            }
            # Merge custom fields
            fields.update(custom_fields)

            if jira_client.mock_mode:
                mock_key = f"{project_key}-{int(datetime.now().timestamp() * 1000) % 10000}"
                issue = {
                    "key": mock_key,
                    "url": f"{jira_client.config.base_url.rstrip('/')}/browse/{mock_key}",
                    "id": mock_key
                }
            else:
                # We reuse the create_issue method but let's pass project/issuetype/labels inside
                issue = jira_client.create_issue(
                    summary=summary,
                    description=desc,
                    issue_type=jira_client.config.default_issue_type or "Story",
                    project_key=project_key,
                    labels=["platform_sync", f"scenario_{scenario_id}"]
                )
                if issue and conf_field_id:
                    # Update custom field separately if create_issue doesn't map arbitrary fields
                    jira_client.update_issue(issue["key"], {conf_field_id: float(scenario.confidence)})

            if not issue:
                raise Exception("Jira client returned empty issue response.")

            issue_key = issue["key"]
            issue_id = issue.get("id") or issue_key
            issue_url = issue["url"]

        # Update DB fields
        scenario.jira_issue_key = issue_key
        scenario.jira_issue_id = issue_id
        scenario.jira_issue_url = issue_url
        now_time = datetime.now(timezone.utc)
        scenario.last_jira_sync_at = now_time
        scenario.jira_last_synced_at = now_time
        scenario.last_jira_sync_status = "SUCCESS"
        scenario.jira_sync_status = "SUCCESS"
        scenario.last_jira_sync_error = None
        scenario.jira_sync_retry_count = 0
        repo.update_scenario(scenario)

        log_sync_event(str(scenario_id), issue_key, "CREATE_STORY", "SUCCESS", duration=time.time() - start_time)

    except Exception as e:
        log_sync_event(str(scenario_id), issue_key, "CREATE_STORY", "FAILED", reason=str(e))
        now_time = datetime.now(timezone.utc)
        scenario.last_jira_sync_at = now_time
        scenario.jira_last_synced_at = now_time
        scenario.last_jira_sync_status = "SYNC_PENDING"
        scenario.jira_sync_status = "SYNC_PENDING"
        scenario.last_jira_sync_error = str(e)
        repo.update_scenario(scenario)
        raise e


async def sync_scenario_updated(scenario_id: UUID):
    """Updates the JIRA issue details for the Scenario when it's edited."""
    start_time = time.time()
    repo = get_project_repository()
    scenario = repo.get_scenario(scenario_id)
    if not scenario:
        return

    issue_key = scenario.jira_issue_key
    if not issue_key:
        # Fallback to created flow if missing
        await sync_scenario_created(scenario_id)
        return

    jira_client = get_jira_client()
    try:
        # Get latest execution results and open bugs from DB to rebuild description
        test_cases = repo.get_test_cases_for_project(scenario.requirement_id)  # fallback filtering in memory
        scenario_tcs = [tc for tc in test_cases if tc.scenario_id == scenario_id]
        
        latest_execution = None
        open_bugs = []
        if scenario_tcs:
            tc_ids = [tc.id for tc in scenario_tcs]
            executions = []
            for tc_id in tc_ids:
                try:
                    # Retrieve executions for each test case
                    tc_execs = repo.get_executions_for_test_case(tc_id) if hasattr(repo, "get_executions_for_test_case") else []
                    executions.extend(tc_execs)
                except Exception:
                    pass
            if executions:
                executions.sort(key=lambda ex: ex.executed_at or datetime.min)
                latest_execution = executions[-1]
                
            # Retrieve bug keys linked
            for tc in scenario_tcs:
                if tc.jira_issue_key and tc.jira_sync_status in ["created", "linked"]:
                    open_bugs.append({"key": tc.jira_issue_key, "url": tc.jira_issue_url})

        desc = build_jira_description(scenario, latest_execution=latest_execution, open_bugs=open_bugs)
        summary = f"Scenario Story: {scenario.scenario_name}"

        fields = {
            "summary": summary,
            "description": desc
        }
        
        # Check custom field
        conf_field_id = jira_client.find_custom_field_by_name("AI Generated Confidence")
        if conf_field_id:
            fields[conf_field_id] = float(scenario.confidence)

        success = jira_client.update_issue(issue_key, fields)
        if not success:
            raise Exception("Jira issue update PUT request failed.")

        now_time = datetime.now(timezone.utc)
        scenario.last_jira_sync_at = now_time
        scenario.jira_last_synced_at = now_time
        scenario.last_jira_sync_status = "SUCCESS"
        scenario.jira_sync_status = "SUCCESS"
        scenario.last_jira_sync_error = None
        scenario.jira_sync_retry_count = 0
        repo.update_scenario(scenario)

        log_sync_event(str(scenario_id), issue_key, "UPDATE_STORY", "SUCCESS", duration=time.time() - start_time)

    except Exception as e:
        log_sync_event(str(scenario_id), issue_key, "UPDATE_STORY", "FAILED", reason=str(e))
        now_time = datetime.now(timezone.utc)
        scenario.last_jira_sync_at = now_time
        scenario.jira_last_synced_at = now_time
        scenario.last_jira_sync_status = "SYNC_PENDING"
        scenario.jira_sync_status = "SYNC_PENDING"
        scenario.last_jira_sync_error = str(e)
        repo.update_scenario(scenario)
        raise e


async def sync_execution_started(scenario_id: UUID):
    """Transitions Scenario JIRA Issue to 'In Progress' when execution starts."""
    start_time = time.time()
    repo = get_project_repository()
    scenario = repo.get_scenario(scenario_id)
    if not scenario:
        return

    issue_key = scenario.jira_issue_key
    if not issue_key:
        return

    jira_client = get_jira_client()
    try:
        # Rebuild description with Execution Status = In Progress
        temp_ex = ExecutionResult(test_case_id=UUID(int=0), status="in_progress")
        desc = build_jira_description(scenario, latest_execution=temp_ex)
        jira_client.update_issue(issue_key, {"description": desc})

        # Query and execute JIRA transition to "In Progress"
        success = jira_client.transition_issue(issue_key, "In Progress")
        if not success:
            # Log transition fallback
            logger.warning(f"Could not transition JIRA issue '{issue_key}' to 'In Progress'. Adding comment trace.")
            jira_client.add_comment(issue_key, "Test execution started. Current status: In Progress.")

        log_sync_event(str(scenario_id), issue_key, "TRANSITION_IN_PROGRESS", "SUCCESS", duration=time.time() - start_time)

    except Exception as e:
        log_sync_event(str(scenario_id), issue_key, "TRANSITION_IN_PROGRESS", "FAILED", reason=str(e))


async def sync_execution_finished(scenario_id: UUID, execution_result_id: UUID):
    """Synchronizes JIRA after execution finishes, uploading reports and logging failure defects."""
    start_time = time.time()
    repo = get_project_repository()
    scenario = repo.get_scenario(scenario_id)
    if not scenario:
        return

    issue_key = scenario.jira_issue_key
    if not issue_key:
        logger.error(f"Cannot sync finished execution: Scenario {scenario_id} has no linked JIRA issue key.")
        return

    # Find project context
    projects = repo.list_projects()
    project = next((p for p in projects if scenario.requirement_id in p.requirements), None)
    project_id = project.id if project else None

    # Retrieve execution result
    execution_result = None
    if project_id:
        execution_result = repo.get_execution_result(project_id, execution_result_id)
    if not execution_result:
        logger.error(f"ExecutionResult {execution_result_id} not found in DB.")
        return

    jira_client = get_jira_client()
    try:
        # 1. Update Scenario JIRA Issue Description
        desc = build_jira_description(scenario, latest_execution=execution_result)
        fields = {"description": desc}
        jira_client.update_issue(issue_key, fields)

        # 2. Transition Issue Status dynamically
        if execution_result.status == ExecutionStatus.PASSED:
            # Try transitioning to "Ready for Testing" or "Done" or "Resolved"
            success = False
            for target_transition in ["Ready for Testing", "Done", "Resolved"]:
                if jira_client.transition_issue(issue_key, target_transition):
                    success = True
                    break
            if not success:
                logger.warning(f"Could not transition '{issue_key}' to Done/Ready for Testing. Adding comment trace.")
                jira_client.add_comment(issue_key, f"Execution completed. Verdict: {execution_result.status.value.upper()}. No matching workflow transition found.")
        else:
            # Failed/Error: Transition to In Progress or Reopen
            success = False
            for target_transition in ["In Progress", "Reopened", "To Do"]:
                if jira_client.transition_issue(issue_key, target_transition):
                    success = True
                    break
            if not success:
                jira_client.add_comment(issue_key, f"Execution completed. Verdict: {execution_result.status.value.upper()}.")

        # 3. Post Execution Audit Comment
        test_cases = repo.get_test_cases_for_project(scenario.requirement_id)
        scenario_tcs = [tc for tc in test_cases if tc.scenario_id == scenario_id]
        execution_count = 1
        if scenario_tcs:
            tc_ids = [tc.id for tc in scenario_tcs]
            executions = []
            for tc_id in tc_ids:
                try:
                    tc_execs = repo.get_executions_for_test_case(tc_id) if hasattr(repo, "get_executions_for_test_case") else []
                    executions.extend(tc_execs)
                except Exception:
                    pass
            execution_count = max(1, len(executions))

        audit_comment = (
            f"Execution #{execution_count}\n\n"
            f"Status: {execution_result.status.value.upper()}\n"
            f"AI Generated Confidence: {scenario.confidence * 100:.0f}%\n"
            f"Execution Time: {execution_result.duration_seconds:.2f} sec\n\n"
            f"Execution Report attached."
        )
        jira_client.add_comment(issue_key, audit_comment)

        # 4. Resilient PDF Report Upload
        report = None
        try:
            report = repo.get_report_by_execution(execution_result_id)
        except Exception:
            pass
        if report and report.get("pdf_path") and os.path.exists(report.get("pdf_path")):
            # Check if already uploaded
            if not report.get("jira_attachment_id"):
                try:
                    pdf_file_path = report.get("pdf_path")
                    with open(pdf_file_path, "rb") as pdf_file:
                        pdf_data = pdf_file.read()
                    filename = f"Execution_Report_{execution_result_id}.pdf"
                    
                    upload_ok = jira_client.upload_attachment(
                        issue_key=issue_key,
                        filename=filename,
                        content=pdf_data,
                        mime_type="application/pdf"
                    )
                    if upload_ok:
                        report["jira_attachment_id"] = filename
                        report["jira_last_uploaded_at"] = datetime.now(timezone.utc).isoformat()
                        repo.save_report(project_id, execution_result_id, report)
                        log_sync_event(str(scenario_id), issue_key, "UPLOAD_PDF", "SUCCESS")
                    else:
                        raise Exception("Jira attachment upload returned status code != 200.")
                except Exception as upload_err:
                    logger.error(f"Jira PDF report upload failed: {upload_err}")
                    jira_client.add_comment(issue_key, f"⚠️ Note: Failed to attach PDF report for Execution #{execution_count}. Error: {upload_err}")
                    log_sync_event(str(scenario_id), issue_key, "UPLOAD_PDF", "FAILED", reason=str(upload_err))

        # 5. Handle Defect/Bug Ticket if failed
        if execution_result.status != ExecutionStatus.PASSED:
            project_key = issue_key.split("-")[0]
            jql_bug = f"project = '{project_key}' AND issuetype = Bug AND labels = 'scenario_{scenario_id}' AND status != 'Closed' AND status != 'Resolved'"
            existing_bugs = jira_client.search_issues(jql_bug)
            
            if existing_bugs:
                bug_issue = existing_bugs[0]
                bug_key = bug_issue["key"]
                bug_url = f"{jira_client.config.base_url.rstrip('/')}/browse/{bug_key}"
                
                bug_comment = (
                    f"Test execution failed again at {execution_result.executed_at.isoformat() if hasattr(execution_result.executed_at, 'isoformat') else str(execution_result.executed_at)}.\n"
                    f"Failure Reason: {execution_result.error_message or 'No specific error log provided.'}"
                )
                jira_client.add_comment(bug_key, bug_comment)
                jira_client.transition_issue(bug_key, "In Progress")

                execution_result.jira_bug_id = bug_key
                execution_result.jira_bug_url = bug_url
                
                for tc in scenario_tcs:
                    tc.jira_issue_key = bug_key
                    tc.jira_issue_url = bug_url
                    tc.jira_sync_status = "linked"
                    tc.jira_last_synced_at = datetime.now(timezone.utc)
                    repo.update_test_case(tc)

                log_sync_event(str(scenario_id), bug_key, "LINK_EXISTING_BUG", "SUCCESS")

            else:
                bug_summary = f"Bug: Test Failure - {scenario.scenario_name}"
                bug_desc = (
                    f"*Failure Summary*\n"
                    f"{execution_result.root_cause_summary or 'Execution Failed'}\n\n"
                    f"*Failure Reason*\n"
                    f"{execution_result.error_message or 'Unknown error'}\n\n"
                    f"*Scenario Link*\n"
                    f"{scenario.jira_issue_url or 'N/A'}\n\n"
                    f"*Execution Timestamp*\n"
                    f"{execution_result.executed_at.isoformat() if hasattr(execution_result.executed_at, 'isoformat') else str(execution_result.executed_at)}"
                )
                
                bug_labels = ["platform_sync", f"scenario_{scenario_id}", "bug"]
                new_bug = jira_client.create_issue(
                    summary=bug_summary,
                    description=bug_desc,
                    issue_type="Bug",
                    project_key=project_key,
                    labels=bug_labels
                )
                if new_bug:
                    bug_key = new_bug["key"]
                    bug_url = new_bug["url"]
                    
                    execution_result.jira_bug_id = bug_key
                    execution_result.jira_bug_url = bug_url

                    for tc in scenario_tcs:
                        tc.jira_issue_key = bug_key
                        tc.jira_issue_url = bug_url
                        tc.jira_sync_status = "created"
                        tc.jira_last_synced_at = datetime.now(timezone.utc)
                        repo.update_test_case(tc)

                    jira_client.link_issues(bug_key, issue_key, "Relates")
                    log_sync_event(str(scenario_id), bug_key, "CREATE_NEW_BUG", "SUCCESS")

        if project_id:
            repo.save_execution_result(project_id, execution_result)

        now_time = datetime.now(timezone.utc)
        scenario.last_jira_sync_at = now_time
        scenario.jira_last_synced_at = now_time
        scenario.last_jira_sync_status = "SUCCESS"
        scenario.jira_sync_status = "SUCCESS"
        scenario.last_jira_sync_error = None
        scenario.jira_sync_retry_count = 0
        repo.update_scenario(scenario)

        log_sync_event(str(scenario_id), issue_key, "FINISH_EXECUTION_SYNC", "SUCCESS", duration=time.time() - start_time)

    except Exception as e:
        log_sync_event(str(scenario_id), issue_key, "FINISH_EXECUTION_SYNC", "FAILED", reason=str(e))
        now_time = datetime.now(timezone.utc)
        scenario.last_jira_sync_at = now_time
        scenario.jira_last_synced_at = now_time
        scenario.last_jira_sync_status = "SYNC_PENDING"
        scenario.jira_sync_status = "SYNC_PENDING"
        scenario.last_jira_sync_error = str(e)
        repo.update_scenario(scenario)
        raise e


async def jira_retry_worker():
    """Background retry worker loop that sweeps DB for SYNC_PENDING tasks."""
    logger.info("JIRA Sync Retry Worker loop starting up...")
    await asyncio.sleep(10)
    while True:
        try:
            await asyncio.sleep(60)
            repo = get_project_repository()
            
            projects = []
            try:
                projects = repo.list_projects()
            except Exception as e:
                logger.error(f"Retry worker failed to list projects: {e}")
                continue

            for p in projects:
                scenarios = []
                try:
                    scenarios = repo.get_scenarios_for_project(p.id)
                except Exception as sc_err:
                    logger.error(f"Retry worker failed to get scenarios for project {p.id}: {sc_err}")
                    continue

                for sc in scenarios:
                    if sc.last_jira_sync_status == "SYNC_PENDING" and sc.jira_sync_retry_count < 3:
                        backoffs = [30, 120, 600]
                        elapsed_s = (datetime.now(timezone.utc) - sc.last_jira_sync_at).total_seconds()
                        delay_s = backoffs[min(sc.jira_sync_retry_count, 2)]
                        
                        if elapsed_s >= delay_s:
                            retry_str = f"{sc.jira_sync_retry_count + 1}/3"
                            log_sync_event(str(sc.id), sc.jira_issue_key, "RETRY_SYNC", "ATTEMPT", retry_val=retry_str)
                            try:
                                await sync_scenario_updated(sc.id)
                            except Exception as err:
                                sc.jira_sync_retry_count += 1
                                sc.last_jira_sync_error = str(err)
                                if sc.jira_sync_retry_count >= 3:
                                    sc.last_jira_sync_status = "SYNC_FAILED"
                                    sc.jira_sync_status = "SYNC_FAILED"
                                repo.update_scenario(sc)
                                log_sync_event(str(sc.id), sc.jira_issue_key, "RETRY_SYNC", "FAILED", reason=str(err), retry_val=retry_str)
        except Exception as loop_err:
            logger.error(f"Critical error in JIRA retry worker loop: {loop_err}")
