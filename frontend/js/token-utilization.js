// Token Utilization view: real LLM token usage and cost per agent, model, role and project.
import { API } from './api.js?v=17';
import { escapeHTML } from './components.js?v=3';

const STORAGE_KEY = 'token_utilization_prefs';
const RANGES = [
  { id: 'today', label: 'Today' },
  { id: '7d', label: '7 days' },
  { id: '30d', label: '30 days' },
  { id: 'mtd', label: 'Month to date' },
  { id: '90d', label: '90 days' },
  { id: 'all', label: 'All time' },
  { id: 'custom', label: 'Custom' }
];
const GROUPS = [
  { id: 'agent', label: 'Agent' },
  { id: 'model', label: 'Model' },
  { id: 'role', label: 'Role' },
  { id: 'provider', label: 'Provider' },
  { id: 'project_id', label: 'Project' },
  { id: 'operation', label: 'Operation' }
];
const FILTER_KEYS = ['project_id', 'agent', 'model', 'role', 'status'];
const SERIES_SLOTS = 8;

const esc = (value) => escapeHTML(value === null || value === undefined ? '' : String(value));

const compact = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 });
const whole = new Intl.NumberFormat();

export function formatTokens(n) {
  n = Number(n) || 0;
  return n < 10000 ? whole.format(n) : compact.format(n);
}

export function formatCost(v) {
  v = Number(v) || 0;
  if (v === 0) return '$0.00';
  if (Math.abs(v) < 0.01) return `$${v.toFixed(4)}`;
  if (Math.abs(v) >= 10000) return `$${compact.format(v)}`;
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatMs(ms) {
  ms = Number(ms) || 0;
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

function formatDateTime(value) {
  if (!value) return '—';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString();
}

export function prettyAgent(key) {
  if (!key) return 'Unattributed';
  return String(key)
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function rangeBounds(range, customFrom, customTo) {
  const now = new Date();
  const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  switch (range) {
    case 'today': return { from: startOfDay(now), to: null };
    case '7d': return { from: new Date(now - 7 * 864e5), to: null };
    case '30d': return { from: new Date(now - 30 * 864e5), to: null };
    case '90d': return { from: new Date(now - 90 * 864e5), to: null };
    case 'mtd': return { from: new Date(now.getFullYear(), now.getMonth(), 1), to: null };
    case 'custom': return {
      from: customFrom ? new Date(`${customFrom}T00:00:00`) : null,
      to: customTo ? new Date(new Date(`${customTo}T00:00:00`).getTime() + 864e5) : null
    };
    default: return { from: null, to: null };
  }
}

function defaultBucket(range) {
  if (range === 'today') return 'hour';
  if (range === '90d' || range === 'all') return 'week';
  return 'day';
}

function cssVar(name, fallback = '') {
  const root = document.getElementById('token-utilization-view') || document.body;
  return getComputedStyle(root).getPropertyValue(name).trim() || fallback;
}

function loadPrefs() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') || {};
  } catch (e) {
    return {};
  }
}

function savePrefs(prefs) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch (e) { /* storage unavailable: preferences just are not remembered */ }
}

export class TokenUtilizationView {
  constructor() {
    const prefs = loadPrefs();
    this.state = {
      range: prefs.range || '30d',
      customFrom: prefs.customFrom || '',
      customTo: prefs.customTo || '',
      bucket: prefs.bucket || '',
      metric: prefs.metric || 'cost',
      split: prefs.split ?? 'agent',
      groupBy: prefs.groupBy || 'agent',
      sort: { key: 'cost_usd', dir: 'desc' },
      filters: { project_id: '', agent: '', model: '', role: '', status: '' },
      callsPage: 1,
      callsSort: '-created_at',
      autoRefresh: !!prefs.autoRefresh
    };
    this.projects = [];
    this.facets = {};
    this.colorKeys = { agent: [], model: [], role: [], provider: [], project_id: [], operation: [], status: [] };
    this.charts = {};
    this.data = {};
    this.expandedCall = null;
    this.timer = null;
    this.mounted = false;
    this.loadSeq = 0;
  }

  // ── lifecycle ───────────────────────────────────────────────────────

  async render(currentProject) {
    this.root = document.getElementById('token-utilization-root');
    if (!this.root) return;
    if (!this.mounted) {
      this.mount();
      this.mounted = true;
    }
    try {
      const projects = await API.getProjects();
      this.projects = Array.isArray(projects) ? projects : [];
    } catch (e) {
      this.projects = [];
    }
    this.syncControls();
    this.scheduleRefresh();
    await this.load();
  }

  isVisible() {
    const panel = document.getElementById('token-utilization-view');
    return !!(panel && panel.classList.contains('active'));
  }

  scheduleRefresh() {
    clearInterval(this.timer);
    if (!this.state.autoRefresh) return;
    this.timer = setInterval(() => {
      if (this.isVisible() && !document.hidden) this.load({ quiet: true });
    }, 30000);
  }

  persist() {
    const { range, customFrom, customTo, bucket, metric, split, groupBy, autoRefresh } = this.state;
    savePrefs({ range, customFrom, customTo, bucket, metric, split, groupBy, autoRefresh });
  }

  queryFilters() {
    const { from, to } = rangeBounds(this.state.range, this.state.customFrom, this.state.customTo);
    const filters = {};
    if (from) filters.from = from.toISOString();
    if (to) filters.to = to.toISOString();
    FILTER_KEYS.forEach((key) => {
      if (this.state.filters[key]) filters[key] = this.state.filters[key];
    });
    return filters;
  }

  // ── data ────────────────────────────────────────────────────────────

