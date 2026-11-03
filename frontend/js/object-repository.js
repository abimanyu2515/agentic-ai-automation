// Object Repository view (spec 0004): pages, elements, locator editing, and scans.
import { API } from './api.js?v=17';
import { escapeHTML } from './components.js?v=3';

const STRATEGIES = ['role', 'label', 'placeholder', 'text', 'test_id', 'css', 'xpath'];
const ELEMENT_TYPES = ['button', 'link', 'input', 'select', 'checkbox', 'radio', 'textarea', 'nav'];
const HEALTH_LABELS = {
  healthy: 'Healthy',
  needs_review: 'Needs review',
  broken: 'Broken',
  missing: 'Missing'
};
const MAX_LOCATORS = 5;

const esc = (value) => escapeHTML(value === null || value === undefined ? '' : String(value));

function healthBadge(health) {
  return `<span class="badge objrepo-health objrepo-health-${esc(health)}">${esc(HEALTH_LABELS[health] || health)}</span>`;
}

function locatorText(locator) {
  if (!locator) return '';
  const name = locator.options && locator.options.name ? `, name "${locator.options.name}"` : '';
  return `${locator.strategy}: ${locator.value}${name}`;
}

function formatDate(value) {
  if (!value) return 'Never';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Never' : date.toLocaleString();
}

export class ObjectRepositoryView {
  constructor() {
    this.project = null;
    this.pages = [];
    this.selectedPageId = null;
    this.elements = [];
    this.healthFilter = '';
    this.scanTimer = null;
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
    root.innerHTML = `
      <div class="objrepo-toolbar">
        <div class="objrepo-scan" aria-live="polite" id="objrepo-scan-status"></div>
        ${this.can('create') ? `<button type="button" class="btn btn-secondary" id="objrepo-scan-btn">Scan now</button>` : ''}
      </div>
      <div class="objrepo-layout">
        <section class="objrepo-pages section-card" aria-labelledby="objrepo-pages-title">
          <h2 id="objrepo-pages-title" class="objrepo-section-title">Pages</h2>
          <div id="objrepo-page-list"><div class="loading-overlay"><div class="spinner"></div><div class="loading-text">Loading pages...</div></div></div>
          ${this.can('create') ? `
          <form id="objrepo-page-form" class="objrepo-page-form" novalidate>
            <h3 class="objrepo-subtitle">Add a page</h3>
            <div class="form-group">
              <label for="objrepo-page-name">Name (snake_case)</label>
              <input id="objrepo-page-name" class="form-control" required pattern="[a-z][a-z0-9_]*" placeholder="login_page">
            </div>
            <div class="form-group">
              <label for="objrepo-page-pattern">URL pattern</label>
              <input id="objrepo-page-pattern" class="form-control" required placeholder="/login or /orders/*">
            </div>
            <p class="objrepo-error" id="objrepo-page-error" role="alert"></p>
            <button type="submit" class="btn btn-primary">Add page</button>
          </form>` : ''}
        </section>
        <section class="objrepo-elements section-card" aria-labelledby="objrepo-elements-title">
          <div class="objrepo-elements-header">
            <h2 id="objrepo-elements-title" class="objrepo-section-title">Elements</h2>
            <div class="objrepo-elements-actions">
              <label for="objrepo-health-filter" class="objrepo-filter-label">Health</label>
              <select id="objrepo-health-filter" class="form-control objrepo-filter">
                <option value="">All</option>
                ${Object.entries(HEALTH_LABELS).map(([value, label]) => `<option value="${value}">${label}</option>`).join('')}
              </select>
              ${this.can('create') ? `<button type="button" class="btn btn-primary" id="objrepo-add-element" disabled>Add element</button>` : ''}
            </div>
          </div>
          <div id="objrepo-element-table"></div>
        </section>
      </div>
      <div class="objrepo-drawer-backdrop" id="objrepo-drawer-backdrop" hidden></div>
      <aside class="objrepo-drawer" id="objrepo-drawer" role="dialog" aria-modal="true" aria-labelledby="objrepo-drawer-title" hidden></aside>
    `;
    root.querySelector('#objrepo-health-filter').value = this.healthFilter;
    root.querySelector('#objrepo-health-filter').addEventListener('change', (e) => {
      this.healthFilter = e.target.value;
      this.loadElements();
    });
    root.querySelector('#objrepo-scan-btn')?.addEventListener('click', () => this.startScan());
    root.querySelector('#objrepo-add-element')?.addEventListener('click', (e) => this.openDrawer(null, e.currentTarget));
    root.querySelector('#objrepo-page-form')?.addEventListener('submit', (e) => this.createPage(e));
    root.querySelector('#objrepo-drawer-backdrop').addEventListener('click', () => this.closeDrawer());
    await this.loadPages();
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
    if (!this.pages.length) {
      list.innerHTML = `<div class="objrepo-empty"><p>No pages yet.</p><p>Run a scan, or add a page and its elements by hand.</p></div>`;
      this.selectedPageId = null;
      this.renderElements();
      return;
    }
    if (!this.pages.some((page) => page.id === this.selectedPageId)) {
      this.selectedPageId = this.pages[0].id;
    }
    list.innerHTML = `<ul class="objrepo-page-list" role="list">${this.pages.map((page) => this.pageItem(page)).join('')}</ul>`;
    list.querySelectorAll('[data-page-id]').forEach((button) => {
      button.addEventListener('click', () => {
        this.selectedPageId = button.getAttribute('data-page-id');
        this.loadPages();
      });
    });
    list.querySelectorAll('[data-delete-page]').forEach((button) => {
      button.addEventListener('click', () => this.deletePage(button.getAttribute('data-delete-page')));
    });
    await this.loadElements();
  }

