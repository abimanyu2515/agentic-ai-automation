// Standalone accessibility scan (spec 0011, AC-10, AC-11).
//
// The backend writes `scan-input.json` into the run folder and runs a fixed
// template spec that calls runStandaloneScan(). The result goes to the same
// `a11y.json` an execution scan writes, so the backend processes both alike.
//
// Steps:
//   1. Every chosen page is opened with no session. A page that redirects to a
//      URL with a visible password field is marked `requires_login` and is not
//      scanned. A redirect off the project origin is never scanned.
//   2. With `include_login` on, the scan opens the target URL in a new context,
//      fills the vault username and password into the login form, and submits.
//      The login succeeds when the URL path changed, no password field is
//      visible, and no `[role=alert]` shows.
//   3. On success the `requires_login` pages are scanned in that signed in
//      context. On failure they stay listed with `error=requires_login`.
//
// The login form detection mirrors the reconnaissance agent's rules
// (`_looks_like_login_page` and its user, email, and login field tokens).
// Credentials only arrive as TARGET_USERNAME and TARGET_PASSWORD and are never
// logged. Never throws: a failure of the whole scan is `status: "failed"`.

import * as fs from 'fs';
import * as path from 'path';
import { normalizeHarvestPath } from './object-harvest-session';
import {
  A11yPageRecord,
  A11yRawReport,
  A11yScanSettings,
  emptyA11yReport,
  scanPages,
  writeA11yFile,
} from './a11y-scan-session';

export interface StandaloneScanInput {
  urls: string[];
  origin: string;
  target_url: string;
  include_login: boolean;
  wcag_tags: string[];
  max_pages: number;
  page_budget_ms: number;
  max_nodes_per_rule: number;
  max_element_screenshots: number;
}

export interface StandaloneScanOptions {
  browser: any;
  runDir: string;
  username?: string;
  password?: string;
  log?: (message: string) => void;
}

const PASSWORD_FIELD = 'input[type="password"]:visible';
const USER_TOKENS = ['user', 'email', 'login'];
const LOGIN_TIMEOUT_MS = 30000;
const SETTLE_TIMEOUT_MS = 10000;

function originOf(url: string): string | null {
  try {
    return new URL(url).origin;
  } catch {
    return null;
  }
}

async function hasVisiblePassword(page: any): Promise<boolean> {
  try {
    return (await page.locator(PASSWORD_FIELD).count()) > 0;
  } catch {
    return false;
  }
}

// The first pass check (AC-11): skip pages that need a session, and any page
// that left the project origin (spec 0011: scans stay on that origin).
function publicPassCheck(origin: string) {
  return async (page: any, url: string, record: A11yPageRecord): Promise<boolean> => {
    const landed = page.url();
    const redirected = normalizeHarvestPath(landed) !== normalizeHarvestPath(url) || originOf(landed) !== origin;
    if (redirected && (await hasVisiblePassword(page))) {
      record.requires_login = true;
      record.error = 'requires_login';
      return false;
    }
    if (originOf(landed) !== origin) {
      record.error = 'navigation_failed';
      return false;
    }
    return true;
  };
}

// The signed in pass check: a page that still bounces off the origin is not scanned.
function signedInCheck(origin: string) {
  return async (page: any, _url: string, record: A11yPageRecord): Promise<boolean> => {
    record.requires_login = true;
    if (originOf(page.url()) !== origin) {
      record.error = (await hasVisiblePassword(page)) ? 'requires_login' : 'navigation_failed';
      return false;
    }
    return true;
  };
}

async function fieldTokens(locator: any): Promise<string> {
  return locator
    .evaluate((el: any) => {
      const labels = Array.from(el.labels || []).map((l: any) => l.textContent || '');
      return [el.name, el.id, el.placeholder, el.autocomplete, el.getAttribute('aria-label'), ...labels]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
    })
    .catch(() => '');
}

