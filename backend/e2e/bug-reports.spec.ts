import { test, expect, type APIRequestContext, type Page, type Route } from '@playwright/test';

// Browser tests for the Bug Reports view (Bug Reporting Agent).
// Needs the backend running with the seeded development users, for example:
//   uvicorn backend.main:app --port 8000
//   BUGS_BASE_URL=http://127.0.0.1:8000 npx playwright test e2e/bug-reports.spec.ts --project=chromium
//
// The page, sign in and project are real. The /bug-reports API is stubbed with
// page.route, so the test needs no failed run, no LLM, and never files a Jira issue.
const BASE_URL = process.env.BUGS_BASE_URL || process.env.OBJREPO_BASE_URL || 'http://127.0.0.1:8000';

test.setTimeout(240_000);

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
  const created = await request.post(`${BASE_URL}/api/v1/projects`, {
    headers: { Authorization: `Bearer ${session.access_token}` },
    data: {
      name: `Bug reports e2e ${suffix}`,
      description: 'Created by the bug reports browser test',
      framework: 'playwright',
      target_url: `https://bugs-${suffix}.example.test`,
    },
  });
  expect(created.ok()).toBeTruthy();
  return (await created.json()).id;
}

function draftReport(projectId: string) {
  const now = new Date().toISOString();
  return {
    id: '11111111-1111-4111-8111-111111111111',
    project_id: projectId,
    execution_id: '22222222-2222-4222-8222-222222222222',
    test_case_id: '33333333-3333-4333-8333-333333333333',
    scenario_id: null,
    requirement_id: null,
    title: 'Refund button ignores manager PIN',
    summary: 'Refund completes without the PIN dialog.',
    steps_to_reproduce: ['Open the order', 'Click Refund'],
    expected_result: 'A PIN dialog blocks the refund',
    actual_result: 'Refund completed immediately',
    severity: 'major',
    priority: 'high',
    environment: { target_url: 'https://shop.example.test', browser: 'chromium' },
    failing_step: 'Click Refund',
    root_cause: 'The refund endpoint skips the PIN check.',
    evidence: ['POST /refund 200'],
    confidence: 0.9,
    flaky_score: 0,
    failure_category: 'Product Bug',
    attachments: [],
    error_signature: 'abc',
    occurrences: [{ execution_id: '22222222-2222-4222-8222-222222222222', executed_at: now }],
    status: 'draft',
    jira_key: null,
    jira_url: null,
    duplicate_of_jira_key: null,
    submit_error: null,
    dismiss_reason: null,
    created_by: 'agent',
    submitted_by: null,
    created_at: now,
    updated_at: now,
    deleted_at: null,
  };
}

// A tiny in memory stand-in for the bug report routes.
async function stubBugReports(page: Page, projectId: string) {
  let report: Record<string, unknown> = draftReport(projectId);
  const calls: { method: string; path: string; body: unknown }[] = [];
  await page.route(`**/api/v1/projects/${projectId}/bug-reports**`, async (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const body = request.postData() ? JSON.parse(request.postData() as string) : null;
    calls.push({ method: request.method(), path, body });
    if (request.method() === 'GET' && path.endsWith('/bug-reports')) {
      const status = new URL(request.url()).searchParams.get('status');
      return route.fulfill({ json: !status || status === report.status ? [report] : [] });
    }
    if (request.method() === 'PATCH') {
      report = { ...report, ...body };
      return route.fulfill({ json: report });
    }
    if (path.endsWith('/submit')) {
      report = { ...report, status: 'submitted', jira_key: 'SHOP-7', jira_url: 'https://jira.example.test/browse/SHOP-7' };
      return route.fulfill({ json: report });
    }
    return route.fulfill({ json: report });
  });
  return calls;
}

async function openBugReports(page: Page, session: Session, projectId: string) {
  await page.addInitScript(({ session, projectId }) => {
    sessionStorage.setItem('access_token', session.access_token);
    sessionStorage.setItem('refresh_token', session.refresh_token);
    sessionStorage.setItem('user', JSON.stringify(session.user));
    localStorage.setItem('active_project_id', projectId);
  }, { session, projectId });
  await page.goto(`${BASE_URL}/`);
  const view = page.locator('#bugs-view');
  const heading = view.getByRole('heading', { name: 'Bug Reports', level: 1 });
  // Startup restores the saved project and then routes to the dashboard, so
  // keep asking for the view until it sticks.
  await expect(async () => {
    await page.evaluate(() => {
      if (window.location.hash !== '#/bugs') window.location.hash = '#/bugs';
    });
    await expect(heading).toBeVisible({ timeout: 1000 });
  }).toPass({ timeout: 150_000 });
  return view;
}

test.describe('Bug Reports view', () => {
  // Startup loads every project the user can see; parallel workers slow it further.
  test.describe.configure({ mode: 'serial' });

  test('a reviewer edits a draft and submits it to Jira', async ({ page, request }) => {
    const session = await signIn(request);
    const projectId = await createProject(request, session);
    const calls = await stubBugReports(page, projectId);
    const view = await openBugReports(page, session, projectId);

    const row = view.getByRole('row', { name: /Refund button ignores manager PIN/ });
    await expect(row).toContainText('Draft');
    await expect(row).toContainText('1 occurrence(s)');

    // Keyboard opens the report.
    await row.focus();
    await page.keyboard.press('Enter');
    const title = view.getByLabel('Title');
    await expect(title).toHaveValue('Refund button ignores manager PIN');
    await expect(view.getByLabel('Steps to reproduce (one per line)')).toHaveValue('Open the order\nClick Refund');

    await title.fill('Refund skips the manager PIN check');
    await view.getByLabel('Severity').selectOption('critical');
    await view.getByRole('button', { name: 'Submit to Jira' }).click();

    // The edit is saved before the submit, then the report shows its Jira key.
    await expect(view.getByRole('link', { name: 'SHOP-7' }).first()).toBeVisible();
    const patch = calls.find(c => c.method === 'PATCH');
    expect(patch?.body).toMatchObject({ title: 'Refund skips the manager PIN check', severity: 'critical' });
    const order = calls.filter(c => c.method !== 'GET').map(c => c.method + ' ' + c.path.split('/').pop());
    expect(order).toEqual([`PATCH 11111111-1111-4111-8111-111111111111`, 'POST submit']);

    // A submitted report is read only.
    await expect(view.getByLabel('Title')).toBeDisabled();
    await expect(view.getByRole('button', { name: 'Submit to Jira' })).toHaveCount(0);
  });

  test('status filters ask the server for that status', async ({ page, request }) => {
    const session = await signIn(request);
    const projectId = await createProject(request, session);
    const calls = await stubBugReports(page, projectId);
    const view = await openBugReports(page, session, projectId);
    await expect(view.getByRole('row', { name: /Refund button/ })).toBeVisible();

    await view.getByRole('tab', { name: 'Submitted' }).click();
    await expect(view.getByText('No bug reports')).toBeVisible();
    await expect(view.getByRole('tab', { name: 'Submitted' })).toHaveAttribute('aria-selected', 'true');
    expect(calls.some(c => c.method === 'GET' && c.path.endsWith('/bug-reports'))).toBeTruthy();
  });
});
