// Bugs view: bug report drafts from the Bug Reporting Agent, reviewed and
// edited here, then submitted to Jira.
import { API } from './api.js?v=27';
import { escapeHTML, showToast } from './components.js?v=10';

const esc = (value) => escapeHTML(value === null || value === undefined ? '' : String(value));

const STATUS_LABELS = {
  draft: 'Draft',
  submit_failed: 'Submit failed',
  submitted: 'Submitted',
  linked_duplicate: 'Linked duplicate',
  resolved: 'Resolved',
  dismissed: 'Dismissed'
};
const STATUS_BADGES = {
  draft: 'badge-review',
  submit_failed: 'badge-rejected',
  submitted: 'badge-approved',
  linked_duplicate: 'badge-approved',
  resolved: 'badge-pending',
  dismissed: 'badge-pending'
};
const FILTERS = [
  ['', 'All'],
  ['draft', 'Drafts'],
  ['submit_failed', 'Submit failed'],
  ['submitted', 'Submitted'],
  ['linked_duplicate', 'Duplicates'],
  ['resolved', 'Resolved'],
  ['dismissed', 'Dismissed']
];
const SEVERITIES = ['critical', 'major', 'minor', 'trivial'];
const PRIORITIES = ['highest', 'high', 'medium', 'low'];
const EDITABLE = new Set(['draft', 'submit_failed']);

export function bugStatusBadge(status) {
  return `<span class="badge ${STATUS_BADGES[status] || 'badge-pending'}">${esc(STATUS_LABELS[status] || status)}</span>`;
}

function options(values, selected) {
  return values.map(v => `<option value="${esc(v)}"${v === selected ? ' selected' : ''}>${esc(v)}</option>`).join('');
}

function jiraKey(report) {
  return report.jira_key || report.duplicate_of_jira_key || '';
}

export class BugReportsView {
  constructor() {
    this.project = null;
    this.reports = [];
    this.filter = '';
    this.selectedId = null;
    this.busy = false;
  }

  get root() {
    return document.getElementById('bugs-content');
  }

  can(action) {
    return typeof window.hasPermission !== 'function' || window.hasPermission('Executions', action);
  }

  async render(project, selectId = null) {
    this.project = project;
    if (selectId) this.selectedId = selectId;
    const root = this.root;
    if (!root) return;
    if (!project) {
      root.innerHTML = '<div class="empty-state"><h3>No project selected</h3><p>Select a project to see its bug reports.</p></div>';
      return;
    }
    root.innerHTML = '<div class="loading-overlay"><p>Loading bug reports...</p></div>';
    try {
      this.reports = await API.listBugReports(project.id, this.filter);
    } catch (err) {
      root.innerHTML = `<div class="empty-state"><h3>Could not load bug reports</h3><p>${esc(err.message)}</p></div>`;
      return;
    }
    if (this.project?.id !== project.id) return;
    if (this.selectedId && !this.reports.some(r => r.id === this.selectedId)) {
      // The report may be outside the current filter; load it directly.
      try {
        const report = await API.getBugReport(project.id, this.selectedId);
        this.reports = [report, ...this.reports];
      } catch {
        this.selectedId = null;
      }
    }
    this.draw();
  }

  draw() {
    const root = this.root;
    const selected = this.reports.find(r => r.id === this.selectedId) || null;
    root.innerHTML = `
      <div class="bug-filters" role="tablist" aria-label="Filter bug reports by status">
        ${FILTERS.map(([value, label]) => `
          <button type="button" role="tab" class="bug-filter${value === this.filter ? ' active' : ''}"
            aria-selected="${value === this.filter}" data-filter="${esc(value)}">${esc(label)}</button>`).join('')}
      </div>
      <div class="bug-layout${selected ? ' has-detail' : ''}">
        <div class="bug-list">${this.listHTML()}</div>
        ${selected ? `<div class="bug-detail card">${this.detailHTML(selected)}</div>` : ''}
      </div>`;
    this.bind(selected);
  }

