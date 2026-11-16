"""
Project dashboard metrics.

Taken out of the `GET /projects/{id}/dashboard-metrics` route so the route
stays thin and the chat assistant's `get_dashboard_metrics` tool returns the
same numbers (spec 0005). `repo` is anything with the project repository's
read methods.
"""

from datetime import datetime
from uuid import UUID


def _status(value) -> str:
    return value.value if hasattr(value, "value") else value


def compute_dashboard_metrics(repo, project_id: UUID, scope: str = "project", scope_id: str | None = None) -> dict:
    """The fields of DashboardMetricsResponse for one project, as a dict."""
    reqs = repo.get_requirements(project_id)
    scenarios = repo.get_scenarios_for_project(project_id)
    test_cases = repo.get_test_cases_for_project(project_id)
    executions = repo.get_execution_results(project_id)

    # Filter based on scope
    if scope == "release" and scope_id:
        try:
            rel_id = UUID(scope_id)
            reqs = [r for r in reqs if r.release_id == rel_id]
            scenarios = [s for s in scenarios if any(r.id == s.requirement_id for r in reqs)]
            test_cases = [tc for tc in test_cases if any(s.id == tc.scenario_id for s in scenarios)]
            executions = [ex for ex in executions if any(tc.id == ex.test_case_id for tc in test_cases)]
        except Exception:
            pass
    elif scope == "cycle" and scope_id:
        try:
            cy_id = UUID(scope_id)
            cycle = repo.get_test_cycle(cy_id)
            if cycle:
                rel_id = cycle.release_id
                reqs = [r for r in reqs if r.release_id == rel_id]
                scenarios = [s for s in scenarios if any(r.id == s.requirement_id for r in reqs)]
                test_cases = [tc for tc in test_cases if any(s.id == tc.scenario_id for s in scenarios)]
                executions = [ex for ex in executions if ex.test_cycle_id == cy_id]
        except Exception:
            pass

    is_empty = len(reqs) == 0 and len(executions) == 0

    total_executions = len(executions)
    passed_executions = len([ex for ex in executions if _status(ex.status) == "passed"])
    pass_ratio = f"{int(round((passed_executions / total_executions) * 100))}%" if total_executions > 0 else "0%"

    # Coverage
    manual_count = api_count = ui_count = 0
    for tc in test_cases:
        exec_type = getattr(tc, "execution_type", None) or "Automated Testing"
        if exec_type == "Manual Testing":
            manual_count += 1
        elif "api" in tc.title.lower() or (tc.steps and any("api" in s.lower() for s in tc.steps)):
            api_count += 1
        else:
            ui_count += 1
    coverage_pct = f"{int(round(((ui_count + api_count) / len(test_cases)) * 100))}%" if test_cases else "0%"

    # Latest execution verdict per test case
    passed_count = failed_count = yet_to_execute_count = 0
    for tc in test_cases:
        tc_execs = [ex for ex in executions if str(ex.test_case_id) == str(tc.id)]
        if not tc_execs:
            yet_to_execute_count += 1
        elif _status(max(tc_execs, key=lambda x: x.executed_at).status) == "passed":
            passed_count += 1
        else:
            failed_count += 1

    # Scenario approval
    approved_count = len([s for s in scenarios if s.approved])
    rejected_count = len([s for s in scenarios if getattr(s, "rejected", False)])
    pending_count = len(scenarios) - approved_count - rejected_count

    # Trend (last 7 days with executions)
    daily: dict[str, dict[str, int]] = {}
    for ex in executions:
        if not ex.executed_at:
            continue
        day = daily.setdefault(ex.executed_at.strftime("%b %d"), {"passed": 0, "failed": 0})
        day["passed" if _status(ex.status) == "passed" else "failed"] += 1
    year = datetime.now().year
    recent_dates = sorted(daily, key=lambda d: datetime.strptime(f"{d} {year}", "%b %d %Y"))[-7:]

    # Domains
    domain_counts: dict[str, int] = {}
    for r in reqs:
        dom = r.business_domain or "general"
        domain_counts[dom] = domain_counts.get(dom, 0) + 1

    # Test case priority
    priority_high = priority_medium = priority_low = 0
    for tc in test_cases:
        prio = (_status(tc.priority) or "medium").lower()
        if prio == "high":
            priority_high += 1
        elif prio == "low":
            priority_low += 1
        else:
            priority_medium += 1

    # Defect status
    defect_open = defect_resolved = defect_retest = defect_closed = 0
    for ex in executions:
        status = _status(ex.status)
        if status == "failed":
            if getattr(ex, "retest_pending_candidate", False):
                defect_retest += 1
            else:
                defect_open += 1
        elif status == "passed":
            if getattr(ex, "retest_pending_candidate", False) or getattr(ex, "jira_bug_id", None):
                defect_resolved += 1
            else:
                defect_closed += 1

    # Five most recent executions
    recent = []
    for ex in sorted(executions, key=lambda x: x.executed_at, reverse=True)[:5]:
        tc = next((t for t in test_cases if str(t.id) == str(ex.test_case_id)), None)
        recent.append({
            "id": ex.id,
            "test_case_id": ex.test_case_id,
            "tcName": tc.title if tc else "Unknown Test Case",
            "tcCustomId": getattr(tc, "custom_id", None) or (f"TC-{str(tc.id)[:4].upper()}" if tc else "TC-XXX"),
            "status": _status(ex.status),
            "duration_seconds": ex.duration_seconds,
            "executed_at": ex.executed_at.strftime("%I:%M %p") if ex.executed_at else "N/A",
        })

    return {
        "totalReqs": len(reqs),
        "totalScenarios": len(scenarios),
        "totalTestCases": len(test_cases),
        "totalExecutions": total_executions,
        "passRatio": pass_ratio,
        "coveragePct": coverage_pct,
        "uiCount": ui_count,
        "apiCount": api_count,
        "manualCount": manual_count,
        "passedCount": passed_count,
        "failedCount": failed_count,
        "yetToExecuteCount": yet_to_execute_count,
        "approvedCount": approved_count,
        "rejectedCount": rejected_count,
        "pendingCount": pending_count,
        "trendLabels": recent_dates,
        "trendPassed": [daily[d]["passed"] for d in recent_dates],
        "trendFailed": [daily[d]["failed"] for d in recent_dates],
        "domainLabels": list(domain_counts.keys()),
        "domainCountsData": list(domain_counts.values()),
        "priorityHighCount": priority_high,
        "priorityMediumCount": priority_medium,
        "priorityLowCount": priority_low,
        "defectOpenCount": defect_open,
        "defectResolvedCount": defect_resolved,
        "defectRetestCount": defect_retest,
        "defectClosedCount": defect_closed,
        "recentExecutions": recent,
        "isEmpty": is_empty,
    }
