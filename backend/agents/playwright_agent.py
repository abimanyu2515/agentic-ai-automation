"""
PlaywrightAgent

Purpose:
    Converts approved test cases into executable Playwright TypeScript scripts using LLM.
    Supports multi-framework generation (Playwright, Selenium, Cucumber).

Responsibilities:
    - Load Playwright script generation prompt template
    - Resolve only the explicitly selected project URL and framework
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
    - project.target_url, project.framework, and optional live page evidence

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

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import re
from uuid import UUID

from backend.agents.base import BaseAgent
from backend.models.state import WorkflowState
from backend.models.test_case import TestCase
from backend.services.llm import LLMService
from backend.utils.prompts import load_prompt

_SYSTEM_PROMPT = (
    "You are a senior QA automation engineer who writes clean, idiomatic Playwright TypeScript tests. "
    "You output ONLY code - no explanation, no markdown code fences, nothing before or after it.\n\n"
    "CRITICAL rules (apply to ANY target web application):\n"
    "1. The test case inputs, supplied test_data, selected project URL, and live page evidence are "
    "the ONLY source of truth. Use supplied test_data values for inputs; "
    "never invent test data or rely on prior knowledge of any specific "
    "demo site, and never hardcode selectors, URLs, or credentials from memory.\n"
    "2. When live page evidence is provided, derive every locator only from it: prefer uniquely "
    "observed accessible locators (getByRole, getByLabel, getByPlaceholder, getByText); use an "
    "observed CSS selector only when the evidence includes it. If a locator is absent, emit a "
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
    "13. Every locator you act on or assert must match exactly ONE element (Playwright strict "
    "mode). A class or text that can repeat (list items, cards, images, prices) must be narrowed "
    "with a more specific selector, a role with a name, filter({ hasText }), or .first().\n"
)


# A whole import statement, including ones split over several lines.
_IMPORT_STATEMENT = re.compile(
    r"""^[ \t]*import\b[^;'"]*?(['"])[^'"]+\1[ \t]*;?[ \t]*$""",
    re.MULTILINE,
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

        if self.repo and state.requirement:
            req_id = state.requirement.id
            if state.project_id is not None:
                target_proj = self.repo.get_project(state.project_id)
                if target_proj is None or req_id not in target_proj.requirements:
                    raise ValueError(
                        f"{self.name}: requirement does not belong to the selected project"
                    )
            else:
                matching_projects = [
                    project for project in self.repo.list_projects()
                    if req_id in project.requirements
                ]
                if len(matching_projects) != 1:
                    raise ValueError(
                        f"{self.name}: requirement must resolve to exactly one project"
                    )
                target_proj = matching_projects[0]

            framework = target_proj.framework or "playwright"
            target_url = target_proj.target_url
            if not target_url:
                raise ValueError(f"{self.name}: selected project has no target URL")

        if self.test_data_template:
            if framework != "playwright":
                raise ValueError(
                    "Test-data script templates are supported only for Playwright."
                )

            state.add_log(
                f"{self.name}: generating reusable templates for "
                f"{len(approved)} test-data case(s)"
            )
            expected_fields = set(approved[0].test_data)
            for test_case in approved:
                if set(test_case.test_data) != expected_fields:
                    raise ValueError(
                        f"{self.name}: test-data fields differ for '{test_case.title}'"
                    )
                if test_case.test_data_sets:
                    if len(approved) != 1:
                        raise ValueError(
                            f"{self.name}: a multi-dataset test case must be generated on its own"
                        )
                    if any(
                        set(dataset) != expected_fields
                        for dataset in test_case.test_data_sets
                    ):
                        raise ValueError(
                            f"{self.name}: test-data fields differ within "
                            f"'{test_case.title}'"
                        )

            if approved[0].test_data_sets:
                test_case = approved[0]
                test_case.playwright_script = self._generate_multi_dataset_script(
                    test_case, framework, target_url, state
                )
                state.add_log(
                    f"{self.name} finished: generated {len(test_case.test_data_sets)} "
                    f"data-driven Playwright tests for '{test_case.title}'"
                )
                return state

            template_case, markers = self._test_data_template_case(approved[0])
            template_script = self._generate_template(
                template_case, markers, framework, target_url, state
            )
            for test_case in approved:
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
                    "__EXPECTED_RESULT__": self._escape_js_string_content(
                        test_case.expected_result
                    ),
                }
                replacements.update({
                    markers[key]: self._escape_js_string_content(str(value))
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
                dom_context=state.dom_context,
                repair_feedback=state.repair_feedback,
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

    def _generate_template(
        self,
        template_case: TestCase,
        markers: dict[str, str],
        framework: str,
        target_url: str | None,
        state: WorkflowState,
    ) -> str:
        template_script = self._generate_script(
            template_case,
            framework,
            target_url,
            test_data_template=True,
            dom_context=state.dom_context,
            repair_feedback=state.repair_feedback,
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
                test_data_template=True,
                template_feedback=(
                    "Your previous script template was rejected because it omitted "
                    "these exact required markers: "
                    f"{', '.join(missing_markers)}. Regenerate the complete script "
                    "and preserve every required marker exactly as provided in the "
                    "test_data, title, and expected result. Do not omit fields."
                ),
                dom_context=state.dom_context,
                repair_feedback=state.repair_feedback,
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
        return template_script

    def _generate_multi_dataset_script(
        self,
        test_case: TestCase,
        framework: str,
        target_url: str | None,
        state: WorkflowState,
    ) -> str:
        """Expand one test case into one Playwright test per dataset.

        Datasets the application should treat differently from the test
        case's own expected result (a valid row in a negative case, or the
        reverse) get their own template, so each test asserts its real outcome.
        """
        scenario = self.repo.get_scenario(test_case.scenario_id) if self.repo else None
        if scenario is None:
            raise ValueError(
                f"{self.name}: scenario for '{test_case.title}' was not found"
            )
        datasets = test_case.test_data_sets
        profiles = (
            test_case.test_data_profiles
            if len(test_case.test_data_profiles) == len(datasets)
            else [{} for _ in datasets]
        )
        case_outcome = self._case_outcome(profiles, scenario.path_type)

        templates: dict[str, tuple[str, dict[str, str]]] = {}
        scripts = []
        for index, (dataset, profile) in enumerate(zip(datasets, profiles), start=1):
            outcome = profile.get("expected_outcome") or case_outcome
            group = "case" if outcome == case_outcome else outcome
            expected_result = (
                test_case.expected_result
                if group == "case"
                else self._outcome_expected_result(outcome)
            )
            if group not in templates:
                template_case, markers = self._dataset_template_case(
                    test_case, expected_result
                )
                templates[group] = (
                    self._generate_template(
                        template_case, markers, framework, target_url, state
                    ),
                    markers,
                )
            template_script, markers = templates[group]

            data_id = f"TD-{test_case.id.hex[:8].upper()}-{index:02d}"
            replacements = {
                "__TEST_DATA_ID__": data_id,
                "__TEST_TYPE__": profile.get("test_type") or scenario.path_type,
                "__EXPECTED_RESULT__": self._escape_js_string_content(expected_result),
            }
            replacements.update({
                markers[key]: self._escape_js_string_content(str(value))
                for key, value in dataset.items()
            })
            scripts.append(
                (data_id, self._apply_template_values(template_script, replacements))
            )
        if len(templates) > 1:
            state.add_log(
                f"{self.name}: used separate templates for accepted and rejected "
                f"datasets of '{test_case.title}'"
            )
        return self._join_data_driven_scripts(scripts)

    @staticmethod
    def _case_outcome(profiles: list[dict[str, str]], path_type: str) -> str:
        counts = Counter(
            profile.get("expected_outcome")
            for profile in profiles
            if profile.get("expected_outcome") in {"accepted", "rejected"}
        )
        if not counts:
            return ""
        if counts["accepted"] != counts["rejected"]:
            return "accepted" if counts["accepted"] > counts["rejected"] else "rejected"
        positive_path = any(term in (path_type or "").lower() for term in ("happy", "positive"))
        return "accepted" if positive_path else "rejected"

    @staticmethod
    def _outcome_expected_result(outcome: str) -> str:
        if outcome == "accepted":
            return (
                "The application accepts the supplied values: the step completes "
                "and no validation or error message is shown."
            )
        return (
            "The application rejects the supplied values: a validation or error "
            "message is shown, or the form is not submitted and the user stays on "
            "the same page."
        )

    @staticmethod
    def _dataset_template_case(
        test_case: TestCase,
        expected_result: str,
    ) -> tuple[TestCase, dict[str, str]]:
        """Template input that keeps the real steps, with data values as markers."""
        markers = {
            key: f"__TEST_DATA_{index:02d}__"
            for index, key in enumerate(test_case.test_data, start=1)
        }

        def mark(text: str) -> str:
            for key, value in test_case.test_data.items():
                value = str(value)
                if len(value.strip()) < 2:
                    continue
                for open_quote, close_quote in (('"', '"'), ("'", "'"), ("“", "”")):
                    text = text.replace(
                        f"{open_quote}{value}{close_quote}",
                        f"{open_quote}{markers[key]}{close_quote}",
                    )
            return text

        template_case = test_case.model_copy(update={
            "title": f"__TEST_DATA_ID__ [__TEST_TYPE__] {test_case.title}",
            "preconditions": [mark(item) for item in test_case.preconditions],
            "steps": [mark(item) for item in test_case.steps],
            "expected_result": expected_result,
            "test_data": {
                key: markers[key] for key in test_case.test_data
            },
        })
        markers.update({
            "__TEST_DATA_ID__": "__TEST_DATA_ID__",
            "__TEST_TYPE__": "__TEST_TYPE__",
        })
        return template_case, markers

    @staticmethod
    def _join_data_driven_scripts(scripts: list[tuple[str, str]]) -> str:
        """Combine per dataset scripts into one spec file.

        Each dataset's code is wrapped in its own test.describe block so
        top level declarations such as `const BASE_URL` stay scoped and
        do not collide across the 20 copies.
        """
        imports: list[str] = []
        blocks = []
        for data_id, script in scripts:
            for match in _IMPORT_STATEMENT.finditer(script):
                statement = " ".join(match.group(0).split())
                if statement not in imports:
                    imports.append(statement)
            body = _IMPORT_STATEMENT.sub("", script).strip()
            blocks.append(f"test.describe({json.dumps(data_id)}, () => {{\n{body}\n}});")
        return "\n".join(imports) + "\n\n" + "\n\n".join(blocks) + "\n"

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

    @staticmethod
    def _escape_js_string_content(value: str) -> str:
        return (
            json.dumps(value, ensure_ascii=False)[1:-1]
            .replace("'", "\\'")
            .replace("`", "\\`")
            .replace("${", "\\${")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029")
        )

    def _generate_script(
        self,
        test_case: TestCase,
        framework: str = "playwright",
        target_url: str | None = None,
        target_username: str | None = None,
        target_password: str | None = None,
        test_data_template: bool = False,
        template_feedback: str | None = None,
        dom_context: dict | None = None,
        repair_feedback: str | None = None,
    ) -> str:
        import os
        base_url = target_url or ""

        # Override framework if configured at test case level
        if dom_context is None and getattr(test_case, "automation_framework", None):
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
                target_username="",
                target_password=""
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
                target_username="",
                target_password=""
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
                target_username="",
                target_password=""
            )
        if test_data_template:
            expected_is_marker = "__EXPECTED_RESULT__" in test_case.expected_result
            required_markers = [
                "__TEST_DATA_ID__",
                "__TEST_TYPE__",
                *(["__EXPECTED_RESULT__"] if expected_is_marker else []),
                *test_case.test_data.values(),
            ]
            prompt += (
                "\n\nThis is a reusable data-driven script template. The platform runs it "
                "once per dataset and replaces every marker with that dataset's real value.\n"
                "- Write exactly ONE test(...) block. Do not declare any other test.\n"
                "- The test title must contain __TEST_DATA_ID__ so each generated test "
                "has a unique name.\n"
                f"- Keep these exact markers as literal string values: {', '.join(required_markers)}.\n"
                "- Wherever a test_data field is typed, selected, or checked, use its "
                "marker string, for example fill('__TEST_DATA_01__'). The steps may "
                "mention example values; always use the marker for fields listed in test_data.\n"
                "- A marker may hold an empty string to test a missing value. Still call "
                "fill() with the marker so the empty value is applied.\n"
                "- Do not branch on marker values and never replace a marker with an "
                "example or a TODO value.\n"
                "- Follow the steps in order; only the marked values vary between datasets.\n"
                + (
                    "- Use __EXPECTED_RESULT__ wherever the expected result is referenced.\n"
                    if expected_is_marker else
                    "- Assert the expected result with concrete, observable checks (URL, "
                    "visible message, field or element state). Do not assert a whole "
                    "sentence as page text unless the expected result quotes it.\n"
                )
            )
        if template_feedback:
            prompt += f"\n\n{template_feedback}"
        prompt += (
            "\n\nProject credentials are never included in this prompt. For a valid login, "
            "use process.env.TARGET_USERNAME and process.env.TARGET_PASSWORD without "
            "fallback values. For an invalid login, use only explicit test_data values. "
            "Do not add a login step unless the case asks for one."
        )
        if dom_context is not None:
            prompt += (
                "\n\nLIVE PAGE EVIDENCE (the only allowed source for locators and observed text):\n"
                + json.dumps(dom_context, ensure_ascii=False, separators=(",", ":"))
                + "\nUse only uniquely observed labels, accessible names, visible text, and selectors. "
                "Do not invent locators. Assert the stated expected_result with an observed "
                "visible result or an outcome explicitly named by the test case."
            )
        if repair_feedback:
            prompt += (
                "\n\nREPAIR FEEDBACK:\n"
                + repair_feedback[:2000]
                + "\nReturn a complete corrected script. Do not repeat the failed approach."
            )

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
                decl_lines.append(
                    f"const BASE_URL = process.env.TARGET_URL || {json.dumps(base_url)};"
                )
            
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
                decl_lines.append(
                    f"BASE_URL = os.environ.get('TARGET_URL', {json.dumps(base_url)})"
                )
            
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