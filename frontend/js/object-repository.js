// Object Repository view (spec 0004): pages, elements, locator editing, and scans.
import { API } from './api.js?v=25';
import { escapeHTML } from './components.js?v=8';

const STRATEGIES = ['role', 'label', 'placeholder', 'text', 'test_id', 'css', 'xpath'];
const ELEMENT_TYPES = ['button', 'link', 'input', 'select', 'checkbox', 'radio', 'textarea', 'nav'];
const HEALTH_LABELS = {
  healthy: 'Healthy',
  needs_review: 'Needs review',
  broken: 'Broken',
  missing: 'Missing'
};
const MAX_LOCATORS = 5;
const PAGE_PREVIEW = 5;

const esc = (value) => escapeHTML(value === null || value === undefined ? '' : String(value));

const ICONS = {
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path stroke-linecap="round" d="M20 20l-3.5-3.5"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.87 12.14A2 2 0 0116.14 21H7.86a2 2 0 01-2-1.86L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path stroke-linecap="round" d="M5 15V5a2 2 0 012-2h10"/></svg>',
  caret: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path stroke-linecap="round" d="M12 5v14M5 12h14"/></svg>',
  page: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8l-5-5zM14 3v5h5M12 11v6M9 14h6"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v4m0 4h.01M10.3 3.9L2.4 17.5A2 2 0 004.1 20.5h15.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"/></svg>',
  scan: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M4 8V5a1 1 0 011-1h3M16 4h3a1 1 0 011 1v3M20 16v3a1 1 0 01-1 1h-3M8 20H5a1 1 0 01-1-1v-3M4 12h16"/></svg>'
};

function healthBadge(health) {
  return `<span class="badge objrepo-health objrepo-health-${esc(health)}">${esc(HEALTH_LABELS[health] || health)}</span>`;
}

function locatorText(locator) {
  if (!locator) return '';
  const name = locator.options && locator.options.name ? `, name "${locator.options.name}"` : '';
  return `${locator.strategy}: ${locator.value}${name}`;
}

// The Playwright call a locator resolves to, so people can read it the way scripts use it.
function jsString(value) {
  return `'${String(value ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'")}'`;
}

function playwrightCall(locator) {
  if (!locator) return '';
  const options = locator.options || {};
  const value = jsString(locator.value);
  const opts = [];
  if (locator.strategy === 'role' && options.name) opts.push(`name: ${jsString(options.name)}`);
  if (options.exact && ['role', 'label', 'text'].includes(locator.strategy)) opts.push('exact: true');
  const suffix = opts.length ? `, { ${opts.join(', ')} }` : '';
  switch (locator.strategy) {
    case 'role': return `getByRole(${value}${suffix})`;
    case 'label': return `getByLabel(${value}${suffix})`;
    case 'placeholder': return `getByPlaceholder(${value})`;
    case 'text': return `getByText(${value}${suffix})`;
    case 'test_id': return `getByTestId(${value})`;
    case 'xpath': return `locator(${jsString(`xpath=${locator.value ?? ''}`)})`;
    default: return `locator(${value})`;
  }
}

// Detection score (share of run lookups where any locator found the element).
function scoreLevel(score) {
  if (!score || score.percent === null || score.percent === undefined) return 'none';
  if (score.percent >= 90) return 'high';
  if (score.percent >= 70) return 'medium';
  return 'low';
}

function formatPercent(percent) {
  return `${Number.isInteger(percent) ? percent : percent.toFixed(1)}%`;
}

function scoreChip(score) {
  const level = scoreLevel(score);
  if (level === 'none') {
    return `<span class="objrepo-score objrepo-score-none" title="No test run has used this page's elements yet">Not run yet</span>`;
  }
  const title = `${score.successes} of ${score.lookups} lookups in test runs found the element`;
  return `<span class="objrepo-score objrepo-score-${level}" title="${esc(title)}">${esc(formatPercent(score.percent))} detected</span>`;
}

function formatDate(value) {
  if (!value) return 'Never';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Never' : date.toLocaleString();
}

const RELATIVE = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
function formatRelative(value) {
  if (!value) return 'Never';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Never';
  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  const units = [['year', 31536000], ['month', 2592000], ['week', 604800], ['day', 86400], ['hour', 3600], ['minute', 60]];
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) return RELATIVE.format(Math.round(seconds / size), unit);
  }
  return 'Just now';
}

function totalCount(counts) {
  return Object.keys(HEALTH_LABELS).reduce((sum, health) => sum + (counts?.[health] || 0), 0);
}

// Stacked bar of element health for one page. The text summary beside it carries the values.
function healthBar(counts) {
  const total = totalCount(counts);
  if (!total) return '<span class="objrepo-healthbar objrepo-healthbar-empty" aria-hidden="true"></span>';
  const segments = Object.keys(HEALTH_LABELS)
    .filter((health) => counts[health])
    .map((health) => `<span class="objrepo-healthbar-seg objrepo-seg-${health}" style="flex-grow: ${counts[health]};"></span>`)
    .join('');
  return `<span class="objrepo-healthbar" aria-hidden="true">${segments}</span>`;
}

export class ObjectRepositoryView {
  constructor() {
    this.project = null;
    this.pages = [];
    this.selectedPageId = null;
    this.elements = [];
    this.healthFilter = '';
    this.pageQuery = '';
    this.showAllPages = false;
    this.elementQuery = '';
    this.expandedElementIds = new Set();
    this.scanTimer = null;
    this.toastTimer = null;
    this.drawerReturnFocus = null;
    this.actions = [];
  }

  // Permissions come from the backend for this project, so project roles apply.
  can(action) {
    return this.actions.includes(action);
  }

  get root() {
    return document.getElementById('object-repository-content');
  }