  listHTML() {
    if (this.reports.length === 0) {
      return `<div class="empty-state"><h3>No bug reports</h3><p>Product bug failures are drafted here automatically after a run is triaged. You can also use <strong>Report bug</strong> in a failed run's triage details.</p></div>`;
    }
    const rows = this.reports.map(r => `
      <tr class="bug-row${r.id === this.selectedId ? ' selected' : ''}" data-id="${esc(r.id)}" tabindex="0">
        <td><div class="bug-title">${esc(r.title)}</div>
          <div class="bug-meta">${esc(r.failure_category || 'Manual report')} · ${esc(r.occurrences.length)} occurrence(s)</div></td>
        <td>${bugStatusBadge(r.status)}</td>
        <td><span class="badge badge-${r.severity === 'critical' || r.severity === 'major' ? 'high' : r.severity === 'minor' ? 'medium' : 'low'}">${esc(r.severity)}</span></td>
        <td>${jiraKey(r) && r.jira_url ? `<a href="${esc(r.jira_url)}" target="_blank" rel="noopener">${esc(jiraKey(r))}</a>` : '-'}</td>
        <td style="color: var(--text-muted);">${esc(new Date(r.updated_at).toLocaleString())}</td>
      </tr>`).join('');
    return `
      <div class="table-container">
        <table class="custom-table">
          <thead><tr><th>BUG</th><th>STATUS</th><th>SEVERITY</th><th>JIRA</th><th>UPDATED</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  detailHTML(r) {
    const editable = EDITABLE.has(r.status) && this.can('edit');
    const disabled = editable ? '' : ' disabled';
    const key = jiraKey(r);
    const evidence = (r.evidence || []).map(line => `<li>${esc(line)}</li>`).join('') || '<li>None captured</li>';
    const env = Object.entries(r.environment || {}).map(([k, v]) => `<div><strong>${esc(k)}:</strong> ${esc(v)}</div>`).join('');
    return `
      <div class="bug-detail-header">
        <div>${bugStatusBadge(r.status)}
          ${key ? ` <a href="${esc(r.jira_url || '#')}" target="_blank" rel="noopener">${esc(key)}</a>` : ''}</div>
        <button type="button" class="modal-close" data-action="close" aria-label="Close bug report">✕</button>
      </div>
      ${r.submit_error ? `<div class="bug-error" role="alert">Jira submit failed: ${esc(r.submit_error)}</div>` : ''}
      ${r.dismiss_reason ? `<div class="bug-meta">Dismissed: ${esc(r.dismiss_reason)}</div>` : ''}
      <form class="bug-form" novalidate>
        <div class="form-group"><label for="bug-title">Title</label>
          <input id="bug-title" name="title" class="form-control" maxlength="255" required value="${esc(r.title)}"${disabled}></div>
        <div class="form-group"><label for="bug-summary">Summary</label>
          <textarea id="bug-summary" name="summary" class="form-control" rows="3"${disabled}>${esc(r.summary)}</textarea></div>
        <div class="form-group"><label for="bug-steps">Steps to reproduce (one per line)</label>
          <textarea id="bug-steps" name="steps_to_reproduce" class="form-control" rows="5"${disabled}>${esc((r.steps_to_reproduce || []).join('\n'))}</textarea></div>
        <div class="bug-two-col">
          <div class="form-group"><label for="bug-expected">Expected result</label>
            <textarea id="bug-expected" name="expected_result" class="form-control" rows="3"${disabled}>${esc(r.expected_result)}</textarea></div>
          <div class="form-group"><label for="bug-actual">Actual result</label>
            <textarea id="bug-actual" name="actual_result" class="form-control" rows="3"${disabled}>${esc(r.actual_result)}</textarea></div>
        </div>
        <div class="bug-two-col">
          <div class="form-group"><label for="bug-severity">Severity</label>
            <select id="bug-severity" name="severity" class="form-control"${disabled}>${options(SEVERITIES, r.severity)}</select></div>
          <div class="form-group"><label for="bug-priority">Priority</label>
            <select id="bug-priority" name="priority" class="form-control"${disabled}>${options(PRIORITIES, r.priority)}</select></div>
        </div>
        <details class="bug-evidence">
          <summary>Triage evidence</summary>
          <div class="bug-meta">Failing step: ${esc(r.failing_step || 'Unknown')}</div>
          <div class="bug-meta">Root cause: ${esc(r.root_cause || 'Not analysed')}</div>
          <div class="bug-meta">Confidence: ${r.confidence === null || r.confidence === undefined ? 'N/A' : esc(Math.round(r.confidence * 100) + '%')}</div>
          <ul>${evidence}</ul>
          ${env}
          <div class="bug-meta"><a href="#/execution/${esc(r.execution_id)}">Open the failed run</a></div>
        </details>
        ${editable ? `
        <div class="bug-actions">
          <button type="button" class="btn btn-secondary" data-action="dismiss">Dismiss</button>
          <button type="submit" class="btn btn-secondary" data-action="save">Save draft</button>
          <button type="button" class="btn btn-primary" data-action="submit">Submit to Jira</button>
        </div>` : ''}
      </form>`;
  }

  bind(selected) {
    const root = this.root;
    root.querySelectorAll('.bug-filter').forEach(btn => btn.addEventListener('click', () => {
      this.filter = btn.dataset.filter;
      this.render(this.project);
    }));
    root.querySelectorAll('.bug-row').forEach(row => {
      const open = () => { this.selectedId = row.dataset.id; this.draw(); };
      row.addEventListener('click', open);
      row.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } });
    });
    if (!selected) return;
    const form = root.querySelector('.bug-form');
    root.querySelector('[data-action="close"]')?.addEventListener('click', () => { this.selectedId = null; this.draw(); });
    form?.addEventListener('submit', (e) => { e.preventDefault(); this.save(selected, form); });
    root.querySelector('[data-action="submit"]')?.addEventListener('click', () => this.submit(selected, form));
    root.querySelector('[data-action="dismiss"]')?.addEventListener('click', () => this.dismiss(selected));
  }

  patchFrom(form) {
    const data = new FormData(form);
    return {
      title: String(data.get('title') || '').trim(),
      summary: String(data.get('summary') || ''),
      steps_to_reproduce: String(data.get('steps_to_reproduce') || '').split('\n').map(s => s.trim()).filter(Boolean),
      expected_result: String(data.get('expected_result') || ''),
      actual_result: String(data.get('actual_result') || ''),
      severity: data.get('severity'),
      priority: data.get('priority')
    };
  }

  replace(report) {
    this.reports = this.reports.map(r => (r.id === report.id ? report : r));
    this.draw();
  }

  async run(work) {
    if (this.busy) return null;
    this.busy = true;
    this.root.querySelectorAll('.bug-actions button').forEach(b => { b.disabled = true; });
    try {
      return await work();
    } catch (err) {
      showToast(err.message || 'Request failed', 'error');
      return null;
    } finally {
      this.busy = false;
      this.root.querySelectorAll('.bug-actions button').forEach(b => { b.disabled = false; });
    }
  }

  async save(report, form, quiet = false) {
    const patch = this.patchFrom(form);
    if (!patch.title) {
      showToast('A bug report needs a title', 'error');
      form.querySelector('#bug-title')?.focus();
      return null;
    }
    const saved = await this.run(() => API.updateBugReport(this.project.id, report.id, patch));
    if (saved) {
      this.replace(saved);
      if (!quiet) showToast('Bug report saved', 'success');
    }
    return saved;
  }

  async submit(report, form) {
    const saved = await this.save(report, form, true);
    if (!saved) return;
    const submitted = await this.run(() => API.submitBugReport(this.project.id, report.id));
    if (!submitted) return;
    this.replace(submitted);
    if (submitted.status === 'submit_failed') {
      showToast(`Jira submit failed: ${submitted.submit_error || 'unknown error'}`, 'error');
    } else if (submitted.status === 'linked_duplicate') {
      showToast(`Linked to existing Jira bug ${submitted.duplicate_of_jira_key}`, 'success');
    } else {
      showToast(`Filed in Jira as ${submitted.jira_key}`, 'success');
    }
  }

  async dismiss(report) {
    const reason = window.prompt('Why dismiss this bug report? (optional)', '');
    if (reason === null) return;
    const dismissed = await this.run(() => API.dismissBugReport(this.project.id, report.id, reason));
    if (dismissed) {
      this.replace(dismissed);
      showToast('Bug report dismissed', 'success');
    }
  }
}