  async load({ quiet = false } = {}) {
    const seq = ++this.loadSeq;
    const filters = this.queryFilters();
    const bucket = this.state.bucket || defaultBucket(this.state.range);
    if (!quiet) this.root.classList.add('tu-loading');
    try {
      const [summary, series, breakdown, agents, models, calls, facets] = await Promise.all([
        API.getTokenUsageSummary(filters),
        API.getTokenUsageTimeseries(filters, bucket, this.state.split),
        API.getTokenUsageBreakdown(filters, this.state.groupBy),
        API.getTokenUsageBreakdown(filters, 'agent'),
        API.getTokenUsageBreakdown(filters, 'model'),
        API.getTokenUsageCalls(filters, this.state.callsPage, 25, this.state.callsSort),
        API.getTokenUsageFacets({})
      ]);
      if (seq !== this.loadSeq) return; // a newer load superseded this one
      this.data = { summary, series, breakdown, agents, models, calls };
      this.facets = facets || {};
      this.updateColorKeys();
      this.renderFilters();
      this.renderAll();
      this.setError('');
    } catch (err) {
      if (seq !== this.loadSeq) return;
      this.setError(err.message || 'Could not load token usage.');
    } finally {
      if (seq === this.loadSeq) this.root.classList.remove('tu-loading');
    }
  }

  // Colours follow the entity: a key keeps its slot across filters and reloads.
  updateColorKeys() {
    Object.keys(this.colorKeys).forEach((field) => {
      const known = this.colorKeys[field];
      (this.facets[field] || []).forEach((key) => {
        if (!known.includes(key)) known.push(key);
      });
    });
  }

  colorFor(field, key) {
    if (key === 'Other') return cssVar('--tu-other');
    if (!field) return cssVar('--tu-series-1');
    const known = this.colorKeys[field] || [];
    let index = known.indexOf(key);
    if (index < 0) {
      known.push(key);
      index = known.length - 1;
    }
    return index < SERIES_SLOTS ? cssVar(`--tu-series-${index + 1}`) : cssVar('--tu-other');
  }

  labelFor(field, key) {
    if (field === 'agent') return prettyAgent(key);
    if (field === 'project_id') {
      const project = this.projects.find((p) => String(p.id) === String(key));
      return project ? project.name : (key === '(none)' ? 'No project' : `${String(key).slice(0, 8)}…`);
    }
    if (key === '(none)') return 'None';
    return key;
  }

  // ── skeleton and controls ───────────────────────────────────────────

  mount() {
    const rangeButtons = RANGES.map((r) =>
      `<button type="button" class="tu-chip" data-range="${r.id}">${esc(r.label)}</button>`).join('');
    this.root.innerHTML = `
      <div class="tu-toolbar section-card">
        <div class="tu-ranges" role="group" aria-label="Date range">${rangeButtons}</div>
        <div class="tu-custom-range" hidden>
          <label>From <input type="date" class="form-control" data-custom="customFrom"></label>
          <label>To <input type="date" class="form-control" data-custom="customTo"></label>
        </div>
        <div class="tu-filter-row">
          <select class="form-control" data-filter="project_id" aria-label="Project"></select>
          <select class="form-control" data-filter="agent" aria-label="Agent"></select>
          <select class="form-control" data-filter="model" aria-label="Model"></select>
          <select class="form-control" data-filter="role" aria-label="Role"></select>
          <select class="form-control" data-filter="status" aria-label="Status"></select>
          <button type="button" class="btn btn-secondary" data-action="clear">Clear filters</button>
          <span class="tu-spacer"></span>
          <label class="tu-toggle"><input type="checkbox" data-action="auto-refresh"> Auto-refresh</label>
          <button type="button" class="btn btn-secondary" data-action="refresh">Refresh</button>
          <button type="button" class="btn btn-primary" data-action="export">Export CSV</button>
        </div>
        <div class="tu-active-filters"></div>
      </div>
      <div class="tu-error" role="alert" hidden></div>
      <div class="tu-notice" hidden></div>

      <div class="tu-kpis"></div>

      <div class="tu-row tu-row-wide">
        <div class="section-card chart-card tu-card">
          <div class="tu-card-head">
            <h2>Usage over time</h2>
            <div class="tu-seg" role="group" aria-label="Metric">
              <button type="button" class="tu-chip" data-metric="cost">Cost</button>
              <button type="button" class="tu-chip" data-metric="tokens">Tokens</button>
              <button type="button" class="tu-chip" data-metric="calls">Calls</button>
            </div>
            <div class="tu-seg" role="group" aria-label="Split by">
              <button type="button" class="tu-chip" data-split="">Total</button>
              <button type="button" class="tu-chip" data-split="agent">By agent</button>
              <button type="button" class="tu-chip" data-split="model">By model</button>
              <button type="button" class="tu-chip" data-split="role">By role</button>
            </div>
            <select class="form-control tu-bucket" aria-label="Interval">
              <option value="">Auto interval</option>
              <option value="hour">Hourly</option>
              <option value="day">Daily</option>
              <option value="week">Weekly</option>
            </select>
          </div>
          <div class="tu-legend" data-legend="timeline"></div>
          <div class="tu-chart tu-chart-tall"><canvas id="tu-timeline-chart" aria-label="Usage over time"></canvas></div>
        </div>
        <div class="section-card tu-card tu-budget"></div>
      </div>

      <div class="tu-row">
        <div class="section-card chart-card tu-card">
          <div class="tu-card-head"><h2>Cost by agent</h2><span class="tu-hint">Click a bar to filter</span></div>
          <div class="tu-chart"><canvas id="tu-agent-chart" aria-label="Cost by agent"></canvas></div>
        </div>
        <div class="section-card chart-card tu-card">
          <div class="tu-card-head"><h2>Tokens by model</h2><span class="tu-hint">Input vs output · click to filter</span></div>
          <div class="tu-legend" data-legend="models"></div>
          <div class="tu-chart"><canvas id="tu-model-chart" aria-label="Tokens by model"></canvas></div>
        </div>
      </div>

      <div class="section-card tu-card">
        <div class="tu-card-head">
          <h2>Breakdown</h2>
          <div class="tu-seg" role="group" aria-label="Group by">
            ${GROUPS.map((g) => `<button type="button" class="tu-chip" data-group="${g.id}">${esc(g.label)}</button>`).join('')}
          </div>
        </div>
        <div class="table-container tu-table-wrap"><table class="custom-table tu-table tu-breakdown"></table></div>
      </div>

      <div class="section-card tu-card">
        <div class="tu-card-head"><h2>Call log</h2><span class="tu-hint">Every LLM attempt, including retries and failures. Click a row for details.</span></div>
        <div class="table-container tu-table-wrap"><table class="custom-table tu-table tu-calls"></table></div>
        <div class="tu-pager"></div>
      </div>

      <details class="section-card tu-card tu-pricing">
        <summary><h2>Model pricing</h2><span class="tu-hint">USD per 1M tokens · overrides apply to new calls</span></summary>
        <div class="tu-pricing-body"></div>
      </details>
    `;
    this.bindEvents();
    this.observeTheme();
  }

