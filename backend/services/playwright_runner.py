"""
The only module allowed to invoke the Playwright CLI directly. Mirrors
LLMService's role for the LLM-calling agents: ExecutionAgent calls this,
never subprocess/npx directly.

What's actually verified here, against a real local Playwright
installation (v1.61.1), not assumed from memory:
- The --reporter=json structure this parses against
- Exit codes (0 if everything passed, non-zero if anything failed)
- --trace=on producing a discoverable attachment in that JSON
- The process-tree-kill fix below, against a real multi-level process
  tree (POSIX) - spawned a child that itself spawned a grandchild, and
  confirmed a naive kill() leaves the grandchild running while killing
  the whole process group correctly reaches it.

What's NOT independently verified: the Windows taskkill /T path (this
sandbox has no Windows environment), and real browser-driven execution
(page.goto, clicking, actual screenshots/video) - needs a real browser
binary, which needs network access to download.

screenshot_path/video_path come from a companion playwright.config.ts
(use: { screenshot, video }) - not simple CLI flags in this Playwright
version, unlike --trace which is. trace_path is the one field of the
three confirmed to populate correctly end to end.

IMPORTANT FAILURE-MODE DESIGN: this class raises PlaywrightRunnerError
only for setup problems that make EVERY future call fail identically
(npx missing) - failing loud and fast there is correct, since nothing
downstream can work either. Anything that goes wrong DURING an actual
run attempt (timeout, a crash mid-execution, unparseable output) returns
a PlaywrightRunResult(status="error", ...) instead of raising. That's a
deliberate choice, not an oversight: if running test case #2 out of 5
blows up, ExecutionAgent's loop needs to keep going for #3, #4, #5
rather than losing all of them because one had a problem. Silently
matches what happens in real Windows use - a single hung browser
shouldn't take down an entire batch's results.
"""

import json
import os
import platform
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

ARTIFACTS_ROOT = Path(__file__).parent.parent / "playwrightt" / "artifacts"

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
_IS_WINDOWS = platform.system() == "Windows"


def _strip_ansi(text: str) -> str:
    """Playwright's JSON reporter still embeds terminal color codes
    (meant for the 'list'/'line' reporters) inside error message text -
    confirmed by actually running a failing test, not assumed."""
    return _ANSI_ESCAPE.sub("", text)


_CONFIG_TEMPLATE = """\
import {{ defineConfig }} from '@playwright/test';
export default defineConfig({{
  use: {{
    browserName: 'chromium',
    headless: {headless_js},
    launchOptions: {{
      slowMo: 1000,
      args: ['--disable-blink-features=AutomationControlled']
    }},
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    {storage_state_config}
  }},
  reporter: [
    ['line'],
    ['json', {{ outputFile: 'report.json' }}]
  ],
  outputDir: '{output_dir}',
  timeout: {timeout_ms},
}});
"""

# Playwright statuses that map directly; anything else (a CLI crash, a
# file that fails to even load) gets treated as "error" rather than
# guessed at.
_KNOWN_STATUSES = {"passed", "failed", "skipped"}


class PlaywrightRunResult(BaseModel):
    """Raw result from actually running a script. No knowledge of which
    TestCase this came from - ExecutionAgent attaches that."""

    status: str  # "passed" | "failed" | "skipped" | "error"
    duration_seconds: float = 0.0
    error_message: str | None = None
    screenshot_path: str | None = None
    video_path: str | None = None
    trace_path: str | None = None
    stdout_lines: list[str] = []  # Capture console output for timeline extraction


class PlaywrightRunnerError(Exception):
    """Raised ONLY for setup problems where every future call would fail
    identically (npx not found). Never raised for a single run attempt
    going wrong - see the module docstring for why that distinction
    matters."""


def _kill_process_tree(pid: int) -> None:
    """Kill a process and everything it spawned, not just the direct
    child. A plain process.kill() only kills the top-level process
    (npx.CMD / the shell wrapper) - npx spawns node, which spawns the
    Playwright runner, which spawns an actual browser. If only the
    top-level process dies, the browser keeps running and keeps its
    output pipes open, so the next communicate() call waits forever for
    pipes that will never close. This is the exact bug that required a
    manual Ctrl+C in a real run - see playwright_runner's history."""
    if _IS_WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    else:
        try:
            os.killpg(pid, 9)  # SIGKILL the whole process group
        except (ProcessLookupError, PermissionError):
            pass


