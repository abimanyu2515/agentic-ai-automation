import { test, expect, type APIRequestContext, type Page } from '@playwright/test';

// Browser tests for script healing in the test case view (spec 0011, AC-14).
// Needs the backend running with the seeded development users, for example:
//   uvicorn backend.main:app --port 8000
//   OBJREPO_BASE_URL=http://127.0.0.1:8000 npx playwright test e2e/script-healing.spec.ts --project=chromium
const BASE_URL = process.env.OBJREPO_BASE_URL || 'http://127.0.0.1:8000';

test.setTimeout(120_000);

type Session = { access_token: string; refresh_token: string; user: unknown };

async function signIn(request: APIRequestContext, email: string, password: string): Promise<Session> {
  const login = await request.post(`${BASE_URL}/api/v1/auth/login`, { data: { email, password } });
  expect(login.ok()).toBeTruthy();
  return login.json();
}

// A real project and requirement. Only a failed run makes a script heal, so the
// scenario and test case lists are served with one repaired script suggestion.
async function createProjectWithRequirement(request: APIRequestContext, session: Session) {
  const headers = { Authorization: `Bearer ${session.access_token}` };
  const suffix = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  const project = await request.post(`${BASE_URL}/api/v1/projects`, {
    headers,
    data: {
      name: `Script healing e2e ${suffix}`,
      description: 'Created by the script healing browser test',
      framework: 'playwright',
      target_url: `https://scriptheal-${suffix}.example.test`,
    },
  });
  expect(project.ok()).toBeTruthy();
  const projectId = (await project.json()).id;
  const requirement = await request.post(`${BASE_URL}/api/v1/projects/${projectId}/requirements`, {
    headers,
    data: { title: 'Login', description: 'Users log in and see the dashboard', priority: 'high', business_domain: 'auth' },
  });
  expect(requirement.ok()).toBeTruthy();
  return { projectId, requirementId: (await requirement.json()).id };
}

const SCENARIO_ID = 'c0000000-0000-4000-8000-000000000011';
const TEST_CASE_ID = 'd0000000-0000-4000-8000-000000000011';
const HEAL_ID = 'e0000000-0000-4000-8000-000000000011';
const OLD_SCRIPT = [
  "test('Login works', async ({ page }) => {",
  "  const btn = await orx(page, 'login_page.login_button');",
  '  await btn.click();',
  "  await expect(page.getByText('Dashboard')).toBeVisible();",
  '});',
].join('\n');
const NEW_SCRIPT = OLD_SCRIPT.replace(
  '  await btn.click();',
  "  await btn.click();\n  await page.getByRole('button', { name: 'Confirm' }).click();",
);

async function serveScriptSuggestion(page: Page, requirementId: string) {
  const state = { status: 'suggested', decidedAt: null as string | null };
  const now = new Date().toISOString();
  const heal = () => ({
    id: HEAL_ID,
    test_case_id: TEST_CASE_ID,
    status: state.status,
    old_script: OLD_SCRIPT,
    new_script: NEW_SCRIPT,
    rca_category: 'Flaky Test / Automation Issue',
    root_cause_summary: 'A Confirm dialog now opens after login.',
    failing_step: 'Click Log in',
    reason: 'Not applied, the repaired script failed a gate: example',
    execution_id: null,
    created_at: now,
    decided_by: null,
    decided_at: state.decidedAt,
  });
  const scenario = {
    id: SCENARIO_ID, requirement_id: requirementId, scenario_ref_id: 'US01', scenario_name: 'Login',
    description: 'Log in', priority: 'high', confidence: 0.9, approved: true, rejected: false,
    generated_at: now, path_type: 'happy_path', tags: [],
  };
  const testCase = () => ({
    id: TEST_CASE_ID, scenario_id: SCENARIO_ID, test_case_ref_id: 'US01-TC01', title: 'Login works',
    preconditions: [], steps: ['Open the login page', 'Click Log in', 'See the dashboard'],
    expected_result: 'The dashboard opens', priority: 'high', status: 'failed', confidence: 0.9,
    evaluation_status: 'approved', evaluation_reason: null,
    playwright_script: state.status === 'accepted' ? NEW_SCRIPT : OLD_SCRIPT,
    script_validation_status: 'unverified', script_validation_attempts: 0, generated_at: now,
    test_data_sets: [], latest_script_heal: heal(),
  });

  await page.route(/\/api\/v1\/projects\/[^/]+\/scenarios$/, (route) => (
    route.request().method() === 'GET' ? route.fulfill({ json: [scenario] }) : route.continue()
  ));
  await page.route(/\/api\/v1\/projects\/[^/]+\/testcases$/, (route) => (
    route.request().method() === 'GET' ? route.fulfill({ json: [testCase()] }) : route.continue()
  ));
  await page.route(new RegExp(`/testcases/script-heals/${HEAL_ID}/accept$`), async (route) => {
    state.status = 'accepted';
    state.decidedAt = new Date().toISOString();
    await route.fulfill({ json: heal() });
  });
  return state;
}

