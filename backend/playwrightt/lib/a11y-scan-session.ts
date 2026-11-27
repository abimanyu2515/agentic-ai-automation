// Accessibility scan during execution (spec 0011, AC-1, AC-2, AC-3).
//
// Two steps, so the test's own steps are never slowed or disturbed:
//   1. While the test runs, the recorder only notes each URL the main frame
//      settles on. Playwright emits `framenavigated` for history API changes
//      (pushState, replaceState) as well as full loads, so one listener covers
//      both. Paths are deduped, ignoring the query string and the hash.
//   2. After the test, scanPages() opens each recorded URL again in a new page
//      of the same (signed in) browser context and runs axe-core on it, within
//      the page cap and the total time budget.
//
// Scanning is best effort. A failure on one page only sets that page's
// `error`; a failure of the whole scan is reported as `status: "failed"`. No
// error ever escapes to the test.

import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { normalizeHarvestPath } from './object-harvest-session';

// Loaded on first scan, so a missing package is a scan_error, never a broken test file.
async function loadAxeBuilder(): Promise<any> {
  const mod: any = await import('@axe-core/playwright');
  return mod.default?.default || mod.default || mod.AxeBuilder;
}

export interface A11yScanSettings {
  enabled: boolean;
  tags: string[];
  maxPages: number;
  budgetMs: number;
  maxNodesPerRule: number;
  // Element screenshots of critical and serious violation nodes, across the
  // whole run (spec 0011, AC-7). 0 or no folder turns them off.
  maxShots?: number;
  shotDir?: string;
}

export type A11yPageError = 'budget' | 'csp_blocked' | 'requires_login' | 'navigation_failed' | 'axe_error';

export interface A11yPageRecord {
  url: string;
  title: string;
  scanned_at: string | null;
  requires_login: boolean;
  error: A11yPageError | null;
}

export interface A11yNode {
  target: any[];
  html: string;
  failureSummary: string;
  impact: string | null;
  // A plain file name inside the run's a11y-shots folder.
  screenshot?: string;
}

export interface A11yRule {
  id: string;
  impact: string | null;
  tags: string[];
  help: string;
  helpUrl: string;
  description: string;
  nodes: A11yNode[];
  nodes_total: number;
}

export interface A11yPageResult {
  url: string;
  violations: A11yRule[];
  incomplete: A11yRule[];
}

export interface A11yRawReport {
  status: 'succeeded' | 'failed';
  // Standalone scans only (AC-11): not_requested, succeeded, or failed.
  login_status?: 'not_requested' | 'succeeded' | 'failed';
  error: string | null;
  wcag_tags: string[];
  pages: A11yPageRecord[];
  results: A11yPageResult[];
  elapsed_ms: number;
  truncated: boolean;
}

export interface A11yRecorder {
  // Stops listening and returns the recorded URLs, in visit order.
  stop(): string[];
}

const SETTLE_MS = 1000;
const REDACTED = '[redacted]';
const MAX_SNIPPET_CHARS = 500;

export function emptyA11yReport(tags: string[]): A11yRawReport {
  return { status: 'succeeded', error: null, wcag_tags: tags, pages: [], results: [], elapsed_ms: 0, truncated: false };
}

function withDeadline<T>(promise: Promise<T>, ms: number): Promise<T | null> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<null>((resolve) => {
    timer = setTimeout(() => resolve(null), Math.max(0, ms));
  });
  return Promise.race([promise, deadline]).finally(() => clearTimeout(timer));
}

