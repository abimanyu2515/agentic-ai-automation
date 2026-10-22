"""
PlaywrightAgent

Purpose:
    Converts approved test cases into executable Playwright TypeScript scripts using LLM.
    Supports multi-framework generation (Playwright, Selenium, Cucumber).

Responsibilities:
    - Load Playwright script generation prompt template
    - Resolve project target URL and credentials from vault
    - Generate executable code via LLM (TypeScript for Playwright, Python for Selenium, Gherkin for Cucumber)
    - Strip markdown code fences from LLM output
    - Validate generated code contains expected framework imports/patterns
    - Inject login credentials securely via environment variables
    - Update test_case.playwright_script with generated code

Workflow Position:
    HumanApprovalAgent (test case approval)
        ↓
    PlaywrightAgent
        ↓
    ExecutionAgent
        ↓
    ReportAgent

Inputs:
    - state.approved_test_cases(): Test cases with evaluation_status=APPROVED or human-approved
    - test_case.title, preconditions, steps, expected_result
    - project.framework: Target automation framework (playwright, selenium, cucumber)
    - project.target_url, target_username, target_password: Application credentials from vault

Outputs:
    - test_case.playwright_script: Generated executable code

Framework Support:
    - Playwright: TypeScript with Playwright Test APIs
    - Selenium: Python with pytest and Selenium WebDriver
    - Cucumber: Gherkin feature files with Python Behave step definitions

LLM Output Format:
    Raw text (not JSON) to avoid escaping issues with quotes, backslashes, and multi-line code.
    Models often wrap code in ```typescript...``` fences despite instructions not to.
    Agent strips fences automatically.

Security:
    - Credentials loaded from VaultService (encrypted at rest)
    - Scripts inject credentials via environment variables (process.env.TARGET_USERNAME)
    - Never hardcode credentials in generated scripts

Validation:
    - Playwright: Must contain "test(" and "import"
    - Selenium: Must contain "import" and ("webdriver" or "pytest" or "selenium")
    - Cucumber: Must contain "Feature:" or "Scenario:" or "@given" or "given("

Critical Rules (target-agnostic):
    - Never hardcode selectors, URLs, or credentials for a specific demo app
    - Derive every locator and value from the test case inputs
    - Prefer accessible locators (getByRole/getByLabel) over raw CSS IDs
    - Resolve credentials only from the project's vault / environment variables

Design Decision:
    One LLM call per test case (not batched) isolates errors. Callers may
    opt into bounded parallel generation when creating larger test suites.
"""

from concurrent.futures import ThreadPoolExecutor
import json
from uuid import UUID

from backend.agents.base import BaseAgent
from backend.models.state import WorkflowState
from backend.models.test_case import TestCase
from backend.config.settings import get_settings
from backend.services.llm import LLMService
from backend.utils.prompts import load_prompt