// Fills and submits the target URL's login form. True only when the success
// check passes. Never throws and never logs a credential.
export async function genericLogin(
  context: any,
  targetUrl: string,
  username: string | undefined,
  password: string | undefined,
  log: (message: string) => void,
): Promise<boolean> {
  if (!username || !password) {
    log('[A11Y] Login skipped: the project has no stored username and password');
    return false;
  }
  const page = await context.newPage();
  try {
    await page.goto(targetUrl, { waitUntil: 'load', timeout: LOGIN_TIMEOUT_MS });
    const passwords = page.locator(PASSWORD_FIELD);
    if ((await passwords.count()) !== 1) {
      log('[A11Y] Login failed: no single visible password field on the target URL');
      return false;
    }
    const passwordField = passwords.first();
    const form = passwordField.locator('xpath=ancestor::form[1]');
    const scope = (await form.count()) > 0 ? form.first() : page;

    const inputs = scope.locator(
      'input:visible:not([type="password"]):not([type="hidden"]):not([type="submit"])' +
        ':not([type="button"]):not([type="checkbox"]):not([type="radio"])',
    );
    let usernameField: any = null;
    const count = await inputs.count();
    for (let i = 0; i < count && !usernameField; i += 1) {
      const candidate = inputs.nth(i);
      const tokens = await fieldTokens(candidate);
      if (USER_TOKENS.some((token) => tokens.includes(token))) usernameField = candidate;
    }
    if (!usernameField) {
      log('[A11Y] Login failed: no username or email field next to the password field');
      return false;
    }

    const startPath = normalizeHarvestPath(page.url());
    await usernameField.fill(username);
    await passwordField.fill(password);
    const submit = scope.locator('button[type="submit"]:visible, input[type="submit"]:visible, button:not([type]):visible');
    if ((await submit.count()) > 0) {
      await submit.first().click({ timeout: LOGIN_TIMEOUT_MS });
    } else {
      await passwordField.press('Enter');
    }
    await page.waitForLoadState('load', { timeout: SETTLE_TIMEOUT_MS }).catch(() => {});
    await page.waitForLoadState('networkidle', { timeout: SETTLE_TIMEOUT_MS }).catch(() => {});

    const pathChanged = normalizeHarvestPath(page.url()) !== startPath;
    const passwordGone = !(await hasVisiblePassword(page));
    const alertShown = (await page.locator('[role="alert"]:visible').count().catch(() => 0)) > 0;
    const ok = pathChanged && passwordGone && !alertShown;
    log(`[A11Y] Login ${ok ? 'succeeded' : 'failed'} (path changed: ${pathChanged}, password field gone: ${passwordGone}, alert: ${alertShown})`);
    return ok;
  } catch (error: any) {
    log(`[A11Y] Login failed: ${String(error?.message || error).split('\n')[0]}`);
    return false;
  } finally {
    await page.close().catch(() => {});
  }
}

export function readScanInput(runDir: string): StandaloneScanInput {
  return JSON.parse(fs.readFileSync(path.join(runDir, 'scan-input.json'), 'utf8'));
}

export async function runStandaloneScan(options: StandaloneScanOptions): Promise<A11yRawReport> {
  const log = options.log || ((message: string) => console.log(message));
  const file = path.join(options.runDir, 'a11y.json');
  let report = emptyA11yReport([]);
  try {
    const input = readScanInput(options.runDir);
    const settings: A11yScanSettings = {
      enabled: true,
      tags: input.wcag_tags,
      maxPages: input.max_pages,
      budgetMs: input.page_budget_ms,
      maxNodesPerRule: input.max_nodes_per_rule,
      maxShots: input.max_element_screenshots,
      shotDir: path.join(options.runDir, 'a11y-shots'),
    };
    const redact = [options.username, options.password];
    report = emptyA11yReport(input.wcag_tags);
    report.login_status = input.include_login ? 'failed' : 'not_requested';

    const publicContext = await options.browser.newContext();
    try {
      report = await scanPages({
        context: publicContext,
        urls: input.urls,
        settings,
        previous: report,
        redact,
        log,
        beforeScan: publicPassCheck(input.origin),
      });
    } finally {
      await publicContext.close().catch(() => {});
    }

    if (input.include_login && report.status !== 'failed') {
      const loginUrls = report.pages.filter((p) => p.error === 'requires_login').map((p) => p.url);
      const signedIn = await options.browser.newContext();
      try {
        const ok = await genericLogin(signedIn, input.target_url, options.username, options.password, log);
        report.login_status = ok ? 'succeeded' : 'failed';
        if (ok && loginUrls.length) {
          // Drop the first pass records so these pages are scanned again, signed in.
          const again = new Set(loginUrls.map((u) => normalizeHarvestPath(u)));
          report.pages = report.pages.filter((p) => !again.has(normalizeHarvestPath(p.url)));
          report = await scanPages({
            context: signedIn,
            urls: loginUrls,
            settings,
            previous: report,
            redact,
            log,
            beforeScan: signedInCheck(input.origin),
          });
        }
      } finally {
        await signedIn.close().catch(() => {});
      }
    }
  } catch (error: any) {
    log(`[A11Y] Standalone scan failed: ${String(error?.message || error).split('\n')[0]}`);
    report.status = 'failed';
    report.error = 'scan_error';
  }
  writeA11yFile(file, report);
  return report;
}
