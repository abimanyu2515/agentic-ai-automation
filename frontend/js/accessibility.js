// Accessibility section of the execution details screen (spec 0011, AC-12).
// Shows the run's release gate, counts by impact, and the findings grouped as
// violations and as needs human review. Every control is a native element
// (details and summary, buttons, links), so it works by keyboard.
import { API } from './api.js?v=30';
import { escapeHTML, showToast } from './components.js?v=10';

const esc = (value) => escapeHTML(value === null || value === undefined ? '' : String(value));

const GATE_BADGE = {
  PASS: 'badge-approved',
  WARN: 'badge-review',
  BLOCKED: 'badge-rejected',
  NOT_SCANNED: 'badge-pending',
};

const GATE_LABEL = {
  PASS: 'Pass',
  WARN: 'Warn',
  BLOCKED: 'Blocked',
  NOT_SCANNED: 'Not scanned',
};

const NOT_SCANNED_REASON = {
  disabled: 'Accessibility scanning is turned off for this project.',
  scan_error: 'The accessibility scan could not run for this execution. The test result is not affected.',
  no_report: 'This execution has no accessibility report. It may have run before scanning was available.',
};

const PAGE_ERROR = {
  budget: 'skipped, page or time limit reached',
  csp_blocked: 'skipped, the page blocks the scanner',
  requires_login: 'skipped, needs login',
  navigation_failed: 'could not be opened',
  axe_error: 'the scanner failed on this page',
};

const IMPACTS = ['critical', 'serious', 'moderate', 'minor'];

const DISCLAIMER = 'An automated scan does not certify WCAG conformance.';

function gateBadge(status) {
  const cls = GATE_BADGE[status] || 'badge-pending';
  return `<span class="badge ${cls}">${esc(GATE_LABEL[status] || status)}</span>`;
}

function countsHtml(counts) {
  const items = [...IMPACTS, 'needs_review'].map((key) => {
    const label = key === 'needs_review' ? 'Needs review' : key[0].toUpperCase() + key.slice(1);
    return `<li class="a11y-count a11y-impact-${esc(key)}"><span class="a11y-count-value">${esc((counts || {})[key] || 0)}</span> ${esc(label)}</li>`;
  });
  return `<ul class="a11y-counts" aria-label="Findings by impact">${items.join('')}</ul>`;
}

function findingHtml(finding) {
  const criteria = (finding.wcag_criteria || []).length
    ? finding.wcag_criteria.map((c) => `WCAG ${esc(c)}`).join(', ')
    : 'No WCAG criterion (best practice)';
  const impact = finding.kind === 'needs_review' ? 'needs review' : (finding.impact || 'unknown');
  const pages = (finding.pages || []).map((url) => `<li><code>${esc(url)}</code></li>`).join('');
  const more = finding.occurrences > (finding.pages || []).length
    ? `<div class="a11y-muted">Seen ${esc(finding.occurrences)} times in this run.</div>` : '';
  const source = finding.ai_source === 'llm' ? 'AI explanation' : 'Explanation (from axe)';
  const fixLabel = finding.kind === 'needs_review' ? 'What a person must check' : 'Suggested fix';
  const bug = finding.bug_report_id
    ? `<button type="button" class="btn btn-secondary a11y-bug-link" data-bug-id="${esc(finding.bug_report_id)}">Open bug report</button>`
    : '';
  return `
    <details class="a11y-finding">
      <summary>
        <span class="a11y-rule"><code>${esc(finding.rule_id)}</code></span>
        <span class="a11y-impact a11y-impact-${esc(impact.replace(' ', '_'))}">${esc(impact)}</span>
        <span class="a11y-help">${esc(finding.help)}</span>
      </summary>
      <div class="a11y-finding-body">
        <div><strong>WCAG:</strong> ${criteria}${finding.help_url ? ` · <a href="${esc(finding.help_url)}" target="_blank" rel="noopener noreferrer">Rule details<span class="sr-only"> for ${esc(finding.rule_id)} (opens in a new tab)</span></a>` : ''}</div>
        <div><strong>Element:</strong> <code>${esc(finding.selector)}</code></div>
        ${finding.html_snippet ? `<pre class="a11y-snippet">${esc(finding.html_snippet)}</pre>` : ''}
        <div><strong>Pages:</strong><ul class="a11y-pages">${pages}</ul>${more}</div>
        <h5>${esc(source)}</h5>
        <p>${esc(finding.ai_explanation || finding.help)}</p>
        ${finding.ai_fix ? `<h5>${esc(fixLabel)}</h5><pre class="a11y-fix">${esc(finding.ai_fix)}</pre>` : ''}
        ${finding.screenshot_path ? `<figure class="a11y-shot" data-shot="${esc(finding.screenshot_path)}" data-rule="${esc(finding.rule_id)}"><figcaption class="a11y-muted">Element screenshot loads when you open this finding.</figcaption></figure>` : ''}
        ${bug}
      </div>
    </details>`;
}

