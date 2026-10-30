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
from typing import Callable
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
    "2. Live page evidence, when provided, covers only the pages it lists (usually the pages "
    "reachable before login). On those pages derive every locator from it: prefer uniquely "
    "observed accessible locators (getByRole, getByLabel, getByPlaceholder, getByText), and use "
    "an observed CSS selector only when the evidence includes it. For pages the evidence does not "
    "list, use the accessible locator the step wording implies: getByRole with the control's "
    "visible name (e.g. getByRole('button', { name: 'Continue' })), getByLabel or getByPlaceholder "
    "with the field's name, or getByText with text quoted in the step. Every locator "
    "string must be a real selector or accessible name: never put a comment, a TODO, a "
    "description, or a Playwright method name such as 'getByLabel' inside locator(), "
    "getByText(), or any other locator call.\n"
    "3. Match the control type to the wording: `<select>` dropdowns use selectOption({ label: '...' }); "
    "text inputs use fill(); buttons/links use click(). Never call selectOption on a text input.\n"
    "4. Start every test with `await page.goto(BASE_URL)`. Each test runs in a fresh, logged-out "
    "browser, so log in first only when the preconditions or steps need an authenticated user; "
    "otherwise do not add login or other setup the case does not ask for. Then execute the steps "
    "in order.\n"
    "5. After submitting a login form, wait for the post-login landing to complete (e.g. wait for a URL "
    "change or for a dashboard/home element named in the steps to become visible) before executing "
    "subsequent steps. Do NOT navigate to arbitrary deep URLs right after login.\n"
    "6. Act through Locator objects, for example `await page.getByRole('button', { name: 'Login' }).click()`, "
    "`await page.getByLabel('Email').fill(value)`, or `await page.locator('#observed-id').click()`. "
    "Never call page.click(), page.fill(), page.type(), page.check(), or page.selectOption() with a "
    "selector string: the platform captures step screenshots only from Locator actions. "
    "page.locator() takes a CSS selector, never a getBy* call written as a string.\n"
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
    "14. Turn every step into real Playwright actions. Never write a test that fails on purpose "
    "(expect(false), an unconditional throw, test.skip, test.fixme, test.fail) or one that only "
    "navigates. When a locator is uncertain, use the best accessible locator from rule 2.\n"
    "15. When a step moves to another page, wait for something the steps, expected result, or "
    "evidence name on that page (a heading, button, or URL fragment) before the next step, so a "
    "failure points at the step that broke.\n"
)


# A whole import statement, including ones split over several lines.
_IMPORT_STATEMENT = re.compile(
    r"""^[ \t]*import\b[^;'"]*?(['"])[^'"]+\1[ \t]*;?[ \t]*$""",
    re.MULTILINE,
)