  async render(project) {
    this.project = project;
    const root = this.root;
    if (!root) return;
    if (!project) {
      root.innerHTML = `<div class="empty-state"><h3>Select a project</h3><p>Pick a project to see its stored pages and elements.</p></div>`;
      return;
    }
    root.innerHTML = `<div class="loading-overlay"><div class="spinner"></div><div class="loading-text">Loading...</div></div>`;
    try {
      this.actions = (await API.getObjectRepositoryPermissions(project.id)).actions || [];
    } catch (err) {
      root.innerHTML = `<div class="empty-state"><h3>Object repository unavailable</h3><p>${esc(err.message)}</p></div>`;
      return;
    }
    if (this.project !== project) return;
    const canCreate = this.can('create');
    root.innerHTML = `
      <div class="objrepo-action-cards">
        ${canCreate ? `
        <button type="button" class="objrepo-action-card" id="objrepo-scan-btn">
          <span class="objrepo-action-icon">${ICONS.scan}</span>
          <span class="objrepo-action-title">Scan the site</span>
          <span class="objrepo-action-text">Collect pages and elements from the project's target site.</span>
          <span class="objrepo-scan" id="objrepo-scan-status" aria-live="polite" data-state="idle"></span>
        </button>
        <button type="button" class="objrepo-action-card" id="objrepo-new-page" aria-expanded="false" aria-controls="objrepo-page-form">
          <span class="objrepo-action-icon">${ICONS.page}</span>
          <span class="objrepo-action-title">Add a page</span>
          <span class="objrepo-action-text">Register a page by name and URL pattern so scripts can use it.</span>
        </button>` : ''}
        <button type="button" class="objrepo-action-card" id="objrepo-review-btn">
          <span class="objrepo-action-icon">${ICONS.alert}</span>
          <span class="objrepo-action-title">Review elements that need attention</span>
          <span class="objrepo-action-text" id="objrepo-review-text">Checking element health...</span>
        </button>
      </div>
      ${canCreate ? `
      <form id="objrepo-page-form" class="objrepo-page-form section-card" novalidate hidden aria-labelledby="objrepo-page-form-title">
        <h2 id="objrepo-page-form-title" class="objrepo-form-title">Add a page</h2>
        <div class="objrepo-page-form-fields">
          <div class="form-group">
            <label for="objrepo-page-name">Name</label>
            <input id="objrepo-page-name" class="form-control" required pattern="[a-z][a-z0-9_]*" placeholder="login_page" autocomplete="off">
            <small class="objrepo-hint">Lowercase with underscores. Scripts refer to the page by this name.</small>
          </div>
          <div class="form-group">
            <label for="objrepo-page-pattern">URL pattern</label>
            <input id="objrepo-page-pattern" class="form-control" required placeholder="/login or /orders/*" autocomplete="off">
          </div>
        </div>
        <p class="objrepo-error" id="objrepo-page-error" role="alert"></p>
        <div class="objrepo-form-row">
          <button type="button" class="btn btn-secondary objrepo-small" id="objrepo-page-cancel">Cancel</button>
          <button type="submit" class="btn btn-primary objrepo-small">Add page</button>
        </div>
      </form>` : ''}
      <div class="objrepo-section" aria-labelledby="objrepo-pages-title">
        <div class="objrepo-section-header">
          <h2 id="objrepo-pages-title" class="objrepo-section-title">Pages <span class="objrepo-total" id="objrepo-page-total"></span></h2>
          <div class="objrepo-search">
            ${ICONS.search}
            <label for="objrepo-page-search" class="objrepo-visually-hidden">Search pages</label>
            <input id="objrepo-page-search" class="form-control" type="search" placeholder="Search" autocomplete="off" value="${esc(this.pageQuery)}">
          </div>
        </div>
        <div id="objrepo-page-list"><div class="loading-overlay"><div class="spinner"></div><div class="loading-text">Loading pages...</div></div></div>
      </div>
      <div class="objrepo-section" id="objrepo-elements-section" aria-labelledby="objrepo-elements-title">
        <div class="objrepo-section-header">
          <div class="objrepo-page-heading">
            <h2 id="objrepo-elements-title" class="objrepo-section-title">Elements</h2>
            <code class="objrepo-page-heading-pattern" id="objrepo-page-heading-pattern"></code>
          </div>
          <div class="objrepo-section-tools">
            <div class="objrepo-search">
              ${ICONS.search}
              <label for="objrepo-element-search" class="objrepo-visually-hidden">Search elements</label>
              <input id="objrepo-element-search" class="form-control" type="search" placeholder="Search" autocomplete="off" value="${esc(this.elementQuery)}">
            </div>
            ${canCreate ? `<button type="button" class="btn btn-primary objrepo-btn-icon objrepo-pill" id="objrepo-add-element" disabled>${ICONS.plus}<span>Add element</span></button>` : ''}
          </div>
        </div>
        <p id="objrepo-page-score" class="objrepo-page-score" aria-live="polite"></p>
        <div class="objrepo-chips" id="objrepo-health-chips" role="group" aria-label="Filter elements by health"></div>
        <div id="objrepo-element-table"></div>
      </div>
      <div class="objrepo-toast" id="objrepo-toast" role="status" aria-live="polite" hidden></div>
      <div class="objrepo-drawer-backdrop" id="objrepo-drawer-backdrop" hidden></div>
      <aside class="objrepo-drawer" id="objrepo-drawer" role="dialog" aria-modal="true" aria-labelledby="objrepo-drawer-title" hidden></aside>
    `;
    root.querySelector('#objrepo-scan-btn')?.addEventListener('click', () => this.startScan());
    root.querySelector('#objrepo-add-element')?.addEventListener('click', (e) => this.openDrawer(null, e.currentTarget));
    root.querySelector('#objrepo-new-page')?.addEventListener('click', () => this.togglePageForm());
    root.querySelector('#objrepo-page-cancel')?.addEventListener('click', () => this.togglePageForm(false));
    root.querySelector('#objrepo-page-form')?.addEventListener('submit', (e) => this.createPage(e));
    root.querySelector('#objrepo-review-btn').addEventListener('click', () => this.reviewAttention());
    root.querySelector('#objrepo-drawer-backdrop').addEventListener('click', () => this.closeDrawer());
    root.querySelector('#objrepo-page-search').addEventListener('input', (e) => {
      this.pageQuery = e.target.value;
      this.renderPageList();
    });
    root.querySelector('#objrepo-element-search').addEventListener('input', (e) => {
      this.elementQuery = e.target.value;
      this.renderElements();
    });
    await this.loadPages();
  }