  pageItem(page) {
    const counts = page.counts || {};
    const selected = page.id === this.selectedPageId;
    const summary = Object.keys(HEALTH_LABELS)
      .filter((health) => counts[health])
      .map((health) => `<span class="objrepo-count objrepo-count-${health}">${counts[health]} ${esc(HEALTH_LABELS[health].toLowerCase())}</span>`)
      .join('');
    return `
      <li class="objrepo-page-item${selected ? ' selected' : ''}">
        <button type="button" class="objrepo-page-button" data-page-id="${esc(page.id)}" aria-pressed="${selected}">
          <span class="objrepo-page-name">${esc(page.name)}</span>
          <span class="objrepo-page-pattern">${esc(page.url_pattern)}</span>
          <span class="objrepo-counts">${summary || '<span class="objrepo-count">No elements</span>'}</span>
        </button>
        ${this.can('delete') ? `<button type="button" class="btn-icon objrepo-delete-page" data-delete-page="${esc(page.id)}" aria-label="Delete page ${esc(page.name)}">Delete</button>` : ''}
      </li>`;
  }

  selectedPage() {
    return this.pages.find((page) => page.id === this.selectedPageId) || null;
  }

  async loadElements() {
    const table = this.root?.querySelector('#objrepo-element-table');
    const addButton = this.root?.querySelector('#objrepo-add-element');
    if (addButton) addButton.disabled = !this.selectedPageId;
    if (!table) return;
    if (!this.selectedPageId) {
      this.elements = [];
      this.renderElements();
      return;
    }
    table.innerHTML = `<div class="loading-overlay"><div class="spinner"></div><div class="loading-text">Loading elements...</div></div>`;
    try {
      const data = await API.getObjectElements(this.project.id, this.selectedPageId, this.healthFilter);
      this.elements = data.elements || [];
    } catch (err) {
      table.innerHTML = `<p class="objrepo-error" role="alert">Could not load elements: ${esc(err.message)}</p>`;
      return;
    }
    this.renderElements();
  }