// Replaces the target credentials and drops every `value` attribute, then caps
// the length. The backend redacts again with the vault and test data values.
export function a11yRedactor(values: Array<string | undefined | null>): (text: any, cap?: number) => string {
  const patterns = Array.from(new Set(values.map((v) => String(v || '').trim()).filter((v) => v.length >= 3)))
    .sort((a, b) => b.length - a.length)
    .map((v) => new RegExp(v.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'));
  return (text: any, cap?: number) => {
    let out = typeof text === 'string' ? text : '';
    out = out.replace(/\svalue\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, '');
    out = patterns.reduce((acc, p) => acc.replace(p, REDACTED), out);
    return cap ? out.slice(0, cap) : out;
  };
}

// The URL without its hash; the path (query and hash ignored) is the dedupe key.
function pageUrl(url: string): string | null {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
    parsed.hash = '';
    return parsed.toString();
  } catch {
    return null;
  }
}

export function startA11yRecorder(page: any): A11yRecorder {
  const urls = new Map<string, string>();
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;

  const record = () => {
    if (page.isClosed()) return;
    const url = pageUrl(page.url());
    const path = url ? normalizeHarvestPath(url) : null;
    if (url && path && !urls.has(path)) urls.set(path, url);
  };

  const onNavigated = (frame: any) => {
    if (stopped || frame !== page.mainFrame()) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      try {
        record();
      } catch {
        // The page may be closing.
      }
    }, SETTLE_MS);
  };

  page.on('framenavigated', onNavigated);

  return {
    stop() {
      stopped = true;
      if (timer) clearTimeout(timer);
      timer = null;
      try {
        page.off('framenavigated', onNavigated);
        // The page the test ended on counts even if it settled under 1000 ms.
        record();
      } catch {
        // The page may already be closed.
      }
      return Array.from(urls.values());
    },
  };
}

function trimRules(rules: any[], maxNodes: number, redact: (t: any, cap?: number) => string): A11yRule[] {
  return (rules || []).map((rule: any) => ({
    id: String(rule.id || ''),
    impact: rule.impact || null,
    tags: Array.isArray(rule.tags) ? rule.tags : [],
    help: String(rule.help || ''),
    helpUrl: String(rule.helpUrl || ''),
    description: String(rule.description || ''),
    nodes_total: (rule.nodes || []).length,
    nodes: (rule.nodes || []).slice(0, maxNodes).map((node: any) => ({
      target: Array.isArray(node.target) ? node.target : [],
      html: redact(node.html, MAX_SNIPPET_CHARS),
      failureSummary: redact(node.failureSummary),
      impact: node.impact || null,
    })),
  }));
}

function pageErrorFor(error: any): A11yPageError {
  const message = String(error?.message || error || '');
  return /content security policy|\bcsp\b|refused to (execute|evaluate|load)|unsafe-eval/i.test(message)
    ? 'csp_blocked'
    : 'axe_error';
}

export interface A11yScanOptions {
  // The test's browser context, so pages behind login stay signed in.
  context: any;
  urls: string[];
  settings: A11yScanSettings;
  // Earlier results in this run (another test of the same spec), so the page
  // cap, the time budget, and the path dedupe apply to the whole run.
  previous: A11yRawReport;
  redact: Array<string | undefined | null>;
  log?: (message: string) => void;
  // Runs after a page opens and before axe. Returning false skips axe for
  // that page; the check sets `record.error` (standalone scan, AC-11).
  beforeScan?: (page: any, url: string, record: A11yPageRecord) => Promise<boolean>;
}

const SHOT_IMPACTS = new Set(['critical', 'serious']);
const SHOT_TIMEOUT_MS = 2000;

function countShots(report: A11yRawReport): number {
  let count = 0;
  for (const result of report.results) {
    for (const rule of result.violations) {
      for (const node of rule.nodes) if (node.screenshot) count += 1;
    }
  }
  return count;
}

function shotName(ruleId: string, url: string, target: any[]): string {
  const rule = ruleId.replace(/[^A-Za-z0-9_-]/g, '_').slice(0, 60) || 'rule';
  const digest = crypto.createHash('sha1').update(`${normalizeHarvestPath(url)}|${JSON.stringify(target)}`).digest('hex');
  return `${rule}-${digest.slice(0, 16)}.png`;
}

// Screenshots critical and serious violation nodes, within the run's cap and
// the time left. A node that cannot be captured (in a frame or a shadow root,
// hidden, detached) is skipped. Never throws.
async function captureShots(
  page: any,
  url: string,
  rules: A11yRule[],
  settings: A11yScanSettings,
  state: { taken: number },
  timeLeft: () => number,
  log: (message: string) => void,
): Promise<void> {
  const max = settings.maxShots || 0;
  if (!settings.shotDir || max <= 0 || state.taken >= max) return;
  try {
    fs.mkdirSync(settings.shotDir, { recursive: true });
  } catch {
    return;
  }
  for (const rule of rules) {
    for (const node of rule.nodes) {
      if (state.taken >= max || timeLeft() < 500) return;
      const impact = node.impact || rule.impact;
      // Only a plain, top level selector can be located again.
      if (!impact || !SHOT_IMPACTS.has(impact) || node.target.length !== 1 || typeof node.target[0] !== 'string') continue;
      const name = shotName(rule.id, url, node.target);
      try {
        await page.locator(node.target[0]).first().screenshot({
          path: path.join(settings.shotDir, name),
          timeout: Math.min(SHOT_TIMEOUT_MS, timeLeft()),
          animations: 'disabled',
        });
        node.screenshot = name;
        state.taken += 1;
      } catch (error: any) {
        log(`[A11Y] Element screenshot skipped for ${rule.id}: ${error?.message || error}`);
      }
    }
  }
}

