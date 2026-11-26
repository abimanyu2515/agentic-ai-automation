import { test, expect, type APIRequestContext, type Page } from '@playwright/test';

// Browser tests for the Object Repository view (spec 0004, AC-11 and AC-13).
// Needs the backend running with the seeded development users, for example:
//   uvicorn backend.main:app --port 8000
//   OBJREPO_BASE_URL=http://127.0.0.1:8000 npx playwright test e2e/object-repository.spec.ts --project=chromium
const BASE_URL = process.env.OBJREPO_BASE_URL || 'http://127.0.0.1:8000';

// Startup loads every project the user can see, which can be slow on a busy
// development database.
test.setTimeout(120_000);

type Session = { access_token: string; refresh_token: string; user: unknown };

async function signIn(request: APIRequestContext, email: string, password: string): Promise<Session> {
  const login = await request.post(`${BASE_URL}/api/v1/auth/login`, { data: { email, password } });
  expect(login.ok()).toBeTruthy();
  return login.json();
}

async function createProject(request: APIRequestContext, session: Session): Promise<string> {
  const suffix = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  const created = await request.post(`${BASE_URL}/api/v1/projects`, {
    headers: { Authorization: `Bearer ${session.access_token}` },
    data: {
      name: `Object repository e2e ${suffix}`,
      description: 'Created by the object repository browser test',
      framework: 'playwright',
      target_url: `https://objrepo-${suffix}.example.test`,
    },
  });
  expect(created.ok()).toBeTruthy();
  return (await created.json()).id;
}

// Seed the session and the saved project the way a returning user has them.
async function seedBrowser(page: Page, session: Session, projectId: string) {
  await page.addInitScript(({ session, projectId }) => {
    sessionStorage.setItem('access_token', session.access_token);
    sessionStorage.setItem('refresh_token', session.refresh_token);
    sessionStorage.setItem('user', JSON.stringify(session.user));
    localStorage.setItem('active_project_id', projectId);
  }, { session, projectId });
}

type RoutingWindow = Window & {
  appInstance?: { startingUp?: boolean; handleRouting: (...args: unknown[]) => Promise<unknown> };
  __routesInFlight?: number;
};

// Every route change to the view renders it again, and startup ends with a
// route change of its own. Count route changes in flight so the test opens the
// view only once routing is idle; a late render would replace a form or drawer
// the test is using.
async function waitForRoutingIdle(page: Page) {
  await page.waitForFunction(() => {
    const w = window as RoutingWindow;
    return w.appInstance?.startingUp === false && w.__routesInFlight === 0;
  }, undefined, { timeout: 60_000 });
}

async function openObjectRepository(page: Page) {
  await page.goto(`${BASE_URL}/`);
  await page.waitForFunction(() => !!(window as RoutingWindow).appInstance, undefined, { timeout: 60_000 });
  await page.evaluate(() => {
    const w = window as RoutingWindow;
    const app = w.appInstance!;
    const original = app.handleRouting.bind(app);
    w.__routesInFlight = 0;
    app.handleRouting = async (...args: unknown[]) => {
      w.__routesInFlight! += 1;
      try {
        return await original(...args);
      } finally {
        w.__routesInFlight! -= 1;
      }
    };
  });
  await waitForRoutingIdle(page);
  await page.evaluate(() => { window.location.hash = '#/object-repository'; });
  const view = page.locator('#object-repository-view');
  // The panel turns active when the route change lands, then routing goes idle.
  await expect(view).toHaveClass(/\bactive\b/, { timeout: 60_000 });
  await waitForRoutingIdle(page);
  await expect(view.getByRole('heading', { name: 'Object Repository', level: 1 })).toBeVisible();
  await expect(view.locator('#objrepo-page-list .loading-overlay')).toHaveCount(0, { timeout: 30_000 });
  return view;
}

async function addLoginPage(page: Page) {
  const view = page.locator('#object-repository-view');
  await view.getByRole('button', { name: /Add a page/ }).click();
  const form = view.locator('#objrepo-page-form');
  await form.getByLabel('Name').fill('login_page');
  await form.getByLabel('URL pattern').fill('/login');
  await form.getByRole('button', { name: 'Add page' }).click();
  await expect(view.getByRole('button', { name: 'Open login_page' })).toBeVisible();
}

async function addLoginButton(page: Page) {
  const view = page.locator('#object-repository-view');
  await view.getByRole('button', { name: 'Add element' }).click();
  const drawer = page.getByRole('dialog');
  await expect(drawer).toBeVisible();
  await drawer.getByLabel('Name', { exact: true }).fill('login_button');
  await drawer.getByLabel('Main locator value').fill('button');
  await drawer.getByLabel('Main locator accessible name').fill('Log in');
  await drawer.getByRole('button', { name: 'Add backup locator' }).click();
  await drawer.getByLabel('Backup 1 strategy').selectOption('css');
  await drawer.getByLabel('Backup 1 value').fill('#login');
  await drawer.getByRole('button', { name: 'Add element' }).click();
  await expect(drawer).toBeHidden();
}

