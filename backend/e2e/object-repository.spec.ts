import { test, expect } from '@playwright/test';

// Browser test for the Object Repository view (spec 0004, AC-13).
// Needs the backend running with the seeded development users, for example:
//   uvicorn backend.main:app --port 8000
//   OBJREPO_BASE_URL=http://127.0.0.1:8000 npx playwright test e2e/object-repository.spec.ts --project=chromium
const BASE_URL = process.env.OBJREPO_BASE_URL || 'http://127.0.0.1:8000';

test('a project manager manages pages, elements, and locators with the keyboard', async ({ page, request }) => {
  const login = await request.post(`${BASE_URL}/api/v1/auth/login`, {
    data: { email: 'pm@agenticai.com', password: 'pmpassword' },
  });
  expect(login.ok()).toBeTruthy();
  const session = await login.json();
  const suffix = Date.now().toString(36);
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
  const project = await created.json();

  await page.addInitScript(({ session, projectId }) => {
    sessionStorage.setItem('access_token', session.access_token);
    sessionStorage.setItem('refresh_token', session.refresh_token);
    sessionStorage.setItem('user', JSON.stringify(session.user));
    localStorage.setItem('active_project_id', projectId);
  }, { session, projectId: project.id });

  await page.goto(`${BASE_URL}/`);
  await expect(page.locator('#current-user-role')).toHaveText('Project Manager');

  // Selecting the saved project routes to the dashboard during startup, so
  // keep asking for the view until the router is listening.
  const view = page.locator('#object-repository-view');
  const heading = view.getByRole('heading', { name: 'Object Repository', level: 1 });
  await expect(async () => {
    await page.evaluate(() => { window.location.hash = '#/object-repository'; });
    await expect(heading).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 20000 });
  await expect(view.getByText('No pages yet.')).toBeVisible();

  // Add a page.
  await view.getByLabel('Name (snake_case)').fill('login_page');
  await view.getByLabel('URL pattern').fill('/login');
  await view.getByRole('button', { name: 'Add page' }).click();
  await expect(view.getByRole('button', { name: /^login_page/ })).toBeVisible();

  // Invalid page input shows the API error.
  await view.getByLabel('Name (snake_case)').fill('Bad Name');
  await view.getByLabel('URL pattern').fill('/other');
  await view.getByRole('button', { name: 'Add page' }).click();
  await expect(view.locator('#objrepo-page-error')).toContainText('snake_case');

  // Add an element through the drawer.
  await view.getByRole('button', { name: 'Add element' }).click();
  const drawer = page.getByRole('dialog', { name: 'Add element' });
  await expect(drawer).toBeVisible();
  await drawer.getByLabel('Name (snake_case)').fill('login_button');
  await drawer.getByLabel('Main locator value').fill('button');
  await drawer.getByLabel('Main locator accessible name').fill('Log in');
  await drawer.getByRole('button', { name: 'Add locator' }).click();
  await drawer.getByLabel('Backup 1 value').fill('#login');
  await drawer.getByRole('button', { name: 'Add element' }).click();
  await expect(drawer).toBeHidden();

  const row = view.getByRole('row', { name: /login_page\.login_button/ });
  await expect(row).toBeVisible();
  await expect(row.getByText('Healthy')).toBeVisible();
  await expect(row).toContainText('role: button, name "Log in"');
  await expect(row).toContainText('+1 backup');

  // Edit with the keyboard: move the backup up, then save.
  const edit = row.getByRole('button', { name: 'Edit' });
  await edit.focus();
  await page.keyboard.press('Enter');
  const editDrawer = page.getByRole('dialog', { name: /Edit login_page\.login_button/ });
  await expect(editDrawer).toBeVisible();
  await editDrawer.getByRole('button', { name: 'Move Backup 1 up' }).focus();
  await page.keyboard.press('Enter');
  await expect(editDrawer.getByLabel('Main locator value')).toHaveValue('#login');
  await editDrawer.getByRole('button', { name: 'Save changes' }).click();
  await expect(editDrawer).toBeHidden();
  await expect(row).toContainText('css: #login');

  // Escape closes the drawer and returns focus to the trigger.
  await edit.click();
  await expect(editDrawer).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(editDrawer).toBeHidden();
  await expect(edit).toBeFocused();

  // Filter by health.
  await view.getByLabel('Health').selectOption('broken');
  await expect(view.getByText('No elements match this health filter.')).toBeVisible();
  await view.getByLabel('Health').selectOption('');
  await expect(row).toBeVisible();

  // Scan is refused for a project that is not a safe test environment.
  await view.getByRole('button', { name: 'Scan now' }).click();
  await expect(view.locator('#objrepo-scan-status')).toContainText('safe test environment');
});
