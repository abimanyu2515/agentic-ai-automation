import { test, expect, type APIRequestContext, type Page } from '@playwright/test';

// Spec 0012, AC-16: a batch deep link opened with no project selected keeps its
// route and shows "Select the project that ran this batch to watch it.", with no
// stream request. Needs the backend running with the seeded development users:
//   uvicorn backend.main:app --port 8000
//   npx playwright test e2e/batch-observer-deep-link.spec.ts --project=chromium
const BASE_URL = process.env.BATCH_BASE_URL || process.env.OBJREPO_BASE_URL || 'http://127.0.0.1:8000';
const BATCH_ID = '44444444-4444-4444-8444-444444444444';
const MESSAGE = 'Select the project that ran this batch to watch it.';

type Session = { access_token: string; refresh_token: string; user: unknown };

async function signIn(request: APIRequestContext, email: string, password: string): Promise<Session> {
  const login = await request.post(`${BASE_URL}/api/v1/auth/login`, { data: { email, password } });
  expect(login.ok()).toBeTruthy();
  return login.json();
}

async function openDeepLink(page: Page, session: Session) {
  const streams: string[] = [];
  page.on('request', (req) => {
    if (req.url().includes('/batches/') && req.url().includes('/stream')) streams.push(req.url());
  });
  await page.addInitScript((session) => {
    sessionStorage.setItem('access_token', session.access_token);
    sessionStorage.setItem('refresh_token', session.refresh_token);
    sessionStorage.setItem('user', JSON.stringify(session.user));
    localStorage.removeItem('active_project_id');
  }, session);
  await page.goto(`${BASE_URL}/#/execution/BATCH-${BATCH_ID}`);
  return streams;
}

async function createProject(request: APIRequestContext, session: Session): Promise<string> {
  const suffix = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  const created = await request.post(`${BASE_URL}/api/v1/projects`, {
    headers: { Authorization: `Bearer ${session.access_token}` },
    data: {
      name: `Batch observer e2e ${suffix}`,
      description: 'Created by the batch observer deep link test',
      framework: 'playwright',
      target_url: `https://batch-${suffix}.example.test`,
    },
  });
  expect(created.ok()).toBeTruthy();
  return (await created.json()).id;
}

// covers: AC-16, AC-17. Startup restores the saved project and keeps the deep
// link, so the observer streams this project's batch. The batch id is unknown
// to the server, so the stream answers with one untracked Error.
test('batch deep link keeps its route and streams for a non admin with a saved project', async ({ page, request }) => {
  const session = await signIn(request, 'pm@agenticai.com', 'pmpassword');
  const projectId = await createProject(request, session);
  const streams: string[] = [];
  page.on('request', (req) => {
    if (req.url().includes('/batches/') && req.url().includes('/stream')) streams.push(req.url());
  });
  await page.addInitScript(({ session, projectId }) => {
    sessionStorage.setItem('access_token', session.access_token);
    sessionStorage.setItem('refresh_token', session.refresh_token);
    sessionStorage.setItem('user', JSON.stringify(session.user));
    localStorage.setItem('active_project_id', projectId);
  }, { session, projectId });

  await page.goto(`${BASE_URL}/#/execution/BATCH-${BATCH_ID}`);

  await expect(page.locator('#obs-connection-status')).toHaveText('Finished');
  await expect(page).toHaveURL(new RegExp(`#/execution/BATCH-${BATCH_ID}$`));
  await expect(page.locator('#execution-workspace-view')).toHaveClass(/active/);
  await expect(page.locator('#execution-observer-body')).not.toContainText(MESSAGE);
  await expect(page.locator('#obs-terminal')).toContainText('not tracked on this server');
  await expect(page.locator('#obs-batch-report-slot button')).toHaveCount(0);
  expect(streams.length).toBeGreaterThan(0);
  expect(streams.every((url) => url.includes(`/projects/${projectId}/batches/${BATCH_ID}/stream`))).toBe(true);
});

for (const who of [
  { label: 'an admin', email: 'admin@agenticai.com', password: 'admin123' },
  { label: 'a non admin with no saved project', email: 'pm@agenticai.com', password: 'pmpassword' },
]) {
  test(`batch deep link with no project shows the AC-16 message for ${who.label}`, async ({ page, request }) => {
    const session = await signIn(request, who.email, who.password);
    const streams = await openDeepLink(page, session);

    await expect(page.locator('#execution-observer-body')).toContainText(MESSAGE);
    await expect(page.locator('#execution-workspace-view')).toHaveClass(/active/);
    await expect(page).toHaveURL(new RegExp(`#/execution/BATCH-${BATCH_ID}$`));
    expect(streams).toEqual([]);
  });
}