  bindEvents() {
    const root = this.root;
    root.addEventListener('click', (event) => {
      const target = event.target.closest('button, th[data-sort], tr[data-call], [data-remove-filter]');
      if (!target || !root.contains(target)) return;

      if (target.dataset.range) {
        this.state.range = target.dataset.range;
        this.state.callsPage = 1;
        this.afterChange();
      } else if (target.dataset.metric) {
        this.state.metric = target.dataset.metric;
        this.persist();
        this.syncControls();
        this.renderTimeline();
      } else if (target.dataset.split !== undefined && target.matches('.tu-chip[data-split]')) {
        this.state.split = target.dataset.split;
        this.afterChange();
      } else if (target.dataset.group) {
        this.state.groupBy = target.dataset.group;
        this.afterChange();
      } else if (target.dataset.removeFilter) {
        this.state.filters[target.dataset.removeFilter] = '';
        this.state.callsPage = 1;
        this.afterChange();
      } else if (target.dataset.sort) {
        const key = target.dataset.sort;
        const sort = this.state.sort;
        this.state.sort = { key, dir: sort.key === key && sort.dir === 'desc' ? 'asc' : 'desc' };
        this.renderBreakdown();
      } else if (target.dataset.callSort) {
        const key = target.dataset.callSort;
        this.state.callsSort = this.state.callsSort === `-${key}` ? key : `-${key}`;
        this.state.callsPage = 1;
        this.load({ quiet: true });
      } else if (target.dataset.page) {
        this.state.callsPage = Number(target.dataset.page);
        this.load({ quiet: true });
      } else if (target.dataset.call) {
        this.expandedCall = this.expandedCall === target.dataset.call ? null : target.dataset.call;
        this.renderCalls();
      } else if (target.dataset.drill) {
        this.drill(target.dataset.drillField, target.dataset.drill);
      } else if (target.dataset.action === 'clear') {
        FILTER_KEYS.forEach((k) => { this.state.filters[k] = ''; });
        this.state.callsPage = 1;
        this.afterChange();
      } else if (target.dataset.action === 'refresh') {
        this.load();
      } else if (target.dataset.action === 'export') {
        this.exportCsv(target);
      } else if (target.dataset.action === 'edit-budget') {
        this.root.querySelector('.tu-budget-form')?.toggleAttribute('hidden');
      } else if (target.dataset.action === 'save-budget') {
        this.saveBudget(target);
      } else if (target.dataset.action === 'save-prices') {
        this.savePrices(target);
      } else if (target.dataset.action === 'reset-price') {
        this.resetPrice(target.dataset.model, target);
      }
    });

    root.addEventListener('change', (event) => {
      const el = event.target;
      if (el.dataset.filter) {
        this.state.filters[el.dataset.filter] = el.value;
        this.state.callsPage = 1;
        this.afterChange();
      } else if (el.dataset.custom) {
        this.state[el.dataset.custom] = el.value;
        this.afterChange();
      } else if (el.classList.contains('tu-bucket')) {
        this.state.bucket = el.value;
        this.afterChange();
      } else if (el.dataset.action === 'auto-refresh') {
        this.state.autoRefresh = el.checked;
        this.persist();
        this.scheduleRefresh();
      }
    });

    root.querySelector('details.tu-pricing').addEventListener('toggle', (event) => {
      if (event.target.open) this.loadPricing();
    });
  }

  observeTheme() {
    new MutationObserver(() => {
      if (this.isVisible() && this.data.summary) this.renderCharts();
    }).observe(document.body, { attributes: true, attributeFilter: ['class'] });
  }

  afterChange() {
    this.persist();
    this.syncControls();
    this.load();
  }

  drill(field, value) {
    if (!FILTER_KEYS.includes(field)) return;
    this.state.filters[field] = this.state.filters[field] === value ? '' : value;
    this.state.callsPage = 1;
    this.afterChange();
  }

  syncControls() {
    const root = this.root;
    root.querySelectorAll('[data-range]').forEach((b) => b.classList.toggle('active', b.dataset.range === this.state.range));
    root.querySelectorAll('[data-metric]').forEach((b) => b.classList.toggle('active', b.dataset.metric === this.state.metric));
    root.querySelectorAll('.tu-chip[data-split]').forEach((b) => b.classList.toggle('active', b.dataset.split === this.state.split));
    root.querySelectorAll('[data-group]').forEach((b) => b.classList.toggle('active', b.dataset.group === this.state.groupBy));
    root.querySelectorAll('.tu-chip').forEach((b) => b.setAttribute('aria-pressed', b.classList.contains('active') ? 'true' : 'false'));
    const custom = root.querySelector('.tu-custom-range');
    custom.hidden = this.state.range !== 'custom';
    root.querySelector('[data-custom="customFrom"]').value = this.state.customFrom;
    root.querySelector('[data-custom="customTo"]').value = this.state.customTo;
    root.querySelector('.tu-bucket').value = this.state.bucket;
    root.querySelector('[data-action="auto-refresh"]').checked = this.state.autoRefresh;
  }