# A comment or string literal. One left to right scan keeps a quote inside a
# comment, or a // inside a string, from being misread.
_JS_COMMENT_OR_STRING = re.compile(
    r"//[^\n]*|/\*[\s\S]*?\*/"
    r"|'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"|`(?:[^`\\]|\\.)*`"
)

# Loop variable name and leading comment for each dataset outcome group.
_DATASET_GROUPS = {
    "case": ("datasets", "Datasets that follow the test case's expected result"),
    "accepted": ("acceptedDatasets", "Datasets the application should accept"),
    "rejected": ("rejectedDatasets", "Datasets the application should reject"),
}


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


def _normalize_playwright_code(code: str, base_url: str) -> str:
    """Guarantee the @playwright/test import includes expect and BASE_URL is declared."""
    # Models sometimes drop the import line; it is always the same one.
    if "test(" in code and "@playwright/test" not in code:
        code = "import { test, expect } from '@playwright/test';\n\n" + code
    # Fix import to always include expect
    lines = code.split("\n")
    for idx, line in enumerate(lines):
        if line.strip().startswith("import { test") and "expect" not in line:
            lines[idx] = line.replace("import { test", "import { test, expect")
            break
    code = "\n".join(lines)

    if "const BASE_URL" not in code and "BASE_URL" in code:
        lines = code.split("\n")
        insert_idx = 0
        for idx, line in enumerate(lines):
            if "import " in line:
                insert_idx = idx + 1
        lines.insert(
            insert_idx,
            f"\nconst BASE_URL = process.env.TARGET_URL || {json.dumps(base_url)};\n",
        )
        code = "\n".join(lines)
    return code


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
        template_recorder: Callable[..., str] | None = None,
    ):
        """template_recorder, when given, records each multi-dataset template
        on the live target app (see script_generator_agent's
        generate_data_driven_template). Without it, or when recording fails,
        templates come from one LLM call grounded on state.dom_context."""
        self.llm_service = llm_service or LLMService()
        self.repo = repo
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self.max_workers = max_workers
        self.test_data_template = test_data_template
        self.template_recorder = template_recorder

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
                feature_context=self._feature_context(state.requirement, test_case),
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
        feature_context = self._feature_context(state.requirement, template_case)
        template_script = self._generate_script(
            template_case,
            framework,
            target_url,
            test_data_template=True,
            dom_context=state.dom_context,
            repair_feedback=state.repair_feedback,
            feature_context=feature_context,
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
                feature_context=feature_context,
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

    def _record_template(
        self,
        template_case: TestCase,
        markers: dict[str, str],
        sample_data: dict[str, str],
        target_url: str | None,
        state: WorkflowState,
    ) -> str | None:
        """Template recorded on the live app with one dataset, or None to fall back."""
        if self.template_recorder is None or not target_url:
            return None
        values = {markers[key]: str(value) for key, value in sample_data.items()}
        try:
            script = self.template_recorder(
                target_url=target_url,
                steps=[
                    self._apply_template_values(step, values)
                    for step in template_case.steps
                ],
                test_title=template_case.title,
                expected_result=template_case.expected_result,
                sample_data={key: str(value) for key, value in sample_data.items()},
                markers={key: markers[key] for key in sample_data},
                preconditions=[
                    self._apply_template_values(item, values)
                    for item in template_case.preconditions
                ],
                repair_feedback=state.repair_feedback,
            )
        except Exception as exc:
            state.add_log(
                f"{self.name}: live template recording failed ({type(exc).__name__}); "
                "using page evidence instead"
            )
            return None

        script = _normalize_playwright_code(_strip_code_fences(script), target_url)
        missing_markers = [
            marker for marker in markers.values() if marker not in script
        ]
        if missing_markers or not _looks_like_valid_code(script, "playwright"):
            state.add_log(
                f"{self.name}: live recorded template was incomplete; "
                "using page evidence instead"
            )
            return None
        state.add_log(f"{self.name}: recorded template on the live target app")
        return script

    def _generate_multi_dataset_script(
        self,
        test_case: TestCase,
        framework: str,
        target_url: str | None,
        state: WorkflowState,
    ) -> str:
        """Run one test case once per dataset, as a loop over a dataset array.

        Datasets the application should treat differently from the test
        case's own expected result (a valid row in a negative case, or the
        reverse) get their own template and loop, so each test asserts its
        real outcome.
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

        templates: dict[str, str] = {}
        rows: dict[str, list[dict]] = {}
        markers: dict[str, str] = {}
        for index, (dataset, profile) in enumerate(zip(datasets, profiles), start=1):
            outcome = profile.get("expected_outcome") or case_outcome
            group = "case" if outcome == case_outcome else outcome
            if group not in templates:
                expected_result = (
                    test_case.expected_result
                    if group == "case"
                    else self._outcome_expected_result(outcome)
                )
                template_case, markers = self._dataset_template_case(
                    test_case, expected_result
                )
                template_script = self._record_template(
                    template_case, markers, dataset, target_url, state
                ) or self._generate_template(
                    template_case, markers, framework, target_url, state
                )
                templates[group] = self._apply_template_values(
                    template_script,
                    {"__EXPECTED_RESULT__": self._escape_js_string_content(expected_result)},
                )
                rows[group] = []
            rows[group].append({
                "id": f"TD-{test_case.id.hex[:8].upper()}-{index:02d}",
                "type": profile.get("test_type") or scenario.path_type,
                "data": {key: str(value) for key, value in dataset.items()},
            })
        if len(templates) > 1:
            state.add_log(
                f"{self.name}: used separate templates for accepted and rejected "
                f"datasets of '{test_case.title}'"
            )

        expressions = {"__TEST_DATA_ID__": "row.id", "__TEST_TYPE__": "row.type"}
        expressions.update({
            markers[key]: f"row.data[{json.dumps(key)}]"
            for key in test_case.test_data
        })
        return self._data_driven_loop_script([
            (*_DATASET_GROUPS[group], templates[group], expressions, rows[group])
            for group in templates
        ])

    def _feature_context(self, requirement, test_case: TestCase) -> str:
        """Requirement and scenario summary, so the model knows the feature under test."""
        lines = []
        if requirement is not None:
            lines.append(f"Requirement: {requirement.title}")
            lines.append(f"Description: {requirement.description[:1500]}")
            for label, items in (
                ("Acceptance criteria", requirement.acceptance_criteria),
                ("Business rules", requirement.business_rules),
            ):
                if items:
                    lines.append(f"{label}:")
                    lines.extend(f"- {item}" for item in items[:10])
        scenario = (
            self.repo.get_scenario(test_case.scenario_id)
            if self.repo else None
        )
        if scenario is not None:
            lines.append(f"Scenario: {scenario.scenario_name} ({scenario.path_type})")
            lines.append(f"Scenario description: {scenario.description[:800]}")
        return "\n".join(lines)

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

    @classmethod
    def _data_driven_loop_script(
        cls,
        groups: list[tuple[str, str, str, dict[str, str], list[dict]]],
    ) -> str:
        """Combine templates into one spec file that loops over each dataset array.

        `groups` holds (array name, comment, template, marker expressions,
        rows). Each iteration runs inside its own test.describe(row.id) block,
        so top level declarations such as `const BASE_URL`, and any hooks,
        stay scoped to one dataset.
        """
        imports: list[str] = []
        sections = []
        for name, comment, template_script, expressions, rows in groups:
            for match in _IMPORT_STATEMENT.finditer(template_script):
                statement = " ".join(match.group(0).split())
                if statement not in imports:
                    imports.append(statement)
            body = cls._bind_markers_to_row(
                _IMPORT_STATEMENT.sub("", template_script).strip(),
                expressions,
            )
            # Indenting would change the text of a multi-line template literal.
            if not any(
                token.startswith("`") and "\n" in token
                for token in _JS_COMMENT_OR_STRING.findall(body)
            ):
                body = "\n".join(
                    f"    {line}" if line.strip() else "" for line in body.split("\n")
                )
            row_lines = "\n".join(f"  {cls._js_literal(row)}," for row in rows)
            sections.append(
                f"// {comment}\n"
                f"const {name} = [\n{row_lines}\n];\n\n"
                f"for (const row of {name}) {{\n"
                f"  test.describe(row.id, () => {{\n{body}\n  }});\n"
                "}"
            )
        return "\n".join(imports) + "\n\n" + "\n\n".join(sections) + "\n"

    @staticmethod
    def _bind_markers_to_row(script: str, expressions: dict[str, str]) -> str:
        """Replace template markers with expressions that read the loop's row.

        A string that is exactly one marker becomes the expression. A string
        that holds a marker among other text becomes a template literal.
        """
        if not expressions:
            return script
        marker_pattern = re.compile(
            "|".join(re.escape(marker) for marker in sorted(expressions, key=len, reverse=True))
        )

        def bind(match: re.Match) -> str:
            token = match.group(0)
            if not marker_pattern.search(token):
                return token
            if token.startswith(("//", "/*")):
                return marker_pattern.sub(lambda m: expressions[m.group(0)], token)
            content = token[1:-1]
            if token[0] != "`":
                if content in expressions:
                    return expressions[content]
                content = content.replace("`", "\\`").replace("${", "\\${")
            return "`" + marker_pattern.sub(
                lambda m: "${" + expressions[m.group(0)] + "}", content
            ) + "`"

        script = _JS_COMMENT_OR_STRING.sub(bind, script)
        # Any marker left sits in code, outside a string.
        return marker_pattern.sub(lambda m: expressions[m.group(0)], script)

    @staticmethod
    def _js_literal(value) -> str:
        return (
            json.dumps(value, ensure_ascii=False)
            .replace(" ", "\\u2028")
            .replace(" ", "\\u2029")
        )

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
        feature_context: str | None = None,
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
                feature_context=feature_context or "None specified",
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
                feature_context=feature_context or "None specified",
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
                feature_context=feature_context or "None specified",
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
            "Every test runs in a fresh, logged-out browser with no saved session. When the "
            "preconditions or steps need an authenticated user (including wording such as "
            "'logged in' or 'existing session'), start by logging in on the login page with "
            "those environment variables, wait for the post-login page, then follow the steps. "
            "Never navigate straight to a page behind the login. Do not add a login step when "
            "the case does not need an authenticated user."
        )
        if dom_context is not None:
            prompt += (
                "\n\nLIVE PAGE EVIDENCE (binding for the pages listed below; other pages were "
                "not inspected):\n"
                + json.dumps(dom_context, ensure_ascii=False, separators=(",", ":"))
                + "\nOn the listed pages use only uniquely observed labels, accessible names, "
                "visible text, and selectors. On pages that are not listed, follow rule 2. "
                "Assert the stated expected_result with an observed visible result or an "
                "outcome explicitly named by the test case."
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
            timeout=180,
        )
        code = _strip_code_fences(raw_output)

        # Post-process generated code to guarantee required environment variables are defined
        # and that expect is always imported
        if framework == "playwright":
            code = _normalize_playwright_code(code, base_url)
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