function groupHtml(title, findings, empty) {
  const body = findings.length ? findings.map(findingHtml).join('') : `<p class="a11y-muted">${esc(empty)}</p>`;
  return `<section class="a11y-group"><h3>${esc(title)} (${findings.length})</h3>${body}</section>`;
}

function pagesHtml(pages) {
  if (!pages || !pages.length) return '<p class="a11y-muted">No pages were scanned.</p>';
  const rows = pages.map((page) => {
    const note = page.error ? ` <span class="a11y-muted">(${esc(PAGE_ERROR[page.error] || page.error)})</span>` : '';
    return `<li><code>${esc(page.url)}</code>${page.title ? ` · ${esc(page.title)}` : ''}${note}</li>`;
  });
  return `<ul class="a11y-pages">${rows.join('')}</ul>`;
}

const SCAN_NOT_SCANNED_REASON = {
  scan_error: 'The scan could not finish. Try again, or check that the project target URL opens.',
};

const SCAN_ERROR = {
  timeout: 'The scan did not finish within 15 minutes and was stopped.',
  login_failed: 'Login failed, so pages behind login were not scanned.',
  scan_error: 'The scan could not finish.',
};

export function accessibilityHtml(report, { headingId = 'a11y-heading', title = 'Accessibility' } = {}) {
  const status = report.gate_status || 'NOT_SCANNED';
  const isScan = report.source === 'scan';
  const header = `
    <div class="a11y-header">
      <h2 id="${esc(headingId)}">${esc(title)}</h2>
      <div class="a11y-gate" aria-label="Release gate">${gateBadge(status)}${report.gate_mode ? ` <span class="a11y-muted">gate mode: ${esc(report.gate_mode)}</span>` : ''}</div>
    </div>`;
  if (status === 'NOT_SCANNED') {
    const reason = (isScan && SCAN_NOT_SCANNED_REASON[report.gate_reason])
      || NOT_SCANNED_REASON[report.gate_reason] || 'This execution was not scanned.';
    return `${header}<p class="a11y-muted">${esc(reason)}</p><p class="a11y-disclaimer">${esc(DISCLAIMER)}</p>`;
  }
  const findings = report.findings || [];
  const violations = findings.filter((f) => f.kind === 'violation');
  const review = findings.filter((f) => f.kind === 'needs_review');
  const loginNote = isScan && report.login_status && report.login_status !== 'not_requested'
    ? `<p class="a11y-muted">Login: ${esc(report.login_status)}${report.error === 'login_failed' ? `. ${esc(SCAN_ERROR.login_failed)}` : ''}</p>` : '';
  return `
    ${header}
    ${loginNote}
    ${countsHtml(report.counts)}
    ${groupHtml('Violations', violations, 'No violations found on the scanned pages.')}
    ${groupHtml('Needs human review', review, 'Nothing needs a manual check.')}
    <details class="a11y-finding"><summary>Scanned pages (${(report.pages || []).length})</summary><div class="a11y-finding-body">${pagesHtml(report.pages)}</div></details>
    <p class="a11y-disclaimer">${esc(DISCLAIMER)}</p>`;
}

// Element screenshots (AC-12) load the first time their finding opens.
function bindScreenshots(container, projectId, reportId) {
  container.querySelectorAll('details.a11y-finding').forEach((details) => {
    const figure = details.querySelector('.a11y-shot');
    if (!figure) return;
    details.addEventListener('toggle', async () => {
      if (!details.open || figure.dataset.loaded) return;
      figure.dataset.loaded = '1';
      const caption = figure.querySelector('figcaption');
      try {
        const blob = await API.getAccessibilityScreenshot(projectId, reportId, figure.dataset.shot);
        const img = document.createElement('img');
        img.src = URL.createObjectURL(blob);
        img.alt = `Screenshot of the element that fails ${figure.dataset.rule}`;
        img.className = 'a11y-shot-img';
        figure.insertBefore(img, caption);
        caption.textContent = 'Element screenshot';
      } catch (err) {
        caption.textContent = 'The element screenshot is no longer available.';
      }
    });
  });
}