  // ----------------------------------------------------------- pages

  togglePageForm(force) {
    const form = this.root?.querySelector('#objrepo-page-form');
    const toggle = this.root?.querySelector('#objrepo-new-page');
    if (!form || !toggle) return;
    const open = force === undefined ? form.hidden : force;
    form.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
    toggle.classList.toggle('active', open);
    if (open) {
      form.querySelector('#objrepo-page-name').focus();
    } else {
      form.reset();
      form.querySelector('#objrepo-page-error').textContent = '';
      toggle.focus();
    }
  }

  async loadPages() {
    const list = this.root?.querySelector('#objrepo-page-list');
    if (!list) return;
    try {
      const data = await API.getObjectPages(this.project.id);
      this.pages = data.pages || [];
    } catch (err) {
      list.innerHTML = `<p class="objrepo-error" role="alert">Could not load pages: ${esc(err.message)}</p>`;
      return;
    }
    if (!this.pages.some((page) => page.id === this.selectedPageId)) {
      this.selectedPageId = this.pages[0]?.id || null;
      this.expandedElementIds.clear();
    }
    // The selected page is the chat assistant's open item (spec 0005).
    window.dispatchEvent(new CustomEvent('chat:context-changed'));
    this.renderPageList();
    this.renderAttentionCard();
    await this.loadElements();
  }

  attentionCounts() {
    return this.pages.reduce((sum, page) => {
      sum.broken += page.counts?.broken || 0;
      sum.needs_review += page.counts?.needs_review || 0;
      return sum;
    }, { broken: 0, needs_review: 0 });
  }

  renderAttentionCard() {
    const text = this.root?.querySelector('#objrepo-review-text');
    const card = this.root?.querySelector('#objrepo-review-btn');
    if (!text || !card) return;
    const { broken, needs_review: review } = this.attentionCounts();
    card.disabled = !broken && !review;
    text.textContent = broken || review
      ? `${broken} broken and ${review} needing review across all pages. Opens the first page with problems.`
      : 'Every element is healthy or missing. Nothing to review right now.';
  }

  // Opens the first page with broken elements (or ones needing review) and filters to them.
  reviewAttention() {
    const pick = (health) => this.pages.find((page) => page.counts?.[health]);
    const page = pick('broken') || pick('needs_review');
    if (!page) return;
    const health = page.counts?.broken ? 'broken' : 'needs_review';
    this.openPage(page.id, health);
  }

  visiblePages() {
    const query = this.pageQuery.trim().toLowerCase();
    if (!query) return this.pages;
    return this.pages.filter((page) => `${page.name} ${page.url_pattern}`.toLowerCase().includes(query));
  }

  renderPageList() {
    const list = this.root?.querySelector('#objrepo-page-list');
    const total = this.root?.querySelector('#objrepo-page-total');
    if (!list) return;
    if (total) total.textContent = this.pages.length ? String(this.pages.length) : '';
    if (!this.pages.length) {
      list.innerHTML = `<div class="objrepo-empty"><p>No pages yet.</p><p>${this.can('create') ? 'Scan the site to collect pages and elements, or add a page by hand.' : 'Pages appear here after someone scans the site.'}</p></div>`;
      return;
    }
    const matching = this.visiblePages();
    if (!matching.length) {
      list.innerHTML = `<div class="objrepo-empty"><p>No page matches "${esc(this.pageQuery.trim())}".</p></div>`;
      return;
    }
    // Searching always shows every match; otherwise the list starts collapsed.
    const collapsed = !this.showAllPages && !this.pageQuery.trim() && matching.length > PAGE_PREVIEW;
    let pages = collapsed ? matching.slice(0, PAGE_PREVIEW) : matching;
    const selected = this.selectedPage();
    if (collapsed && selected && !pages.includes(selected)) pages = [...pages.slice(0, PAGE_PREVIEW - 1), selected];
    const canDelete = this.can('delete');
    list.innerHTML = `
      <div class="table-container objrepo-table-wrap">
        <table class="custom-table objrepo-pages-table">
          <caption class="objrepo-visually-hidden">Pages in the object repository</caption>
          <thead><tr>
            <th scope="col">Page</th><th scope="col">URL pattern</th><th scope="col">Elements</th>
            <th scope="col">Health</th><th scope="col">Detection</th>
            <th scope="col"><span class="objrepo-visually-hidden">Actions</span></th>
          </tr></thead>
          <tbody>${pages.map((page) => this.pageRow(page, canDelete)).join('')}</tbody>
        </table>
      </div>
      ${matching.length > PAGE_PREVIEW && !this.pageQuery.trim() ? `
        <div class="objrepo-list-footer">
          <button type="button" class="btn btn-primary objrepo-pill" id="objrepo-expand-pages" aria-expanded="${!collapsed}">${collapsed ? `Show all ${matching.length} pages` : 'Show fewer pages'}</button>
        </div>` : ''}`;
    list.querySelectorAll('[data-open-page]').forEach((button) => {
      button.addEventListener('click', () => this.openPage(button.getAttribute('data-open-page')));
    });
    list.querySelectorAll('tr[data-page-row]').forEach((row) => {
      row.addEventListener('click', (e) => {
        if (e.target.closest('button, a, input')) return;
        this.openPage(row.getAttribute('data-page-row'));
      });
    });
    list.querySelectorAll('[data-delete-page]').forEach((button) => {
      button.addEventListener('click', () => this.deletePage(button.getAttribute('data-delete-page')));
    });
    list.querySelector('#objrepo-expand-pages')?.addEventListener('click', () => {
      this.showAllPages = !this.showAllPages;
      this.renderPageList();
      this.root?.querySelector('#objrepo-expand-pages')?.focus();
    });
  }