  renderElements() {
    const table = this.root?.querySelector('#objrepo-element-table');
    if (!table) return;
    if (!this.selectedPageId) {
      table.innerHTML = `<div class="objrepo-empty"><p>Select a page to see its elements.</p></div>`;
      return;
    }
    if (!this.elements.length) {
      table.innerHTML = `<div class="objrepo-empty"><p>${this.healthFilter ? 'No elements match this health filter.' : 'This page has no elements yet.'}</p></div>`;
      return;
    }
    const canEdit = this.can('edit');
    const canDelete = this.can('delete');
    table.innerHTML = `
      <div class="table-container">
        <table class="custom-table objrepo-table">
          <caption class="objrepo-visually-hidden">Elements of ${esc(this.selectedPage()?.name || 'the page')}</caption>
          <thead><tr>
            <th scope="col">Element</th><th scope="col">Type</th><th scope="col">Health</th>
            <th scope="col">Main locator</th><th scope="col">Last used</th>
            ${canEdit || canDelete ? '<th scope="col"><span class="objrepo-visually-hidden">Actions</span></th>' : ''}
          </tr></thead>
          <tbody>
            ${this.elements.map((element) => `
              <tr>
                <td><code class="objrepo-key">${esc(element.key)}</code>${element.manually_edited ? ' <span class="objrepo-tag">edited</span>' : ''}</td>
                <td>${esc(element.element_type)}</td>
                <td>${healthBadge(element.health)}</td>
                <td class="objrepo-locator">${esc(locatorText(element.locators[0]))}${element.locators.length > 1 ? ` <span class="objrepo-more">+${element.locators.length - 1} backup</span>` : ''}</td>
                <td>${esc(formatDate(element.last_used_at))}</td>
                ${canEdit || canDelete ? `<td class="objrepo-row-actions">
                  ${canEdit ? `<button type="button" class="btn btn-secondary objrepo-small" data-edit-element="${esc(element.id)}">Edit</button>` : ''}
                  ${canDelete ? `<button type="button" class="btn btn-danger objrepo-small" data-delete-element="${esc(element.id)}" aria-label="Delete ${esc(element.key)}">Delete</button>` : ''}
                </td>` : ''}
              </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
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

  async createPage(event) {
    event.preventDefault();
    const name = this.root.querySelector('#objrepo-page-name').value.trim();
    const urlPattern = this.root.querySelector('#objrepo-page-pattern').value.trim();
    const error = this.root.querySelector('#objrepo-page-error');
    error.textContent = '';
    try {
      const page = await API.createObjectPage(this.project.id, { name, url_pattern: urlPattern });
      this.selectedPageId = page.id;
      event.target.reset();
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
      if (result.referencing_test_cases) {
        alert(`${result.referencing_test_cases} test case script(s) still reference elements of this page.`);
      }
      await this.loadPages();
    } catch (err) {
      alert(`Could not delete the page: ${err.message}`);
    }
  }

  async deleteElement(elementId) {
    const element = this.elements.find((item) => item.id === elementId);
    if (!element || !confirm(`Delete element "${element.key}"?`)) return;
    try {
      const result = await API.deleteObjectElement(this.project.id, elementId);
      if (result.referencing_test_cases) {
        alert(`${result.referencing_test_cases} test case script(s) still reference ${element.key}.`);
      }
      await this.loadPages();
    } catch (err) {
      alert(`Could not delete the element: ${err.message}`);
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
    drawer.innerHTML = `
      <div class="objrepo-drawer-header">
        <h2 id="objrepo-drawer-title">${isNew ? 'Add element' : `Edit ${esc(this.selectedPage()?.name || '')}.${esc(draft.name)}`}</h2>
        <button type="button" class="modal-close" id="objrepo-drawer-close" aria-label="Close">&times;</button>
      </div>
      <form id="objrepo-element-form" novalidate>
        <div class="form-group">
          <label for="objrepo-el-name">Name (snake_case)</label>
          <input id="objrepo-el-name" class="form-control" value="${esc(draft.name)}" required>
          ${isNew ? '' : '<small class="objrepo-hint">Renaming rewrites every script that uses this key.</small>'}
        </div>
        <div class="form-group">
          <label for="objrepo-el-type">Type</label>
          <select id="objrepo-el-type" class="form-control">
            ${ELEMENT_TYPES.map((type) => `<option value="${type}"${type === draft.element_type ? ' selected' : ''}>${type}</option>`).join('')}
          </select>
        </div>
        <div class="form-group">
          <label for="objrepo-el-description">Description</label>
          <input id="objrepo-el-description" class="form-control" value="${esc(draft.description)}">
        </div>
        <fieldset class="objrepo-locators">
          <legend>Locators, tried in order (${draft.locators.length} of ${MAX_LOCATORS})</legend>
          ${draft.locators.map((locator, index) => this.locatorRow(locator, index, draft.locators.length)).join('')}
          <button type="button" class="btn btn-secondary objrepo-small" id="objrepo-add-locator" ${draft.locators.length >= MAX_LOCATORS ? 'disabled' : ''}>Add locator</button>
        </fieldset>
        <p class="objrepo-error" id="objrepo-element-error" role="alert"></p>
        <div class="form-actions">
          <button type="button" class="btn btn-secondary" id="objrepo-drawer-cancel">Cancel</button>
          <button type="submit" class="btn btn-primary">${isNew ? 'Add element' : 'Save changes'}</button>
        </div>
      </form>`;
    drawer.querySelector('#objrepo-drawer-close').addEventListener('click', () => this.closeDrawer());
    drawer.querySelector('#objrepo-drawer-cancel').addEventListener('click', () => this.closeDrawer());
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
    drawer.querySelector('#objrepo-element-form').addEventListener('submit', (e) => this.saveElement(e));
    if (focusSelector) drawer.querySelector(focusSelector)?.focus();
  }

  locatorRow(locator, index, total) {
    const options = locator.options || {};
    const showName = locator.strategy === 'role';
    const showExact = ['role', 'label', 'text'].includes(locator.strategy);
    const label = index === 0 ? 'Main locator' : `Backup ${index}`;
    return `
      <div class="objrepo-locator-row" data-locator-row="${index}" role="group" aria-label="${label}">
        <span class="objrepo-locator-label">${label}</span>
        <label class="objrepo-visually-hidden" for="objrepo-loc-strategy-${index}">${label} strategy</label>
        <select id="objrepo-loc-strategy-${index}" class="form-control" data-field="strategy">
          ${STRATEGIES.map((strategy) => `<option value="${strategy}"${strategy === locator.strategy ? ' selected' : ''}>${strategy}</option>`).join('')}
        </select>
        <label class="objrepo-visually-hidden" for="objrepo-loc-value-${index}">${label} value</label>
        <input id="objrepo-loc-value-${index}" class="form-control" data-field="value" value="${esc(locator.value)}" placeholder="${locator.strategy === 'role' ? 'button' : 'value'}">
        ${showName ? `
          <label class="objrepo-visually-hidden" for="objrepo-loc-name-${index}">${label} accessible name</label>
          <input id="objrepo-loc-name-${index}" class="form-control" data-field="name" value="${esc(options.name || '')}" placeholder="accessible name">` : ''}
        ${showExact ? `<label class="objrepo-exact"><input type="checkbox" data-field="exact"${options.exact ? ' checked' : ''}> exact</label>` : ''}
        <div class="objrepo-locator-buttons">
          <button type="button" class="btn-icon" data-locator-action="up" data-index="${index}" aria-label="Move ${label} up" ${index === 0 ? 'disabled' : ''}>&uarr;</button>
          <button type="button" class="btn-icon" data-locator-action="down" data-index="${index}" aria-label="Move ${label} down" ${index === total - 1 ? 'disabled' : ''}>&darr;</button>
          <button type="button" class="btn-icon" data-locator-action="remove" data-index="${index}" aria-label="Remove ${label}" ${total === 1 ? 'disabled' : ''}>&times;</button>
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
        if (result.scripts_rewritten) {
          alert(`Renamed. ${result.scripts_rewritten} test case script(s) were updated to the new key.`);
        }
      } else {
        await API.createObjectElement(this.project.id, this.selectedPageId, payload);
      }
      this.closeDrawer();
      await this.loadPages();
    } catch (err) {
      error.textContent = Array.isArray(err.detail) ? err.detail.map((d) => d.msg).join('; ') : err.message;
    }
  }

  // ------------------------------------------------------------ scan

  async startScan() {
    const status = this.root.querySelector('#objrepo-scan-status');
    const button = this.root.querySelector('#objrepo-scan-btn');
    status.textContent = 'Starting scan...';
    try {
      const { scan_id: scanId } = await API.startObjectScan(this.project.id);
      if (button) button.disabled = true;
      this.pollScan(scanId);
    } catch (err) {
      status.textContent = `Scan could not start: ${err.message}`;
    }
  }

  pollScan(scanId) {
    clearTimeout(this.scanTimer);
    const projectId = this.project.id;
    const tick = async () => {
      if (!this.project || this.project.id !== projectId || !this.root) return;
      const status = this.root.querySelector('#objrepo-scan-status');
      const button = this.root.querySelector('#objrepo-scan-btn');
      try {
        const scan = await API.getObjectScan(projectId, scanId);
        if (scan.status === 'queued' || scan.status === 'running') {
          status.textContent = scan.status === 'queued' ? 'Scan queued...' : 'Scanning pages...';
          this.scanTimer = setTimeout(tick, 1500);
          return;
        }
        if (button) button.disabled = false;
        status.textContent = scan.status === 'succeeded'
          ? `Scan finished: ${scan.pages_scanned} page(s), ${scan.added} added, ${scan.updated} updated, ${scan.marked_missing} marked missing.`
          : `Scan failed: ${scan.error || 'unknown error'}`;
        await this.loadPages();
      } catch (err) {
        if (button) button.disabled = false;
        status.textContent = `Could not read scan progress: ${err.message}`;
      }
    };
    tick();
  }
}