// Scans the URLs not yet in `previous` and returns the merged report. Never throws.
export async function scanPages(options: A11yScanOptions): Promise<A11yRawReport> {
  const { context, urls, settings, previous } = options;
  const log = options.log || ((message: string) => console.warn(message));
  const redact = a11yRedactor(options.redact);
  const report: A11yRawReport = {
    ...previous,
    wcag_tags: settings.tags,
    pages: [...previous.pages],
    results: [...previous.results],
  };
  const seen = new Set(report.pages.map((p) => normalizeHarvestPath(p.url)).filter(Boolean) as string[]);
  const started = Date.now();
  const spent = () => previous.elapsed_ms + (Date.now() - started);
  let scanned = report.pages.filter((p) => p.error !== 'budget').length;
  const shots = { taken: countShots(previous) };

  try {
    const AxeBuilder = urls.length ? await loadAxeBuilder() : null;
    for (const url of urls) {
      const path = normalizeHarvestPath(url);
      if (!path || seen.has(path)) continue;
      seen.add(path);

      const remaining = settings.budgetMs - spent();
      if (scanned >= settings.maxPages || remaining <= 0) {
        report.pages.push({ url, title: '', scanned_at: null, requires_login: false, error: 'budget' });
        continue;
      }
      scanned += 1;

      const record: A11yPageRecord = { url, title: '', scanned_at: null, requires_login: false, error: null };
      report.pages.push(record);
      const page = await context.newPage();
      try {
        try {
          const opened = await withDeadline(page.goto(url, { waitUntil: 'load', timeout: remaining }), remaining);
          if (opened === null && spent() >= settings.budgetMs) {
            record.error = 'budget';
            continue;
          }
        } catch (error: any) {
          record.error = spent() >= settings.budgetMs ? 'budget' : 'navigation_failed';
          log(`[A11Y] Could not open ${path}: ${error?.message || error}`);
          continue;
        }
        record.title = redact(await page.title().catch(() => ''), 200);
        if (options.beforeScan && !(await options.beforeScan(page, url, record).catch(() => true))) continue;
        try {
          const result: any = await withDeadline(
            new AxeBuilder({ page }).withTags(settings.tags).analyze(),
            settings.budgetMs - spent(),
          );
          if (!result) {
            record.error = 'budget';
            log(`[A11Y] Scan of ${path} stopped at the ${settings.budgetMs} ms budget`);
            continue;
          }
          record.scanned_at = new Date().toISOString();
          const violations = trimRules(result.violations, settings.maxNodesPerRule, redact);
          await captureShots(page, url, violations, settings, shots, () => settings.budgetMs - spent(), log);
          report.results.push({
            url,
            violations,
            incomplete: trimRules(result.incomplete, settings.maxNodesPerRule, redact),
          });
        } catch (error: any) {
          record.error = pageErrorFor(error);
          log(`[A11Y] Scan of ${path} skipped (${record.error}): ${error?.message || error}`);
        }
      } finally {
        await page.close().catch(() => {});
      }
    }
  } catch (error: any) {
    // The context itself failed (closed, crashed): the whole scan failed.
    log(`[A11Y] Scan failed: ${error?.message || error}`);
    report.status = 'failed';
    report.error = 'scan_error';
  }
  report.elapsed_ms = spent();
  return report;
}

// Run folder helpers for the Python runner's generated spec.
export function readA11yFile(file: string, tags: string[]): A11yRawReport {
  try {
    const data = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (data && Array.isArray(data.pages) && Array.isArray(data.results)) return data;
  } catch {
    // Missing or unreadable: start fresh.
  }
  return emptyA11yReport(tags);
}

export function writeA11yFile(file: string, report: A11yRawReport): void {
  try {
    fs.writeFileSync(file, JSON.stringify(report));
  } catch {
    console.warn('[A11Y] Could not write a11y.json');
  }
}
