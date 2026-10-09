from pathlib import Path
from uuid import UUID, uuid4
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import os
import logging

from backend.models.execution_result import ExecutionResult
from backend.repository.project_repository import ProjectRepository
from backend.config.report_config import ReportConfig

logger = logging.getLogger(__name__)

def map_timeline_event_to_step(event_data: dict, index: int, project_id: UUID, execution_id: UUID, test_case=None) -> dict:
    """
    Parses a single raw timeline event from the Playwright runner and wraps it
    in a structured step dictionary containing Action, Expected, Actual, and Status fields.
    """
    event_name = event_data.get("event", "")
    time_val = event_data.get("time", 0.0)
    evt_type = event_data.get("type", "info")
    details = event_data.get("details", "")

    # Locate screenshot path
    screenshot_filename = details if details.endswith(".png") else None
    if screenshot_filename and "screenshot:" in screenshot_filename.lower():
        screenshot_filename = screenshot_filename.split(":", 1)[1].strip()
    if not screenshot_filename and "screenshot" in event_name.lower():
        screenshot_filename = f"screenshot-{index}.png"

    # Dynamic fallback for error events: scan screenshots folder
    if not screenshot_filename and (evt_type == "error" or "error" in event_name.lower()):
        screenshots_dir = Path("backend/playwrightt/public/artifacts") / str(execution_id) / "screenshots"
        if screenshots_dir.exists():
            screenshot_files = list(screenshots_dir.glob("*.png"))
            if screenshot_files:
                error_files = [f for f in screenshot_files if "error" in f.name.lower() or "failed" in f.name.lower()]
                if error_files:
                    screenshot_filename = error_files[0].name
                else:
                    # Fallback to last screenshot chronologically
                    screenshot_files.sort(key=lambda x: x.stat().st_mtime if x.exists() else 0)
                    screenshot_filename = screenshot_files[-1].name

    screenshot_url = None
    if screenshot_filename:
        workspace_dir = Path("backend/playwrightt/public/artifacts") / str(execution_id) / "screenshots" / screenshot_filename
        if workspace_dir.exists():
            screenshot_url = str(workspace_dir.absolute())

    # Determine step status from event type and overall execution status
    # Only mark as failed if this specific event was an error
    if evt_type == "error":
        status = "❌ Failed"
    elif "error" in event_name.lower() and "no error" not in event_name.lower():
        status = "❌ Failed"  
    else:
        status = "✅ Passed"
    
    time_str = f"{time_val:.2f}s"
    
    # Strip prefix if it exists, e.g. "Click: Clicking the login button" -> "Clicking the login button"
    event_prefix = ""
    event_detail = event_name
    if ":" in event_name:
        parts = event_name.split(":", 1)
        event_prefix = parts[0].strip()
        event_detail = parts[1].strip()

    title = event_name
    action_text = f"Action not yet performed"
    expected = f"Expected behavior not yet assessed"
    actual = "No Observation Yet"

    if evt_type == "error" or "error" in event_name.lower():
        title = f"Error: {event_name}"
        action_text = "Executing the current test step"
        expected = "The website should respond as expected"
        actual = f"{status}: {details or 'Timeout or assertion error occurred'}"
    else:
        # Build meaningful descriptions from the event prefix and detail
        pfx_lower = event_prefix.lower()
        if pfx_lower == "navigate" or event_detail.startswith("http"):
            site_name = event_detail.replace("https://", "").replace("http://", "").split("/")[0]
            title = f"Navigate to {site_name}"
            action_text = f"The browser navigates to '{event_detail}'"
            expected = f"Application home page should load successfully"
            actual = f"Page loaded: {site_name}"


        elif pfx_lower == "click" or pfx_lower == "clicking":
            elem_desc = event_detail.strip()
            title = f"Click {elem_desc}"
            action_text = f"The user clicks on the {elem_desc}"
            expected = f"The click should be accepted and trigger the next page action"
            actual = f"Clicked {elem_desc} successfully"

        elif pfx_lower in ("type text", "type", "fill"):
            elem_desc = event_detail.strip().rstrip(".")
            title = f"Type into {elem_desc}"
            action_text = f"The user types text into the {elem_desc} field"
            expected = f"Text should be entered correctly in the field"
            actual = f"Text entered in {elem_desc}"

        elif pfx_lower in ("select option", "select"):
            elem_desc = event_detail.strip().rstrip(".")

            title = f"Select from {elem_desc}"
            action_text = f"The user selects an option from the {elem_desc} dropdown"
            expected = f"A valid option should be selected from the dropdown"
            actual = f"Option selected from {elem_desc}"

        elif "assert" in event_name.lower() or "expect" in event_name.lower():
            title = f"Verify: {event_detail}"
            action_text = f"Verify the condition: {event_detail}"
            expected = f"The assertion should pass"
            actual = f"Verification passed"

        elif pfx_lower in ("running", "step"):
            title = event_detail or event_name
            action_text = f"Proceeding: {title}"
            expected = f"Step executes without error"
            actual = f"{title} completed"

        elif pfx_lower == "session restored" or "session" in pfx_lower:
            title = "Initialize Session"
            action_text = "Session is restored from storage"
            expected = "Application restores the previous session"
            actual = "Session restored"

        elif "browser opened" in event_name.lower() or "browser" in pfx_lower:
            title = "Initialize Browser"
            action_text = "Browser is opened with a clean context"
            expected = "Browser context should be ready for testing"
            actual = "Browser ready"

        elif pfx_lower == "screenshot":
            title = "Screenshot Captured"
            action_text = "A screenshot is taken to record the UI state"
            expected = "Screenshot file should be saved"
            actual = "Screenshot image saved on disk"


    # If this event has a screenshot, try to tie it to a test case step
    if screenshot_filename:
        import re
        fn_lower = screenshot_filename.lower()
        step_num = None
        
        # Match standard step format: step-01, step-1
        match = re.match(r"^step-(\d+)", fn_lower)
        if not match:
            match = re.match(r"^(\d+)", fn_lower)
        if match:
            step_num = int(match.group(1))
            
        if test_case and step_num is not None and 1 <= step_num <= len(test_case.steps):
            step_desc = test_case.steps[step_num - 1]
            title = f"Step {step_num}: {step_desc}"
            action_text = f"Execute step {step_num}: {step_desc}"
            expected = f"Executing: {step_desc}."
            if step_num == len(test_case.steps):
                actual = f"Verified expected result: {test_case.expected_result}"
            else:
                actual = f"Step {step_num} completed successfully"

    return {
        "step_name": title,
        "title": title, # support old key
        "time": time_str,
        "action": action_text,
        "expected": expected,
        "observation": expected, # support old key
        "actual": actual,
        "result": actual, # support old key
        "status": status,
        "screenshot_url": screenshot_url,
        "screenshot_name": screenshot_filename
    }

