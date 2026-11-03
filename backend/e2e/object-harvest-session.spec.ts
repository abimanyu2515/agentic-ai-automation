import { test, expect, Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { startHarvest, HarvestSnapshot } from '../playwrightt/lib/object-harvest-session';

// The harvest session both runners use (spec 0004, AC-16, AC-17, AC-20).
// Pages are served through page.route, so no backend is needed:
//   npx playwright test e2e/object-harvest-session.spec.ts --project=chromium
const SOURCE = fs.readFileSync(path.join(__dirname, '..', 'playwrightt', 'lib', 'object-harvest.js'), 'utf8');
const ORIGIN = 'http://harvest.test';
const SETTINGS = { enabled: true, maxPages: 20, budgetMs: 3000 };
const SETTLE_WAIT = 1600;

async function serve(page: Page) {
  await page.route(`${ORIGIN}/**`, (route) => {
    const name = new URL(route.request().url()).pathname;
    route.fulfill({
      contentType: 'text/html',
      body: `<html><body><h1>${name}</h1>
        <button id="go-${name.replace(/\W/g, '')}">Open ${name}</button>
        <button>Hello standard_user</button>
        <a href="/spa" onclick="history.pushState({}, '', '/spa/next'); return false;" id="spa">Client route</a>
      </body></html>`,
    });
  });
}

function session(page: Page, harvested = new Set<string>(), overrides: Partial<typeof SETTINGS> = {}, source = SOURCE) {
  const snapshots: HarvestSnapshot[] = [];
  const handle = startHarvest({
    page,
    source,
    settings: { ...SETTINGS, ...overrides },
    harvested,
    redact: ['standard_user', 'ro'],
    onSnapshot: (snapshot) => snapshots.push(snapshot),
    log: () => {},
  });
  return { handle, snapshots, harvested };
}

test('each settled path is snapshotted once, client routes count, and the cap holds', async ({ page }) => {
  await serve(page);
  const { handle, snapshots } = session(page, new Set(), { maxPages: 3 });
  for (const name of ['/one', '/two', '/one/', '/two', '/three', '/four']) {
    await page.goto(`${ORIGIN}${name}`);
    await page.waitForTimeout(SETTLE_WAIT);
  }
  await handle.stop();
  expect(snapshots.map((s) => s.url)).toEqual(['/one', '/two', '/three']);

  const spa = session(page);
  await page.goto(`${ORIGIN}/spa`);
  await page.waitForTimeout(SETTLE_WAIT);
  await page.click('#spa');
  await page.waitForTimeout(SETTLE_WAIT);
  await spa.handle.stop();
  expect(spa.snapshots.map((s) => s.url)).toEqual(['/spa', '/spa/next']);
});

test('a page left before it settles is not snapshotted', async ({ page }) => {
  await serve(page);
  const { handle, snapshots } = session(page);
  await page.goto(`${ORIGIN}/brief`);
  await page.waitForTimeout(300);
  await page.goto(`${ORIGIN}/settled`);
  await page.waitForTimeout(SETTLE_WAIT);
  await handle.stop();
  expect(snapshots.map((s) => s.url)).toEqual(['/settled']);
});

test('two executions on a shared page each harvest only their own pages', async ({ page }) => {
  await serve(page);
  const first = session(page);
  await page.goto(`${ORIGIN}/first`);
  await page.waitForTimeout(SETTLE_WAIT);
  await first.handle.stop();

  const second = session(page);
  await page.goto(`${ORIGIN}/second`);
  await page.waitForTimeout(SETTLE_WAIT);
  await second.handle.stop();

  expect(first.snapshots.map((s) => s.url)).toEqual(['/first']);
  expect(second.snapshots.map((s) => s.url)).toEqual(['/second']);
});

test('runner redaction replaces credentials and ignores values shorter than 3 characters', async ({ page }) => {
  await serve(page);
  const { handle, snapshots } = session(page);
  await page.goto(`${ORIGIN}/greeting`);
  await page.waitForTimeout(SETTLE_WAIT);
  await handle.stop();
  const text = JSON.stringify(snapshots);
  expect(text).not.toContain('standard_user');
  expect(text).toContain('Hello [redacted]');
  expect(text).toContain('Client route');
});

test('a throwing or hanging extractor never fails the run and the end wait is bounded', async ({ page }) => {
  await serve(page);
  const throwing = session(page, new Set(), {}, '() => { throw new Error("boom"); }');
  await page.goto(`${ORIGIN}/throws`);
  await page.waitForTimeout(SETTLE_WAIT);
  await throwing.handle.stop();
  expect(throwing.snapshots).toEqual([]);

  const hanging = session(page, new Set(), { budgetMs: 500 }, '() => new Promise(() => {})');
  await page.goto(`${ORIGIN}/hangs`);
  await page.waitForTimeout(1100);
  const started = Date.now();
  await hanging.handle.stop();
  expect(Date.now() - started).toBeLessThan(1500);
  expect(hanging.snapshots).toEqual([]);
});

test('a closed page is skipped without an error', async ({ browser }) => {
  const page = await browser.newPage();
  await serve(page);
  const { handle, snapshots } = session(page);
  await page.goto(`${ORIGIN}/closing`);
  await page.close();
  await new Promise((resolve) => setTimeout(resolve, SETTLE_WAIT));
  await handle.stop();
  expect(snapshots).toEqual([]);
});
