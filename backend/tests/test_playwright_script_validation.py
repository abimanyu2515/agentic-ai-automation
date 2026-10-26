from backend.services.playwright_script_validation import validate_playwright_script


_DOM_CONTEXT = {
    "pages": [
        {
            "title": "Accounts",
            "forms": [
                {
                    "fields": [
                        {
                            "label": "Email",
                            "placeholder": "",
                            "selector": '[name="email"]',
                            "match_count": 1,
                        }
                    ]
                }
            ],
            "elements": [
                {
                    "role": "button",
                    "name": "Save",
                    "selector": '[data-testid="save"]',
                    "match_count": 1,
                },
                {
                    "role": "alert",
                    "name": "Account saved",
                    "selector": '[role="alert"]',
                    "match_count": 1,
                },
            ],
        }
    ]
}


def test_compiled_script_with_uniquely_observed_locators_passes():
    script = """import { test, expect } from "@playwright/test";

test("save account", async ({ page }) => {
  await page.getByLabel("Email").fill("qa@example.test");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Account saved")).toBeVisible();
});
"""

    assert validate_playwright_script(script, _DOM_CONTEXT) == []


def test_script_with_unobserved_locator_fails_validation():
    script = """import { test, expect } from "@playwright/test";

test("delete account", async ({ page }) => {
  await page.getByRole("button", { name: "Delete" }).click();
});
"""

    issues = validate_playwright_script(script, _DOM_CONTEXT)
    assert "Role locator button named 'delete' is not uniquely observed" in issues
    assert "Script does not assert an expected outcome" in issues


def test_expected_result_phrase_must_be_asserted():
    script = """import { test, expect } from "@playwright/test";

test("save account", async ({ page }) => {
  await expect(page.getByText("Accounts")).toBeVisible();
});
"""

    issues = validate_playwright_script(
        script,
        _DOM_CONTEXT,
        expected_result='The "Account saved" confirmation appears.',
    )

    assert "Script assertions do not reference an expected result phrase" in issues


def test_invalid_typescript_fails_validation():
    assert "TypeScript compilation failed" in validate_playwright_script("test(", _DOM_CONTEXT)


def test_getByPlaceholder_locator_validates_against_observed_fields():
    script = """import { test, expect } from "@playwright/test";

test("fill form", async ({ page }) => {
  await page.getByPlaceholder("Enter email").fill("test@example.com");
  await expect(page.getByText("Accounts")).toBeVisible();
});
"""
    assert "Placeholder locator 'Enter email' is not uniquely observed" in (
        validate_playwright_script(script, _DOM_CONTEXT)
    )


def test_getByText_locator_validates_against_elements_and_page_title():
    script = """import { test, expect } from "@playwright/test";

test("check text", async ({ page }) => {
  await expect(page.getByText("Accounts")).toBeVisible();
});
"""
    assert validate_playwright_script(script, _DOM_CONTEXT) == []


def test_getByTestId_locator_validates_against_observed_selectors():
    script = """import { test, expect } from "@playwright/test";

test("click save", async ({ page }) => {
  await page.getByTestId("save").click();
  await expect(page.getByRole("button", { name: "Save" })).toBeVisible();
});
"""
    assert validate_playwright_script(script, _DOM_CONTEXT) == []


def test_locator_without_literal_value_fails():
    """Dynamic locator values (variables) should fail validation."""
    script = """import { test, expect } from "@playwright/test";

test("dynamic locator", async ({ page }) => {
  const btn = "Save";
  await page.getByRole("button", { name: btn }).click();
});
"""
    issues = validate_playwright_script(script, _DOM_CONTEXT)
    # The regex for role locator checks for literal string - variable usage will fail
    # The issue is that the role locator pattern expects a literal string, not a variable
    assert any("A role locator does not use an observed, named control" in issue or "not a literal value" in issue for issue in issues)


def test_selector_locator_matches_observed_selectors():
    script = """import { test, expect } from "@playwright/test";

test("click via selector", async ({ page }) => {
  await page.locator('[data-testid="save"]').click();
  await expect(page.getByRole("button", { name: "Save" })).toBeVisible();
});
"""
    assert validate_playwright_script(script, _DOM_CONTEXT) == []


def test_multiple_pages_in_dom_context():
    """Validation should check locators across all pages in dom_context."""
    multi_page_context = {
        "pages": [
            {
                "title": "Login",
                "forms": [],
                "elements": [{"role": "button", "name": "Login", "selector": '[name="login"]', "match_count": 1}],
            },
            {
                "title": "Dashboard",
                "forms": [],
                "elements": [{"role": "button", "name": "Logout", "selector": '[name="logout"]', "match_count": 1}],
            },
        ]
    }
    script = """import { test, expect } from "@playwright/test";

test("login then logout", async ({ page }) => {
  await page.getByRole("button", { name: "Login" }).click();
  await page.getByRole("button", { name: "Logout" }).click();
  await expect(page.getByRole("button", { name: "Logout" })).toBeVisible();
});
"""
    assert validate_playwright_script(script, multi_page_context) == []


def test_form_fields_are_validated():
    """Form field locators (getByLabel) should be validated against observed form fields."""
    script = """import { test, expect } from "@playwright/test";

test("fill email", async ({ page }) => {
  await page.getByLabel("Email").fill("test@example.com");
  await expect(page.getByLabel("Email")).toBeVisible();
});
"""
    assert validate_playwright_script(script, _DOM_CONTEXT) == []


def test_password_fields_not_in_dom_context_does_not_leak():
    """Password fields should not be included in dom_context for security."""
    # This is a design requirement - password fields should be filtered out
    # The validation function should not crash if they're missing
    context_without_password = {
        "pages": [
            {
                "title": "Login",
                "forms": [
                    {
                        "fields": [
                            {"label": "Username", "selector": '[name="username"]', "match_count": 1}
                        ]
                    }
                ],
                "elements": [],
            }
        ]
    }
    script = """import { test, expect } from "@playwright/test";

test("login", async ({ page }) => {
  await page.getByLabel("Username").fill("user");
  await expect(page.getByLabel("Username")).toBeVisible();
});
"""
    assert validate_playwright_script(script, context_without_password) == []