async function openTestCases(page: Page, session: Session, projectId: string) {
  await page.addInitScript(({ session, projectId }) => {
    sessionStorage.setItem('access_token', session.access_token);
    sessionStorage.setItem('refresh_token', session.refresh_token);
    sessionStorage.setItem('user', JSON.stringify(session.user));
    localStorage.setItem('active_project_id', projectId);
  }, { session, projectId });
  await page.goto(`${BASE_URL}/`);
  // Startup ends with a route change of its own, so change the route only after it.
  await page.waitForFunction(
    () => (window as Window & { appInstance?: { startingUp?: boolean } }).appInstance?.startingUp === false,
    undefined,
    { timeout: 60_000 },
  );
  await page.evaluate(() => { window.location.hash = '#/testcases'; });
  const view = page.locator('#testcases-view');
  await expect(view).toHaveClass(/\bactive\b/, { timeout: 60_000 });
  return view;
}

test.describe('Script healing in the test case view (spec 0011)', () => {
  test('a project manager reviews the repaired script diff and accepts it with the keyboard (AC-14)', async ({ page, request }) => {
    const session = await signIn(request, 'pm@agenticai.com', 'pmpassword');
    const { projectId, requirementId } = await createProjectWithRequirement(request, session);
    const healState = await serveScriptSuggestion(page, requirementId);
    const view = await openTestCases(page, session, projectId);

    const row = view.getByRole('row', { name: /Login works/ }).first();
    await expect(row.getByText('Script suggestion', { exact: true })).toBeVisible({ timeout: 30_000 });
    await row.click();

    const section = view.getByRole('region', { name: 'Script healing' });
    await expect(section).toContainText('Waiting for review');
    await expect(section).toContainText('Flaky Test / Automation Issue');
    await expect(section).toContainText('A Confirm dialog now opens after login.');
    await expect(section.getByRole('figure', { name: 'New script' })).toContainText("name: 'Confirm'");
    await expect(section.getByRole('figure', { name: 'Old script' })).not.toContainText("name: 'Confirm'");
    // The added line is announced in text, not only by colour.
    await expect(section.locator('.tc-script-heal-new .sr-only')).toHaveText('added: ');

    const accepted = page.waitForRequest((r) => r.url().endsWith(`/script-heals/${HEAL_ID}/accept`) && r.method() === 'POST');
    await section.getByRole('button', { name: 'Accept' }).focus();
    await page.keyboard.press('Enter');
    await accepted;
    expect(healState.status).toBe('accepted');

    // The view reloads: the badge and status follow the accepted heal, and only Revert is left.
    await expect(row.getByText('Script repaired', { exact: true })).toBeVisible();
    const reloaded = view.getByRole('region', { name: 'Script healing' });
    await expect(reloaded).toContainText('Accepted');
    await expect(reloaded.getByRole('button', { name: 'Accept' })).toHaveCount(0);
    await expect(reloaded.getByRole('button', { name: 'Revert' })).toBeVisible();
  });
});
