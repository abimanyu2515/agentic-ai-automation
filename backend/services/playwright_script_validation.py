from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


_ROLE_LOCATOR = re.compile(
    r"""\.getByRole\(\s*(['"])(.*?)\1\s*,\s*\{\s*name\s*:\s*(['"])(.*?)\3""",
    re.DOTALL,
)
_STRING_LOCATORS = {
    "label": re.compile(r"""\.getByLabel\(\s*(['"])(.*?)\1""", re.DOTALL),
    "placeholder": re.compile(r"""\.getByPlaceholder\(\s*(['"])(.*?)\1""", re.DOTALL),
    "text": re.compile(r"""\.getByText\(\s*(['"])(.*?)\1""", re.DOTALL),
    "selector": re.compile(r"""\.locator\(\s*(['"])(.*?)\1""", re.DOTALL),
    "test id": re.compile(r"""\.getByTestId\(\s*(['"])(.*?)\1""", re.DOTALL),
}


def validate_playwright_script(
    script: str,
    dom_context: dict[str, Any],
    expected_result: str | None = None,
) -> list[str]:
    issues = _compile_typescript(script)
    issues.extend(_validate_locators(script, dom_context))
    issues.extend(_validate_assertions(script, dom_context, expected_result))
    return issues


def _compile_typescript(script: str) -> list[str]:
    workspace = Path(__file__).resolve().parents[1] / "playwrightt"
    compiler = workspace / "node_modules" / "typescript" / "lib" / "tsc.js"
    node = shutil.which("node")
    if not node or not compiler.is_file():
        raise RuntimeError("The Playwright workspace TypeScript compiler is unavailable")

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".spec.ts",
        prefix=".script-validation-",
        dir=workspace,
        delete=False,
    ) as source_file:
        source_file.write(script)
        source_path = Path(source_file.name)

    try:
        result = subprocess.run(
            [
                node,
                str(compiler),
                "--noEmit",
                "--skipLibCheck",
                "--target",
                "ES2020",
                "--module",
                "commonjs",
                "--moduleResolution",
                "node",
                "--esModuleInterop",
                "--types",
                "node",
                str(source_path),
            ],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ["TypeScript compilation timed out"]
    finally:
        source_path.unlink(missing_ok=True)

    if result.returncode == 0:
        return []
    return ["TypeScript compilation failed"]


def _validate_locators(script: str, dom_context: dict[str, Any]) -> list[str]:
    pages = dom_context.get("pages", [])
    issues: list[str] = []

    role_calls = list(re.finditer(r"\.getByRole\s*\(", script))
    role_locators = list(_ROLE_LOCATOR.finditer(script))
    if len(role_calls) != len(role_locators):
        issues.append("A role locator does not use an observed, named control")

    for match in role_locators:
        role, name = match.group(2).casefold(), match.group(4).casefold()
        observed = [
            element
            for page in pages
            for element in [
                *page.get("elements", []),
                *[
                    {
                        "role": field.get("role"),
                        "name": field.get("label"),
                    }
                    for form in page.get("forms", [])
                    for field in form.get("fields", [])
                ],
            ]
            if str(element.get("role", "")).casefold() == role
            and str(element.get("name", "")).strip().casefold() == name
        ]
        if len(observed) != 1:
            issues.append(f"Role locator {role} named {name!r} is not uniquely observed")

    fields = [
        field
        for page in pages
        for form in page.get("forms", [])
        for field in form.get("fields", [])
    ]
    elements = [
        element
        for page in pages
        for element in page.get("elements", [])
    ]
    method_names = {
        "label": "getByLabel",
        "placeholder": "getByPlaceholder",
        "text": "getByText",
        "selector": "locator",
        "test id": "getByTestId",
    }
    for kind, pattern in _STRING_LOCATORS.items():
        calls = list(
            re.finditer(rf"\.{method_names[kind]}\s*\(", script)
        )
        matches = list(pattern.finditer(script))
        if len(calls) != len(matches):
            issues.append(f"A {kind} locator is not a literal value")

        for match in matches:
            value = match.group(2).strip()
            if kind == "label":
                count = sum(
                    str(field.get("label", "")).strip().casefold() == value.casefold()
                    for field in fields
                )
            elif kind == "placeholder":
                count = sum(
                    str(field.get("placeholder", "")).strip().casefold() == value.casefold()
                    for field in fields
                )
            elif kind == "text":
                count = sum(
                    str(element.get("name", "")).strip().casefold() == value.casefold()
                    for element in elements
                ) + sum(
                    value.casefold() in {
                        str(page.get("title", "")).strip().casefold(),
                        *(str(text).strip().casefold() for text in page.get("visible_text", [])),
                    }
                    for page in pages
                )
            elif kind == "selector":
                count = sum(
                    selector == value and match_count == 1
                    for selector, match_count in set(_observed_selectors(dom_context))
                )
            else:
                selector = f'[data-testid="{value}"]'
                count = sum(
                    selector == observed and match_count == 1
                    for observed, match_count in set(_observed_selectors(dom_context))
                )
            if count != 1:
                issues.append(f"{kind.title()} locator {value!r} is not uniquely observed")

    return issues


def _validate_assertions(
    script: str,
    dom_context: dict[str, Any],
    expected_result: str | None,
) -> list[str]:
    issues: list[str] = []
    assertion_calls = list(
        re.finditer(
            r"\bawait\s+expect(?:\.soft)?\s*\([\s\S]*?\)\s*\.to[A-Z]\w*\s*\(",
            script,
        )
    )
    if not assertion_calls:
        return ["Script does not assert an expected outcome"]

    if expected_result:
        expected_literals = re.findall(r"""['"]([^'"]{3,})['"]""", expected_result)
        if expected_literals:
            assertion_text = "\n".join(
                script[match.start():script.find(";", match.end()) if ";" in script[match.end():] else len(script)]
                for match in assertion_calls
            )
            if not any(
                literal.casefold() in assertion_text.casefold()
                for literal in expected_literals
            ):
                issues.append("Script assertions do not reference an expected result phrase")
    return issues


def _observed_selectors(dom_context: dict[str, Any]) -> list[tuple[str, int]]:
    selectors: list[tuple[str, int]] = []
    for page in dom_context.get("pages", []):
        for element in page.get("elements", []) + page.get("navigation", []):
            selector = element.get("selector")
            if selector:
                selectors.append((str(selector), int(element.get("match_count", 0))))
        for form in page.get("forms", []):
            for selector, match_count in [
                (form.get("selector"), form.get("match_count", 0)),
                (
                    (form.get("submit_button") or {}).get("selector"),
                    (form.get("submit_button") or {}).get("match_count", 0),
                ),
            ]:
                if selector:
                    selectors.append((str(selector), int(match_count)))
            for field in form.get("fields", []):
                selector = field.get("selector")
                if selector:
                    selectors.append((str(selector), int(field.get("match_count", 0))))
    return selectors