_SYSTEM_PROMPT = (
    "You are a senior QA automation engineer who writes clean, idiomatic Playwright TypeScript tests. "
    "You output ONLY code - no explanation, no markdown code fences, nothing before or after it.\n\n"
    "CRITICAL rules (apply to ANY target web application):\n"
    "1. The test case inputs (title, preconditions, steps, expected result, and test_data) plus the "
    "project's target base URL are the ONLY source of truth. Use supplied test_data values for inputs; "
    "never invent test data or rely on prior knowledge of any specific "
    "demo site, and never hardcode selectors, URLs, or credentials from memory.\n"
    "2. Derive every locator from the steps: prefer accessible locators (getByRole, getByLabel, "
    "getByPlaceholder, getByText) with unambiguous names; fall back to stable CSS/ID selectors only "
    "when the steps name them. If a locator cannot be derived with confidence, emit a "
    "`// TODO: locator not specified in inputs` comment instead of fabricating one.\n"
    "3. Match the control type to the wording: `<select>` dropdowns use selectOption({ label: '...' }); "
    "text inputs use fill(); buttons/links use click(). Never call selectOption on a text input.\n"
    "4. Do NOT perform pre-login actions unless explicitly specified in the Preconditions or Steps. "
    "Start directly by navigating to the base URL (goto(BASE_URL)) and then execute the steps in order.\n"
    "5. After submitting a login form, wait for the post-login landing to complete (e.g. wait for a URL "
    "change or for a dashboard/home element named in the steps to become visible) before executing "
    "subsequent steps. Do NOT navigate to arbitrary deep URLs right after login.\n"
    "6. ALWAYS use page.locator('selector').click() and page.locator('selector').fill('text') instead of "
    "page.click('selector') or page.fill('selector'). This is required by the platform to capture "
    "step-by-step screenshots and timeline reports.\n"
    "7. Do NOT use `const text = await locator.textContent(); expect(text).toContain('val')`. This does not "
    "auto-wait and causes race conditions. ALWAYS use Playwright's auto-waiting assertions, e.g. "
    "`await expect(page.locator('selector')).toContainText('val')` or "
    "`await expect(page.locator('selector')).toHaveValue('val')`.\n"
    "8. Only assert the navigation or outcome stated in the expected result. Never invent destination URLs, "
    "page names, or success messages.\n"
    "9. Never assume a date/data format. If the steps do not specify one, use a reasonable format and flag it "
    "with a `// NOTE:` comment.\n"
    "10. ALWAYS import { test, expect } from '@playwright/test' (not just `test`). The `expect` is required "
    "for assertions.\n"
    "11. CRITICAL DROPDOWN RULES:\n"
    "    - If a step says to keep a dropdown or field at its default value or do nothing, do NOT interact with "
    "it or call `selectOption`. Simply generate a comment, e.g. `// Keep default - no action needed`.\n"
    "    - When using `selectOption` to select an option, ALWAYS select by label wrapper: "
    "`selectOption({ label: \"Option Label Text\" })`. Do NOT pass unexplained raw strings.\n"
    "12. For async operations (e.g. form submissions that process server-side), wait with an adequate timeout "
    "on the expected outcome (e.g. `await expect(page).toHaveURL(/confirm/, { timeout: 15000 });`) instead of "
    "fixed sleeps. Rely on Playwright auto-waiting; never use waitForTimeout().\n"
)


def _strip_code_fences(text: str) -> str:
    """Models routinely wrap code in ```typescript ... ``` even when told
    not to. Strip it if present; leave the text alone if it isn't."""
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    lines = lines[1:]  # drop the opening ``` or ```typescript line
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _looks_like_valid_code(code: str, framework: str) -> bool:
    code_lower = code.lower()
    if framework == "selenium":
        return "import" in code and ("webdriver" in code_lower or "pytest" in code_lower or "selenium" in code_lower)
    elif framework == "cucumber":
        return "feature:" in code_lower or "scenario:" in code_lower or "@given" in code_lower or "given(" in code_lower
    else:
        return "test(" in code and "import" in code


def _format_preconditions(preconditions: list[str]) -> str:
    if not preconditions:
        return "None specified"
    return "\n".join(f"- {p}" for p in preconditions)


def _format_steps(steps: list[str]) -> str:
    return "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps))