class ReportService:
    def __init__(self, repo: ProjectRepository = None):
        if repo is None:
            from backend.services.project_service import ProjectService
            self.repo = ProjectService().repo
        else:
            self.repo = repo

    def enrich_steps_with_llm(self, mapped_steps: list[dict], test_case, execution_status: str) -> list[dict]:
        """
        AI enhancement of execution steps - KEEPS ALL STEPS, only improves descriptions.
        
        Rules:
        1. NEVER remove or filter steps - keep every single step
        2. NEVER change the status - preserve original pass/fail from execution
        3. ENHANCE descriptions to be clear, concise, and business-focused
        4. Use test case context to make step names meaningful
        5. Preserve all metadata (time, screenshots, status)
        """
        if not mapped_steps:
            return mapped_steps

        try:
            import json
            from backend.services.llm import LLMService
            from pydantic import BaseModel, Field

            class EnhancedStep(BaseModel):
                """AI-enhanced step description - preserves structure, improves clarity"""
                step_index: int = Field(description="The 0-based index matching the input step")
                step_name: str = Field(description="Clear, concise step title using business terminology")
                action: str = Field(description="What action was performed - clear and specific")
                expected: str = Field(description="Expected outcome in business terms")
                actual: str = Field(description="Actual result - specific about what happened")

            class EnhancedStepsList(BaseModel):
                steps: list[EnhancedStep]

            llm = LLMService()

            tc_title = test_case.title if test_case else "Playwright Automation Test"
            tc_expected = getattr(test_case, "expected_result", "") if test_case else ""
            tc_steps = getattr(test_case, "steps", []) if test_case else []

            # Build input for AI - include ALL steps with their current descriptions
            steps_input = []
            for i, step in enumerate(mapped_steps):
                steps_input.append({
                    "index": i,
                    "step_name": step.get("step_name", ""),
                    "action": step.get("action", ""),
                    "expected": step.get("expected", ""),
                    "actual": step.get("actual", ""),
                    "status": step.get("status", ""),
                    "time": step.get("time", "")
                })

            user_prompt = f"""
You are enhancing an automated test execution report. Your goal: make step descriptions CLEAR, CONCISE, and BUSINESS-FOCUSED while KEEPING ALL STEPS.

Test Case: {tc_title}
Expected Result: {tc_expected}
Test Case Steps:
{chr(10).join([f"{i+1}. {s}" for i, s in enumerate(tc_steps)])}

Overall Execution Status: {execution_status}

Current execution steps (ALL {len(steps_input)} steps must be returned with improved descriptions):
{json.dumps(steps_input, indent=2)}

CRITICAL RULES:
1. Return EXACTLY {len(steps_input)} steps - one for each input step at the same index
2. NEVER skip or remove steps
3. IMPROVE descriptions to be clear and business-focused:
   - Use terminology from the test case
   - Make actions specific (what button, what field, what value)
   - Make expected/actual meaningful (not generic "step executes")
   - Focus on business outcomes, not technical implementation
4. If a step references a test case step number, use that step's description
5. Keep step names concise but informative

Example transformations:
- "Step executes" → "User selects location from dropdown"
- "Completed" → "Sydney location selected successfully"
- "Action not yet performed" → "Navigate to hotel search page"
"""

            res = llm.structured_generate(
                user=user_prompt,
                response_model=EnhancedStepsList,
                system="You are an expert QA report writer. You enhance step descriptions to be clear, concise, and business-focused. You NEVER remove steps - you only improve their descriptions. Every input step gets an enhanced output step."
            )

            # Verify we got all steps back
            if len(res.steps) != len(mapped_steps):
                print(f"[AI Enhancement Warning]: Expected {len(mapped_steps)} steps, got {len(res.steps)}. Using original steps.")
                return mapped_steps

            # Build enhanced steps, preserving ALL original metadata
            enhanced_steps = []
            for i, (original, enhanced) in enumerate(zip(mapped_steps, res.steps)):
                if enhanced.step_index != i:
                    print(f"[AI Enhancement Warning]: Index mismatch at {i}. Using original steps.")
                    return mapped_steps
                
                enhanced_steps.append({
                    "step_name": enhanced.step_name,
                    "title": enhanced.step_name,
                    "time": original.get("time", "0.00s"),
                    "action": enhanced.action,
                    "expected": enhanced.expected,
                    "observation": enhanced.expected,
                    "actual": enhanced.actual,
                    "result": enhanced.actual,
                    "status": original.get("status", "✅ Passed"),  # PRESERVE original status
                    "screenshot_url": original.get("screenshot_url"),
                    "screenshot_name": original.get("screenshot_name")
                })

            print(f"[AI Report Enhancement]: Enhanced {len(enhanced_steps)} step descriptions")
            return enhanced_steps

        except Exception as e:
            print(f"[LLM Report Enhancement Error]: {e}")
            # Fallback: return original steps unchanged
            import traceback
            traceback.print_exc()
            return mapped_steps

    def compile_reports(self, project_id: UUID, execution_result: ExecutionResult) -> dict:
        """
        Compiles HTML, PDF, and JUnit reports based on the Playwright execution results
        and persists them to the repository.
        """
        print(f"[Report Engine] Legacy engine selected via configuration. Invoking compile_reports().")
        logger.info(f"Using legacy report engine for execution {execution_result.id}")
        
        reports_dir = Path("data/reports")
        reports_dir.mkdir(parents=True, exist_ok=True)

        execution_id_str = str(execution_result.id)
        
        # Extract rich payload timeline and screenshots
        payload = getattr(execution_result, "_raw_payload", {}) or {}
        raw_timeline = payload.get("timeline", []) or getattr(execution_result, "timeline", []) or []
        
        # Self-healing: if raw_timeline is empty, try to retrieve it dynamically
        if not raw_timeline:
            # 1. Try to fetch from local Next.js status API
            try:
                import urllib.request
                import json
                url = f"http://localhost:3000/api/status?id={execution_id_str}"
                with urllib.request.urlopen(url, timeout=1) as response:
                    data = json.loads(response.read().decode('utf-8'))
                    raw_timeline = data.get("timeline", [])
            except Exception:
                pass
                
        if not raw_timeline:
            # 2. Try to parse from Next.js report.html file on disk if it exists
            next_report_path = Path("backend/playwrightt/public/artifacts") / execution_id_str / "report.html"
            if next_report_path.exists():
                try:
                    import re
                    content = next_report_path.read_text(encoding="utf-8")
                    matches = re.findall(r'<li class="([^"]+)">\s*<span[^>]*>([^<]+)</span>\s*<strong>([^<]+)</strong>', content)
                    for m in matches:
                        evt_type, time_str, event_name = m
                        t_val = 0.0
                        if "(" in time_str:
                            t_val_str = time_str.split("(")[1].split("s")[0]
                            try:
                                t_val = float(t_val_str)
                            except ValueError:
                                pass
                        raw_timeline.append({
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "time": t_val,
                            "event": event_name,
                            "type": evt_type
                        })
                except Exception as parse_err:
                    print(f"[Timeline recovery failed from report.html]: {parse_err}")

        test_case = None
        try:
            test_case = self.repo.get_test_case(execution_result.test_case_id)
        except Exception as tc_err:
            print(f"[ReportService] Failed to load test case {execution_result.test_case_id} for mapping: {tc_err}")

        # Next.js artifacts screenshot directory
        next_screenshots_dir = Path("backend/playwrightt/public/artifacts") / execution_id_str / "screenshots"
        next_screenshots_dir.mkdir(parents=True, exist_ok=True)

        failure_screenshot_name = None
        if execution_result.screenshot_path:
            src_screenshot = Path(execution_result.screenshot_path)
            if src_screenshot.exists():
                failure_screenshot_name = "failure-screenshot.png"
                dst_screenshot = next_screenshots_dir / failure_screenshot_name
                try:
                    import shutil
                    shutil.copy2(src_screenshot, dst_screenshot)
                except Exception as copy_err:
                    print(f"[ReportService] Failed to copy failure screenshot: {copy_err}")

        # Copy all step screenshots from the testcase execution run directory
        import shutil
        run_dir = Path("backend/playwrightt/artifacts") / str(execution_result.test_case_id)
        src_screenshots_dir = run_dir / "screenshots"
        if src_screenshots_dir.exists():
            for png in src_screenshots_dir.glob("*.png"):
                try:
                    shutil.copy2(png, next_screenshots_dir / png.name)
                except Exception as copy_err:
                    print(f"[ReportService] Failed to copy step screenshot {png.name}: {copy_err}")

        mapped_steps = []
        for idx, evt in enumerate(raw_timeline):
            step_mapped = map_timeline_event_to_step(evt, idx, project_id, execution_result.id, test_case=test_case)
            mapped_steps.append(step_mapped)

        # Merge separate screenshot / Visual State Capture events into preceding action steps
        merged_steps = []
        for step in mapped_steps:
            title_lower = step["step_name"].lower()
            skip_phrases = ("screenshot", "visual state", "initialize session", "initialize browser")
            is_meta = any(p in title_lower for p in skip_phrases)
            if is_meta:
                if step["screenshot_name"] and merged_steps:
                    merged_steps[-1]["screenshot_name"] = step["screenshot_name"]
                    merged_steps[-1]["screenshot_url"] = step["screenshot_url"]
                continue
            merged_steps.append(step)
        mapped_steps = merged_steps

        # Add failure screenshot to the last step if execution failed and it doesn't have one
        if execution_result.status.value in ("failed", "error") and failure_screenshot_name and mapped_steps:
            if not mapped_steps[-1]["screenshot_name"]:
                mapped_steps[-1]["screenshot_name"] = failure_screenshot_name
                mapped_steps[-1]["screenshot_url"] = str((next_screenshots_dir / failure_screenshot_name).absolute())

        # Apply AI-driven enhancement to make descriptions clear and business-focused
        # KEEPS ALL STEPS - only improves descriptions
        mapped_steps = self.enrich_steps_with_llm(mapped_steps, test_case, execution_result.status.value)

        # Determine target web URL
        from backend.config.settings import get_settings
        settings = get_settings()
        web_url = payload.get("base_url") or settings.playwright.base_url or "https://adactinhotelapp.com/"

        # 1. Compile JUnit XML
        junit_path = reports_dir / f"junit_{execution_id_str}.xml"
        root = ET.Element("testsuites")
        suite = ET.SubElement(
            root, "testsuite", 
            name="Playwright Testcase", 
            tests=str(max(1, len(mapped_steps))), 
            failures="1" if execution_result.status.value == "failed" else "0",
            errors="1" if execution_result.status.value == "error" else "0",
            time=str(execution_result.duration_seconds)
        )
        tc_el = ET.SubElement(
            suite, "testcase", 
            name=f"TestCase_{execution_result.test_case_id}",
            classname="PlaywrightTests",
            time=str(execution_result.duration_seconds)
        )
        if execution_result.status.value == "failed":
            failure = ET.SubElement(tc_el, "failure", message="Assertion Failed")
            failure.text = execution_result.error_message or "Assertion Error"
        elif execution_result.status.value == "error":
            error = ET.SubElement(tc_el, "error", message="Execution Error")
            error.text = execution_result.error_message or "Tooling Error"

        tree = ET.ElementTree(root)
        tree.write(str(junit_path), encoding="utf-8", xml_declaration=True)

        # 2. Compile HTML report
        html_path = reports_dir / f"report_{execution_id_str}.html"
        
        steps_html = []
        for idx, step in enumerate(mapped_steps):
            screenshot_tag = ""
            if step["screenshot_name"]:
                # Use API route to serve screenshots
                img_src = f"/api/artifacts/{execution_id_str}/screenshot/{step['screenshot_name']}"
                screenshot_tag = f"""
                <div class="step-image">
                    <img src="{img_src}" alt="Screenshot {idx + 1}" />
                </div>
                """
            else:
                screenshot_tag = """
                <div class="no-image-box">
                    <span>No Screenshot Captured</span>
                </div>
                """
            
            steps_html.append(f"""
            <div class="step-card">
                <div class="step-left">
                    <div class="step-header">
                        <span class="step-num">{step['step_name']}</span>
                        <span class="step-time">Time: {step['time']}</span>
                        <span class="step-status" style="margin-left: auto; font-weight: bold; color: {'#10b981' if '✅' in step['status'] else '#ef4444'};">{step['status']}</span>
                    </div>
                    <div class="step-details">
                        <div class="detail-row">
                            <span class="detail-label">Action</span>
                            <div style="padding-left: 8px; border-left: 2px solid #3b82f6; font-size: 0.95rem; line-height: 1.5;">{step['action']}</div>
                        </div>
                        <div class="detail-row" style="margin-top: 8px;">
                            <span class="detail-label">Expected</span>
                            <div style="padding-left: 8px; border-left: 2px solid #f59e0b; font-size: 0.95rem; color: #94a3b8; line-height: 1.5;">{step['expected']}</div>
                        </div>
                        <div class="detail-row" style="margin-top: 8px;">
                            <span class="detail-label">Actual</span>
                            <div style="padding-left: 8px; border-left: 2px solid #10b981; font-size: 0.95rem; color: #cbd5e1; line-height: 1.5;">{step['actual']}</div>
                        </div>
                    </div>
                </div>
                <div class="step-right">
                    {screenshot_tag}
                </div>
            </div>
            """)
        
        steps_html_str = "\n".join(steps_html)
        if not steps_html_str:
            steps_html_str = "<div class='step-card'><p>No execution timeline events recorded.</p></div>"

        summary_outcome_text = (
            "The automated testcase completed successfully on the target application. "
            f"A total of {len(mapped_steps)} steps were executed, verifying form element interactions. "
            "Visual screenshots were captured at critical checkpoints to verify correctness."
            if execution_result.status.value == "passed" else
            "The automated test execution encountered errors or assertions failed. "
            f"Verification aborted at step {len(mapped_steps)}. Visual logs have been persisted for failure diagnostics."
        )

        banner_html = ""
        is_error = execution_result.status.value in ("failed", "error")
        escaped_error_msg = ""
        if execution_result.error_message:
            escaped_error_msg = execution_result.error_message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

        if is_error:
            banner_html = f"""
        <div class="error-banner">
            <div class="error-title">Test Execution Failed</div>
            <div class="error-msg">{escaped_error_msg}</div>
            <div style="margin-top: 16px; display: flex; gap: 12px; align-items: center;">
                <button class="btn-rerun" onclick="rerunTestCase('{execution_result.test_case_id}')">Rerun Execution</button>
                <a href="http://localhost:3000/execution/{execution_id_str}" target="_blank" class="btn-workspace">Open in Playwright Workspace</a>
            </div>
        </div>
        """
        else:
            banner_html = f"""
        <div class="success-banner">
            <div class="success-title">Test Execution Passed</div>
            <div style="margin-top: 16px; display: flex; gap: 12px; align-items: center;">
                <button class="btn-rerun" onclick="rerunTestCase('{execution_result.test_case_id}')">Rerun Execution</button>
                <a href="http://localhost:3000/execution/{execution_id_str}" target="_blank" class="btn-workspace">Open in Playwright Workspace</a>
            </div>
        </div>
        """

        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Execution Report - {execution_id_str}</title>
    <style>
        body {{
            font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            background-color: #0f172a;
            color: #f8fafc;
            padding: 40px 20px;
            margin: 0;
        }}
        .container {{
            max-width: 1000px;
            margin: 0 auto;
        }}
        .header {{
            background-color: #1e293b;
            border: 1px solid #334155;
            border-radius: 12px;
            padding: 32px;
            margin-bottom: 32px;
            display: flex;
            flex-direction: column;
            gap: 24px;
        }}
        .header-main {{
            display: flex;
            align-items: center;
            gap: 20px;
            border-bottom: 1px solid #334155;
            padding-bottom: 20px;
        }}
        .header-title h1 {{
            margin: 0 0 6px 0;
            font-size: 24px;
            color: #3b82f6;
        }}
        .header-title p {{
            margin: 0;
            color: #64748b;
            font-size: 13px;
            font-family: monospace;
        }}
        .header-metadata {{
            display: flex;
            flex-direction: column;
            gap: 16px;
        }}
        .meta-row {{
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}
        .meta-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr 1fr 1fr;
            gap: 16px;
        }}
        @media (max-width: 600px) {{
            .meta-grid {{
                grid-template-columns: 1fr;
            }}
        }}
        .meta-label {{
            font-size: 11px;
            color: #64748b;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            font-weight: bold;
        }}
        .meta-value {{
            font-size: 15px;
            color: #e2e8f0;
            font-weight: 500;
        }}
        .link-value {{
            color: #3b82f6;
            text-decoration: none;
        }}
        .link-value:hover {{
            text-decoration: underline;
        }}
        .badge {{
            display: inline-block;
            padding: 8px 16px;
            border-radius: 6px;
            font-weight: bold;
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .badge-passed {{ background-color: #10b981; color: #ffffff; }}
        .badge-failed {{ background-color: #ef4444; color: #ffffff; }}
        .badge-error {{ background-color: #f59e0b; color: #ffffff; }}
        
        .error-banner {{
            background-color: #7f1d1d;
            border: 1px solid #ef4444;
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 32px;
        }}
        .error-title {{
            font-size: 18px;
            font-weight: bold;
            color: #fca5a5;
            margin-bottom: 8px;
        }}
        .error-msg {{
            font-family: monospace;
            font-size: 13px;
            color: #fecaca;
            white-space: pre-wrap;
            background-color: #450a0a;
            padding: 12px;
            border-radius: 6px;
            border: 1px solid #991b1b;
            overflow-x: auto;
        }}
        .success-banner {{
            background-color: #064e3b;
            border: 1px solid #10b981;
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 32px;
        }}
        .success-title {{
            font-size: 18px;
            font-weight: bold;
            color: #a7f3d0;
        }}
        .btn-rerun {{
            background-color: #3b82f6;
            color: #ffffff;
            border: none;
            padding: 10px 20px;
            border-radius: 6px;
            font-weight: bold;
            font-size: 14px;
            cursor: pointer;
            transition: background-color 0.2s;
        }}
        .btn-rerun:hover {{
            background-color: #2563eb;
        }}
        .btn-rerun:disabled {{
            background-color: #4b5563;
            cursor: not-allowed;
        }}
        .btn-workspace {{
            background-color: #1e293b;
            color: #e2e8f0;
            border: 1px solid #475569;
            padding: 10px 20px;
            border-radius: 6px;
            font-weight: bold;
            font-size: 14px;
            text-decoration: none;
            display: inline-block;
            transition: background-color 0.2s, border-color 0.2s;
        }}
        .btn-workspace:hover {{
            background-color: #334155;
            border-color: #64748b;
        }}

        h2 {{
            font-size: 20px;
            color: #f1f5f9;
            margin-top: 32px;
            margin-bottom: 20px;
            border-bottom: 1px solid #334155;
            padding-bottom: 8px;
        }}
        
        .step-card {{
            background-color: #1e293b;
            border: 1px solid #334155;
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
            display: grid;
            grid-template-columns: 1.2fr 1fr;
            gap: 24px;
        }}
        @media (max-width: 768px) {{
            .step-card {{
                grid-template-columns: 1fr;
            }}
        }}
        .step-left {{
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}
        .step-right {{
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .step-header {{
            display: flex;
            align-items: center;
            gap: 12px;
            border-bottom: 1px solid #334155;
            padding-bottom: 12px;
            margin-bottom: 12px;
        }}
        .step-num {{
            background-color: #3b82f6;
            color: #ffffff;
            font-size: 12px;
            font-weight: bold;
            padding: 4px 10px;
            border-radius: 6px;
            text-transform: uppercase;
        }}
        .step-title {{
            font-weight: bold;
            font-size: 16px;
            color: #e2e8f0;
            flex-grow: 1;
        }}
        .step-time {{
            font-family: monospace;
            font-size: 13px;
            color: #94a3b8;
        }}
        .step-details {{
            display: flex;
            flex-direction: column;
            gap: 12px;
            font-size: 14px;
        }}
        .detail-row {{
            line-height: 1.6;
            color: #cbd5e1;
        }}
        .detail-label {{
            font-weight: bold;
            color: #94a3b8;
            display: block;
            margin-bottom: 2px;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .step-image img {{
            max-width: 100%;
            max-height: 240px;
            border-radius: 8px;
            border: 1px solid #475569;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
            object-fit: contain;
        }}
        .no-image-box {{
            width: 100%;
            height: 150px;
            background-color: #0f172a;
            border: 2px dashed #334155;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #475569;
            font-size: 13px;
        }}
        .summary-card {{
            background-color: #1e293b;
            border: 1px solid #334155;
            border-radius: 12px;
            padding: 24px;
            margin-top: 32px;
        }}
        .summary-title {{
            font-size: 16px;
            font-weight: bold;
            color: #3b82f6;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 12px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="header-main">
                <span class="badge badge-{execution_result.status.value}">
                    {execution_result.status.value.upper()}
                </span>
                <div class="header-title">
                    <h1>Enterprise Test Execution Report</h1>
                    <p>Execution ID: {execution_id_str}</p>
                </div>
            </div>
            <div class="header-metadata">
                <div class="meta-row">
                    <span class="meta-label">Website Under Test</span>
                    <a href="{web_url}" target="_blank" class="meta-value link-value">{web_url}</a>
                </div>
                <div class="meta-grid">
                    <div class="meta-item">
                        <span class="meta-label">Started At</span>
                        <span class="meta-value">{execution_result.executed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}</span>
                    </div>
                    <div class="meta-item">
                        <span class="meta-label">Execution Duration</span>
                        <span class="meta-value">{execution_result.duration_seconds:.2f} seconds</span>
                    </div>
                    <div class="meta-item">
                        <span class="meta-label">Browser</span>
                        <span class="meta-value">Chromium {execution_result.browser_version or ""}</span>
                    </div>
                    <div class="meta-item">
                        <span class="meta-label">Total Steps</span>
                        <span class="meta-value">{len(mapped_steps)}</span>
                    </div>
                </div>
            </div>
        </div>

        {banner_html}
        
        <h2>Step-by-Step Walkthrough</h2>
        <div class="steps-container">
            {steps_html_str}
        </div>

        <div class="summary-card">
            <div class="summary-title">Execution Summary</div>
            <div style="line-height: 1.6; color: #cbd5e1;">
                {summary_outcome_text}
            </div>
        </div>
    </div>

    <script>
    async function rerunTestCase(testCaseId) {{
        const btn = document.querySelector('.btn-rerun');
        if (!btn) return;
        btn.disabled = true;
        btn.innerHTML = '🔄 Re-running...';
        try {{
            const port = window.location.port;
            const backendUrl = port === '3000' ? 'http://localhost:8000' : '';
            const projectId = '{project_id}';
            const response = await fetch(`${{backendUrl}}/api/v1/projects/${{projectId}}/testcases/${{testCaseId}}/execute`, {{
                method: 'POST'
            }});
            if (response.ok) {{
                const data = await response.json();
                alert('Test case execution started! Redirecting to live simulation...');
                window.open(`http://localhost:3000/execution/${{data.execution_id}}`, '_blank');
            }} else {{
                alert('Failed to trigger execution. Please try again from the dashboard.');
            }}
        }} catch (err) {{
            alert('Error: ' + err.message);
        }} finally {{
            btn.disabled = false;
            btn.innerHTML = '🔄 Rerun Execution';
        }}
    }}
    </script>
</body>
</html>
"""
        html_path = reports_dir / f"report_{execution_id_str}.html"
        html_path.write_text(html_content, encoding="utf-8")

        # Overwrite Next.js artifact report.html as well for local browser viewing
        next_report_dir = Path("backend/playwrightt/public/artifacts") / execution_id_str
        next_report_dir.mkdir(parents=True, exist_ok=True)
        next_report_path = next_report_dir / "report.html"
        next_report_path.write_text(html_content, encoding="utf-8")

        # 3. Compile PDF report using PyMuPDF (fitz)
        pdf_path = reports_dir / f"report_{execution_id_str}.pdf"
        try:
            import fitz
            doc = fitz.open()
            
            def add_page(title_text=""):
                page = doc.new_page()
                # Draw top header bar
                page.draw_rect(fitz.Rect(0, 0, 595, 45), color=(0.09, 0.14, 0.23), fill=(0.09, 0.14, 0.23))
                page.insert_text(fitz.Point(30, 28), "Enterprise AI Test Automation - Execution Report", fontsize=10, color=(1, 1, 1), fontname="helvetica-bold")
                if title_text:
                    page.insert_text(fitz.Point(380, 28), title_text, fontsize=9, color=(0.7, 0.8, 1.0), fontname="helvetica")
                
                # Draw bottom footer bar
                page.draw_line(fitz.Point(30, 810), fitz.Point(565, 810), color=(0.8, 0.8, 0.8), width=0.5)
                page.insert_text(fitz.Point(30, 825), f"Execution ID: {execution_id_str}", fontsize=8, color=(0.5, 0.5, 0.5), fontname="helvetica-bold")
                page.insert_text(fitz.Point(490, 825), f"Page {len(doc)}", fontsize=8, color=(0.5, 0.5, 0.5), fontname="helvetica")
                return page

            # --- Page 1: Overview Summary ---
            page = add_page("Overview")
            
            # Title
            page.insert_text(fitz.Point(30, 90), "Automation Execution Verdict Report", fontsize=18, color=(0.1, 0.2, 0.4), fontname="helvetica-bold")
            page.draw_line(fitz.Point(30, 100), fitz.Point(565, 100), color=(0.2, 0.5, 0.9), width=2)

            # Status Box
            is_passed = execution_result.status.value == "passed"
            status_color = (0.06, 0.72, 0.5) if is_passed else (0.93, 0.26, 0.26)
            page.draw_rect(fitz.Rect(30, 120, 150, 220), color=status_color, fill=status_color, width=0)
            page.insert_text(fitz.Point(50, 145), "RESULT VERDICT", fontsize=9, color=(1, 1, 1), fontname="helvetica-bold")
            page.insert_text(fitz.Point(50, 185), execution_result.status.value.upper(), fontsize=20, color=(1, 1, 1), fontname="helvetica-bold")

            # Metadata Table Details
            meta_y = 120
            # Draw table outline
            page.draw_rect(fitz.Rect(170, meta_y, 565, meta_y + 100), color=(0.8, 0.8, 0.8), width=1)
            
            # Header Cells
            page.draw_rect(fitz.Rect(170, meta_y, 565, meta_y + 20), color=(0.95, 0.96, 0.98), fill=(0.95, 0.96, 0.98))
            page.insert_text(fitz.Point(180, meta_y + 14), "WEBSITE UNDER TEST (TARGET URL)", fontsize=8, color=(0.4, 0.4, 0.4), fontname="helvetica-bold")
            page.insert_text(fitz.Point(180, meta_y + 35), web_url, fontsize=9, color=(0.2, 0.5, 0.9), fontname="helvetica-bold")

            # Middle grid division lines
            page.draw_line(fitz.Point(170, meta_y + 50), fitz.Point(565, meta_y + 50), color=(0.8, 0.8, 0.8), width=1)
            page.draw_line(fitz.Point(300, meta_y + 50), fitz.Point(300, meta_y + 100), color=(0.8, 0.8, 0.8), width=1)
            page.draw_line(fitz.Point(430, meta_y + 50), fitz.Point(430, meta_y + 100), color=(0.8, 0.8, 0.8), width=1)

            # Metadata values
            page.insert_text(fitz.Point(180, meta_y + 65), "STARTED AT", fontsize=7, color=(0.5, 0.5, 0.5), fontname="helvetica-bold")
            page.insert_text(fitz.Point(180, meta_y + 85), execution_result.executed_at.strftime('%Y-%m-%d %H:%M:%S'), fontsize=8, color=(0.2, 0.2, 0.2), fontname="helvetica")

            page.insert_text(fitz.Point(310, meta_y + 65), "EXECUTION TIME", fontsize=7, color=(0.5, 0.5, 0.5), fontname="helvetica-bold")
            page.insert_text(fitz.Point(310, meta_y + 85), f"{execution_result.duration_seconds:.2f} seconds", fontsize=8, color=(0.2, 0.2, 0.2), fontname="helvetica")

            page.insert_text(fitz.Point(440, meta_y + 65), "TOTAL STEPS", fontsize=7, color=(0.5, 0.5, 0.5), fontname="helvetica-bold")
            page.insert_text(fitz.Point(440, meta_y + 85), str(len(mapped_steps)), fontsize=8, color=(0.2, 0.2, 0.2), fontname="helvetica")

            # Draw step-by-step walkthrough header
            page.insert_text(fitz.Point(30, 250), "Step-By-Step Execution Walkthrough", fontsize=12, color=(0.1, 0.2, 0.4), fontname="helvetica-bold")
            page.draw_line(fitz.Point(30, 258), fitz.Point(565, 258), color=(0.8, 0.8, 0.8), width=1)

            # Step cards loop
            y_cursor = 270
            for idx, event in enumerate(mapped_steps):
                card_height = 145
                spacing = 10
                
                # Check pagination boundary (Page 1 fits 3 steps; page 2 onwards fits 4 steps)
                if y_cursor + card_height > 800:
                    page = add_page("Walkthrough Details")
                    y_cursor = 60

                # 1. Draw outer card border
                card_rect = fitz.Rect(30, y_cursor, 565, y_cursor + card_height)
                page.draw_rect(card_rect, color=(0.85, 0.87, 0.9), width=1)

                # 2. Draw card header band
                page.draw_rect(fitz.Rect(30, y_cursor, 565, y_cursor + 22), color=(0.95, 0.96, 0.98), fill=(0.95, 0.96, 0.98))
                
                # Draw step number badge
                badge_rect = fitz.Rect(35, y_cursor + 4, 80, y_cursor + 18)
                page.draw_rect(badge_rect, color=(0.23, 0.51, 0.96), fill=(0.23, 0.51, 0.96), width=0)
                page.insert_text(fitz.Point(43, y_cursor + 13), f"STEP {idx + 1}", fontsize=7, color=(1, 1, 1), fontname="helvetica-bold")
                
                # Step Title and time
                page.insert_text(fitz.Point(90, y_cursor + 15), event['title'], fontsize=9, color=(0.1, 0.2, 0.4), fontname="helvetica-bold")
                page.insert_text(fitz.Point(510, y_cursor + 15), f"+{event['time']}", fontsize=8, color=(0.5, 0.5, 0.5), fontname="helvetica-oblique")

                # 3. Draw Left Column: Action, Observation, Result Text
                text_rect = fitz.Rect(35, y_cursor + 28, 275, y_cursor + card_height - 6)
                step_details_text = (
                    f"Actions Taken:\n{event['action']}\n\n"
                    f"Observation:\n{event['observation']}\n\n"
                    f"Result:\n{event['result']}"
                )
                page.insert_textbox(text_rect, step_details_text, fontsize=7.5, fontname="helvetica", color=(0.15, 0.15, 0.15))

                # Vertical separator line
                page.draw_line(fitz.Point(280, y_cursor + 22), fitz.Point(280, y_cursor + card_height), color=(0.85, 0.87, 0.9), width=0.5)

                # 4. Draw Right Column: Screenshot image or placeholder box
                img_rect = fitz.Rect(288, y_cursor + 28, 558, y_cursor + card_height - 8)
                if event["screenshot_url"]:
                    try:
                        page.insert_image(img_rect, filename=event["screenshot_url"])
                    except Exception as img_err:
                        page.draw_rect(img_rect, color=(0.95, 0.95, 0.95), fill=(0.95, 0.95, 0.95))
                        page.insert_text(fitz.Point(340, y_cursor + 75), f"[Image Error: {img_err}]", fontsize=7, color=(0.8, 0.2, 0.2), fontname="helvetica-bold")
                else:
                    page.draw_rect(img_rect, color=(0.97, 0.97, 0.97), fill=(0.97, 0.97, 0.97))
                    page.insert_text(fitz.Point(365, y_cursor + 75), "No Screenshot Captured", fontsize=8, color=(0.6, 0.6, 0.6), fontname="helvetica-bold")

                y_cursor += card_height + spacing

            # --- End Executive Summary Card ---
            summary_height = 80
            if y_cursor + summary_height > 800:
                page = add_page("Execution Summary")
                y_cursor = 60
            
            page.draw_rect(fitz.Rect(30, y_cursor, 565, y_cursor + summary_height), color=(0.23, 0.51, 0.96), fill=(0.95, 0.97, 1.0), width=1)
            page.insert_text(fitz.Point(40, y_cursor + 18), "EXECUTION SUMMARY", fontsize=9, color=(0.2, 0.4, 0.8), fontname="helvetica-bold")
            
            page.insert_textbox(
                fitz.Rect(40, y_cursor + 25, 555, y_cursor + summary_height - 5),
                summary_outcome_text,
                fontsize=8.5,
                fontname="helvetica",
                color=(0.15, 0.15, 0.15)
            )

            doc.save(str(pdf_path))
            doc.close()
        except Exception as pdf_err:
            print(f"[PDF compile error]: {pdf_err}")
            pdf_path.write_bytes(b"%PDF-1.4 ...")

        report_payload = {
            "junit_path": str(junit_path),
            "html_path": str(html_path),
            "pdf_path": str(pdf_path)
        }
        report_payload = self.repo.save_report(project_id, UUID(execution_id_str), report_payload)

        return report_payload

    def compile_reports_with_engine_selection(
        self,
        project_id: UUID,
        execution_result: ExecutionResult
    ) -> dict:
        """
        Compile reports using the configured report engine (legacy or professional).
        
        This method implements the safe migration strategy:
        - Checks feature flag to determine which engine to use
        - Tries professional engine first if enabled
        - Falls back to legacy engine if professional fails (when fallback is enabled)
        - Always ensures a report is generated
        
        Args:
            project_id: Project UUID
            execution_result: ExecutionResult with execution data
            
        Returns:
            Dictionary with report paths (html_path, pdf_path, junit_path)
        """
        print(f"[Report Engine] compile_reports_with_engine_selection() called for execution {execution_result.id}")
        logger.info(f"Compiling reports for execution {execution_result.id} with engine selection")
        
        # Print runtime configuration
        print(f"[Report Engine] REPORT_ENGINE value: {ReportConfig.REPORT_ENGINE}")
        print(f"[Report Engine] is_professional_enabled(): {ReportConfig.is_professional_enabled()}")
        print(f"[Report Engine] should_fallback_on_error(): {ReportConfig.should_fallback_on_error()}")
        
        # Check if professional report engine is enabled
        if ReportConfig.is_professional_enabled():
            print(f"[Report Engine] Selected: PROFESSIONAL")
            logger.info("Professional report engine enabled, attempting generation")
            
            try:
                return self._compile_professional_reports(project_id, execution_result)
            except Exception as e:
                import traceback
                full_traceback = traceback.format_exc()
                logger.error(f"Professional report generation failed: {e}\n{full_traceback}")
                print(f"[Report Engine] Professional generation failed: {e}")
                print(f"[Report Engine] Full traceback:\n{full_traceback}")
                
                # Fallback to legacy if configured
                if ReportConfig.should_fallback_on_error():
                    print(f"[Report Engine] Fallback: Activated (to LEGACY)")
                    logger.warning("Falling back to legacy report engine")
                    return self.compile_reports(project_id, execution_result)
                else:
                    # Re-raise if fallback is disabled
                    print(f"[Report Engine] Fallback: Disabled (re-raising error)")
                    raise
        else:
            print(f"[Report Engine] Selected: LEGACY")
            logger.info("Using legacy report engine")
            return self.compile_reports(project_id, execution_result)
    
    def _compile_professional_reports(
        self,
        project_id: UUID,
        execution_result: ExecutionResult
    ) -> dict:
        """
        Compile reports using the Professional Report Engine.
        
        This is the new professional report generation pipeline that:
        - Uses ExecutionAnalyzer to generate structured validations
        - Uses ProfessionalReportGenerator to render HTML/PDF
        - Generates business rule validation and confidence scores
        
        Args:
            project_id: Project UUID
            execution_result: ExecutionResult with execution data
            
        Returns:
            Dictionary with report paths (html_path, pdf_path, junit_path)
        """
        try:
            print("[ReportService] Step 1 - _compile_professional_reports() entered")
            print(f"[ExecutionAnalyzer] Starting initialization...")
            from backend.services.execution_analyzer import ExecutionAnalyzer
            from backend.services.professional_report_generator import ProfessionalReportGenerator
            from backend.services.llm import LLMService
            
            logger.info(f"Compiling professional reports for execution {execution_result.id}")
            
            # Print object summaries
            print("[ReportService] Step 2 - Printing object summaries")
            print(f"[ReportService] execution_result type: {type(execution_result)}")
            print(f"[ReportService] execution_result.id: {execution_result.id}")
            print(f"[ReportService] timeline length: {len(execution_result.timeline or [])}")
            print(f"[ReportService] screenshots length: {len(execution_result.screenshots or [])}")
            
            # Load test case for context
            print("[ReportService] Step 3 - Loading test case")
            test_case = None
            try:
                test_case = self.repo.get_test_case(execution_result.test_case_id)
                print(f"[ExecutionAnalyzer] test_case loaded: {type(test_case)}")
                if test_case:
                    print(f"[ReportService] test_case type: {type(test_case)}")
                    print(f"[ReportService] test_case.steps type: {type(test_case.steps)}")
                    print(f"[ReportService] test_case.steps contents: {test_case.steps}")
                    print(f"[ReportService] test_case.expected_result: {getattr(test_case, 'expected_result', 'N/A')}")
                    print(f"[ReportService] test_case.title: {getattr(test_case, 'title', getattr(test_case, 'scenario_name', 'N/A'))}")
                    print(f"[ExecutionAnalyzer] test_case.steps: {test_case.steps}")
                    print(f"[ExecutionAnalyzer] test_case.steps type: {type(test_case.steps)}")
                    if test_case.steps and len(test_case.steps) > 0:
                        print(f"[ExecutionAnalyzer] first step type: {type(test_case.steps[0])}")
                        print(f"[ExecutionAnalyzer] first step repr: {repr(test_case.steps[0])}")
            except Exception as e:
                import traceback
                print(f"[ReportService] Failed to load test case: {e}")
                traceback.print_exc()
                logger.error(f"Failed to load test case {execution_result.test_case_id}: {e}")
                # Continue with minimal context
            
            # Extract test case steps
            print("[ReportService] Step 4 - Extracting test case steps")
            test_case_steps = []
            if test_case and test_case.steps:
                # Handle both string steps and object steps with .description attribute
                for step in test_case.steps:
                    if isinstance(step, str):
                        test_case_steps.append(step)
                    elif hasattr(step, 'description'):
                        test_case_steps.append(step.description)
                    else:
                        # Fallback: convert to string
                        test_case_steps.append(str(step))
            
            # Extract website URL from settings or test case
            print("[ReportService] Step 5 - Extracting website URL")
            website_url = "https://example.com"  # Default fallback
            try:
                project = self.repo.get_project(project_id)
                if project:
                    # Try to get target_url from project first
                    if hasattr(project, 'target_url') and project.target_url:
                        website_url = project.target_url
                    # Fallback to settings
                    elif hasattr(project, 'settings') and project.settings:
                        website_url = project.settings.get('playwright', {}).get('base_url', website_url)
                    print(f"[ReportService] Extracted website URL: {website_url}")
            except Exception as e:
                import traceback
                print(f"[ReportService] Failed to extract website URL: {e}")
                traceback.print_exc()
                pass
            
            # Initialize Execution Analyzer
            print("[ReportService] Step 6 - Creating ExecutionAnalyzer")
            print(f"[ExecutionAnalyzer] Instantiating LLMService...")
            llm_service = LLMService()
            print("[ReportService] Step 7 - LLMService created")
            print(f"[ExecutionAnalyzer] Instantiating ExecutionAnalyzer...")
            analyzer = ExecutionAnalyzer(llm_service=llm_service)
            print("[ReportService] Step 8 - ExecutionAnalyzer created")
            print(f"[ExecutionAnalyzer] Started")
            
            # Analyze execution and generate structured validations
            print("[ReportService] Step 9 - Calling analyze_execution()")
            print(f"[ExecutionAnalyzer] Analyzing execution...")
            
            # Extract test case attributes defensively
            print("[ReportService] Step 10 - Extracting test case attributes")
            test_case_title = "Unknown Test Case"
            test_case_description = ""
            expected_result = ""
            
            if test_case:
                if hasattr(test_case, 'title'):
                    test_case_title = test_case.title
                elif hasattr(test_case, 'scenario_name'):
                    test_case_title = test_case.scenario_name
                else:
                    test_case_title = str(test_case)
                
                if hasattr(test_case, 'description'):
                    test_case_description = test_case.description
                else:
                    test_case_description = ""
                
                if hasattr(test_case, 'expected_result'):
                    expected_result = test_case.expected_result
                else:
                    expected_result = ""
            
            print(f"[ExecutionAnalyzer] test_case_title: {test_case_title}")
            print(f"[ExecutionAnalyzer] test_case_description: {test_case_description}")
            print(f"[ExecutionAnalyzer] expected_result: {expected_result}")
            print(f"[ExecutionAnalyzer] test_case_steps: {test_case_steps}")
            
            context = analyzer.analyze_execution(
                execution_result=execution_result,
                test_case_title=test_case_title,
                test_case_description=test_case_description,
                expected_result=expected_result,
                test_case_steps=test_case_steps,
                website_url=website_url,
                project_id=str(project_id),
                business_rules=[]  # Could be extracted from requirements in future
            )
            print("[ReportService] Step 11 - analyze_execution() completed")
            print(f"[ExecutionAnalyzer] Analysis complete")
        
            # Initialize Professional Report Generator
            print("[ReportService] Step 12 - Creating ProfessionalReportGenerator")
            print(f"[ProfessionalReportGenerator] Starting initialization...")
            template_dir = Path(ReportConfig.PROFESSIONAL_TEMPLATE_DIR)
            output_dir = Path(ReportConfig.PROFESSIONAL_REPORT_DIR)
            generator = ProfessionalReportGenerator(template_dir=template_dir)
            print("[ReportService] Step 13 - ProfessionalReportGenerator created")
            print(f"[ProfessionalReportGenerator] Started")
            
            # Generate professional reports
            print("[ReportService] Step 14 - Calling generate_reports()")
            print(f"[ProfessionalReportGenerator] Generating reports...")
            report_paths = generator.generate_reports(
                context=context,
                output_dir=output_dir,
                execution_id=str(execution_result.id)
            )
            print("[ReportService] Step 15 - generate_reports() completed")
            print(f"[Professional Report Generated] HTML: {report_paths['html_path']}")
            print(f"[Professional Report Generated] PDF: {report_paths.get('pdf_path', 'N/A')}")
            
            # Generate JUnit XML (reuse existing logic)
            print("[ReportService] Step 16 - Generating JUnit XML")
            junit_path = self._generate_junit_xml(execution_result, output_dir)
            print("[ReportService] Step 17 - JUnit XML generated")
            
            # Save report paths to repository
            print("[ReportService] Step 18 - Saving report to repository")
            report_payload = {
                "html_path": report_paths["html_path"],
                "pdf_path": report_paths.get("pdf_path"),  # May be None if PDF generation failed
                "junit_path": str(junit_path),
                "engine": "professional"
            }
            
            print(f"[Report Engine] Saving professional report to repository:")
            print(f"[Report Engine]   html_path: {report_payload['html_path']}")
            print(f"[Report Engine]   pdf_path: {report_payload.get('pdf_path')}")
            print(f"[Report Engine]   engine: {report_payload['engine']}")
            
            report_payload = self.repo.save_report(
                project_id, 
                execution_result.id, 
                report_payload
            )
            
            print("[ReportService] Step 19 - Repository updated")
            print(f"[Report Engine] Repository save result: {report_payload}")
            print("[ReportService] Step 20 - Professional pipeline completed")
            logger.info(f"Successfully compiled professional reports: {report_payload}")
            return report_payload
            
        except Exception as e:
            import traceback
            print("[ReportService] FULL TRACEBACK - _compile_professional_reports() failed")
            print(f"[ReportService] Exception type: {type(e).__name__}")
            print(f"[ReportService] Exception message: {e}")
            traceback.print_exc()
            raise
    
    def _generate_junit_xml(
        self,
        execution_result: ExecutionResult,
        output_dir: Path
    ) -> Path:
        """
        Generate JUnit XML report (shared between legacy and professional engines).
        """
        execution_id_str = str(execution_result.id)
        junit_path = output_dir / f"junit_{execution_id_str}.xml"
        
        testsuite = ET.Element("testsuite")
        testsuite.set("name", "Playwright Testcase")
        testsuite.set("tests", "1")
        testsuite.set("failures", "1" if execution_result.status.value == "failed" else "0")
        testsuite.set("errors", "1" if execution_result.status.value == "error" else "0")
        testsuite.set("time", str(execution_result.duration_seconds))
        
        testcase = ET.SubElement(testsuite, "testcase")
        testcase.set("name", f"TestCase_{execution_result.test_case_id}")
        testcase.set("classname", "PlaywrightTests")
        testcase.set("time", str(execution_result.duration_seconds))
        
        if execution_result.status.value in ["failed", "error"]:
            failure = ET.SubElement(testcase, "failure" if execution_result.status.value == "failed" else "error")
            failure.set("message", execution_result.error_message or "Test failed")
            failure.text = execution_result.error_message or "Test failed"
        
        tree = ET.ElementTree(testsuite)
        tree.write(str(junit_path), encoding="utf-8", xml_declaration=True)
        
        return junit_path

    def compile_batch_report(self, project_id: UUID, context) -> dict:
        reports_dir = Path("data/reports")
        reports_dir.mkdir(parents=True, exist_ok=True)
        
        batch_id_str = context.batch_id
        
        total_tasks = len(context.queue)
        passed = context.passed
        failed = context.failed
        skipped = context.skipped
        
        task_rows = []
        for item in context.queue:
            status_class = "badge-passed" if item.status == "passed" else "badge-failed" if item.status in ["failed", "error"] else "badge-error"
            tc_title = "N/A"
            try:
                tc = self.repo.get_test_case(item.testcase_id)
                if tc:
                    tc_title = tc.title
            except Exception:
                pass
            
            task_rows.append(f"""
            <tr>
                <td><strong>{item.task_id}</strong></td>
                <td>{tc_title}</td>
                <td><code style="background: #0f172a; padding: 2px 6px; border-radius: 4px; border: 1px solid #334155;">{item.required_state}</code></td>
                <td>{item.duration:.2f}s</td>
                <td><span class="badge {status_class}">{item.status.upper()}</span></td>
                <td style="font-size: 12px; font-family: monospace; color: #fca5a5;">{item.error_message or ""}</td>
            </tr>
            """)
        
        task_rows_html = "\n".join(task_rows)
        
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Batch Execution Summary - {batch_id_str}</title>
    <style>
        body {{
            font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            background-color: #0f172a;
            color: #f8fafc;
            padding: 40px 20px;
            margin: 0;
        }}
        .container {{
            max-width: 1000px;
            margin: 0 auto;
        }}
        .header {{
            background-color: #1e293b;
            border: 1px solid #334155;
            border-radius: 12px;
            padding: 32px;
            margin-bottom: 32px;
        }}
        .header-title h1 {{
            margin: 0 0 6px 0;
            font-size: 26px;
            color: #3b82f6;
        }}
        .header-title p {{
            margin: 0;
            color: #64748b;
            font-size: 13px;
            font-family: monospace;
        }}
        .metrics-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr 1fr 1fr;
            gap: 20px;
            margin-bottom: 32px;
        }}
        .metric-card {{
            background: #1e293b;
            border: 1px solid #334155;
            padding: 20px;
            border-radius: 12px;
            text-align: center;
        }}
        .metric-val {{
            font-size: 32px;
            font-weight: bold;
            margin-bottom: 4px;
        }}
        .metric-label {{
            font-size: 11px;
            text-transform: uppercase;
            color: #64748b;
            font-weight: bold;
            letter-spacing: 0.5px;
        }}
        .custom-table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 24px;
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 8px;
            overflow: hidden;
        }}
        .custom-table th, .custom-table td {{
            padding: 14px;
            text-align: left;
            border-bottom: 1px solid #334155;
        }}
        .custom-table th {{
            background-color: #0f172a;
            color: #64748b;
            font-size: 11px;
            text-transform: uppercase;
            font-weight: bold;
            letter-spacing: 0.5px;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-weight: bold;
            font-size: 11px;
            text-transform: uppercase;
        }}
        .badge-passed {{ background-color: #10b981; color: #ffffff; }}
        .badge-failed {{ background-color: #ef4444; color: #ffffff; }}
        .badge-error {{ background-color: #f59e0b; color: #ffffff; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="header-title">
                <h1>Batch Execution Summary Dashboard</h1>
                <p>Batch ID: {batch_id_str} | Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
            </div>
        </div>

        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-val" style="color: #3b82f6;">{total_tasks}</div>
                <div class="metric-label">Total Tasks</div>
            </div>
            <div class="metric-card">
                <div class="metric-val" style="color: #10b981;">{passed}</div>
                <div class="metric-label">Passed</div>
            </div>
            <div class="metric-card">
                <div class="metric-val" style="color: #ef4444;">{failed}</div>
                <div class="metric-label">Failed</div>
            </div>
            <div class="metric-card">
                <div class="metric-val" style="color: #f59e0b;">{skipped}</div>
                <div class="metric-label">Skipped</div>
            </div>
        </div>

        <h2>Detailed Task Execution Logs</h2>
        <table class="custom-table">
            <thead>
                <tr>
                    <th>Task ID</th>
                    <th>Test Case Title</th>
                    <th>Required State</th>
                    <th>Duration</th>
                    <th>Status</th>
                    <th>Error Message</th>
                </tr>
            </thead>
            <tbody>
                {task_rows_html}
            </tbody>
        </table>
    </div>
</body>
</html>
"""
        batch_html_path = reports_dir / f"batch_report_{batch_id_str}.html"
        batch_html_path.write_text(html_content, encoding="utf-8")
        
        return {"batch_report_path": str(batch_html_path)}

    def compile_combined_batch_report_html(self, project_id: UUID, batch_id: UUID) -> str:
        # 1. Fetch all executions for the project
        all_execs = self.repo.list_execution_results(project_id)
        
        # 2. Filter for executions belonging to this batch_id (test_cycle_id)
        batch_execs = [ex for ex in all_execs if ex.test_cycle_id == batch_id]
        
        if not batch_execs:
            # Try string comparison as fallback
            batch_execs = [ex for ex in all_execs if str(ex.test_cycle_id) == str(batch_id)]
            
        if not batch_execs:
            raise ValueError(f"No executions found for Batch ID: {batch_id}")
            
        # Sort chronologically
        batch_execs.sort(key=lambda x: x.executed_at)
        
        # Count statistics
        passed = sum(1 for ex in batch_execs if ex.status.value == "passed")
        failed = sum(1 for ex in batch_execs if ex.status.value in ("failed", "error"))
        skipped = sum(1 for ex in batch_execs if ex.status.value == "skipped")
        total_runs = len(batch_execs)
        
        # Build sections for each test case
        sections_html = []
        for tc_idx, ex in enumerate(batch_execs):
            tc = self.repo.get_test_case(ex.test_case_id)
            tc_title = tc.title if tc else f"Test Case {ex.test_case_id}"
            
            payload = getattr(ex, "_raw_payload", {}) or {}
            raw_timeline = payload.get("timeline", []) or getattr(ex, "timeline", []) or []
            
            # If empty, try to parse from Next.js report on disk
            if not raw_timeline:
                next_report_path = Path("backend/playwrightt/public/artifacts") / str(ex.id) / "report.html"
                if next_report_path.exists():
                    try:
                        import re
                        content = next_report_path.read_text(encoding="utf-8")
                        matches = re.findall(r'<li class="([^"]+)">\s*<span[^>]*>([^<]+)</span>\s*<strong>([^<]+)</strong>', content)
                        for m in matches:
                            evt_type, time_str, event_name = m
                            t_val = 0.0
                            if "(" in time_str:
                                t_val_str = time_str.split("(")[1].split("s")[0]
                                try:
                                    t_val = float(t_val_str)
                                except ValueError:
                                    pass
                            raw_timeline.append({
                                "event": event_name,
                                "time": t_val,
                                "type": evt_type
                            })
                    except Exception:
                        pass
            
            # Map timeline to steps
            mapped_steps = []
            for step_idx, evt in enumerate(raw_timeline):
                step_mapped = map_timeline_event_to_step(evt, step_idx, project_id, ex.id, test_case=tc)
                mapped_steps.append(step_mapped)
                
            # mapped_steps = self.enrich_steps_with_llm(mapped_steps, tc)

            import urllib.parse
            video_tag = ""
            if ex.video_path:
                video_url = f"/api/v1/projects/{project_id}/executions/{ex.id}/video?path={urllib.parse.quote(ex.video_path)}"
                video_tag = f"""
                <div style="margin-top: 16px; margin-bottom: 24px; max-width: 480px;">
                    <div style="font-size: 11px; font-weight: bold; color: #64748b; text-transform: uppercase; margin-bottom: 8px; letter-spacing: 0.5px;">Simulation Video</div>
                    <video src="{video_url}" controls style="width: 100%; border-radius: 8px; border: 1px solid #334155; background: #000;"></video>
                </div>
                """

            steps_cards = []
            for idx, step in enumerate(mapped_steps):
                screenshot_tag = ""
                if step["screenshot_url"]:
                    # Serve screenshot relative to the execution using unified route
                    img_src = f"/api/artifacts/{ex.id}/screenshot/{step['screenshot_name']}"
                    screenshot_tag = f"""
                    <div class="step-image">
                        <img src="{img_src}" alt="Screenshot {idx + 1}" style="max-width: 100%; border-radius: 8px; border: 1px solid #334155;" />
                    </div>
                    """
                steps_cards.append(f"""
                <div class="step-card" style="background-color: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 20px; margin-bottom: 16px; display: flex; justify-content: space-between; gap: 24px;">
                    <div class="step-left" style="flex: 1.5;">
                        <div class="step-header" style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px;">
                            <span class="step-num" style="background-color: #3b82f6; color: #ffffff; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px;">Step {idx + 1}</span>
                            <span class="step-title" style="font-weight: 600; color: #f8fafc; font-size: 14px;">{step['title']}</span>
                        </div>
                        <div class="step-details" style="display: flex; flex-direction: column; gap: 8px; font-size: 13px; color: #94a3b8;">
                            <div><strong>Action:</strong> {step['action']}</div>
                            <div><strong>Observation:</strong> {step['observation']}</div>
                            <div><strong>Result:</strong> {step['result']}</div>
                        </div>
                    </div>
                    <div class="step-right" style="flex: 1; text-align: right; min-width: 200px;">
                        {screenshot_tag}
                    </div>
                </div>
                """)
            
            steps_html = "\n".join(steps_cards) if steps_cards else "<div style='color: #64748b; font-size: 13px; padding: 12px;'>No step timeline events recorded for this test case.</div>"
            
            status_color = "#10b981" if ex.status.value == "passed" else "#ef4444"
            error_banner = ""
            if ex.status.value in ("failed", "error") and ex.error_message:
                escaped_err = ex.error_message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
                error_banner = f"""
                <div style="background-color: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 8px; padding: 16px; margin-bottom: 16px; color: #fca5a5; font-family: monospace; font-size: 12px; white-space: pre-wrap;">
                    {escaped_err}
                </div>
                """
                
            sections_html.append(f"""
            <div class="testcase-section" style="margin-bottom: 48px; border-top: 1px solid #334155; padding-top: 24px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                    <h2 style="margin: 0; font-size: 18px; color: #f8fafc;">Case {tc_idx + 1}: {tc_title}</h2>
                    <span style="background-color: {status_color}; color: #ffffff; padding: 4px 10px; border-radius: 9999px; font-size: 11px; font-weight: bold; text-transform: uppercase;">{ex.status.value}</span>
                </div>
                <div style="font-size: 13px; color: #64748b; margin-bottom: 16px;">
                    Duration: {ex.duration_seconds:.2f}s | Executed At: {ex.executed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}
                </div>
                {error_banner}
                {video_tag}
                {steps_html}
            </div>
            """)
            
        sections_str = "\n".join(sections_html)
        
        # Combined HTML layout
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Batch Execution Report - {batch_id}</title>
    <style>
        body {{
            font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            background-color: #0f172a;
            color: #f8fafc;
            padding: 40px 20px;
            margin: 0;
        }}
        .container {{
            max-width: 1000px;
            margin: 0 auto;
        }}
        .header {{
            background-color: #1e293b;
            border: 1px solid #334155;
            border-radius: 12px;
            padding: 32px;
            margin-bottom: 32px;
        }}
        .header-title h1 {{
            margin: 0 0 6px 0;
            font-size: 26px;
            color: #3b82f6;
        }}
        .header-title p {{
            margin: 0;
            color: #64748b;
            font-size: 13px;
            font-family: monospace;
        }}
        .metrics-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr 1fr 1fr;
            gap: 20px;
            margin-bottom: 32px;
        }}
        .metric-card {{
            background: #1e293b;
            border: 1px solid #334155;
            padding: 20px;
            border-radius: 12px;
            text-align: center;
        }}
        .metric-val {{
            font-size: 32px;
            font-weight: bold;
            margin-bottom: 4px;
        }}
        .metric-label {{
            font-size: 11px;
            text-transform: uppercase;
            color: #64748b;
            font-weight: bold;
            letter-spacing: 0.5px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="header-title">
                <h1>Batch Execution Report</h1>
                <p>Batch Context ID: {batch_id} | Compiled At: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
            </div>
        </div>

        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-val" style="color: #3b82f6;">{total_runs}</div>
                <div class="metric-label">Total Cases</div>
            </div>
            <div class="metric-card">
                <div class="metric-val" style="color: #10b981;">{passed}</div>
                <div class="metric-label">Passed</div>
            </div>
            <div class="metric-card">
                <div class="metric-val" style="color: #ef4444;">{failed}</div>
                <div class="metric-label">Failed</div>
            </div>
            <div class="metric-card">
                <div class="metric-val" style="color: #f59e0b;">{skipped}</div>
                <div class="metric-label">Skipped</div>
            </div>
        </div>

        <h2 style="font-size: 20px; color: #f8fafc; margin-bottom: 24px;">Sequential Execution Details</h2>
        {sections_str}
    </div>
</body>
</html>
"""
        return html_content

# Register execution completed listener to handle report compilation automatically
from backend.services.execution_service import register_execution_completed_listener

def _on_execution_completed(project_id, execution_result):
    print(f"[Report Engine] _on_execution_completed() called for execution {execution_result.id}")
    print(f"[Report Engine] Stack trace:")
    import traceback
    traceback.print_stack()
    
    # Print current configuration
    from backend.config.report_config import ReportConfig
    ReportConfig.print_config()
    
    service = ReportService()
    # Use engine selection method to respect feature flag
    print(f"[Report Engine] Calling compile_reports_with_engine_selection()...")
    service.compile_reports_with_engine_selection(project_id, execution_result)

register_execution_completed_listener(_on_execution_completed)