  // Opens a page's elements below the list and brings them into view.
  openPage(pageId, health = '') {
    const changed = pageId !== this.selectedPageId || health !== this.healthFilter;
    if (pageId !== this.selectedPageId) {
      this.elementQuery = '';
      this.expandedElementIds.clear();
      const search = this.root?.querySelector('#objrepo-element-search');
      if (search) search.value = '';
    }
    this.selectedPageId = pageId;
    this.healthFilter = health;
    window.dispatchEvent(new CustomEvent('chat:context-changed'));
    this.renderPageList();
    const section = this.root?.querySelector('#objrepo-elements-section');
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    // Scroll only the workspace pane. scrollIntoView would also scroll the document
    // and every other ancestor, shifting the whole layout.
    const scroller = section?.closest('.workspace');
    if (section && scroller) {
      const margin = parseFloat(getComputedStyle(section).scrollMarginTop) || 0;
      const top = section.getBoundingClientRect().top - scroller.getBoundingClientRect().top
        + scroller.scrollTop - margin;
      scroller.scrollTo({ top, behavior: reduce ? 'auto' : 'smooth' });
    }
    if (changed) this.loadElements();
  }

  pageRow(page, canDelete) {
    const counts = page.counts || {};
    const selected = page.id === this.selectedPageId;
    const total = totalCount(counts);
    const summary = Object.keys(HEALTH_LABELS)
      .filter((health) => counts[health])
      .map((health) => `<span class="objrepo-count objrepo-count-${health}">${counts[health]} ${esc(HEALTH_LABELS[health].toLowerCase())}</span>`)
      .join('');
    // Overall page status badge: the worst health present on the page.
    let statusHealth = 'healthy';
    if (counts.broken) statusHealth = 'broken';
    else if (counts.needs_review) statusHealth = 'needs_review';
    else if (counts.missing && !counts.healthy) statusHealth = 'missing';
    const statusLabel = statusHealth === 'healthy' ? 'All healthy' : HEALTH_LABELS[statusHealth];
    return `
      <tr class="objrepo-page-row${selected ? ' selected' : ''}" data-page-row="${esc(page.id)}"${selected ? ' aria-current="true"' : ''}>
        <td class="objrepo-page-name">${esc(page.name)}</td>
        <td><code class="objrepo-page-pattern">${esc(page.url_pattern)}</code></td>
        <td>${total}</td>
        <td class="objrepo-health-cell">
          ${healthBar(counts)}
          <span class="objrepo-counts">${summary || '<span class="objrepo-count">No elements</span>'}</span>
        </td>
        <td>${scoreChip(page.score)}</td>
        <td class="objrepo-row-actions">
          <span class="objrepo-status-badge objrepo-status-${statusHealth}" title="${esc(statusLabel)}">${esc(statusLabel)}</span>
          <button type="button" class="objrepo-open" data-open-page="${esc(page.id)}" aria-label="Open ${esc(page.name)}">${selected ? 'Opened' : 'Open'}</button>
          ${canDelete ? `<button type="button" class="btn-icon objrepo-icon-danger" data-delete-page="${esc(page.id)}" aria-label="Delete page ${esc(page.name)}" title="Delete page">${ICONS.trash}</button>` : ''}
        </td>
      </tr>`;
  }

  selectedPage() {
    return this.pages.find((page) => page.id === this.selectedPageId) || null;
  }

  // -------------------------------------------------------- elements

  async loadElements() {
    const table = this.root?.querySelector('#objrepo-element-table');
    const addButton = this.root?.querySelector('#objrepo-add-element');
    if (addButton) addButton.disabled = !this.selectedPageId;
    this.renderPageHeading();
    this.renderPageScore();
    this.renderHealthChips();
    if (!table) return;
    if (!this.selectedPageId) {
      this.elements = [];
      this.renderElements();
      return;
    }
    const pageId = this.selectedPageId;
    const filter = this.healthFilter;
    table.innerHTML = `<div class="loading-overlay"><div class="spinner"></div><div class="loading-text">Loading elements...</div></div>`;
    try {
      const data = await API.getObjectElements(this.project.id, pageId, filter);
      // Ignore a response that a newer selection or filter has replaced.
      if (pageId !== this.selectedPageId || filter !== this.healthFilter) return;
      this.elements = data.elements || [];
    } catch (err) {
      table.innerHTML = `<p class="objrepo-error" role="alert">Could not load elements: ${esc(err.message)}</p>`;
      return;
    }
    this.renderElements();
  }

  renderPageHeading() {
    const title = this.root?.querySelector('#objrepo-elements-title');
    const pattern = this.root?.querySelector('#objrepo-page-heading-pattern');
    if (!title || !pattern) return;
    const page = this.selectedPage();
    title.textContent = page ? page.name : 'Elements';
    pattern.textContent = page ? page.url_pattern : '';
    pattern.hidden = !page;
  }

  renderPageScore() {
    const target = this.root?.querySelector('#objrepo-page-score');
    if (!target) return;
    const page = this.selectedPage();
    if (!page) {
      target.innerHTML = '';
      return;
    }
    const score = page.score;
    if (scoreLevel(score) === 'none') {
      target.innerHTML = `Locator detection: ${scoreChip(score)} <span class="objrepo-page-score-detail">The score appears after a test run uses this page's elements.</span>`;
      return;
    }
    target.innerHTML = `Locator detection: ${scoreChip(score)} <span class="objrepo-page-score-detail">${esc(score.successes)} of ${esc(score.lookups)} lookups in test runs found the element (main or backup locator).</span>`;
  }

