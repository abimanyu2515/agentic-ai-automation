import { test, expect, type APIRequestContext, type Page } from '@playwright/test';

// Browser tests for hash routing in the single page app (frontend/js/app.js).
// A route change made while an earlier route is still loading its data must win:
// the earlier, now stale, route must not write its own hash back afterwards.
// Needs the backend running with the seeded development users, for example:
//   uvicorn backend.main:app --port 8000
//   OBJREPO_BASE_URL=http://127.0.0.1:8000 npx playwright test e2e/startup-routing.spec.ts --project=chromium
const BASE_URL = process.env.OBJREPO_BASE_URL || 'http://127.0.0.1:8000';

test.setTimeout(120_000);

type Session = { access_token: string; refresh_token: string; user: unknown };

async function signIn(request: APIRequestContext): Promise<Session> {
  const login = await request.post(`${BASE_URL}/api/v1/auth/login`, {
    data: { email: 'pm@agenticai.com', password: 'pmpassword' },
  });
  expect(login.ok()).toBeTruthy();
  return login.json();
}

async function createProject(request: APIRequestContext, session: Session): Promise<string> {
  const suffix = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  const project = await request.post(`${BASE_URL}/api/v1/projects`, {
    headers: { Authorization: `Bearer ${session.access_token}` },
    data: {
      name: `Routing e2e ${suffix}`,
      description: 'Created by the routing browser test',
      framework: 'playwright',
      target_url: `https://routing-${suffix}.example.test`,
    },
  });
  expect(project.ok()).toBeTruthy();
  return (await project.json()).id;
}

// Signs in, restores the project, and waits until startup routing has settled
// on the dashboard. Then it counts route handlers still running in
// `window.__routesInFlight`, so a test can wait for every handler to finish.
async function openSettledApp(page: Page, session: Session, projectId: string) {
  await page.addInitScript(({ session, projectId }) => {
    sessionStorage.setItem('access_token', session.access_token);
    sessionStorage.setItem('refresh_token', session.refresh_token);
    sessionStorage.setItem('user', JSON.stringify(session.user));
    localStorage.setItem('active_project_id', projectId);
  }, { session, projectId });
  await page.goto(`${BASE_URL}/`);
  await page.waitForFunction(
    () => (window as any).appInstance?.startingUp === false,
    undefined,
    { timeout: 60_000 },
  );
  await expect(page.locator('#dashboard-view')).toHaveClass(/\bactive\b/, { timeout: 60_000 });
  await page.waitForLoadState('networkidle');
  await page.evaluate(() => {
    const w = window as any;
    const app = w.appInstance;
    const original = app.handleRouting.bind(app);
    w.__routesInFlight = 0;
    app.handleRouting = async () => {
      w.__routesInFlight += 1;
      try {
        return await original();
      } finally {
        w.__routesInFlight -= 1;
      }
    };
  });
}

// Holds the next GET matching `pattern` until `release()` is called. Later
// matching requests pass straight through.
async function holdNextRequest(page: Page, pattern: RegExp) {
  let release!: () => void;
  const released = new Promise<void>((resolve) => { release = resolve; });
  let held = false;
  let markHeld!: () => void;
  const isHeld = new Promise<void>((resolve) => { markHeld = resolve; });
  await page.route(pattern, async (route) => {
    if (route.request().method() === 'GET' && !held) {
      held = true;
      markHeld();
      await released;
    }
    await route.continue();
  });
  return { isHeld, release };
}

async function waitForRoutingToFinish(page: Page) {
  await page.waitForFunction(() => (window as any).__routesInFlight === 0, undefined, { timeout: 60_000 });
}

test.describe('Hash routing while an earlier route is still loading', () => {
  let session: Session;
  let projectId: string;

  test.beforeEach(async ({ page, request }) => {
    session = await signIn(request);
    projectId = await createProject(request, session);
    await openSettledApp(page, session, projectId);
  });

  test('the newer view stays when the earlier route finishes loading later', async ({ page }) => {
    // Arrange: the Scenarios route stalls on its data refresh.
    const requirements = await holdNextRequest(page, /\/api\/v1\/projects\/[^/]+\/requirements$/);
    await page.evaluate(() => { window.location.hash = '#/scenarios'; });
    await requirements.isHeld;

    // Act: move on to Test Cases, then let the stale Scenarios refresh finish.
    await page.evaluate(() => { window.location.hash = '#/testcases'; });
    await expect(page.locator('#testcases-view')).toHaveClass(/\bactive\b/, { timeout: 60_000 });
    requirements.release();
    await waitForRoutingToFinish(page);

    // Assert: Test Cases is still the route and the open view.
    expect(await page.evaluate(() => window.location.hash)).toBe('#/testcases');
    await expect(page.locator('#testcases-view')).toHaveClass(/\bactive\b/);
    await expect(page.locator('#scenarios-view')).not.toHaveClass(/\bactive\b/);
  });

  test('a newer route also wins over an execution observer that is still loading', async ({ page }) => {
    // Arrange: the execution observer route stalls on its execution detail.
    const executionId = '00000000-0000-4000-8000-0000000000e1';
    const detail = await holdNextRequest(page, new RegExp(`/executions/${executionId}$`));
    await page.evaluate((id) => { window.location.hash = `#/execution/${id}`; }, executionId);
    await detail.isHeld;

    // Act: move on to Test Cases, then let the stale execution detail finish.
    await page.evaluate(() => { window.location.hash = '#/testcases'; });
    await expect(page.locator('#testcases-view')).toHaveClass(/\bactive\b/, { timeout: 60_000 });
    detail.release();
    await waitForRoutingToFinish(page);

    // Assert: Test Cases is still the route; the execution workspace did not take over.
    expect(await page.evaluate(() => window.location.hash)).toBe('#/testcases');
    await expect(page.locator('#testcases-view')).toHaveClass(/\bactive\b/);
    await expect(page.locator('#execution-workspace-view')).not.toHaveClass(/\bactive\b/);
  });

  test('a single route change with no overlap still opens its view', async ({ page }) => {
    // Act
    await page.evaluate(() => { window.location.hash = '#/testcases'; });
    await waitForRoutingToFinish(page);

    // Assert
    expect(await page.evaluate(() => window.location.hash)).toBe('#/testcases');
    await expect(page.locator('#testcases-view')).toHaveClass(/\bactive\b/);
  });
});
