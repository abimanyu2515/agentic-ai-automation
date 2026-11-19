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

async function openObjectRepository(page: Page) {
  await page.goto(`${BASE_URL}/`);
  // Startup restores the saved project and then routes to the dashboard, so
  // keep asking for the view until it sticks.
  const view = page.locator('#object-repository-view');
  const heading = view.getByRole('heading', { name: 'Object Repository', level: 1 });
  await expect(async () => {
    await page.evaluate(() => {
      if (window.location.hash !== '#/object-repository') window.location.hash = '#/object-repository';
    });
    await expect(heading).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 60_000 });
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