  renderHealthChips() {
    const target = this.root?.querySelector('#objrepo-health-chips');
    if (!target) return;
    const page = this.selectedPage();
    if (!page) {
      target.innerHTML = '';
      return;
    }
    const counts = page.counts || {};
    const chip = (value, label, count) => {
      const active = this.healthFilter === value;
      return `<button type="button" class="objrepo-chip${value ? ` objrepo-chip-${value}` : ''}" data-health="${value}" aria-pressed="${active}"${!count && !active && value ? ' disabled' : ''}>
        ${value ? '<span class="objrepo-chip-dot" aria-hidden="true"></span>' : ''}${esc(label)} <span class="objrepo-chip-count">${count}</span>
      </button>`;
    };
    target.innerHTML = chip('', 'All', totalCount(counts))
      + Object.entries(HEALTH_LABELS).map(([value, label]) => chip(value, label, counts[value] || 0)).join('');
    target.querySelectorAll('[data-health]').forEach((button) => {
      button.addEventListener('click', () => {
        const value = button.getAttribute('data-health');
        // Clicking the active filter again clears it.
        this.healthFilter = this.healthFilter === value ? '' : value;
        this.loadElements().then(() => this.root?.querySelector(`[data-health="${this.healthFilter}"]`)?.focus());
      });
    });
  }

  visibleElements() {
    const query = this.elementQuery.trim().toLowerCase();
    if (!query) return this.elements;
    return this.elements.filter((element) => {
      const haystack = [
        element.key, element.name, element.description, element.element_type,
        ...element.locators.map((loc) => `${locatorText(loc)} ${playwrightCall(loc)}`)
      ].join(' ').toLowerCase();
      return haystack.includes(query);
    });
  }

  renderElements() {
    const table = this.root?.querySelector('#objrepo-element-table');
    if (!table) return;
    if (!this.selectedPageId) {
      table.innerHTML = `<div class="objrepo-empty"><p>${this.pages.length ? 'Select a page to see its elements.' : 'Elements appear here once the project has a page.'}</p></div>`;
      return;
    }
    if (!this.elements.length) {
      table.innerHTML = `<div class="objrepo-empty"><p>${this.healthFilter ? `No ${esc(HEALTH_LABELS[this.healthFilter].toLowerCase())} elements on this page.` : 'This page has no elements yet.'}</p></div>`;
      return;
    }
    const elements = this.visibleElements();
    if (!elements.length) {
      table.innerHTML = `<div class="objrepo-empty"><p>No element matches "${esc(this.elementQuery.trim())}".</p></div>`;
      return;
    }
    const canEdit = this.can('edit');
    const canDelete = this.can('delete');
    const hasActions = canEdit || canDelete;
    const columns = hasActions ? 5 : 4;
    table.innerHTML = `
      <div class="table-container">
        <table class="custom-table objrepo-table">
          <caption class="objrepo-visually-hidden">Elements of ${esc(this.selectedPage()?.name || 'the page')}</caption>
          <thead><tr>
            <th scope="col">Element</th><th scope="col">Health</th>
            <th scope="col">Main locator</th><th scope="col">Last used</th>
            ${hasActions ? '<th scope="col"><span class="objrepo-visually-hidden">Actions</span></th>' : ''}
          </tr></thead>
          <tbody>
            ${elements.map((element) => {
              const expanded = this.expandedElementIds.has(element.id);
              const backups = element.locators.length - 1;
              return `
              <tr class="objrepo-row${expanded ? ' expanded' : ''}" data-row-id="${esc(element.id)}">
                <td>
                  <div class="objrepo-element-cell">
                    <button type="button" class="objrepo-expand" data-expand="${esc(element.id)}" aria-expanded="${expanded}" aria-controls="objrepo-detail-${esc(element.id)}" aria-label="Show all locators for ${esc(element.key)}">${ICONS.caret}</button>
                    <code class="objrepo-key">${esc(element.key)}</code>
                    <button type="button" class="objrepo-copy" data-copy="${esc(element.key)}" aria-label="Copy key ${esc(element.key)}" title="Copy key">${ICONS.copy}</button>
                  </div>
                  <div class="objrepo-element-meta">
                    <span class="objrepo-type">${esc(element.element_type)}</span>
                    ${element.manually_edited ? '<span class="objrepo-tag" title="A person changed this element, so scans will not overwrite it">edited</span>' : ''}
                  </div>
                </td>
                <td>${healthBadge(element.health)}</td>
                <td class="objrepo-locator">
                  <code class="objrepo-pw" title="${esc(locatorText(element.locators[0]))}">${esc(playwrightCall(element.locators[0]))}</code>
                  ${backups > 0 ? `<span class="objrepo-more">+${backups} backup${backups > 1 ? 's' : ''}</span>` : ''}
                </td>
                <td class="objrepo-last-used"><span title="${esc(formatDate(element.last_used_at))}">${esc(formatRelative(element.last_used_at))}</span></td>
                ${hasActions ? `<td class="objrepo-row-actions">
                  ${canEdit ? `<button type="button" class="btn btn-secondary objrepo-small" data-edit-element="${esc(element.id)}">Edit</button>` : ''}
                  ${canDelete ? `<button type="button" class="btn-icon objrepo-icon-danger" data-delete-element="${esc(element.id)}" aria-label="Delete ${esc(element.key)}" title="Delete element">${ICONS.trash}</button>` : ''}
                </td>` : ''}
              </tr>
              <tr class="objrepo-detail-row" id="objrepo-detail-${esc(element.id)}"${expanded ? '' : ' hidden'}>
                <td colspan="${columns}">
                  <div class="objrepo-detail">
                    ${element.description ? `<p class="objrepo-detail-desc">${esc(element.description)}</p>` : ''}
                    <ol class="objrepo-locator-list">
                      ${element.locators.map((loc, index) => `
                        <li>
                          <span class="objrepo-locator-rank">${index === 0 ? 'Main' : `Backup ${index}`}</span>
                          <code class="objrepo-pw">${esc(playwrightCall(loc))}</code>
                        </li>`).join('')}
                    </ol>
                    <p class="objrepo-detail-hint">Scripts try these in order and use the first one that finds the element.</p>
                  </div>
                </td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>
      ${elements.length < this.elements.length ? `<p class="objrepo-result-count">Showing ${elements.length} of ${this.elements.length} elements</p>` : ''}`;
    table.querySelectorAll('[data-expand]').forEach((button) => {
      button.addEventListener('click', () => this.toggleElement(button.getAttribute('data-expand')));
    });
    // Clicking anywhere on a row that is not a control expands it too.
    table.querySelectorAll('tr.objrepo-row').forEach((row) => {
      row.addEventListener('click', (e) => {
        if (e.target.closest('button, a, input, select, textarea')) return;
        this.toggleElement(row.getAttribute('data-row-id'));
      });
    });
    table.querySelectorAll('[data-copy]').forEach((button) => {
      button.addEventListener('click', () => this.copyKey(button.getAttribute('data-copy')));
    });
    table.querySelectorAll('[data-edit-element]').forEach((button) => {
      button.addEventListener('click', () => {
        const element = this.elements.find((item) => item.id === button.getAttribute('data-edit-element'));
        this.openDrawer(element, button);
      });
    });
    table.querySelectorAll('[data-delete-element]').forEach((button) => {
      button.addEventListener('click', () => this.deleteElement(button.getAttribute('data-delete-element')));
    });
  }

  toggleElement(elementId) {
    const table = this.root?.querySelector('#objrepo-element-table');
    if (!table) return;
    const open = !this.expandedElementIds.has(elementId);
    if (open) this.expandedElementIds.add(elementId);
    else this.expandedElementIds.delete(elementId);
    const key = CSS.escape(elementId);
    table.querySelector(`tr[data-row-id="${key}"]`)?.classList.toggle('expanded', open);
    table.querySelector(`[data-expand="${key}"]`)?.setAttribute('aria-expanded', String(open));
    const detail = table.querySelector(`#objrepo-detail-${key}`);
    if (detail) detail.hidden = !open;
  }

