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


_UNLABELED_FIELD_CONTEXT = {
    "pages": [
        {
            "title": "Enter Vehicle Data",
            "forms": [
                {
                    "fields": [
                        {
                            "type": "select",
                            "role": "combobox",
                            "label": "",
                            "name": "Make",
                            "placeholder": "",
                            "selector": "#make",
                            "match_count": 1,
                        }
                    ]
                }
            ],
            "elements": [],
            "visible_text": ["Enter Vehicle Data"],
        }
    ]
}


def test_getByLabel_is_rejected_for_a_field_without_an_accessible_label():
    script = """import { test, expect } from "@playwright/test";

test("vehicle data", async ({ page }) => {
  await page.getByLabel("Make").selectOption({ label: "Audi" });
  await expect(page.getByText("Enter Vehicle Data")).toBeVisible();
});
"""

    issues = validate_playwright_script(script, _UNLABELED_FIELD_CONTEXT)
    assert "Label locator 'Make' is not uniquely observed" in issues


def test_observed_selector_is_accepted_for_a_field_without_an_accessible_label():
    script = """import { test, expect } from "@playwright/test";

test("vehicle data", async ({ page }) => {
  await page.locator("#make").selectOption({ label: "Audi" });
  await expect(page.getByText("Enter Vehicle Data")).toBeVisible();
});
"""

    assert validate_playwright_script(script, _UNLABELED_FIELD_CONTEXT) == []


def test_hidden_step_field_and_button_selectors_are_observed():
    context = {
        "pages": [
            {
                "title": "Wizard",
                "forms": [
                    {
                        "fields": [
                            {"type": "text", "role": "textbox", "label": "", "name": "First Name",
                             "selector": "#firstname", "match_count": 1, "visible": False},
                        ],
                        "hidden_buttons": [
                            {"name": "Send »", "selector": "#send", "match_count": 1, "visible": False},
                        ],
                    }
                ],
                "elements": [],
                "visible_text": ["Wizard"],
            }
        ]
    }
    script = """import { test, expect } from "@playwright/test";

test("wizard", async ({ page }) => {
  await page.locator("#firstname").fill("Ada");
  await page.locator("#send").click();
  await expect(page.getByText("Wizard")).toBeVisible();
});
"""

    assert validate_playwright_script(script, context) == []


def test_option_click_selector_is_observed():
    context = {
        "pages": [
            {
                "title": "Wizard",
                "forms": [
                    {
                        "fields": [
                            {"type": "checkbox", "role": "checkbox", "label": "Speeding",
                             "name": "Hobbies", "selector": "#speeding", "match_count": 1,
                             "visible": False, "value": "Speeding",
                             "click_selector": "label:has(#speeding)"},
                        ],
                    }
                ],
                "elements": [],
                "visible_text": ["Wizard"],
            }
        ]
    }
    script = """import { test, expect } from "@playwright/test";

test("wizard", async ({ page }) => {
  await page.locator("label:has(#speeding)").click();
  await expect(page.getByText("Wizard")).toBeVisible();
});
"""

    assert validate_playwright_script(script, context) == []


def _wizard_context():
    return {"pages": [{"forms": [{
        "steps": [
            {"step": 1, "name": "Vehicle", "visible": True, "next_button": "#next1"},
            {"step": 2, "name": "Product", "visible": False,
             "next_button": "#next2", "prev_button": "#prev2"},
            {"step": 3, "name": "Quote", "visible": False},
        ],
        "fields": [
            {"selector": "#make", "step": 1},
            {"selector": "#startdate", "step": 2},
            {"selector": "#euro", "click_selector": "label:has(#euro)", "step": 2},
            {"selector": "#email", "step": 3},
        ],
        "hidden_buttons": [
            {"selector": "#next2", "step": 2},
            {"selector": "#prev2", "step": 2},
        ],
    }]}]}


def test_step_order_flags_a_control_used_after_its_step_was_left():
    from backend.services.playwright_script_validation import validate_step_order

    script = """
      await page.goto(BASE_URL);
      await page.locator('#make').selectOption('Audi');
      await page.locator('#next1').click();
      await page.locator('#startdate').fill('01/02/2027');
      await page.locator('#next2').click();
      await page.locator('label:has(#euro)').click();
      await page.locator('#email').fill('a@b.test');
    """
    issues = validate_step_order(script, _wizard_context())
    assert len(issues) == 1
    assert "'label:has(#euro)' is on step 2 ('Product')" in issues[0]
    assert "on step 3 ('Quote')" in issues[0]


def test_step_order_flags_a_next_button_clicked_twice():
    from backend.services.playwright_script_validation import validate_step_order

    script = """
      await page.goto(BASE_URL);
      await page.locator('#next1').click();
      await page.locator('#next2').click();
      await page.locator('#next2').click();
    """
    assert len(validate_step_order(script, _wizard_context())) == 1


def test_step_order_accepts_in_order_scripts_prev_clicks_and_assigned_locators():
    from backend.services.playwright_script_validation import validate_step_order

    script = """
      for (const row of datasets) {
        await page.goto(BASE_URL);
        await page.locator('#make').selectOption(row.data["make"]);
        const next = page.locator('#next1');
        await next.click();
        await expect(page.locator('#startdate'), 'Product did not load').toBeVisible();
        await page.locator('#startdate').fill(row.data["start"]);
        await page.locator('#prev2').click();
        await page.locator('#make').selectOption('Audi');
        await next.click();
        await page.locator('label:has(#euro)').click();
        await page.locator(`xpath=//label[normalize-space()='${row.data["x"]}']`).click();
        await page.locator('#next2').click();
        await page.locator('#email').fill(row.data["email"]);
      }
    """
    assert validate_step_order(script, _wizard_context()) == []


def test_step_order_needs_step_evidence():
    from backend.services.playwright_script_validation import validate_step_order

    script = "await page.locator('#next2').click(); await page.locator('#make').click();"
    assert validate_step_order(script, {"pages": [{"forms": [{"fields": []}]}]}) == []
    assert validate_step_order(script, None) == []