// Self healing (spec 0011, AC-14). No route creates a suggestion directly (only a
// failed run does), so the page and element are real, and the browser is served
// one suggested heal for the element through the heal routes and the element list.
type Locator = { strategy: string; value: string; options: Record<string, unknown> };
const HEAL_ID = 'a0000000-0000-4000-8000-000000000011';
const OLD_LOCATORS: Locator[] = [{ strategy: 'role', value: 'button', options: { name: 'Log in' } }];
const NEW_LOCATORS: Locator[] = [
  { strategy: 'role', value: 'button', options: { name: 'Sign in' } },
  { strategy: 'test_id', value: 'login-submit', options: {} },
];

async function seedLoginButton(request: APIRequestContext, session: Session, projectId: string): Promise<string> {
  const headers = { Authorization: `Bearer ${session.access_token}` };
  const base = `${BASE_URL}/api/v1/projects/${projectId}/object-repository`;
  const created = await request.post(`${base}/pages`, { headers, data: { name: 'login_page', url_pattern: '/login' } });
  expect(created.ok()).toBeTruthy();
  const pageId = (await created.json()).id;
  const element = await request.post(`${base}/pages/${pageId}/elements`, {
    headers,
    data: { name: 'login_button', element_type: 'button', locators: OLD_LOCATORS },
  });
  expect(element.ok()).toBeTruthy();
  return (await element.json()).id;
}

async function serveSuggestion(page: Page, elementId: string) {
  const state = { status: 'suggested', decidedAt: null as string | null };
  const heal = () => ({
    id: HEAL_ID,
    element_id: elementId,
    key: 'login_page.login_button',
    status: state.status,
    method: 'llm',
    confidence: 0.72,
    old_locators: OLD_LOCATORS,
    new_locators: NEW_LOCATORS,
    reason: 'Same test id on the page; the label changed from Log in to Sign in.',
    evidence_source: 'harvest',
    execution_id: null,
    test_case_id: null,
    created_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    decided_by: null,
    decided_at: state.decidedAt,
  });
  const locatorRows = (locators: Locator[]) => locators.map((loc, position) => ({
    ...loc, id: `b0000000-0000-4000-8000-00000000000${position}`, position,
    success_count: 0, failure_count: 0, last_succeeded_at: null,
  }));
  const withHeal = (element: Record<string, unknown>) => ({
    ...element,
    latest_heal: heal(),
    ...(state.status === 'accepted' ? { locators: locatorRows(NEW_LOCATORS) } : {}),
  });

  await page.route(/\/object-repository\/heals(\?.*)?$/, (route) => route.fulfill({
    json: state.status === 'suggested' ? [heal()] : [],
  }));
  await page.route(/\/object-repository\/pages\/[^/]+\/elements(\?.*)?$/, async (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    const response = await route.fetch();
    const body = await response.json();
    body.elements = (body.elements || []).map((el: Record<string, unknown>) => (el.id === elementId ? withHeal(el) : el));
    await route.fulfill({ response, json: body });
  });
  await page.route(new RegExp(`/object-repository/heals/${HEAL_ID}/accept$`), async (route) => {
    state.status = 'accepted';
    state.decidedAt = new Date().toISOString();
    await route.fulfill({ json: { heal: heal(), element: { id: elementId } } });
  });
  return state;
}