  async copyKey(key) {
    try {
      await navigator.clipboard.writeText(key);
      this.toast(`Copied ${key}`);
    } catch {
      this.toast('Could not copy. Select the key and copy it by hand.', 'error');
    }
  }

  toast(message, tone = 'info') {
    const target = this.root?.querySelector('#objrepo-toast');
    if (!target) return;
    clearTimeout(this.toastTimer);
    target.textContent = message;
    target.dataset.tone = tone;
    target.hidden = false;
    this.toastTimer = setTimeout(() => { target.hidden = true; }, tone === 'error' ? 6000 : 3500);
  }

  async createPage(event) {
    event.preventDefault();
    const name = this.root.querySelector('#objrepo-page-name').value.trim();
    const urlPattern = this.root.querySelector('#objrepo-page-pattern').value.trim();
    const error = this.root.querySelector('#objrepo-page-error');
    error.textContent = '';
    if (!/^[a-z][a-z0-9_]*$/.test(name)) {
      error.textContent = 'Use lowercase letters, numbers and underscores, starting with a letter.';
      return;
    }
    if (!urlPattern) {
      error.textContent = 'Enter the URL pattern this page matches.';
      return;
    }
    try {
      const page = await API.createObjectPage(this.project.id, { name, url_pattern: urlPattern });
      this.selectedPageId = page.id;
      this.healthFilter = '';
      this.expandedElementIds.clear();
      this.togglePageForm(false);
      this.toast(`Added ${name}`);
      await this.loadPages();
    } catch (err) {
      error.textContent = err.message;
    }
  }

  async deletePage(pageId) {
    const page = this.pages.find((item) => item.id === pageId);
    if (!page || !confirm(`Delete page "${page.name}" and all of its elements?`)) return;
    try {
      const result = await API.deleteObjectPage(this.project.id, pageId);
      this.toast(result.referencing_test_cases
        ? `Deleted ${page.name}. ${result.referencing_test_cases} test case script(s) still reference its elements.`
        : `Deleted ${page.name}`, result.referencing_test_cases ? 'warning' : 'info');
      await this.loadPages();
    } catch (err) {
      this.toast(`Could not delete the page: ${err.message}`, 'error');
    }
  }

  async deleteElement(elementId) {
    const element = this.elements.find((item) => item.id === elementId);
    if (!element || !confirm(`Delete element "${element.key}"?`)) return;
    try {
      const result = await API.deleteObjectElement(this.project.id, elementId);
      this.expandedElementIds.delete(elementId);
      this.toast(result.referencing_test_cases
        ? `Deleted ${element.key}. ${result.referencing_test_cases} test case script(s) still reference it.`
        : `Deleted ${element.key}`, result.referencing_test_cases ? 'warning' : 'info');
      await this.loadPages();
    } catch (err) {
      this.toast(`Could not delete the element: ${err.message}`, 'error');
    }
  }

  // ---------------------------------------------------------- drawer

  openDrawer(element, trigger) {
    const drawer = this.root.querySelector('#objrepo-drawer');
    const backdrop = this.root.querySelector('#objrepo-drawer-backdrop');
    this.drawerReturnFocus = trigger || document.activeElement;
    this.draft = element
      ? {
          id: element.id,
          name: element.name,
          element_type: element.element_type,
          description: element.description || '',
          locators: element.locators.map((loc) => ({ strategy: loc.strategy, value: loc.value, options: { ...(loc.options || {}) } }))
        }
      : { id: null, name: '', element_type: 'button', description: '', locators: [{ strategy: 'role', value: 'button', options: {} }] };
    drawer.hidden = false;
    backdrop.hidden = false;
    this.renderDrawer();
    drawer.querySelector('#objrepo-el-name')?.focus();
    drawer.onkeydown = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        this.closeDrawer();
      } else if (e.key === 'Tab') {
        const focusable = [...drawer.querySelectorAll('button, input, select, textarea')].filter((el) => !el.disabled);
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
  }

  closeDrawer() {
    const drawer = this.root?.querySelector('#objrepo-drawer');
    if (!drawer || drawer.hidden) return;
    drawer.hidden = true;
    this.root.querySelector('#objrepo-drawer-backdrop').hidden = true;
    if (this.drawerReturnFocus && document.body.contains(this.drawerReturnFocus)) {
      this.drawerReturnFocus.focus();
    }
  }