  renderFilters() {
    const options = {
      project_id: { label: 'All projects', values: this.projects.map((p) => String(p.id)) },
      agent: { label: 'All agents', values: this.facets.agent || [] },
      model: { label: 'All models', values: this.facets.model || [] },
      role: { label: 'All roles', values: this.facets.role || [] },
      status: { label: 'All statuses', values: this.facets.status || [] }
    };
    FILTER_KEYS.forEach((field) => {
      const select = this.root.querySelector(`[data-filter="${field}"]`);
      const current = this.state.filters[field];
      const values = [...new Set([...options[field].values, ...(current ? [current] : [])])];
      select.innerHTML = `<option value="">${esc(options[field].label)}</option>` +
        values.map((v) => `<option value="${esc(v)}">${esc(this.labelFor(field, v))}</option>`).join('');
      select.value = current;
    });

    const active = FILTER_KEYS.filter((k) => this.state.filters[k]);
    this.root.querySelector('.tu-active-filters').innerHTML = active.map((k) => `
      <button type="button" class="tu-pill" data-remove-filter="${k}" title="Remove filter">
        ${esc(k === 'project_id' ? 'Project' : k.charAt(0).toUpperCase() + k.slice(1))}: <strong>${esc(this.labelFor(k, this.state.filters[k]))}</strong> ✕
      </button>`).join('');
  }

  setError(message) {
    const box = this.root.querySelector('.tu-error');
    box.hidden = !message;
    box.textContent = message;
  }

  // ── rendering ───────────────────────────────────────────────────────

  renderAll() {
    this.renderNotice();
    this.renderKpis();
    this.renderBudget();
    this.renderCharts();
    this.renderBreakdown();
    this.renderCalls();
    if (this.root.querySelector('details.tu-pricing').open) this.loadPricing();
  }

  renderCharts() {
    this.renderTimeline();
    this.renderAgentChart();
    this.renderModelChart();
  }

  renderNotice() {
    const notice = this.root.querySelector('.tu-notice');
    const unpriced = this.data.summary?.unpriced_models || [];
    notice.hidden = unpriced.length === 0;
    notice.innerHTML = unpriced.length
      ? `<strong>${unpriced.length} model${unpriced.length > 1 ? 's have' : ' has'} no price</strong> (${unpriced.map(esc).join(', ')}).
         Their tokens are counted but their cost shows as $0. Add a price under <em>Model pricing</em> below.`
      : '';
  }

  renderKpis() {
    const s = this.data.summary || {};
    const d = s.deltas || {};
    const hasPrevious = !!s.previous;
    const delta = (key, upIsBad = true) => {
      const value = d[key];
      if (!hasPrevious) return '';
      if (value === null || value === undefined) return '<span class="tu-delta">no prior data</span>';
      const up = value > 0;
      const cls = value === 0 ? '' : ((up === upIsBad) ? 'tu-delta-bad' : 'tu-delta-good');
      return `<span class="tu-delta ${cls}">${up ? '▲' : value < 0 ? '▼' : '•'} ${Math.abs(value)}% vs prior period</span>`;
    };
    const cards = [
      { label: 'Total cost', value: formatCost(s.cost_usd), sub: delta('cost_usd') },
      { label: 'Total tokens', value: formatTokens(s.total_tokens),
        sub: `<span class="tu-sub">${formatTokens(s.prompt_tokens)} in · ${formatTokens(s.completion_tokens)} out</span>${delta('total_tokens')}` },
      { label: 'LLM calls', value: whole.format(s.calls || 0), sub: delta('calls', false) },
      { label: 'Avg cost / call', value: formatCost(s.avg_cost_per_call),
        sub: `<span class="tu-sub">${formatTokens(Math.round(s.avg_tokens_per_call || 0))} tokens / call</span>${delta('avg_cost_per_call')}` },
      { label: 'Error rate', value: `${((s.error_rate || 0) * 100).toFixed(1)}%`,
        sub: `<span class="tu-sub">${whole.format(s.errors || 0)} failed · ${whole.format(s.retries || 0)} retries</span>${delta('error_rate')}` },
      { label: 'Avg latency', value: formatMs(s.avg_latency_ms), sub: delta('avg_latency_ms') }
    ];
    this.root.querySelector('.tu-kpis').innerHTML = cards.map((c) => `
      <div class="metric-card tu-kpi">
        <span class="metric-label">${esc(c.label)}</span>
        <span class="metric-value">${esc(c.value)}</span>
        ${c.sub || ''}
      </div>`).join('');
  }