class PlaywrightAgent(BaseAgent):
    name = "Playwright Agent"

    def __init__(
        self,
        llm_service: LLMService | None = None,
        repo=None,
        max_workers: int = 1,
        test_data_template: bool = False,
    ):
        self.llm_service = llm_service or LLMService()
        self.repo = repo
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self.max_workers = max_workers
        self.test_data_template = test_data_template

    def run(self, state: WorkflowState) -> WorkflowState:
        if state.requirement is None:
            raise ValueError(f"{self.name} requires state.requirement to be set")

        approved = state.approved_test_cases()
        if not approved:
            state.add_log(f"{self.name}: no approved test cases to convert")
            return state

        state.add_log(f"{self.name} started")

        # Resolve project framework via repository
        framework = "playwright"
        target_url = None
        target_username = None
        target_password = None

        if self.repo and state.requirement:
            req_id = state.requirement.id
            projects = self.repo.list_projects()
            target_proj = None
            for proj in projects:
                if req_id in proj.requirements:
                    target_proj = proj
                    framework = proj.framework or "playwright"
                    break
            
            if not target_proj and projects:
                target_proj = projects[0]
                framework = target_proj.framework or "playwright"
            
            if target_proj:
                target_url = target_proj.target_url
                target_username = target_proj.target_username
                if target_proj.target_password_enc:
                    from backend.utils.crypto import decrypt_value
                    target_password = decrypt_value(target_proj.target_password_enc)

                 # Fallback or override from vault
                try:
                    from backend.services.vault_service import VaultService
                    v_user, v_pass = VaultService().get_credentials(str(target_proj.id))
                    if v_user:
                        target_username = v_user
                    if v_pass:
                        target_password = v_pass
                except Exception as vault_err:
                    state.add_log(f"[PlaywrightAgent] Vault load warning: {vault_err}")

                # No hardcoded credential fallback: if neither the project nor the
                # vault provides credentials, leave them as None so generated
                # scripts use clearly-marked placeholders / env variables.
                if not target_username or not target_password:
                    state.add_log(
                        f"[{self.name}] No project/vault credentials found; "
                        "scripts will use placeholder credentials."
                    )

        if self.test_data_template:
            if framework != "playwright":
                raise ValueError(
                    "Test-data script templates are supported only for Playwright."
                )

            state.add_log(
                f"{self.name}: generating one reusable template for "
                f"{len(approved)} test-data cases"
            )
            template_case, markers = self._test_data_template_case(approved[0])
            template_script = self._generate_script(
                template_case,
                framework,
                target_url,
                target_username,
                target_password,
                test_data_template=True,
            )
            missing_markers = [
                marker for marker in markers.values()
                if marker not in template_script
            ]
            if missing_markers:
                state.add_log(
                    f"{self.name}: template omitted {len(missing_markers)} "
                    "required data marker(s); retrying template generation once"
                )
                template_script = self._generate_script(
                    template_case,
                    framework,
                    target_url,
                    target_username,
                    target_password,
                    test_data_template=True,
                    template_feedback=(
                        "Your previous script template was rejected because it omitted "
                        "these exact required markers: "
                        f"{', '.join(missing_markers)}. Regenerate the complete script "
                        "and preserve every required marker exactly as provided in the "
                        "test_data, title, and expected result. Do not omit fields."
                    ),
                )
                missing_markers = [
                    marker for marker in markers.values()
                    if marker not in template_script
                ]
            if missing_markers:
                raise ValueError(
                    f"{self.name}: generated test-data template omitted required "
                    f"data markers: {', '.join(missing_markers)}"
                )

            expected_fields = set(approved[0].test_data)
            for test_case in approved:
                if set(test_case.test_data) != expected_fields:
                    raise ValueError(
                        f"{self.name}: test-data fields differ for '{test_case.title}'"
                    )
                scenario = (
                    self.repo.get_scenario(test_case.scenario_id)
                    if self.repo else None
                )
                data_id = next(
                    (
                        tag for tag in (scenario.tags if scenario else [])
                        if tag.startswith("TD-")
                    ),
                    None,
                )
                if data_id is None:
                    raise ValueError(
                        f"{self.name}: scenario for '{test_case.title}' has no test-data ID"
                    )

                replacements = {
                    "__TEST_DATA_ID__": data_id,
                    "__TEST_TYPE__": scenario.path_type,
                    "__EXPECTED_RESULT__": json.dumps(
                        test_case.expected_result,
                        ensure_ascii=False,
                    )[1:-1],
                }
                replacements.update({
                    markers[key]: json.dumps(str(value), ensure_ascii=False)[1:-1]
                    for key, value in test_case.test_data.items()
                })
                test_case.playwright_script = self._apply_template_values(
                    template_script,
                    replacements,
                )

            state.add_log(
                f"{self.name} finished: generated one reusable Playwright "
                f"template for {len(approved)} test-data case(s)"
            )
            return state

        def generate_script(test_case: TestCase) -> str:
            return self._generate_script(
                test_case,
                framework,
                target_url,
                target_username,
                target_password,
            )

        if self.max_workers == 1 or len(approved) == 1:
            scripts = map(generate_script, approved)
            for test_case, script in zip(approved, scripts):
                test_case.playwright_script = script
        else:
            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(approved))) as executor:
                for test_case, script in zip(approved, executor.map(generate_script, approved)):
                    test_case.playwright_script = script

        state.add_log(f"{self.name} finished: generated {len(approved)} {framework.capitalize()} script(s)")
        return state

    @staticmethod
    def _test_data_template_case(test_case: TestCase) -> tuple[TestCase, dict[str, str]]:
        markers = {
            key: f"__TEST_DATA_{index:02d}__"
            for index, key in enumerate(test_case.test_data, start=1)
        }
        template_case = test_case.model_copy(update={
            "title": "Test data __TEST_DATA_ID__: __TEST_TYPE__",
            "steps": [
                "Complete the requirement workflow using the supplied test_data values.",
                "Verify the expected_result for the supplied test_data values.",
            ],
            "expected_result": "__EXPECTED_RESULT__",
            "test_data": {
                key: markers[key] for key in test_case.test_data
            },
        })
        markers.update({
            "__TEST_DATA_ID__": "__TEST_DATA_ID__",
            "__TEST_TYPE__": "__TEST_TYPE__",
            "__EXPECTED_RESULT__": "__EXPECTED_RESULT__",
        })
        return template_case, markers

    @staticmethod
    def _apply_template_values(script: str, replacements: dict[str, str]) -> str:
        for marker, value in replacements.items():
            script = script.replace(marker, value)
        return script

    def _generate_script(
        self,
        test_case: TestCase,
        framework: str = "playwright",
        target_url: str | None = None,
        target_username: str | None = None,
        target_password: str | None = None,
        test_data_template: bool = False,
        template_feedback: str | None = None,
    ) -> str:
        import os
        settings = get_settings()
        base_url = target_url or settings.playwright.base_url or ""

        # Override framework if configured at test case level
        if getattr(test_case, "automation_framework", None):
            framework = test_case.automation_framework.lower()

        framework_context_str = "None specified"
        if "imported" in framework or "existing" in framework:
            # Locate and load the imported POM classes index
            # Look up project directory path from test case scenario requirement
            project_id_str = None
            if self.repo:
                scenario = self.repo.get_scenario(test_case.scenario_id)
                if scenario:
                    req_id = scenario.requirement_id
                    projects = self.repo.list_projects()
                    for proj in projects:
                        if req_id in proj.requirements:
                            project_id_str = str(proj.id)
                            break
            if project_id_str:
                pom_path = os.path.join("data", "projects", project_id_str, "pom_index.json")
                if os.path.exists(pom_path):
                    try:
                        with open(pom_path, "r", encoding="utf-8") as f:
                            pom_data = json.load(f)
                            framework_context_str = json.dumps(pom_data, indent=2)
                    except Exception:
                        pass

        title_lower = test_case.title.lower()
        is_login_test = "login" in title_lower or any("login" in step.lower() for step in test_case.steps[:2])

        if framework == "selenium":
            system_prompt = (
                "You are a senior QA automation engineer who writes clean, idiomatic Selenium WebDriver tests in Python using pytest. "
                "You output ONLY code - no explanation, no markdown code fences, nothing before or after it."
            )
            prompt = load_prompt(
                "playwright_prompt.txt",
                title=test_case.title,
                preconditions=_format_preconditions(test_case.preconditions),
                steps=_format_steps(test_case.steps),
                expected_result=test_case.expected_result,
                test_data=json.dumps(test_case.test_data, ensure_ascii=False),
                base_url=base_url,
                framework_context=framework_context_str,
                target_username=target_username or "default_username",
                target_password=target_password or "default_password"
            )
            if target_username and target_password:
                if is_login_test:
                    prompt += (
                        f"\n\nLoad the credentials securely from environment variables at the top of the test code block:\n"
                        f"import os\n"
                        f"BASE_URL = os.environ.get('TARGET_URL', '{base_url}')\n"
                        f"USERNAME = os.environ.get('TARGET_USERNAME', '{target_username}')\n"
                        f"PASSWORD = os.environ.get('TARGET_PASSWORD', '{target_password}')\n"
                        f"Use USERNAME and PASSWORD when the test steps request entering the valid username and password."
                    )
                else:
                    prompt += (
                        f"\n\nAt the start of the test, after navigating to the base URL, you MUST log in. "
                        f"Load the credentials securely from environment variables at the top of the test code block: "
                        f"import os\n"
                        f"BASE_URL = os.environ.get('TARGET_URL', '{base_url}')\n"
                        f"USERNAME = os.environ.get('TARGET_USERNAME', '{target_username}')\n"
                        f"PASSWORD = os.environ.get('TARGET_PASSWORD', '{target_password}')\n"
                        f"Navigate to BASE_URL. Type USERNAME into the username field, PASSWORD into the password field, click login, and wait for the post-login landing to load (e.g. wait for a URL change or for a dashboard element named in the steps to become visible) before executing subsequent steps."
                    )
            prompt += "\n\nWrite this test case using Selenium Python with pytest instead of Playwright."
        elif framework == "cucumber":
            system_prompt = (
                "You are a senior QA automation engineer who writes Cucumber BDD test specifications. "
                "You output a single Gherkin .feature specification followed by Python Behave/step definitions. "
                "You output ONLY the feature and step code - no explanation, no markdown code fences, nothing before or after it."
            )
            prompt = load_prompt(
                "playwright_prompt.txt",
                title=test_case.title,
                preconditions=_format_preconditions(test_case.preconditions),
                steps=_format_steps(test_case.steps),
                expected_result=test_case.expected_result,
                test_data=json.dumps(test_case.test_data, ensure_ascii=False),
                base_url=base_url,
                framework_context=framework_context_str,
                target_username=target_username or "default_username",
                target_password=target_password or "default_password"
            )
            if target_username and target_password:
                if is_login_test:
                    prompt += (
                        f"\n\nRetrieve the username and password from environment variables `TARGET_USERNAME` and `TARGET_PASSWORD` in your behave steps, "
                        f"and use them when the steps request entering the valid credentials (username: '{target_username}', password: '{target_password}')."
                    )
                else:
                    prompt += (
                        f"\n\nAt the start of the test, after navigating to the base URL, you MUST log in. "
                        f"Retrieve the username and password from environment variables `TARGET_USERNAME` and `TARGET_PASSWORD` in your behave steps (using fallback username: '{target_username}', password: '{target_password}')."
                    )
            prompt += "\n\nWrite this test case using Gherkin feature syntax and Python step definitions instead of Playwright."
        else:
            system_prompt = _SYSTEM_PROMPT

            prompt = load_prompt(
                "playwright_prompt.txt",
                title=test_case.title,
                preconditions=_format_preconditions(test_case.preconditions),
                steps=_format_steps(test_case.steps),
                expected_result=test_case.expected_result,
                test_data=json.dumps(test_case.test_data, ensure_ascii=False),
                base_url=base_url,
                framework_context=framework_context_str,
                target_username=target_username or "default_username",
                target_password=target_password or "default_password"
            )
            if target_username and target_password:
                if is_login_test:
                    prompt += (
                        f"\n\nLoad the credentials securely from environment variables at the top of the test code block:\n"
                        f"const BASE_URL = process.env.TARGET_URL || '{base_url}';\n"
                        f"const USERNAME = process.env.TARGET_USERNAME || '{target_username}';\n"
                        f"const PASSWORD = process.env.TARGET_PASSWORD || '{target_password}';\n"
                        f"Use USERNAME and PASSWORD when the test steps request entering the valid username and password."
                    )
                else:
                    prompt += (
                        f"\n\nAt the start of the test, after navigating to the base URL, you MUST log in. "
                        f"Load the credentials securely from environment variables at the top of the test code block: "
                        f"const BASE_URL = process.env.TARGET_URL || '{base_url}';\n"
                        f"const USERNAME = process.env.TARGET_USERNAME || '{target_username}';\n"
                        f"const PASSWORD = process.env.TARGET_PASSWORD || '{target_password}';\n"
                        f"Navigate to BASE_URL. Type USERNAME into the username field, PASSWORD into the password field, click login, and wait for the post-login landing to load (e.g. wait for a URL change or for a dashboard element named in the steps to become visible) before executing subsequent steps."
                    )

        if test_data_template:
            prompt += (
                "\n\nThis is a reusable data-driven script template. Keep these exact "
                "markers as literal values in the generated script, including in the "
                "test title, input values, and expected-result assertion: "
                "__TEST_DATA_ID__, __TEST_TYPE__, __EXPECTED_RESULT__, and every "
                "__TEST_DATA_NN__ marker provided in test_data. Do not replace markers "
                "with examples or TODO values. The application workflow must use the "
                "provided requirement and steps; only the marked values vary by case."
            )
        if template_feedback:
            prompt += f"\n\n{template_feedback}"

        raw_output = self.llm_service.generate(
            system=system_prompt,
            user=prompt,
            timeout=120,
        )
        code = _strip_code_fences(raw_output)

        # Post-process generated code to guarantee required environment variables are defined
        # and that expect is always imported
        if framework == "playwright":
            # Fix import to always include expect
            lines = code.split("\n")
            for idx, line in enumerate(lines):
                if line.strip().startswith("import { test") and "expect" not in line:
                    lines[idx] = line.replace("import { test", "import { test, expect")
                    break
            code = "\n".join(lines)

            decl_lines = []
            if "const BASE_URL" not in code and "BASE_URL" in code:
                decl_lines.append(f"const BASE_URL = process.env.TARGET_URL || '{base_url}';")
            if "const USERNAME" not in code and "USERNAME" in code:
                decl_lines.append(f"const USERNAME = process.env.TARGET_USERNAME || '{target_username or 'default_username'}';")
            if "const PASSWORD" not in code and "PASSWORD" in code:
                decl_lines.append(f"const PASSWORD = process.env.TARGET_PASSWORD || '{target_password or 'default_password'}';")
            
            if decl_lines:
                lines = code.split("\n")
                insert_idx = 0
                for idx, line in enumerate(lines):
                    if "import " in line:
                        insert_idx = idx + 1
                lines.insert(insert_idx, "\n" + "\n".join(decl_lines) + "\n")
                code = "\n".join(lines)
        elif framework == "selenium":
            decl_lines = []
            if "BASE_URL =" not in code and "BASE_URL" in code:
                decl_lines.append(f"BASE_URL = os.environ.get('TARGET_URL', '{base_url}')")
            if "USERNAME =" not in code and "USERNAME" in code:
                decl_lines.append(f"USERNAME = os.environ.get('TARGET_USERNAME', '{target_username or 'default_username'}')")
            if "PASSWORD =" not in code and "PASSWORD" in code:
                decl_lines.append(f"PASSWORD = os.environ.get('TARGET_PASSWORD', '{target_password or 'default_password'}')")
            
            if decl_lines:
                lines = code.split("\n")
                insert_idx = 0
                for idx, line in enumerate(lines):
                    if "import " in line:
                        insert_idx = idx + 1
                lines.insert(insert_idx, "\n" + "\n".join(decl_lines) + "\n")
                code = "\n".join(lines)

        if not _looks_like_valid_code(code, framework):
            if framework == "playwright":
                raise ValueError(
                    f"{self.name}: output for '{test_case.title}' doesn't look like a "
                    f"Playwright test (missing 'test(' or 'import') - got: {code[:200]!r}"
                )
            else:
                raise ValueError(
                    f"{self.name}: output for '{test_case.title}' doesn't look like a valid "
                    f"{framework.capitalize()} script - got: {code[:200]!r}"
                )

        return code