export async function renderAccessibilitySection(container, projectId, executionId) {
  if (!container) return;
  container.setAttribute('aria-busy', 'true');
  try {
    const report = await API.getExecutionAccessibility(projectId, executionId);
    container.innerHTML = accessibilityHtml(report || {});
    container.querySelectorAll('.a11y-bug-link').forEach((button) => {
      button.addEventListener('click', () => window.openBugReport && window.openBugReport(button.dataset.bugId));
    });
    if (report && report.id) bindScreenshots(container, projectId, report.id);
  } catch (err) {
    container.innerHTML = `<h2 id="a11y-heading">Accessibility</h2><p class="a11y-muted">Could not load the accessibility report: ${esc(err.message)}</p>`;
  } finally {
    container.removeAttribute('aria-busy');
  }
}


// ---------------------------------------------------------------- standalone scans
// The project page's "Run accessibility scan" (spec 0011, AC-10, AC-11, AC-12):
// a dialog with the page checklist, custom paths, and an include login option,
// plus the list of past scan reports. Native controls only, so it all works by
// keyboard; the dialog keeps focus inside while open and returns it on close.

const ACTIVE = new Set(['queued', 'running']);
const POLL_MS = 3000;

function formatDate(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString();
}

function statusText(report) {
  if (report.status === 'queued') return 'Queued';
  if (report.status === 'running') return 'Running';
  if (report.status === 'failed') return SCAN_ERROR[report.error] ? `Failed: ${SCAN_ERROR[report.error]}` : 'Failed';
  return 'Finished';
}

function countsSummary(counts) {
  const c = counts || {};
  return `${c.critical || 0} critical, ${c.serious || 0} serious, ${c.moderate || 0} moderate, ${c.minor || 0} minor, ${c.needs_review || 0} need review`;
}

export class AccessibilityScansPanel {
  constructor() {
    this.project = null;
    this.pollTimer = null;
    this.pages = [];
    this.maxPages = 10;
    this.returnFocus = null;
    this.bound = false;
  }

  el(id) {
    return document.getElementById(id);
  }

  canStart() {
    return typeof window.hasPermission !== 'function' || window.hasPermission('Executions', 'edit');
  }

  async render(project) {
    this.project = project || null;
    this.bind();
    const section = this.el('a11y-scans-section');
    if (!section) return;
    section.style.display = this.project ? '' : 'none';
    if (!this.project) return;
    const button = this.el('btn-a11y-scan');
    if (button) {
      button.disabled = !this.canStart();
      button.title = button.disabled ? 'You need permission to edit executions to start a scan.' : '';
    }
    this.el('a11y-scan-report').innerHTML = '';
    await this.refreshList();
  }