  renderBudget() {
    const b = this.data.summary?.budget || {};
    const card = this.root.querySelector('.tu-budget');
    const hasBudget = b.monthly_budget_usd > 0;
    const used = Math.min(b.used_pct || 0, 100);
    const projected = Math.min(b.projected_pct || 0, 100);
    const stateLabel = { ok: 'On track', warning: 'Approaching budget', exceeded: 'Budget exceeded', none: 'No budget set' }[b.state] || '';
    const stateIcon = { ok: '✓', warning: '!', exceeded: '✕', none: '–' }[b.state] || '';
    const scope = this.state.filters.project_id ? 'this project' : 'all projects';
    card.innerHTML = `
      <div class="tu-card-head"><h2>Monthly budget</h2>
        <button type="button" class="btn btn-secondary tu-btn-sm" data-action="edit-budget">Edit</button></div>
      <div class="tu-budget-state tu-state-${esc(b.state)}"><span class="tu-state-icon" aria-hidden="true">${stateIcon}</span>${esc(stateLabel)}</div>
      <div class="tu-budget-figures">
        <div><span class="metric-label">Month to date</span><span class="tu-big">${formatCost(b.month_to_date_usd)}</span></div>
        <div><span class="metric-label">Projected month end</span><span class="tu-big">${formatCost(b.projected_month_end_usd)}</span></div>
      </div>
      ${hasBudget ? `
        <div class="tu-meter" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${used}"
             aria-label="Budget used">
          <div class="tu-meter-projected" style="width:${projected}%"></div>
          <div class="tu-meter-used tu-state-bg-${esc(b.state)}" style="width:${used}%"></div>
          <div class="tu-meter-threshold" style="left:${Math.min(b.alert_threshold_pct, 100)}%" title="Alert threshold"></div>
        </div>
        <div class="tu-meter-caption">${(b.used_pct || 0).toFixed(1)}% of ${formatCost(b.monthly_budget_usd)} used ·
          alert at ${esc(b.alert_threshold_pct)}% · spend for ${esc(scope)}</div>`
      : `<p class="tu-sub">Set a monthly budget to track spend against it and get a warning before you run over.</p>`}
      <div class="tu-budget-form" hidden>
        <label>Monthly budget (USD)
          <input type="number" min="0" step="0.01" class="form-control" data-budget="monthly_budget_usd" value="${esc(b.monthly_budget_usd || 0)}"></label>
        <label>Alert at (% of budget)
          <input type="number" min="0" max="100" step="1" class="form-control" data-budget="alert_threshold_pct" value="${esc(b.alert_threshold_pct ?? 80)}"></label>
        <button type="button" class="btn btn-primary tu-btn-sm" data-action="save-budget">Save</button>
      </div>`;
  }

