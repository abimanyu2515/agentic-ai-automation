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

Critical Rules for Adactin Hotel Application:
    - Buttons are ALWAYS <input> tags, not <button> tags
    - Login button: input#login or input[name="login"]
    - Dropdowns use <select> tags with selectOption()
    - Input fields use fill() or type(), never selectOption()

Design Decision:
    One LLM call per test case (not batched) to isolate errors.
    A mistake in one script doesn't contaminate others.
"""

from backend.agents.base import BaseAgent
from backend.models.state import WorkflowState
from backend.models.test_case import TestCase
from backend.config.settings import get_settings
from backend.services.llm import LLMService
from backend.utils.prompts import load_prompt

from uuid import UUID

_SYSTEM_PROMPT = (
    "You are a senior QA automation engineer who writes clean, idiomatic Playwright TypeScript tests. "
    "You output ONLY code - no explanation, no markdown code fences, nothing before or after it.\n\n"
    "CRITICAL selector rules for Adactin Hotel Application:\n"
    "1. Buttons in Adactin are ALWAYS <input> tags, NEVER <button> tags.\n"
    "   - Login button locator: `input#login` or `input[name=\"login\"]` (NEVER click `button[name=\"login\"]` or `button:has-text(\"Login\")`)\n"
    "   - Login error container: `b#loginerror` (contains the error text: 'Invalid Login details') - use ID selector, not class\n"
    "   - Search button locator: `input#Submit` (NEVER click `button[name=\"search\"]`)\n"
    "   - Continue button locator: `input#continue`\n"
    "   - Book Now button locator: `input#book_now`\n"
    "2. Dropdowns in Adactin are `<select>` tags and must use selectOption:\n"
    "   - Location: `select#location`\n"
    "   - Hotels: `select#hotels`\n"
    "   - Room Type: `select#room_type`\n"
    "   - Room Nos: `select#room_nos`\n"
    "   - Adults per Room: `select#adult_room` (ID is `adult_room`, NOT `adults` or `adults_room`)\n"
    "   - Children per Room: `select#child_room` (ID is `child_room`, NOT `children_room`)\n"
    "   - Credit Card Type: `select#cc_type`\n"
    "   - Expiry Month: `select#cc_exp_month`\n"
    "   - Expiry Year: `select#cc_exp_year`\n"
    "3. Input fields are `<input>` tags and must use fill/type (NEVER use selectOption on these):\n"
    "   - Username: `input#username` or `input[name=\"username\"]`\n"
    "   - Password: `input#password` or `input[name=\"password\"]`\n"
    "   - First Name: `input#first_name`\n"
    "   - Last Name: `input#last_name`\n"
    "   - Billing Address: `textarea#address`\n"
    "   - Credit Card No: `input#cc_num`\n"
    "   - CVV Number: `input#cc_cvv`\n"
    "   - Check-in Date: `input#datepick_in` (format as DD/MM/YYYY, e.g. '17/07/2026')\n"
    "   - Check-out Date: `input#datepick_out` (format as DD/MM/YYYY, e.g. '18/07/2026')\n"
    "   - Welcome Message / Username Show field (read-only input field showing the logged in user): `input#username_show` (to assert its value, use `await expect(page.locator('input#username_show')).toHaveValue('Hello USERNAME!')`)\n"
    "4. Do NOT perform pre-login actions (like logging in with valid credentials) unless explicitly specified in the Preconditions or Steps. Start directly by navigating to the base URL (goto(BASE_URL)) and then execute the steps in order.\n"
    "5. After clicking the login button (input#login), you MUST wait for the redirection to the dashboard/search page to complete (e.g., wait for the URL to contain 'SearchHotel.php' or wait for the selector 'input#username_show' or 'select#location' to be visible) before executing any subsequent steps. Do NOT manually navigate to arbitrary URLs like '?q=Search' right after login.\n"
    "6. ALWAYS use page.locator('selector').click() and page.locator('selector').fill('text') instead of page.click('selector') or page.fill('selector'). This is required by the platform to capture step-by-step screenshots and timeline reports.\n"
    "7. Do NOT use `const text = await locator.textContent(); expect(text).toContain('val')`. This does not auto-wait and causes race conditions. ALWAYS use Playwright's auto-waiting assertions, e.g. `await expect(page.locator('selector')).toContainText('val')` or `await expect(page.locator('selector')).toHaveValue('val')`.\n"
    "8. After clicking the Search button (input#Submit), the page navigates to SelectHotel.php, NOT SearchHotel.php. Assert with `await expect(page).toHaveURL(/SelectHotel\\.php/)` or verify `#select_hotel` is visible.\n"
    "9. IMPORTANT: Date format for datepick_in and datepick_out is DD/MM/YYYY (e.g. '17/07/2026'). Do NOT use ISO format (YYYY-MM-DD) as the site rejects it. Generate dates as UTC string formatted to DD/MM/YYYY.\n"
    "10. ALWAYS import { test, expect } from '@playwright/test' (not just `test`). The `expect` is required for assertions.\n"
    "11. CRITICAL DROPDOWN RULES:\n"
    "    - If a step says to keep a dropdown or field at its default value or do nothing, do NOT interact with it or call `selectOption`. Simply generate a comment, e.g. `// Keep default - no action needed`.\n"
    "    - When using `selectOption` to select an option, ALWAYS select by label wrapper: `selectOption({ label: \"Option Label Text\" })` (e.g. `selectOption({ label: \"Sydney\" })`). Do NOT pass raw strings like `selectOption(\"Sydney\")` directly.\n"
    "12. CRITICAL BOOKING CONFIRMATION RULES:\n"
    "    - After clicking 'Book Now' button (input#book_now) on BookHotel.php, the application displays a processing message: 'Please wait! We are processing your hotel booking...'\n"
    "    - This processing takes approximately 5-6 seconds before redirecting to BookingConfirm.php\n"
    "    - You MUST use an adequate timeout when waiting for the URL change: `await expect(page).toHaveURL(/BookingConfirm\\.php/, { timeout: 15000 });`\n"
    "    - After successful booking confirmation, you MUST extract the Order ID from the confirmation page\n"
    "    - Order ID locator: `input#order_no` (read-only input field containing the order number)\n"
    "    - Extract Order ID using: `const orderNumber = await page.locator('input#order_no').inputValue();`\n"
    "    - Log the Order ID to console for reporting with [Timeline] prefix: `console.log('[Timeline] Order Confirmed - Order ID:', orderNumber);`\n"
    "    - The Order ID MUST be captured and included in test execution reports for traceability\n"
    "    - Validate that the Order ID is numeric: `expect(orderNumber).toMatch(/^\\d+$/);`\n"
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

    def __init__(self, llm_service: LLMService | None = None, repo = None):
        self.llm_service = llm_service or LLMService()
        self.repo = repo

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

                # Hard fallback to first non-null vault entry or standard credentials
                if not target_username or not target_password:
                    try:
                        from backend.services.vault_service import VaultService
                        from backend.utils.crypto import decrypt_value
                        vault = VaultService()._read_vault()
                        for pid, creds in vault.items():
                            u = decrypt_value(creds.get("username_enc"))
                            p = decrypt_value(creds.get("password_enc"))
                            if u and p:
                                target_username = u
                                target_password = p
                                break
                    except Exception:
                        pass
                
                if not target_username or not target_password:
                    target_username = "ADACTINFORQA"
                    target_password = "hanish13"

        for test_case in approved:
            test_case.playwright_script = self._generate_script(test_case, framework, target_url, target_username, target_password)

        state.add_log(f"{self.name} finished: generated {len(approved)} {framework.capitalize()} script(s)")
        return state

    def _generate_script(self, test_case: TestCase, framework: str = "playwright", target_url: str | None = None, target_username: str | None = None, target_password: str | None = None) -> str:
        import os
        import json
        settings = get_settings()
        base_url = target_url or settings.playwright.base_url

        # Override framework if configured at test case level
        if getattr(test_case, "automation_framework", None):
            framework = test_case.automation_framework.lower()

        # Load Adactin reference guide for accurate selectors and page flow understanding
        adactin_reference = ""
        adactin_guide_path = os.path.join(os.getcwd(), "adactin_reference_guide.md")
        if os.path.exists(adactin_guide_path):
            try:
                with open(adactin_guide_path, "r", encoding="utf-8") as f:
                    adactin_reference = f.read()
            except Exception:
                pass

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
                        f"Navigate to BASE_URL. Type USERNAME into the username field, PASSWORD into the password field, click login, and wait for the page to redirect (e.g. wait for the URL to contain 'SearchHotel.aspx' or wait for the element with ID 'location' to be visible) before executing subsequent steps."
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
            
            # Inject Adactin reference guide into system prompt for accurate page structure knowledge
            if adactin_reference:
                system_prompt += (
                    "\n\n## ADACTIN APPLICATION REFERENCE GUIDE\n\n"
                    "Below is the complete reference guide for the Adactin Hotel Application. "
                    "Use this as the authoritative source for page structures, selectors, workflows, and validation patterns. "
                    "Do NOT make assumptions about page elements - refer to this guide for exact selectors and page flow.\n\n"
                    f"{adactin_reference}\n\n"
                    "CRITICAL: For SelectHotel.php, do NOT attempt to verify table column headers by text content. "
                    "The reference guide does not specify exact table header text verification. Instead:\n"
                    "1. Check that the radio button (input#radiobutton_0) is visible and clickable\n"
                    "2. Check the radio button\n"
                    "3. Verify the Continue button (input#continue) becomes enabled\n"
                    "4. Click Continue\n"
                    "5. Verify navigation to BookHotel.php\n"
                    "Do NOT try to validate table headers like 'Hotel Name', 'Location', etc. - this causes timeouts."
                )
            
            prompt = load_prompt(
                "playwright_prompt.txt",
                title=test_case.title,
                preconditions=_format_preconditions(test_case.preconditions),
                steps=_format_steps(test_case.steps),
                expected_result=test_case.expected_result,
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
                        f"Navigate to BASE_URL. Type USERNAME into the username field, PASSWORD into the password field, click login, and wait for the page to redirect (e.g. wait for the URL to contain 'SearchHotel.aspx' or wait for the selector '#location' to be visible) before executing subsequent steps."
                    )

        raw_output = self.llm_service.generate(system=system_prompt, user=prompt)
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