  bind() {
    if (this.bound) return;
    this.bound = true;
    this.el('btn-a11y-scan')?.addEventListener('click', () => this.openDialog());
    this.el('a11y-scan-cancel')?.addEventListener('click', () => this.closeDialog());
    this.el('a11y-scan-close')?.addEventListener('click', () => this.closeDialog());
    this.el('a11y-scan-form')?.addEventListener('submit', (event) => {
      event.preventDefault();
      this.submit();
    });
    this.el('a11y-scan-add-path')?.addEventListener('click', () => this.addCustomPath());
    this.el('a11y-scan-custom-path')?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        this.addCustomPath();
      }
    });
    this.el('a11y-scan-pages')?.addEventListener('change', () => this.updateCount());
    this.el('a11y-scan-modal')?.addEventListener('keydown', (event) => this.onDialogKey(event));
    this.el('a11y-scan-list')?.addEventListener('click', (event) => {
      const button = event.target.closest('[data-a11y-report]');
      if (button) this.showReport(button.dataset.a11yReport);
    });
  }

  // ------------------------------------------------------------ report list

  async refreshList() {
    const list = this.el('a11y-scan-list');
    if (!list || !this.project) return;
    const projectId = this.project.id;
    let data;
    try {
      data = await API.listAccessibilityReports(projectId, { source: 'scan', limit: 20 });
    } catch (err) {
      list.innerHTML = `<p class="a11y-muted">Could not load past scans: ${esc(err.message)}</p>`;
      return;
    }
    if (!this.project || this.project.id !== projectId) return;
    const items = (data && data.items) || [];
    if (!items.length) {
      list.innerHTML = '<p class="a11y-muted">No scans yet. Run one to check chosen pages without writing a test.</p>';
    } else {
      const rows = items.map((r) => {
        const active = ACTIVE.has(r.status);
        const pages = (r.pages || []).length;
        return `
          <tr>
            <td>${esc(formatDate(r.created_at))}</td>
            <td>${active ? `<span class="badge badge-pending">${esc(statusText(r))}</span>` : gateBadge(r.gate_status)}</td>
            <td>${active ? '' : esc(statusText(r))}</td>
            <td>${active ? '' : `${esc(pages)} page${pages === 1 ? '' : 's'}`}</td>
            <td>${active ? '' : esc(countsSummary(r.counts))}</td>
            <td>${active ? '' : `<button type="button" class="btn btn-secondary" data-a11y-report="${esc(r.id)}">View<span class="sr-only"> scan from ${esc(formatDate(r.created_at))}</span></button>`}</td>
          </tr>`;
      }).join('');
      list.innerHTML = `
        <div class="table-container">
          <table class="custom-table">
            <caption class="sr-only">Past accessibility scans, newest first</caption>
            <thead><tr><th scope="col">Started</th><th scope="col">Gate</th><th scope="col">Status</th><th scope="col">Pages</th><th scope="col">Findings</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>`;
    }
    this.schedulePoll(items.some((r) => ACTIVE.has(r.status)));
  }

  schedulePoll(active) {
    clearTimeout(this.pollTimer);
    this.pollTimer = null;
    if (!active) return;
    this.pollTimer = setTimeout(() => {
      // Stops once the user left the project page.
      if (this.el('a11y-scans-section')?.offsetParent !== null) this.refreshList();
    }, POLL_MS);
  }

  async showReport(reportId) {
    const target = this.el('a11y-scan-report');
    if (!target || !this.project) return;
    target.setAttribute('aria-busy', 'true');
    try {
      const report = await API.getAccessibilityReport(this.project.id, reportId);
      target.innerHTML = accessibilityHtml(report, {
        headingId: 'a11y-scan-report-heading',
        title: `Scan from ${formatDate(report.created_at)}`,
      });
      target.querySelectorAll('.a11y-bug-link').forEach((button) => {
        button.addEventListener('click', () => window.openBugReport && window.openBugReport(button.dataset.bugId));
      });
      bindScreenshots(target, this.project.id, report.id);
      const heading = this.el('a11y-scan-report-heading');
      if (heading) {
        heading.tabIndex = -1;
        heading.focus();
      }
    } catch (err) {
      target.innerHTML = `<p class="a11y-muted">Could not load the scan report: ${esc(err.message)}</p>`;
    } finally {
      target.removeAttribute('aria-busy');
    }
  }

  // ------------------------------------------------------------ dialog

  async openDialog() {
    if (!this.project) return;
    this.returnFocus = document.activeElement;
    const modal = this.el('a11y-scan-modal');
    const list = this.el('a11y-scan-pages');
    const login = this.el('a11y-scan-include-login');
    const loginHelp = this.el('a11y-scan-login-help');
    this.setError('');
    this.el('a11y-scan-custom-path').value = '';
    const safe = !!this.project.is_safe_test_environment;
    login.checked = false;
    login.disabled = !safe;
    loginHelp.textContent = safe
      ? "Signs in with the project's stored username and password, then scans the pages that redirected to a login page. Multi step and single sign on logins are not supported."
      : 'Available only for projects marked as a safe test environment, because it submits real credentials. Public pages can still be scanned.';
    list.innerHTML = '<li class="a11y-muted">Loading pages...</li>';
    modal.classList.add('active');
    this.el('a11y-scan-submit').disabled = true;

    try {
      const data = await API.getAccessibilityScanPages(this.project.id);
      this.pages = data.pages || [];
      this.maxPages = data.max_pages || 10;
    } catch (err) {
      this.pages = [];
      this.setError(`Could not load the pages to scan: ${err.message}`);
    }
    this.nextIndex = this.pages.length;
    list.innerHTML = this.pages.map((page, i) => this.pageItem(page, i)).join('')
      || '<li class="a11y-muted">No pages found. Add a path below.</li>';
    this.updateCount();
    const first = modal.querySelector('input:not([disabled]), button:not([disabled])');
    if (first) first.focus();
  }

  pageItem(page, index) {
    const id = `a11y-scan-page-${index}`;
    const source = { target_url: 'target URL', object_repository: 'object repository', test_case: 'test case', custom: 'added by you' }[page.source] || page.source;
    if (page.skipped_reason) {
      return `<li class="a11y-scan-page"><input type="checkbox" id="${id}" disabled aria-describedby="${id}-note" />
        <label for="${id}"><code>${esc(page.url)}</code></label>
        <span id="${id}-note" class="a11y-muted">skipped, a wildcard pattern cannot be opened (${esc(source)})</span></li>`;
    }
    return `<li class="a11y-scan-page"><input type="checkbox" id="${id}" data-url="${esc(page.url)}" checked />
      <label for="${id}"><code>${esc(page.url)}</code> <span class="a11y-muted">${esc(source)}</span></label></li>`;
  }

  addCustomPath() {
    const input = this.el('a11y-scan-custom-path');
    let value = (input.value || '').trim();
    if (!value) return;
    if (!/^https?:\/\//i.test(value) && !value.startsWith('/')) value = `/${value}`;
    let url;
    try {
      const base = new URL(this.project.target_url);
      url = new URL(value, base.origin);
      if (url.origin !== base.origin) {
        this.setError(`Only pages on ${base.origin} can be scanned.`);
        input.focus();
        return;
      }
    } catch (err) {
      this.setError('That is not a valid path.');
      input.focus();
      return;
    }
    url.hash = '';
    const list = this.el('a11y-scan-pages');
    const taken = Array.from(list.querySelectorAll('input[data-url]')).some((i) => {
      try { return new URL(i.dataset.url).pathname === url.pathname; } catch (e) { return false; }
    });
    if (taken) {
      this.setError(`${url.pathname} is already in the list.`);
      input.focus();
      return;
    }
    this.setError('');
    const item = document.createElement('template');
    item.innerHTML = this.pageItem({ url: url.href, source: 'custom' }, this.nextIndex++).trim();
    list.querySelector('li.a11y-muted:not(.a11y-scan-page)')?.remove();
    list.appendChild(item.content.firstChild);
    input.value = '';
    this.updateCount();
    this.announce(`Added ${url.pathname}`);
    input.focus();
  }

  chosenUrls() {
    return Array.from(this.el('a11y-scan-pages').querySelectorAll('input[data-url]:checked')).map((i) => i.dataset.url);
  }

  updateCount() {
    const count = this.chosenUrls().length;
    const over = count > this.maxPages;
    const note = this.el('a11y-scan-count');
    note.textContent = `${count} of at most ${this.maxPages} pages chosen${over ? '. Untick some pages to continue.' : '.'}`;
    note.classList.toggle('a11y-scan-over', over);
    this.el('a11y-scan-submit').disabled = count === 0 || over;
  }

  setError(message) {
    const box = this.el('a11y-scan-error');
    box.textContent = message;
    box.style.display = message ? '' : 'none';
  }

  announce(message) {
    const live = this.el('a11y-scan-live');
    if (live) live.textContent = message;
  }

  async submit() {
    const urls = this.chosenUrls();
    if (!urls.length || urls.length > this.maxPages) return;
    const button = this.el('a11y-scan-submit');
    button.disabled = true;
    try {
      await API.startAccessibilityScan(this.project.id, urls, this.el('a11y-scan-include-login').checked);
      this.closeDialog();
      showToast('Accessibility scan started. The list updates when it finishes.', 'success');
      await this.refreshList();
    } catch (err) {
      if (!err.shown) this.setError(err.message);
      button.disabled = false;
    }
  }

  closeDialog() {
    this.el('a11y-scan-modal')?.classList.remove('active');
    if (this.returnFocus && typeof this.returnFocus.focus === 'function') this.returnFocus.focus();
    this.returnFocus = null;
  }

  onDialogKey(event) {
    if (event.key === 'Escape') {
      event.preventDefault();
      this.closeDialog();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = Array.from(this.el('a11y-scan-modal').querySelectorAll(
      'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )).filter((node) => node.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
}