class PlaywrightRunner:
    def __init__(self, timeout_seconds: int = 60):
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        script: str,
        run_id: str,
        on_log: Callable[[str], None] | None = None,
        storage_state_path: str | None = None,
        project_id: str | None = None,
        headless: bool | None = None
    ) -> PlaywrightRunResult:
        npx = shutil.which("npx")
        if npx is None:
            raise PlaywrightRunnerError(
                "npx not found on PATH - Node.js and npm must be installed, "
                "and @playwright/test must be installed in this project "
                "(npm install --save-dev @playwright/test)."
            )

        # Resolve headless configuration dynamically from settings
        if headless is None:
            try:
                from backend.config.settings import get_settings
                headless = get_settings().playwright.headless
            except Exception:
                headless = True

        run_dir = ARTIFACTS_ROOT / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        report_path = run_dir / "report.json"
        if report_path.exists():
            try:
                report_path.unlink()
            except OSError:
                pass

        # Clean screenshots folder
        screenshots_dir = run_dir / "screenshots"
        if screenshots_dir.exists():
            try:
                shutil.rmtree(screenshots_dir)
            except Exception:
                pass
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        spec_content = script
        
        # Inject the automatic screenshot wrapper block
        screenshot_wrapper = """
import { test, expect } from '@playwright/test';
import * as path from 'path';
import * as fs from 'fs';

test.beforeEach(async ({ page }) => {
  const screenshotsDir = path.join(__dirname, 'screenshots');
  if (!fs.existsSync(screenshotsDir)) {
    fs.mkdirSync(screenshotsDir, { recursive: true });
  }

  let stepIndex = 0;
  let lastTimelineMessage = '';

  const origConsoleLog = console.log;
  console.log = function(...args) {
    origConsoleLog(...args);
    const msg = args.join(' ');
    if (msg.startsWith('[Timeline]')) {
      lastTimelineMessage = msg.replace('[Timeline]', '').trim();
    }
  };

  const sanitizeFilename = (str) => {
    return str.toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '');
  };

  const takeStepScreenshot = async (actionName: string) => {
    stepIndex++;
    const cleanDesc = sanitizeFilename(lastTimelineMessage || actionName);
    const filename = `step-${String(stepIndex).padStart(2, '0')}-${cleanDesc}.png`;
    const filepath = path.join(screenshotsDir, filename);
    try {
      await page.waitForTimeout(300);
      await page.screenshot({ path: filepath });
      console.log(`[Timeline] Screenshot: ${filename}`);
    } catch (e) {
      // Ignore screenshot errors
    }
  };

  const origGoto = page.goto.bind(page);
  page.goto = async (url, options) => {
    const res = await origGoto(url, options);
    await takeStepScreenshot('goto');
    return res;
  };

  const LocatorProto = Object.getPrototypeOf(page.locator('body'));

  const origLocClick = LocatorProto.click;
  LocatorProto.click = async function(options) {
    const res = await origLocClick.call(this, options);
    await takeStepScreenshot('click');
    return res;
  };

  const origLocFill = LocatorProto.fill;
  LocatorProto.fill = async function(value, options) {
    const res = await origLocFill.call(this, value, options);
    await takeStepScreenshot('fill');
    return res;
  };

  const origLocSelectOption = LocatorProto.selectOption;
  LocatorProto.selectOption = async function(values, options) {
    const res = await origLocSelectOption.call(this, values, options);
    await takeStepScreenshot('selectOption');
    return res;
  };
});
"""
        spec_content = screenshot_wrapper + "\n" + spec_content

        if storage_state_path:
            escaped_state_path = storage_state_path.replace("\\", "/")
            spec_content += f"\n\ntest.afterEach(async ({{ page }}) => {{\n  try {{\n    await page.context().storageState({{ path: '{escaped_state_path}' }});\n  }} catch (e) {{\n    console.error('Failed to save storage state:', e);\n  }}\n}});\n"
        
        (run_dir / "test.spec.ts").write_text(spec_content)
        config_path = run_dir / "playwright.config.ts"

        storage_state_config = ""
        if storage_state_path and os.path.exists(storage_state_path):
            escaped_path = storage_state_path.replace("\\", "/")
            storage_state_config = f"storageState: '{escaped_path}',"

        config_path.write_text(
            _CONFIG_TEMPLATE.format(
                output_dir=str(run_dir / "test-results").replace("\\", "/"),
                timeout_ms=self.timeout_seconds * 1000,
                storage_state_config=storage_state_config,
                headless_js="true" if headless else "false"
            )
        )

        hard_timeout = self.timeout_seconds + 15
        args = [npx, "playwright", "test", "--config", str(config_path), "--trace=on"]
        
        # Load vault credentials into child process environment
        child_env = os.environ.copy()
        if "PLAYWRIGHT_CHROMIUM_CHANNEL" in child_env:
            del child_env["PLAYWRIGHT_CHROMIUM_CHANNEL"]
        if project_id:
            try:
                from backend.services.vault_service import VaultService
                username, password = VaultService().get_credentials(project_id)
                if username:
                    child_env["TARGET_USERNAME"] = username
                if password:
                    child_env["TARGET_PASSWORD"] = password
            except Exception as e:
                if on_log:
                    on_log(f"[Warning] Failed to load credentials from vault: {e}")

        popen_kwargs = {
            "cwd": Path(__file__).parent.parent / "playwrightt",
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "env": child_env
        }
        if _IS_WINDOWS:
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        process = subprocess.Popen(args, **popen_kwargs)

        q = queue.Queue()
        def enqueue_output(out, q):
            for line in iter(out.readline, ''):
                q.put(line)
            out.close()

        t = threading.Thread(target=enqueue_output, args=(process.stdout, q))
        t.daemon = True
        t.start()

        start_time = time.time()
        stdout_lines = []

        while True:
            elapsed = time.time() - start_time
            if elapsed > hard_timeout:
                _kill_process_tree(process.pid)
                return PlaywrightRunResult(
                    status="error",
                    duration_seconds=float(hard_timeout),
                    error_message=f"Playwright did not finish within {hard_timeout}s and was force-killed.",
                    stdout_lines=stdout_lines,
                )

            try:
                line = q.get_nowait()
            except queue.Empty:
                if process.poll() is not None:
                    break
                time.sleep(0.05)
                continue

            line_str = line.strip()
            if line_str:
                stdout_lines.append(line_str)
                if on_log:
                    on_log(line_str)

        while not q.empty():
            try:
                line = q.get_nowait()
                line_str = line.strip()
                if line_str:
                    stdout_lines.append(line_str)
                    if on_log:
                        on_log(line_str)
            except queue.Empty:
                break

        if not report_path.exists():
            err_msg = "\n".join(stdout_lines[-5:]) if stdout_lines else "No output"
            return PlaywrightRunResult(
                status="error",
                error_message=f"Playwright did not produce report.json. Last output: {err_msg}",
                stdout_lines=stdout_lines,
            )

        try:
            report = json.loads(report_path.read_text())
        except Exception as exc:
            return PlaywrightRunResult(
                status="error",
                error_message=f"Failed to read report.json: {exc}",
                stdout_lines=stdout_lines,
            )

        return self._parse_report(report, stdout_lines)

    def _parse_report(self, report: dict, stdout_lines: list[str]) -> PlaywrightRunResult:
        suites = report.get("suites", [])
        specs = suites[0].get("specs", []) if suites else []

        if not specs:
            # The file loaded but no test() was found in it - almost
            # always a syntax error or malformed script, not a real
            # pass/fail verdict.
            errors = report.get("errors", [])
            message = errors[0].get("message") if errors else "No test found in the generated script"
            return PlaywrightRunResult(status="error", error_message=_strip_ansi(str(message)), stdout_lines=stdout_lines)

        test_result = specs[0]["tests"][0]["results"][0]
        status = test_result["status"]

        if status not in _KNOWN_STATUSES:
            # e.g. "timedOut" or "interrupted" - real Playwright statuses
            # this hasn't been specifically mapped for; don't guess.
            status = "error"

        error_message = test_result.get("error", {}).get("message")
        if error_message:
            error_message = _strip_ansi(error_message)
        attachments = {a["name"]: a["path"] for a in test_result.get("attachments", [])}

        return PlaywrightRunResult(
            status=status,
            duration_seconds=test_result.get("duration", 0) / 1000.0,
            error_message=error_message,
            screenshot_path=attachments.get("screenshot"),
            video_path=attachments.get("video"),
            trace_path=attachments.get("trace"),
            stdout_lines=stdout_lines,
        )