  readDraft() {
    const drawer = this.root.querySelector('#objrepo-drawer');
    this.draft.name = drawer.querySelector('#objrepo-el-name').value.trim();
    this.draft.element_type = drawer.querySelector('#objrepo-el-type').value;
    this.draft.description = drawer.querySelector('#objrepo-el-description').value;
    drawer.querySelectorAll('[data-locator-row]').forEach((row) => {
      const index = Number(row.getAttribute('data-locator-row'));
      const locator = this.draft.locators[index];
      locator.strategy = row.querySelector('[data-field="strategy"]').value;
      locator.value = row.querySelector('[data-field="value"]').value;
      const nameInput = row.querySelector('[data-field="name"]');
      const options = {};
      if (locator.strategy === 'role' && nameInput && nameInput.value.trim()) options.name = nameInput.value.trim();
      const exact = row.querySelector('[data-field="exact"]');
      if (exact && exact.checked && ['role', 'label', 'text'].includes(locator.strategy)) options.exact = true;
      locator.options = options;
    });
  }

  renderDrawer(focusSelector) {
    const drawer = this.root.querySelector('#objrepo-drawer');
    const draft = this.draft;
    const isNew = !draft.id;
    const pageName = this.selectedPage()?.name || '';
    drawer.innerHTML = `
      <div class="objrepo-drawer-header">
        <div>
          <h2 id="objrepo-drawer-title">${isNew ? 'Add element' : 'Edit element'}</h2>
          <code class="objrepo-drawer-key" id="objrepo-drawer-key">${esc(pageName)}.${esc(draft.name || 'new_element')}</code>
        </div>
        <button type="button" class="modal-close" id="objrepo-drawer-close" aria-label="Close">&times;</button>
      </div>
      <form id="objrepo-element-form" novalidate>
        <div class="objrepo-form-grid">
          <div class="form-group">
            <label for="objrepo-el-name">Name</label>
            <input id="objrepo-el-name" class="form-control" value="${esc(draft.name)}" required autocomplete="off" placeholder="submit_button">
            <small class="objrepo-hint">${isNew ? 'Lowercase with underscores.' : 'Renaming rewrites every script that uses this key.'}</small>
          </div>
          <div class="form-group">
            <label for="objrepo-el-type">Type</label>
            <select id="objrepo-el-type" class="form-control">
              ${ELEMENT_TYPES.map((type) => `<option value="${type}"${type === draft.element_type ? ' selected' : ''}>${type}</option>`).join('')}
            </select>
          </div>
        </div>
        <div class="form-group">
          <label for="objrepo-el-description">Description</label>
          <input id="objrepo-el-description" class="form-control" value="${esc(draft.description)}" placeholder="What this element does on the page">
        </div>
        <fieldset class="objrepo-locators">
          <legend>Locators <span class="objrepo-legend-note">tried in order, ${draft.locators.length} of ${MAX_LOCATORS}</span></legend>
          ${draft.locators.map((locator, index) => this.locatorRow(locator, index, draft.locators.length)).join('')}
          <button type="button" class="btn btn-secondary objrepo-small objrepo-btn-icon" id="objrepo-add-locator" ${draft.locators.length >= MAX_LOCATORS ? 'disabled' : ''}>${ICONS.plus}<span>Add backup locator</span></button>
        </fieldset>
        <p class="objrepo-error" id="objrepo-element-error" role="alert"></p>
        <div class="form-actions">
          <button type="button" class="btn btn-secondary" id="objrepo-drawer-cancel">Cancel</button>
          <button type="submit" class="btn btn-primary">${isNew ? 'Add element' : 'Save changes'}</button>
        </div>
      </form>`;
    drawer.querySelector('#objrepo-drawer-close').addEventListener('click', () => this.closeDrawer());
    drawer.querySelector('#objrepo-drawer-cancel').addEventListener('click', () => this.closeDrawer());
    drawer.querySelector('#objrepo-el-name').addEventListener('input', (e) => {
      drawer.querySelector('#objrepo-drawer-key').textContent = `${pageName}.${e.target.value.trim() || 'new_element'}`;
    });
    drawer.querySelector('#objrepo-add-locator').addEventListener('click', () => {
      this.readDraft();
      this.draft.locators.push({ strategy: 'css', value: '', options: {} });
      this.renderDrawer(`[data-locator-row="${this.draft.locators.length - 1}"] [data-field="value"]`);
    });
    drawer.querySelectorAll('[data-locator-action]').forEach((button) => {
      button.addEventListener('click', () => {
        this.readDraft();
        const index = Number(button.getAttribute('data-index'));
        const action = button.getAttribute('data-locator-action');
        const locators = this.draft.locators;
        let focusIndex = index;
        if (action === 'up' && index > 0) { [locators[index - 1], locators[index]] = [locators[index], locators[index - 1]]; focusIndex = index - 1; }
        if (action === 'down' && index < locators.length - 1) { [locators[index + 1], locators[index]] = [locators[index], locators[index + 1]]; focusIndex = index + 1; }
        if (action === 'remove' && locators.length > 1) { locators.splice(index, 1); focusIndex = Math.min(index, locators.length - 1); }
        this.renderDrawer(`[data-locator-row="${focusIndex}"] [data-field="strategy"]`);
      });
    });
    drawer.querySelectorAll('[data-field="strategy"]').forEach((select) => {
      select.addEventListener('change', () => {
        this.readDraft();
        this.renderDrawer(`[data-locator-row="${select.closest('[data-locator-row]').getAttribute('data-locator-row')}"] [data-field="strategy"]`);
      });
    });
    // Live preview of the Playwright call while typing, without re-rendering the form.
    drawer.querySelectorAll('[data-locator-row]').forEach((row) => {
      row.addEventListener('input', () => {
        this.readDraft();
        const index = Number(row.getAttribute('data-locator-row'));
        const preview = row.querySelector('[data-preview]');
        if (preview) preview.textContent = playwrightCall(this.draft.locators[index]);
      });
    });
    drawer.querySelector('#objrepo-element-form').addEventListener('submit', (e) => this.saveElement(e));
    if (focusSelector) drawer.querySelector(focusSelector)?.focus();
  }