test.describe('Object Repository self healing (spec 0011)', () => {
  test('a project manager reviews and accepts a self healing suggestion with the keyboard (AC-14)', async ({ page, request }) => {
    const session = await signIn(request, 'pm@agenticai.com', 'pmpassword');
    const projectId = await createProject(request, session);
    const elementId = await seedLoginButton(request, session, projectId);
    const healState = await serveSuggestion(page, elementId);
    await seedBrowser(page, session, projectId);
    const view = await openObjectRepository(page);

    // The attention card counts the pending suggestion, and the row shows a text badge.
    await expect(view.locator('#objrepo-review-text')).toContainText('1 self healing suggestion waiting');
    const row = view.getByRole('row', { name: /login_page\.login_button/ });
    await expect(row.getByText('Suggestion', { exact: true })).toBeVisible();

    // Open the drawer with the keyboard and review the Healing section.
    const edit = row.getByRole('button', { name: 'Edit' });
    await edit.focus();
    await page.keyboard.press('Enter');
    const drawer = page.getByRole('dialog');
    const healing = drawer.getByRole('region', { name: 'Healing' });
    await expect(healing).toContainText('Waiting for review');
    await expect(healing).toContainText('72%');
    await expect(healing).toContainText('AI pick among page candidates');
    await expect(healing).toContainText('the label changed from Log in to Sign in');
    await expect(healing).toContainText(`getByRole('button', { name: 'Log in' })`);
    await expect(healing).toContainText(`getByTestId('login-submit')`);
    await expect(healing.getByRole('button', { name: 'Reject' })).toBeVisible();

    const accepted = page.waitForRequest((r) => r.url().endsWith(`/heals/${HEAL_ID}/accept`) && r.method() === 'POST');
    await healing.getByRole('button', { name: 'Accept' }).focus();
    await page.keyboard.press('Enter');
    await accepted;
    expect(healState.status).toBe('accepted');

    // The drawer reloads the element: the heal is accepted and can now be reverted.
    await expect(view.locator('#objrepo-toast')).toContainText('Accepted the suggestion for login_page.login_button');
    await expect(healing).toContainText('Accepted');
    await expect(healing.getByRole('button', { name: 'Accept' })).toHaveCount(0);
    await expect(healing.getByRole('button', { name: 'Revert' })).toBeVisible();
    await expect(drawer.getByLabel('Main locator accessible name')).toHaveValue('Sign in');

    await page.keyboard.press('Escape');
    await expect(drawer).toBeHidden();
    await expect(row.getByText('Healed', { exact: true })).toBeVisible();
    await expect(view.locator('#objrepo-review-text')).toContainText('no self healing suggestion is waiting');
  });

  test('a stakeholder sees the Healing section read only (AC-14)', async ({ page, request }) => {
    const pm = await signIn(request, 'pm@agenticai.com', 'pmpassword');
    const projectId = await createProject(request, pm);
    const elementId = await seedLoginButton(request, pm, projectId);
    const stakeholder = await signIn(request, 'stakeholder@agenticai.com', 'stakeholderpassword');
    await serveSuggestion(page, elementId);
    await seedBrowser(page, stakeholder, projectId);
    const view = await openObjectRepository(page);

    const row = view.getByRole('row', { name: /login_page\.login_button/ });
    await expect(row.getByText('Suggestion', { exact: true })).toBeVisible();
    await row.getByRole('button', { name: 'Show all locators for login_page.login_button' }).click();
    const healing = view.getByRole('region', { name: 'Healing' });
    await expect(healing).toContainText('Waiting for review');
    await expect(healing.getByRole('button')).toHaveCount(0);
  });
});

