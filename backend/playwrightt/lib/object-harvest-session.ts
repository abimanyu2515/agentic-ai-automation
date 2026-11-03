// Harvest during execution (spec 0004, AC-16, AC-17, AC-20).
//
// Both runners use this: the workspace engine per execution, and the Python
// runner's generated spec per test. It snapshots each distinct path the main
// page settles on (1000 ms with no main frame navigation) with the shared
// extractor in lib/object-harvest.js. Harvesting is best effort: every error,
// page close, and deadline is swallowed, so it can never change a run's result.

export interface HarvestSettings {
  enabled: boolean;
  maxPages: number;
  budgetMs: number;
}

export interface HarvestSnapshot {
  url: string;
  elements: any[];
}

export interface HarvestOptions {
  page: any;
  // The extractor's function expression text.
  source: string;
  settings: HarvestSettings;
  // Normalized paths already taken in this execution (or worker).
  harvested: Set<string>;
  // Values to replace with [redacted] (the target credentials).
  redact: Array<string | undefined | null>;
  onSnapshot: (snapshot: HarvestSnapshot) => void;
  log?: (message: string) => void;
}

export interface HarvestSession {
  stop(): Promise<void>;
}

const SETTLE_MS = 1000;
const REDACTED = '[redacted]';

// Path only, trailing slash removed, case kept; the hash is ignored.
export function normalizeHarvestPath(url: string): string | null {
  try {
    const path = new URL(url).pathname || '/';
    return path.replace(/\/+$/, '') || '/';
  } catch {
    return null;
  }
}

function withDeadline<T>(promise: Promise<T>, ms: number): Promise<T | null> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<null>((resolve) => {
    timer = setTimeout(() => resolve(null), ms);
  });
  return Promise.race([promise, deadline]).finally(() => clearTimeout(timer));
}

function redactor(values: HarvestOptions['redact']): (text: any) => any {
  const patterns = Array.from(new Set(values.map((v) => String(v || '').trim()).filter((v) => v.length >= 3)))
    .sort((a, b) => b.length - a.length)
    .map((v) => new RegExp(v.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'));
  return (text: any) => (typeof text === 'string' ? patterns.reduce((out, p) => out.replace(p, REDACTED), text) : text);
}

export function redactHarvestElements(elements: any[], values: HarvestOptions['redact']): any[] {
  const redact = redactor(values);
  return (elements || []).map((element) => ({
    ...element,
    label: redact(element.label),
    placeholder: redact(element.placeholder),
    attributes: Object.fromEntries(Object.entries(element.attributes || {}).map(([k, v]) => [k, redact(v)])),
    locators: (element.locators || []).map((locator: any) => ({
      ...locator,
      value: redact(locator.value),
      options: locator.options && 'name' in locator.options
        ? { ...locator.options, name: redact(locator.options.name) }
        : locator.options,
    })),
  }));
}

export function startHarvest(options: HarvestOptions): HarvestSession {
  const { page, source, settings, harvested, redact, onSnapshot } = options;
  const log = options.log || ((message: string) => console.warn(message));
  let timer: ReturnType<typeof setTimeout> | null = null;
  let pending: Promise<void> | null = null;
  let stopped = false;

  const take = async () => {
    if (stopped || page.isClosed()) return;
    const path = normalizeHarvestPath(page.url());
    if (!path || harvested.has(path) || harvested.size >= settings.maxPages) return;
    harvested.add(path);
    try {
      const result: any = await withDeadline(page.evaluate(`(${source})({})`), settings.budgetMs);
      if (!result) {
        log(`[OBJREPO-HARVEST] Snapshot of ${path} abandoned after ${settings.budgetMs} ms`);
        return;
      }
      if (normalizeHarvestPath(page.url()) !== path) {
        // The page moved while the snapshot ran; a later settle can retry it.
        harvested.delete(path);
        return;
      }
      onSnapshot({ url: path, elements: redactHarvestElements(result.elements, redact) });
    } catch (error: any) {
      log(`[OBJREPO-HARVEST] Snapshot of ${path} skipped: ${error?.message || error}`);
    }
  };

  const onNavigated = (frame: any) => {
    if (stopped || frame !== page.mainFrame()) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      const previous = pending || Promise.resolve();
      pending = previous.then(take).catch(() => {});
    }, SETTLE_MS);
  };

  page.on('framenavigated', onNavigated);

  return {
    async stop() {
      stopped = true;
      if (timer) clearTimeout(timer);
      timer = null;
      try {
        page.off('framenavigated', onNavigated);
      } catch {
        // The page may already be closed.
      }
      // Wait at most one budget for a snapshot still running, then drop it.
      if (pending) await withDeadline(pending, settings.budgetMs);
    },
  };
}