  baseChartOptions() {
    const text = cssVar('--text-secondary', '#94a3b8');
    const grid = cssVar('--tu-grid', '#1f2937');
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 250 },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: cssVar('--bg-secondary', '#111827'),
          titleColor: cssVar('--text-primary', '#f8fafc'),
          bodyColor: cssVar('--text-secondary', '#94a3b8'),
          borderColor: cssVar('--border-focus', '#374151'),
          borderWidth: 1,
          padding: 10,
          boxPadding: 4
        }
      },
      scales: {
        x: { ticks: { color: text }, grid: { display: false }, border: { color: grid } },
        y: { ticks: { color: text }, grid: { color: grid }, border: { display: false }, beginAtZero: true }
      }
    };
  }

  chartUnavailable(canvasId) {
    const canvas = document.getElementById(canvasId);
    if (canvas) canvas.parentElement.innerHTML = '<div class="tu-empty">Chart library offline. The tables below have the same data.</div>';
  }

  replaceChart(name, canvasId, config) {
    this.charts[name]?.destroy();
    if (typeof Chart === 'undefined') {
      this.chartUnavailable(canvasId);
      return null;
    }
    const canvas = document.getElementById(canvasId);
    if (!canvas) return null;
    canvas.parentElement.querySelectorAll('.tu-empty-overlay').forEach((el) => el.remove());
    this.charts[name] = new Chart(canvas, config);
    return this.charts[name];
  }

  metricValues(series) {
    if (this.state.metric === 'tokens') return series.tokens;
    if (this.state.metric === 'calls') return series.calls;
    return series.cost_usd;
  }

  formatMetric(value) {
    if (this.state.metric === 'tokens') return formatTokens(value);
    if (this.state.metric === 'calls') return whole.format(value);
    return formatCost(value);
  }

  renderTimeline() {
    const data = this.data.series;
    if (!data) return;
    const bucket = data.bucket;
    const labels = (data.labels || []).map((l) => {
      const d = new Date(l);
      return bucket === 'hour'
        ? d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
        : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    });
    const split = data.split_by;
    const surface = cssVar('--bg-secondary', '#111827');
    const stacked = !!split;
    const datasets = (data.series || []).map((s) => {
      const color = this.colorFor(split, s.key);
      return stacked
        ? { label: this.labelFor(split, s.key), data: this.metricValues(s), backgroundColor: color,
            borderColor: surface, borderWidth: { top: 2 }, borderRadius: 0, borderSkipped: 'bottom', maxBarThickness: 48 }
        : { label: 'All', data: this.metricValues(s), borderColor: color, backgroundColor: `${color}22`,
            fill: true, borderWidth: 2, tension: 0.25, pointRadius: labels.length > 40 ? 0 : 3, pointHoverRadius: 5 };
    });
    const legend = this.root.querySelector('[data-legend="timeline"]');
    legend.innerHTML = stacked ? datasets.map((ds, i) => `
      <span class="tu-legend-item"><span class="tu-swatch" style="background:${ds.backgroundColor}"></span>${esc(ds.label)}</span>`).join('') : '';

    const options = this.baseChartOptions();
    options.interaction = { mode: 'index', intersect: false };
    options.scales.x.stacked = stacked;
    options.scales.y.stacked = stacked;
    options.scales.y.ticks.callback = (v) => this.formatMetric(v);
    options.plugins.tooltip.callbacks = {
      label: (ctx) => ` ${ctx.dataset.label}: ${this.formatMetric(ctx.parsed.y)}`,
      footer: (items) => (stacked && items.length > 1)
        ? `Total: ${this.formatMetric(items.reduce((sum, it) => sum + (it.parsed.y || 0), 0))}` : ''
    };
    options.plugins.tooltip.filter = (item) => !stacked || item.parsed.y > 0;
    this.replaceChart('timeline', 'tu-timeline-chart', {
      type: stacked ? 'bar' : 'line',
      data: { labels, datasets },
      options
    });
  }

  renderAgentChart() {
    const rows = (this.data.agents?.rows || []).slice(0, 12);
    const color = cssVar('--tu-series-1');
    const selected = this.state.filters.agent;
    const options = this.baseChartOptions();
    options.indexAxis = 'y';
    options.scales.x.grid = { color: cssVar('--tu-grid') };
    options.scales.x.ticks.callback = (v) => formatCost(v);
    options.scales.y.grid = { display: false };
    options.plugins.tooltip.callbacks = {
      label: (ctx) => {
        const row = rows[ctx.dataIndex];
        return [` Cost: ${formatCost(row.cost_usd)} (${row.cost_share_pct}% of spend)`,
          ` Tokens: ${formatTokens(row.total_tokens)} · Calls: ${whole.format(row.calls)}`,
          ` Stage: ${row.stage}`];
      }
    };
    options.onClick = (_, elements) => {
      if (elements.length) this.drill('agent', rows[elements[0].index].key);
    };
    options.onHover = (event, elements) => {
      event.native.target.style.cursor = elements.length ? 'pointer' : 'default';
    };
    this.replaceChart('agents', 'tu-agent-chart', {
      type: 'bar',
      data: {
        labels: rows.map((r) => prettyAgent(r.key)),
        datasets: [{
          label: 'Cost',
          data: rows.map((r) => r.cost_usd),
          backgroundColor: rows.map((r) => (selected && r.key !== selected ? `${color}55` : color)),
          borderRadius: 4,
          borderSkipped: 'start',
          maxBarThickness: 22
        }]
      },
      options
    });
    if (!rows.length) this.emptyCanvas('tu-agent-chart');
  }

  renderModelChart() {
    const rows = (this.data.models?.rows || []).slice(0, 10);
    const inputColor = cssVar('--tu-series-1');
    const outputColor = cssVar('--tu-series-2');
    const surface = cssVar('--bg-secondary', '#111827');
    this.root.querySelector('[data-legend="models"]').innerHTML = `
      <span class="tu-legend-item"><span class="tu-swatch" style="background:${inputColor}"></span>Input tokens</span>
      <span class="tu-legend-item"><span class="tu-swatch" style="background:${outputColor}"></span>Output tokens</span>`;
    const options = this.baseChartOptions();
    options.indexAxis = 'y';
    options.scales.x.stacked = true;
    options.scales.y.stacked = true;
    options.scales.x.grid = { color: cssVar('--tu-grid') };
    options.scales.x.ticks.callback = (v) => formatTokens(v);
    options.scales.y.grid = { display: false };
    options.interaction = { mode: 'index', axis: 'y', intersect: false };
    options.plugins.tooltip.callbacks = {
      label: (ctx) => ` ${ctx.dataset.label}: ${formatTokens(ctx.parsed.x)}`,
      footer: (items) => {
        const row = rows[items[0].dataIndex];
        return `Cost: ${formatCost(row.cost_usd)} · ${whole.format(row.calls)} calls`;
      }
    };
    options.onClick = (_, elements) => {
      if (elements.length) this.drill('model', rows[elements[0].index].key);
    };
    options.onHover = (event, elements) => {
      event.native.target.style.cursor = elements.length ? 'pointer' : 'default';
    };
    const bar = { borderColor: surface, borderWidth: { right: 2 }, borderSkipped: 'start', maxBarThickness: 22 };
    this.replaceChart('models', 'tu-model-chart', {
      type: 'bar',
      data: {
        labels: rows.map((r) => r.key),
        datasets: [
          { label: 'Input', data: rows.map((r) => r.prompt_tokens), backgroundColor: inputColor, ...bar },
          { label: 'Output', data: rows.map((r) => r.completion_tokens), backgroundColor: outputColor, ...bar, borderWidth: 0, borderRadius: { topRight: 4, bottomRight: 4 } }
        ]
      },
      options
    });
    if (!rows.length) this.emptyCanvas('tu-model-chart');
  }

  emptyCanvas(canvasId) {
    const canvas = document.getElementById(canvasId);
    const wrap = canvas?.parentElement;
    if (!wrap || wrap.querySelector('.tu-empty-overlay')) return;
    wrap.insertAdjacentHTML('beforeend', '<div class="tu-empty-overlay">No LLM calls in this period</div>');
  }

  renderBreakdown() {
    const table = this.root.querySelector('.tu-breakdown');
    const field = this.data.breakdown?.group_by || this.state.groupBy;
    const rows = [...(this.data.breakdown?.rows || [])];
    const { key, dir } = this.state.sort;
    rows.sort((a, b) => {
      const av = a[key]; const bv = b[key];
      const cmp = typeof av === 'string' ? String(av).localeCompare(String(bv)) : (av || 0) - (bv || 0);
      return dir === 'asc' ? cmp : -cmp;
    });
    const groupLabel = GROUPS.find((g) => g.id === field)?.label || 'Group';
    const cols = [
      { key: 'key', label: groupLabel },
      ...(field === 'agent' ? [{ key: 'stage', label: 'Stage' }] : []),
      ...(field !== 'model' ? [{ key: 'models', label: 'Models', nosort: true }] : []),
      { key: 'calls', label: 'Calls', num: true },
      { key: 'prompt_tokens', label: 'Input', num: true },
      { key: 'completion_tokens', label: 'Output', num: true },
      { key: 'total_tokens', label: 'Total tokens', num: true },
      { key: 'cost_usd', label: 'Cost', num: true },
      { key: 'cost_share_pct', label: '% of spend', num: true },
      { key: 'avg_tokens_per_call', label: 'Tokens / call', num: true },
      { key: 'avg_latency_ms', label: 'Avg latency', num: true },
      { key: 'errors', label: 'Errors', num: true }
    ];
    const head = cols.map((c) => {
      const arrow = key === c.key ? (dir === 'asc' ? ' ▲' : ' ▼') : '';
      return c.nosort
        ? `<th>${esc(c.label)}</th>`
        : `<th data-sort="${c.key}" class="tu-sortable ${c.num ? 'tu-num' : ''}" aria-sort="${key === c.key ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}">${esc(c.label)}${arrow}</th>`;
    }).join('');
    const drillable = FILTER_KEYS.includes(field);
    const body = rows.length ? rows.map((r) => {
      const swatch = ['agent', 'model', 'role', 'provider', 'project_id', 'operation', 'status'].includes(field)
        ? `<span class="tu-swatch" style="background:${this.colorFor(field, r.key)}"></span>` : '';
      const name = drillable
        ? `<button type="button" class="tu-link" data-drill="${esc(r.key)}" data-drill-field="${field}" title="Filter by this ${esc(groupLabel.toLowerCase())}">${swatch}${esc(this.labelFor(field, r.key))}</button>`
        : `${swatch}${esc(this.labelFor(field, r.key))}`;
      const cells = {
        key: name,
        stage: `<span class="badge tu-stage">${esc(r.stage)}</span>`,
        models: (r.models || []).map(esc).join('<br>'),
        calls: whole.format(r.calls),
        prompt_tokens: formatTokens(r.prompt_tokens),
        completion_tokens: formatTokens(r.completion_tokens),
        total_tokens: formatTokens(r.total_tokens),
        cost_usd: `${formatCost(r.cost_usd)}${r.unpriced_calls ? ' <span class="tu-unpriced" title="Some calls used an unpriced model">*</span>' : ''}`,
        cost_share_pct: `<div class="tu-share"><span style="width:${Math.min(r.cost_share_pct, 100)}%"></span></div>${r.cost_share_pct.toFixed(1)}%`,
        avg_tokens_per_call: formatTokens(Math.round(r.avg_tokens_per_call)),
        avg_latency_ms: formatMs(r.avg_latency_ms),
        errors: r.errors ? `<span class="tu-error-count">${whole.format(r.errors)}</span>` : '0'
      };
      return `<tr>${cols.map((c) => `<td class="${c.num ? 'tu-num' : ''}">${cells[c.key]}</td>`).join('')}</tr>`;
    }).join('') : `<tr><td colspan="${cols.length}" class="tu-empty">No LLM calls match these filters yet. Generate scenarios, test cases, test data or scripts and they will appear here.</td></tr>`;
    table.innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body}</tbody>`;
  }

  renderCalls() {
    const page = this.data.calls || { items: [], total: 0, page: 1, page_size: 25 };
    const table = this.root.querySelector('.tu-calls');
    const sort = this.state.callsSort;
    const th = (key, label, num) => {
      const arrow = sort === key ? ' ▲' : sort === `-${key}` ? ' ▼' : '';
      return `<th class="tu-sortable ${num ? 'tu-num' : ''}"><button type="button" class="tu-th-btn" data-call-sort="${key}">${esc(label)}${arrow}</button></th>`;
    };
    const statusBadge = (s) => {
      const label = { success: '✓ Success', error: '✕ Error', rate_limited: '! Rate limited' }[s] || s;
      return `<span class="badge tu-status tu-status-${esc(s)}">${esc(label)}</span>`;
    };
    const rows = page.items.map((c) => {
      const open = this.expandedCall === c.id;
      const project = c.project_id ? this.labelFor('project_id', c.project_id) : '—';
      const detail = open ? `
        <tr class="tu-call-detail"><td colspan="9">
          <dl>
            <dt>Operation</dt><dd>${esc(c.operation || '—')}</dd>
            <dt>Project</dt><dd>${esc(project)}</dd>
            <dt>Run</dt><dd><code>${esc(c.run_id || '—')}</code></dd>
            <dt>Requirement</dt><dd><code>${esc(c.requirement_id || '—')}</code></dd>
            <dt>Test case</dt><dd><code>${esc(c.test_case_id || '—')}</code></dd>
            <dt>Provider</dt><dd>${esc(c.provider)} · role ${esc(c.role || '—')}</dd>
            <dt>Attempt</dt><dd>${esc(c.attempt)}</dd>
            <dt>Cost split</dt><dd>${formatCost(c.input_cost_usd)} input + ${formatCost(c.output_cost_usd)} output (${esc(c.price_source)})</dd>
            ${c.error_type ? `<dt>Error</dt><dd>${esc(c.error_type)}</dd>` : ''}
          </dl>
        </td></tr>` : '';
      return `
        <tr data-call="${esc(c.id)}" class="tu-call-row ${open ? 'open' : ''}" tabindex="0" aria-expanded="${open}">
          <td>${esc(formatDateTime(c.created_at))}</td>
          <td><span class="tu-swatch" style="background:${this.colorFor('agent', c.agent)}"></span>${esc(prettyAgent(c.agent))}</td>
          <td>${esc(c.model)}</td>
          <td class="tu-num">${formatTokens(c.prompt_tokens)}</td>
          <td class="tu-num">${formatTokens(c.completion_tokens)}</td>
          <td class="tu-num">${c.priced || c.status !== 'success' ? formatCost(c.cost_usd) : '<span class="tu-unpriced">unpriced</span>'}</td>
          <td class="tu-num">${formatMs(c.latency_ms)}</td>
          <td>${statusBadge(c.status)}${c.attempt > 1 ? ` <span class="tu-sub">retry ${esc(c.attempt - 1)}</span>` : ''}</td>
          <td>${esc(c.operation || '—')}</td>
        </tr>${detail}`;
    }).join('');
    table.innerHTML = `
      <thead><tr>
        ${th('created_at', 'Time')}${th('agent', 'Agent')}${th('model', 'Model')}
        <th class="tu-num">Input</th><th class="tu-num">Output</th>
        ${th('cost_usd', 'Cost', true)}${th('latency_ms', 'Latency', true)}
        <th>Status</th><th>Operation</th>
      </tr></thead>
      <tbody>${rows || '<tr><td colspan="9" class="tu-empty">No calls in this period.</td></tr>'}</tbody>`;

    const pages = Math.max(1, Math.ceil(page.total / page.page_size));
    const current = page.page;
    this.root.querySelector('.tu-pager').innerHTML = page.total ? `
      <span class="tu-sub">${whole.format((current - 1) * page.page_size + 1)}–${whole.format(Math.min(current * page.page_size, page.total))} of ${whole.format(page.total)}</span>
      <button type="button" class="btn btn-secondary tu-btn-sm" data-page="${current - 1}" ${current <= 1 ? 'disabled' : ''}>Previous</button>
      <span class="tu-sub">Page ${current} of ${pages}</span>
      <button type="button" class="btn btn-secondary tu-btn-sm" data-page="${current + 1}" ${current >= pages ? 'disabled' : ''}>Next</button>` : '';

    table.querySelectorAll('tr[data-call]').forEach((row) => {
      row.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          row.click();
        }
      });
    });
  }

  // ── pricing and budget ──────────────────────────────────────────────

  async loadPricing() {
    const body = this.root.querySelector('.tu-pricing-body');
    try {
      const [rows, settings] = await Promise.all([API.getTokenUsagePricing(), API.getTokenUsageSettings()]);
      this.settings = settings;
      const sourceLabel = { override: 'Custom price', litellm: 'LiteLLM price map', none: 'Unpriced' };
      body.innerHTML = rows.length ? `
        <div class="table-container"><table class="custom-table tu-table">
          <thead><tr><th>Model</th><th>Provider</th><th>Roles</th><th>Source</th>
            <th class="tu-num">Input $ / 1M</th><th class="tu-num">Output $ / 1M</th><th></th></tr></thead>
          <tbody>${rows.map((r) => `
            <tr>
              <td><strong>${esc(r.model)}</strong><div class="tu-sub">${esc(r.model_string)}</div></td>
              <td>${esc(r.provider)}</td>
              <td>${(r.roles || []).map(esc).join(', ') || '—'}</td>
              <td><span class="badge tu-source tu-source-${esc(r.source)}">${esc(sourceLabel[r.source] || r.source)}</span></td>
              <td class="tu-num"><input type="number" min="0" step="0.0001" class="form-control tu-price-input" data-price-model="${esc(r.model)}" data-price-kind="input_per_1m" value="${esc(r.input_per_1m)}"></td>
              <td class="tu-num"><input type="number" min="0" step="0.0001" class="form-control tu-price-input" data-price-model="${esc(r.model)}" data-price-kind="output_per_1m" value="${esc(r.output_per_1m)}"></td>
              <td>${r.source === 'override' ? `<button type="button" class="btn btn-secondary tu-btn-sm" data-action="reset-price" data-model="${esc(r.model)}">Use default</button>` : ''}</td>
            </tr>`).join('')}
          </tbody></table></div>
        <div class="tu-pricing-actions">
          <span class="tu-sub">Prices you change are saved as custom prices. Past calls keep the cost they were recorded with.</span>
          <button type="button" class="btn btn-primary" data-action="save-prices">Save prices</button>
        </div>` : '<div class="tu-empty">No models configured yet.</div>';
      this.priceSnapshot = Object.fromEntries(rows.map((r) => [r.model, r]));
    } catch (err) {
      body.innerHTML = `<div class="tu-error">${esc(err.message)}</div>`;
    }
  }

  async saveSettings(patch, button) {
    const current = this.settings || await API.getTokenUsageSettings();
    if (button) button.disabled = true;
    try {
      this.settings = await API.updateTokenUsageSettings({ ...current, ...patch });
      return true;
    } catch (err) {
      this.setError(`Could not save: ${err.message}`);
      return false;
    } finally {
      if (button) button.disabled = false;
    }
  }

  async saveBudget(button) {
    const read = (name) => Number(this.root.querySelector(`[data-budget="${name}"]`).value);
    const budget = read('monthly_budget_usd');
    const threshold = read('alert_threshold_pct');
    if (!(budget >= 0) || !(threshold >= 0 && threshold <= 100)) {
      this.setError('Budget must be 0 or more, and the alert threshold between 0 and 100.');
      return;
    }
    if (await this.saveSettings({ monthly_budget_usd: budget, alert_threshold_pct: threshold }, button)) {
      await this.load({ quiet: true });
    }
  }

  async savePrices(button) {
    const settings = this.settings || await API.getTokenUsageSettings();
    const overrides = { ...(settings.pricing_overrides || {}) };
    const edits = {};
    this.root.querySelectorAll('.tu-price-input').forEach((input) => {
      const model = input.dataset.priceModel;
      edits[model] = edits[model] || {};
      edits[model][input.dataset.priceKind] = Number(input.value);
    });
    Object.entries(edits).forEach(([model, price]) => {
      const before = this.priceSnapshot?.[model];
      const changed = !before || before.input_per_1m !== price.input_per_1m || before.output_per_1m !== price.output_per_1m;
      if (changed || overrides[model]) {
        if (!(price.input_per_1m >= 0 && price.output_per_1m >= 0)) return;
        overrides[model] = { input_per_1m: price.input_per_1m, output_per_1m: price.output_per_1m };
      }
    });
    if (await this.saveSettings({ pricing_overrides: overrides }, button)) await this.loadPricing();
  }

  async resetPrice(model, button) {
    const settings = this.settings || await API.getTokenUsageSettings();
    const overrides = { ...(settings.pricing_overrides || {}) };
    delete overrides[model];
    if (await this.saveSettings({ pricing_overrides: overrides }, button)) await this.loadPricing();
  }

  async exportCsv(button) {
    button.disabled = true;
    try {
      const csv = await API.exportTokenUsageCsv(this.queryFilters());
      const blob = new Blob([csv], { type: 'text/csv' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `token-usage-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      this.setError(`Export failed: ${err.message}`);
    } finally {
      button.disabled = false;
    }
  }
}