  locatorRow(locator, index, total) {
    const options = locator.options || {};
    const showName = locator.strategy === 'role';
    const showExact = ['role', 'label', 'text'].includes(locator.strategy);
    const label = index === 0 ? 'Main locator' : `Backup ${index}`;
    return `
      <div class="objrepo-locator-row${index === 0 ? ' is-main' : ''}" data-locator-row="${index}" role="group" aria-label="${label}">
        <div class="objrepo-locator-head">
          <span class="objrepo-locator-label">${label}</span>
          <div class="objrepo-locator-buttons">
            <button type="button" class="btn-icon" data-locator-action="up" data-index="${index}" aria-label="Move ${label} up" title="Try earlier" ${index === 0 ? 'disabled' : ''}>&uarr;</button>
            <button type="button" class="btn-icon" data-locator-action="down" data-index="${index}" aria-label="Move ${label} down" title="Try later" ${index === total - 1 ? 'disabled' : ''}>&darr;</button>
            <button type="button" class="btn-icon" data-locator-action="remove" data-index="${index}" aria-label="Remove ${label}" title="Remove" ${total === 1 ? 'disabled' : ''}>&times;</button>
          </div>
        </div>
        <div class="objrepo-locator-fields${showName ? ' has-name' : ''}">
          <label class="objrepo-visually-hidden" for="objrepo-loc-strategy-${index}">${label} strategy</label>
          <select id="objrepo-loc-strategy-${index}" class="form-control" data-field="strategy">
            ${STRATEGIES.map((strategy) => `<option value="${strategy}"${strategy === locator.strategy ? ' selected' : ''}>${strategy}</option>`).join('')}
          </select>
          <label class="objrepo-visually-hidden" for="objrepo-loc-value-${index}">${label} value</label>
          <input id="objrepo-loc-value-${index}" class="form-control" data-field="value" value="${esc(locator.value)}" placeholder="${locator.strategy === 'role' ? 'button' : 'value'}" autocomplete="off">
          ${showName ? `
            <label class="objrepo-visually-hidden" for="objrepo-loc-name-${index}">${label} accessible name</label>
            <input id="objrepo-loc-name-${index}" class="form-control" data-field="name" value="${esc(options.name || '')}" placeholder="accessible name" autocomplete="off">` : ''}
        </div>
        <div class="objrepo-locator-foot">
          <code class="objrepo-pw objrepo-preview" data-preview aria-label="Playwright call">${esc(playwrightCall(locator))}</code>
          ${showExact ? `<label class="objrepo-exact"><input type="checkbox" data-field="exact"${options.exact ? ' checked' : ''}> Exact match</label>` : ''}
        </div>
      </div>`;
  }

  async saveElement(event) {
    event.preventDefault();
    this.readDraft();
    const error = this.root.querySelector('#objrepo-element-error');
    error.textContent = '';
    const payload = {
      name: this.draft.name,
      element_type: this.draft.element_type,
      description: this.draft.description || null,
      locators: this.draft.locators
    };
    try {
      if (this.draft.id) {
        const result = await API.updateObjectElement(this.project.id, this.draft.id, payload);
        this.toast(result.scripts_rewritten
          ? `Saved. ${result.scripts_rewritten} test case script(s) now use the new key.`
          : `Saved ${this.draft.name}`);
      } else {
        await API.createObjectElement(this.project.id, this.selectedPageId, payload);
        this.toast(`Added ${this.draft.name}`);
      }
      this.closeDrawer();
      await this.loadPages();
    } catch (err) {
      error.textContent = Array.isArray(err.detail) ? err.detail.map((d) => d.msg).join('; ') : err.message;
    }
  }

  // ------------------------------------------------------------ scan

  setScanStatus(text, state) {
    const status = this.root?.querySelector('#objrepo-scan-status');
    if (!status) return;
    status.dataset.state = state;
    status.innerHTML = text ? `${state === 'running' ? '<span class="objrepo-scan-spinner" aria-hidden="true"></span>' : ''}<span>${esc(text)}</span>` : '';
  }

  async startScan() {
    const button = this.root.querySelector('#objrepo-scan-btn');
    this.setScanStatus('Starting scan...', 'running');
    if (button) button.disabled = true;
    try {
      const { scan_id: scanId } = await API.startObjectScan(this.project.id);
      this.pollScan(scanId);
    } catch (err) {
      if (button) button.disabled = false;
      this.setScanStatus(`Scan could not start: ${err.message}`, 'error');
    }
  }

  pollScan(scanId) {
    clearTimeout(this.scanTimer);
    const projectId = this.project.id;
    const tick = async () => {
      if (!this.project || this.project.id !== projectId || !this.root) return;
      const button = this.root.querySelector('#objrepo-scan-btn');
      try {
        const scan = await API.getObjectScan(projectId, scanId);
        if (scan.status === 'queued' || scan.status === 'running') {
          this.setScanStatus(scan.status === 'queued' ? 'Scan queued...' : 'Scanning pages...', 'running');
          this.scanTimer = setTimeout(tick, 1500);
          return;
        }
        if (button) button.disabled = false;
        if (scan.status === 'succeeded') {
          this.setScanStatus(`Scan finished: ${scan.pages_scanned} page(s), ${scan.added} added, ${scan.updated} updated, ${scan.marked_missing} marked missing.`, 'done');
        } else {
          this.setScanStatus(`Scan failed: ${scan.error || 'unknown error'}`, 'error');
        }
        await this.loadPages();
      } catch (err) {
        if (button) button.disabled = false;
        this.setScanStatus(`Could not read scan progress: ${err.message}`, 'error');
      }
    };
    tick();
  }
}