test.describe('Object Repository view', () => {
  test('a project manager adds a page and an element and edits its locators with the keyboard (AC-13)', async ({ page, request }) => {
    const session = await signIn(request, 'pm@agenticai.com', 'pmpassword');
    await seedBrowser(page, session, await createProject(request, session));
    const view = await openObjectRepository(page);
    await expect(page.locator('#current-user-role')).toHaveText('Project Manager');

    // Empty state, and the actions a project manager may take.
    await expect(view.getByText('No pages yet.')).toBeVisible();
    await expect(view.getByRole('button', { name: /Scan the site/ })).toBeVisible();
    await expect(view.getByRole('button', { name: 'Add element' })).toBeDisabled();

    await addLoginPage(page);
    await addLoginButton(page);

    // The row shows the key, a text health badge, the main locator, and the backup count.
    const row = view.getByRole('row', { name: /login_page\.login_button/ });
    await expect(row).toBeVisible();
    await expect(row.getByText('Healthy', { exact: false })).toBeVisible();
    await expect(row).toContainText(`getByRole('button', { name: 'Log in' })`);
    await expect(row).toContainText('+1 backup');

    // Keyboard only edit: move the backup up, then save.
    const edit = row.getByRole('button', { name: 'Edit' });
    await edit.focus();
    await page.keyboard.press('Enter');
    const drawer = page.getByRole('dialog');
    await expect(drawer).toBeVisible();
    await drawer.getByRole('button', { name: 'Move Backup 1 up' }).focus();
    await page.keyboard.press('Enter');
    await expect(drawer.getByLabel('Main locator value')).toHaveValue('#login');
    await drawer.getByRole('button', { name: 'Save changes' }).focus();
    await page.keyboard.press('Enter');
    await expect(drawer).toBeHidden();
    await expect(row).toContainText(`locator('#login')`);

    // Escape closes the drawer and returns focus to the trigger.
    await edit.click();
    await expect(drawer).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(drawer).toBeHidden();
    await expect(edit).toBeFocused();
  });

  test('an invalid page name shows the snake_case rule and adds nothing (AC-4, AC-13)', async ({ page, request }) => {
    const session = await signIn(request, 'pm@agenticai.com', 'pmpassword');
    await seedBrowser(page, session, await createProject(request, session));
    const view = await openObjectRepository(page);

    await view.getByRole('button', { name: /Add a page/ }).click();
    const form = view.locator('#objrepo-page-form');
    await form.getByLabel('Name').fill('Bad Name');
    await form.getByLabel('URL pattern').fill('/other');
    await form.getByRole('button', { name: 'Add page' }).click();

    await expect(view.locator('#objrepo-page-error')).toContainText('lowercase letters, numbers and underscores');
    await expect(view.getByText('No pages yet.')).toBeVisible();
  });

  test('health filter chips count elements by health in text and filter the table (AC-13)', async ({ page, request }) => {
    const session = await signIn(request, 'pm@agenticai.com', 'pmpassword');
    await seedBrowser(page, session, await createProject(request, session));
    const view = await openObjectRepository(page);
    await addLoginPage(page);
    await addLoginButton(page);

    const chips = view.getByRole('group', { name: 'Filter elements by health' });
    await expect(chips.locator('[data-health="healthy"]')).toContainText('Healthy 1');
    // A health with no elements cannot be chosen.
    await expect(chips.locator('[data-health="broken"]')).toBeDisabled();

    const healthy = chips.locator('[data-health="healthy"]');
    await healthy.click();
    await expect(healthy).toHaveAttribute('aria-pressed', 'true');
    await expect(view.getByRole('row', { name: /login_page\.login_button/ })).toBeVisible();
  });

  test('a scan is refused for a project that is not a safe test environment (AC-3, AC-13)', async ({ page, request }) => {
    const session = await signIn(request, 'pm@agenticai.com', 'pmpassword');
    await seedBrowser(page, session, await createProject(request, session));
    const view = await openObjectRepository(page);

    const scan = page.waitForResponse((r) => r.url().endsWith('/object-repository/scan') && r.request().method() === 'POST');
    await view.getByRole('button', { name: /Scan the site/ }).click();
    expect((await scan).status()).toBe(409);
    await expect(view.locator('#objrepo-scan-status')).toContainText('safe test environment');
  });

  test('a stakeholder sees the repository read only (AC-11)', async ({ browser, page, request }) => {
    const pm = await signIn(request, 'pm@agenticai.com', 'pmpassword');
    const projectId = await createProject(request, pm);
    await seedBrowser(page, pm, projectId);
    await openObjectRepository(page);
    await addLoginPage(page);
    await addLoginButton(page);

    const context = await browser.newContext();
    const stakeholderPage = await context.newPage();
    const stakeholder = await signIn(context.request, 'stakeholder@agenticai.com', 'stakeholderpassword');
    await seedBrowser(stakeholderPage, stakeholder, projectId);
    const view = await openObjectRepository(stakeholderPage);

    await expect(view.getByRole('button', { name: /Scan the site/ })).toHaveCount(0);
    await expect(view.getByRole('button', { name: /Add a page/ })).toHaveCount(0);
    await expect(view.getByRole('button', { name: 'Add element' })).toHaveCount(0);
    const row = view.getByRole('row', { name: /login_page\.login_button/ });
    await expect(row).toBeVisible();
    await expect(row.getByRole('button', { name: 'Edit' })).toHaveCount(0);
    await context.close();
  });
});

test('changing the route while startup restores the project keeps the saved project', async ({ page, request }) => {
  const session = await signIn(request, 'pm@agenticai.com', 'pmpassword');
  const projectId = await createProject(request, session);
  await seedBrowser(page, session, projectId);
  // Hold the project list until the route has changed, so the change always
  // lands while startup is still restoring the saved project.
  let releaseProjects!: () => void;
  const routeChanged = new Promise<void>((resolve) => { releaseProjects = resolve; });
  await page.route(/\/api\/v1\/projects$/, async (route) => {
    if (route.request().method() === 'GET') await routeChanged;
    await route.continue();
  });
  const projectsRequested = page.waitForRequest((r) => /\/api\/v1\/projects$/.test(r.url()) && r.method() === 'GET');
  await page.goto(`${BASE_URL}/`);
  await projectsRequested;
  await page.evaluate(() => { window.location.hash = '#/object-repository'; });
  releaseProjects();

  // Startup finishes on the restored project, not on the project picker.
  await expect(page.getByRole('button', { name: 'Exit Project' })).toBeVisible({ timeout: 60_000 });
  await expect(page).not.toHaveURL(/#\/project-start$/);
  expect(await page.evaluate(() => localStorage.getItem('active_project_id'))).toBe(projectId);
});
