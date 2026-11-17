// Chat assistant panel (spec 0005).
// A floating button and panel that live outside the view panels, so
// switching views never resets them. The open state and the active thread
// per project are kept in localStorage only as hints; the thread itself is
// always loaded from the server.
import { API } from './api.js?v=20';
import { escapeHTML } from './components.js?v=5';

const OPEN_KEY = 'chat_panel_open';
const EXPANDED_KEY = 'chat_panel_expanded';
const threadKey = projectId => `chat_thread_${projectId}`;

function readHint(key) {
  try { return localStorage.getItem(key); } catch (e) { return null; }
}

function writeHint(key, value) {
  try {
    if (value === null || value === undefined) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch (e) {}
}

let purifyHooked = false;

// AC-23: Markdown through marked, then DOMPurify, before it reaches the DOM.
// Without either library the text is escaped instead, never inserted raw.
export function renderMarkdown(text) {
  if (!window.marked || !window.DOMPurify) {
    return `<p>${escapeHTML(text || '').replace(/\n/g, '<br>')}</p>`;
  }
  if (!purifyHooked) {
    window.DOMPurify.addHook('afterSanitizeAttributes', node => {
      if (node.tagName === 'A' && node.getAttribute('href')) {
        node.setAttribute('target', '_blank');
        node.setAttribute('rel', 'noopener noreferrer');
      }
    });
    purifyHooked = true;
  }
  const html = window.marked.parse(text || '', { gfm: true, breaks: true });
  // No images or form controls: an injected image URL would make the browser
  // send data to another server without any click.
  return window.DOMPurify.sanitize(html, { FORBID_TAGS: ['img', 'style', 'form', 'input', 'button', 'textarea', 'select'] });
}

const ICON_CHAT = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
const ICON_LIST = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="20" y2="18"/></svg>';
const ICON_PLUS = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';
const ICON_EXPAND = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>';
const ICON_CLOSE = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';

// AC-27: fixed item type labels for the context line.
const ITEM_TYPE_LABELS = {
  requirement: 'requirement',
  scenario: 'scenario',
  test_case: 'test case',
  execution: 'execution',
  object_repository_page: 'page'
};
const CONTEXT_NAME_MAX = 60;

// What the panel shows while a read tool runs ("Looking up test cases…").
const TOOL_LABELS = {
  get_project_overview: 'the project overview',
  list_requirements: 'requirements',
  get_requirement: 'a requirement',
  list_scenarios: 'scenarios',
  list_test_cases: 'test cases',
  get_test_case: 'a test case',
  list_executions: 'executions',
  get_execution: 'an execution',
  get_dashboard_metrics: 'dashboard metrics',
  list_object_repository_pages: 'object repository pages'
};

function shortName(name) {
  const text = String(name || '').replace(/\s+/g, ' ').trim();
  if (!text) return '';
  return text.length > CONTEXT_NAME_MAX ? `${text.slice(0, CONTEXT_NAME_MAX)}…` : text;
}

// The sidebar nav item text for a view, else the view key.
function viewLabel(view) {
  const nav = document.querySelector(`.nav-item[data-view="${CSS.escape(view)}"]`);
  const text = nav ? nav.textContent.replace(/\s+/g, ' ').trim() : '';
  return text || view;
}

// Action cards (spec 0005, AC-10 to AC-16).
const ACTION_POLL_MS = 3000;
const ACTIVE_STATUSES = new Set(['queued', 'running']);
const ACTION_STATUS_TEXT = {
  proposed: 'Waiting for you',
  queued: 'Queued',
  running: 'Running…',
  succeeded: 'Done',
  failed: 'Failed',
  cancelled: 'Cancelled',
  expired: 'Expired, ask again'
};
const LINK_LABELS = {
  scenarios: 'Open scenarios',
  testcases: 'Open test cases',
  playwright: 'Open the script',
  executions: 'Open executions',
  requirements: 'Open requirements'
};
const ACTION_ERROR_TEXT = { 403: 'You no longer have permission' };

const plural = (count, word) => `${count} ${word}${count === 1 ? '' : 's'}`;

// The card's main line and target list come from the server's type, params,
// and targets, never from the model written summary (spec 0005, card rendering).
function describeAction(action) {
  const targets = action.targets || [];
  const params = action.params || {};
  if (action.type === 'generate_scenarios') {
    const requirement = targets.find(t => t.type === 'requirement');
    const replaced = targets.filter(t => t.type === 'scenario');
    const title = `Generate ${plural(Number(params.count) || 3, 'scenario')} for requirement '${requirement ? requirement.name : ''}'`;
    if (params.mode === 'replace' && replaced.length) {
      return {
        title,
        listLabel: `Then delete the ${plural(replaced.length, 'current scenario')} it replaces:`,
        list: replaced
      };
    }
    return { title, listLabel: 'Keeps the existing scenarios.', list: [] };
  }
  if (['update_requirement', 'update_scenario', 'update_test_case'].includes(action.type)) {
    const target = targets[0];
    const changes = params.changes || {};
    const oldValues = params.old_values || {};
    const fieldLabels = {
      title: 'Title',
      description: 'Description',
      priority: 'Priority',
      business_domain: 'Business domain',
      scenario_name: 'Scenario name',
      steps: 'Steps',
      expected_result: 'Expected result',
      data: 'Test data'
    };
    const valueText = value => typeof value === 'string' ? value : JSON.stringify(value);
    const fields = Object.entries(changes).map(([field, value]) => ({
      name: `${fieldLabels[field] || field}: ${valueText(oldValues[field]) || '(empty)'} → ${valueText(value)}`,
      id: ''
    }));
    return {
      title: `Update ${target ? target.name : String(action.type).replace('update_', '')}`,
      listLabel: 'Will set:',
      list: fields
    };
  }
  if (action.type === 'review_test_case') {
    const title = params.decision === 'rejected' ? 'Reject test case' : 'Approve test case';
    return { title, listLabel: 'On:', list: targets };
  }
  if (action.type === 'generate_test_data') {
    return { title: 'Generate test data (20 datasets)', listLabel: 'For:', list: targets };
  }
  const words = String(action.type || 'action').replace(/_/g, ' ');
  const label = action.danger === 'delete' ? 'Will delete:' : 'On:';
  return { title: words.charAt(0).toUpperCase() + words.slice(1), listLabel: targets.length ? label : '', list: targets };
}

const ERROR_TEXT = {
  404: 'This conversation no longer exists. Start a new chat.',
  409: 'A reply is still being written in this conversation. Wait for it to finish.',
  429: 'Too many messages, wait a moment.'
};

export class ChatPanel {
  constructor(app) {
    this.app = app;
    this.root = null;
    this.projectId = null;
    this.threadId = null;
    this.messages = [];
    this.helpMessages = [];
    this.hasMore = false;
    this.loadingOlder = false;
    this.streaming = false;
    this.controller = null;
    this.threadCursor = null;
    this.pollTimer = null;
    this.onProjectChanged = () => this.syncProject();
    this.onContextChanged = () => this.renderContextLine();
  }

  // ------------------------------------------------------------- lifecycle

  mount() {
    if (this.root) return;
    const root = document.createElement('div');
    root.className = 'chat-root';
    root.innerHTML = `
      <button type="button" class="chat-fab" title="Ask the assistant">${ICON_CHAT}</button>
      <section class="chat-panel" hidden>
        <header class="chat-header">
          <button type="button" class="chat-icon-btn" data-chat="threads" title="Conversations">${ICON_LIST}</button>
          <div class="chat-title">
            <span class="chat-title-main">Assistant</span>
          </div>
          <button type="button" class="chat-icon-btn" data-chat="new" title="New chat">${ICON_PLUS}</button>
          <button type="button" class="chat-icon-btn chat-expand-btn" data-chat="expand" title="Expand">${ICON_EXPAND}</button>
          <button type="button" class="chat-icon-btn" data-chat="close" title="Close">${ICON_CLOSE}</button>
        </header>
        <div class="chat-threads" hidden>
          <div class="chat-threads-list"></div>
          <button type="button" class="chat-link-btn chat-threads-more" hidden>Load more</button>
        </div>
        <div class="chat-messages"></div>
        <div class="chat-context" hidden></div>
        <form class="chat-form">
          <textarea class="chat-input" rows="2" maxlength="4000" placeholder="Ask about this project"></textarea>
          <div class="chat-form-actions">
            <span class="chat-hint"></span>
            <button type="button" class="btn btn-secondary chat-stop" hidden>Stop</button>
            <button type="submit" class="btn btn-primary chat-send">Send</button>
          </div>
        </form>
      </section>`;
    document.body.appendChild(root);
    this.root = root;
    this.el = {
      fab: root.querySelector('.chat-fab'),
      panel: root.querySelector('.chat-panel'),
      threads: root.querySelector('.chat-threads'),
      threadList: root.querySelector('.chat-threads-list'),
      threadsMore: root.querySelector('.chat-threads-more'),
      messages: root.querySelector('.chat-messages'),
      form: root.querySelector('.chat-form'),
      input: root.querySelector('.chat-input'),
      send: root.querySelector('.chat-send'),
      stop: root.querySelector('.chat-stop'),
      hint: root.querySelector('.chat-hint'),
      context: root.querySelector('.chat-context')
    };

    this.el.fab.addEventListener('click', () => this.toggle());
    root.querySelector('[data-chat="close"]').addEventListener('click', () => this.close());
    root.querySelector('[data-chat="new"]').addEventListener('click', () => this.newChat());
    root.querySelector('[data-chat="expand"]').addEventListener('click', () => this.toggleExpanded());
    root.querySelector('[data-chat="threads"]').addEventListener('click', () => this.toggleThreads());
    this.el.threadsMore.addEventListener('click', () => this.loadThreads(true));
    this.el.form.addEventListener('submit', e => { e.preventDefault(); this.send(); });
    this.el.stop.addEventListener('click', () => this.stop());
    this.el.messages.addEventListener('click', e => this.onCardClick(e));
    this.el.messages.addEventListener('input', e => this.onCardInput(e));
    this.el.messages.addEventListener('scroll', () => {
      if (this.el.messages.scrollTop < 40) this.loadOlder();
    });
    window.addEventListener('app:project-changed', this.onProjectChanged);
    window.addEventListener('chat:context-changed', this.onContextChanged);

    if (readHint(EXPANDED_KEY) === '1') this.el.panel.classList.add('chat-expanded');
    this.syncProject();
    if (readHint(OPEN_KEY) === '1') this.open();
  }

  unmount() {
    this.stop();
    this.stopPolling();
    window.removeEventListener('app:project-changed', this.onProjectChanged);
    window.removeEventListener('chat:context-changed', this.onContextChanged);
    if (this.root) this.root.remove();
    this.root = null;
  }

  isOpen() {
    return this.root && !this.el.panel.hidden;
  }

  open() {
    this.el.panel.hidden = false;
    this.el.fab.classList.add('chat-fab-active');
    writeHint(OPEN_KEY, '1');
    this.render();
    if (this.projectId && this.threadId && !this.messages.length) this.loadThread(this.threadId);
    this.renderContextLine();
    this.el.input.focus();
  }

  close() {
    this.el.panel.hidden = true;
    this.el.fab.classList.remove('chat-fab-active');
    writeHint(OPEN_KEY, '0');
  }

  toggle() {
    if (this.isOpen()) this.close();
    else this.open();
  }

  toggleExpanded() {
    const expanded = this.el.panel.classList.toggle('chat-expanded');
    writeHint(EXPANDED_KEY, expanded ? '1' : '0');
  }

  // --------------------------------------------------------------- project

  syncProject() {
    const project = this.app.currentProject;
    const projectId = project ? project.id : null;
    if (projectId === this.projectId) return;
    this.stop();
    this.stopPolling();
    this.projectId = projectId;
    this.threadId = projectId ? readHint(threadKey(projectId)) : null;
    this.messages = [];
    this.helpMessages = [];
    this.hasMore = false;
    this.threadCursor = null;
    this.el.threads.hidden = true;
    if (this.isOpen() && this.threadId) this.loadThread(this.threadId);
    this.render();
    this.renderContextLine();
  }

  // AC-27: a read only line naming what the next message will be about.
  // Display only: it is never sent to the server.
  renderContextLine() {
    if (!this.root) return;
    const line = this.el.context;
    if (!this.projectId || typeof this.app.getChatContext !== 'function') {
      line.hidden = true;
      line.textContent = '';
      return;
    }
    const context = this.app.getChatContext();
    let text;
    if (context.item_type && context.item_id) {
      const label = ITEM_TYPE_LABELS[context.item_type] || context.item_type;
      const name = shortName(this.app.getChatItemName(context.item_type, context.item_id));
      text = name ? `About: ${label} '${name}'` : `About: ${label}`;
    } else {
      text = `About: ${viewLabel(context.view)}`;
    }
    line.textContent = text;
    line.title = text;
    line.hidden = false;
  }

  setThread(threadId) {
    this.threadId = threadId;
    if (this.projectId) writeHint(threadKey(this.projectId), threadId);
  }

  newChat() {
    if (this.streaming) return;
    // AC-5: New chat only clears the panel; the server creates the thread
    // when the first message is sent.
    this.stopPolling();
    this.setThread(null);
    this.messages = [];
    this.helpMessages = [];
    this.hasMore = false;
    this.el.threads.hidden = true;
    this.render();
    this.el.input.focus();
  }

  // ---------------------------------------------------------------- threads

  async toggleThreads() {
    if (!this.projectId) return;
    const showing = this.el.threads.hidden;
    this.el.threads.hidden = !showing;
    if (showing) await this.loadThreads(false);
  }

  async loadThreads(more) {
    if (!this.projectId) return;
    const projectId = this.projectId;
    if (!more) {
      this.threadCursor = null;
      this.el.threadList.innerHTML = '<div class="chat-muted">Loading…</div>';
    }
    try {
      const page = await API.getChatThreads(projectId, more ? this.threadCursor : null);
      if (projectId !== this.projectId) return;
      if (!more) this.el.threadList.innerHTML = '';
      if (!page.items.length && !more) {
        this.el.threadList.innerHTML = '<div class="chat-muted">No conversations yet.</div>';
      }
      for (const thread of page.items) {
        const entry = document.createElement('div');
        entry.className = 'chat-thread-entry';
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'chat-thread-item' + (thread.id === this.threadId ? ' active' : '');
        button.innerHTML = `<span class="chat-thread-title">${escapeHTML(thread.title)}</span>
          <span class="chat-thread-time">${escapeHTML(new Date(thread.last_activity_at).toLocaleString())}</span>`;
        button.addEventListener('click', () => {
          this.el.threads.hidden = true;
          this.loadThread(thread.id);
        });
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'chat-thread-delete';
        remove.title = 'Delete conversation';
        remove.setAttribute('aria-label', `Delete ${thread.title}`);
        remove.textContent = '×';
        remove.addEventListener('click', event => {
          event.stopPropagation();
          this.deleteThread(thread.id);
        });
        entry.append(button, remove);
        this.el.threadList.appendChild(entry);
      }
      this.threadCursor = page.next_cursor;
      this.el.threadsMore.hidden = !page.next_cursor;
    } catch (err) {
      this.el.threadList.innerHTML = `<div class="chat-error">${escapeHTML(err.message)}</div>`;
    }
  }

  async loadThread(threadId) {
    if (this.streaming || !this.projectId) return;
    const projectId = this.projectId;
    this.stopPolling();
    this.setThread(threadId);
    this.messages = [];
    this.hasMore = false;
    this.render('Loading…');
    try {
      const page = await API.getChatMessages(projectId, threadId);
      if (projectId !== this.projectId || threadId !== this.threadId) return;
      this.messages = page.items;
      this.hasMore = page.has_more;
      this.render();
      this.scrollToBottom();
      // AC-15: a card still queued or running after a reload keeps polling.
      this.ensurePolling();
    } catch (err) {
      if (projectId !== this.projectId) return;
      if (err.status === 404) {
        // AC-2: purged or deleted elsewhere; drop the hint, show a new chat.
        this.setThread(null);
        this.messages = [];
        this.render();
      } else {
        this.render();
        this.showError(err.message);
      }
    }
  }

  async deleteThread(threadId) {
    if (!this.projectId) return;
    const projectId = this.projectId;
    try {
      await API.deleteChatThread(projectId, threadId);
      if (projectId !== this.projectId) return;
      if (threadId === this.threadId) this.newChat();
      await this.loadThreads(false);
    } catch (err) {
      this.showError(err.message);
    }
  }

  async loadOlder() {
    if (!this.hasMore || this.loadingOlder || !this.threadId || !this.messages.length) return;
    this.loadingOlder = true;
    const projectId = this.projectId;
    const threadId = this.threadId;
    const box = this.el.messages;
    const before = box.scrollHeight - box.scrollTop;
    try {
      const page = await API.getChatMessages(projectId, threadId, this.messages[0].seq);
      if (projectId !== this.projectId || threadId !== this.threadId) return;
      this.messages = [...page.items, ...this.messages];
      this.hasMore = page.has_more;
      this.render();
      box.scrollTop = box.scrollHeight - before;
    } catch (err) {
      console.error('Could not load older chat messages', err);
    } finally {
      this.loadingOlder = false;
    }
  }

  // ---------------------------------------------------------------- sending

  async send() {
    const content = this.el.input.value.trim();
    if (!content || this.streaming) return;
    if (!this.projectId) {
      await this.sendHelp(content);
      return;
    }
    const projectId = this.projectId;

    // AC-14: the server expires every earlier card still waiting for Run.
    for (const message of this.messages) {
      if (message.action && message.action.status === 'proposed') message.action = { ...message.action, status: 'expired' };
    }
    const userMessage = { id: `local-user-${Date.now()}`, role: 'user', content };
    const reply = { id: `local-reply-${Date.now()}`, role: 'assistant', content: '', pending: true };
    this.messages.push(userMessage, reply);
    this.el.input.value = '';
    this.setStreaming(true);
    this.render();
    this.scrollToBottom();

    const controller = new AbortController();
    this.controller = controller;
    let frame = null;
    const paint = () => {
      frame = null;
      this.updateBubble(reply);
      this.scrollToBottom(true);
    };

    try {
      await API.sendChatMessage(projectId, {
        thread_id: this.threadId,
        content,
        view_context: this.app.getChatContext()
      }, (event, data) => {
        if (projectId !== this.projectId) return;
        if (event === 'thread') {
          this.setThread(data.thread_id);
        } else if (event === 'message_start') {
          const node = this.el.messages.querySelector(`.chat-msg[data-id="${reply.id}"]`);
          userMessage.id = data.user_message_id;
          reply.id = data.assistant_message_id;
          if (node) node.dataset.id = reply.id;
        } else if (event === 'token') {
          reply.content += data.text;
          reply.tool = null;
          if (!frame) frame = requestAnimationFrame(paint);
        } else if (event === 'tool') {
          reply.tool = data.state === 'start' ? data.name : null;
          if (!frame) frame = requestAnimationFrame(paint);
        } else if (event === 'action') {
          reply.action = data.action;
          if (!frame) frame = requestAnimationFrame(paint);
        } else if (event === 'done') {
          reply.pending = false;
          reply.tool = null;
        } else if (event === 'error') {
          reply.pending = false;
          reply.tool = null;
          reply.interrupted = true;
          reply.error = data.message;
        }
      }, controller.signal);
    } catch (err) {
      reply.pending = false;
      if (err.name === 'AbortError') {
        reply.interrupted = true;
      } else {
        reply.error = ERROR_TEXT[err.status] || err.message;
        if (!reply.content) this.messages = this.messages.filter(m => m !== reply);
        if (err.status === 404) this.setThread(null);
      }
    } finally {
      if (frame) cancelAnimationFrame(frame);
      reply.pending = false;
      if (this.controller === controller) this.controller = null;
      if (projectId === this.projectId) {
        this.setStreaming(false);
        this.render();
        if (reply.error && !this.messages.includes(reply)) this.showError(reply.error);
        this.scrollToBottom();
      }
    }
  }

  async sendHelp(content) {
    const history = this.helpMessages
      .filter(message => message.role === 'user' || message.role === 'assistant')
      .slice(-10)
      .map(message => ({ role: message.role, content: message.content }));
    const userMessage = { id: `local-help-user-${Date.now()}`, role: 'user', content };
    const reply = { id: `local-help-reply-${Date.now()}`, role: 'assistant', content: '', pending: true };
    this.helpMessages.push(userMessage, reply);
    this.el.input.value = '';
    this.setStreaming(true);
    this.render();
    this.scrollToBottom();

    const controller = new AbortController();
    this.controller = controller;
    let frame = null;
    try {
      await API.sendChatHelp({ content, history }, (event, data) => {
        if (event === 'message_start') {
          userMessage.id = data.user_message_id;
          reply.id = data.assistant_message_id;
        } else if (event === 'token') {
          reply.content += data.text;
          if (!frame) {
            frame = requestAnimationFrame(() => {
              frame = null;
              this.updateBubble(reply);
              this.scrollToBottom(true);
            });
          }
        } else if (event === 'done') {
          reply.pending = false;
        } else if (event === 'error') {
          reply.pending = false;
          reply.interrupted = true;
          reply.error = data.message;
        }
      }, controller.signal);
    } catch (err) {
      reply.pending = false;
      if (err.name === 'AbortError') reply.interrupted = true;
      else {
        reply.error = err.status === 429 ? ERROR_TEXT[429] : err.message;
        if (!reply.content) this.helpMessages = this.helpMessages.filter(message => message !== reply);
      }
    } finally {
      if (frame) cancelAnimationFrame(frame);
      if (this.controller === controller) this.controller = null;
      this.helpMessages = this.helpMessages.slice(-10);
      this.setStreaming(false);
      this.render();
      this.scrollToBottom();
    }
  }

  stop() {
    if (this.controller) this.controller.abort();
    this.controller = null;
  }

  setStreaming(streaming) {
    this.streaming = streaming;
    if (!this.root) return;
    this.el.input.disabled = streaming;
    this.el.send.disabled = streaming;
    this.el.send.hidden = streaming;
    this.el.stop.hidden = !streaming;
  }

  // -------------------------------------------------------------- rendering

  render(status = null) {
    if (!this.root) return;
    const box = this.el.messages;
    this.setStreaming(this.streaming);
    this.el.hint.textContent = '';

    if (!this.projectId) {
      this.el.input.placeholder = 'Ask how to use the app';
      if (!this.helpMessages.length) {
        box.innerHTML = `<div class="chat-empty"><strong>Need help getting started?</strong>
          <span>Ask how to use the app. Select a project for answers about its data.</span></div>`;
        return;
      }
      box.innerHTML = this.helpMessages.map(message => this.bubbleHTML(message)).join('');
      return;
    }
    this.el.input.placeholder = 'Ask about this project';

    if (status) {
      box.innerHTML = `<div class="chat-muted chat-center">${escapeHTML(status)}</div>`;
      return;
    }
    if (!this.messages.length) {
      box.innerHTML = `<div class="chat-empty"><strong>Ask anything about this project.</strong>
        <span>For example: "What does the login requirement cover?" or "How do I generate a script?"</span></div>`;
      return;
    }
    const older = this.hasMore ? '<div class="chat-muted chat-center">Scroll up for older messages</div>' : '';
    box.innerHTML = older + this.messages.map(m => this.bubbleHTML(m)).join('');
  }

  bubbleHTML(message) {
    const id = escapeHTML(String(message.id));
    if (message.role === 'user') {
      return `<div class="chat-msg chat-msg-user" data-id="${id}"><div class="chat-bubble">${escapeHTML(message.content)}</div></div>`;
    }
    const card = message.action ? this.cardHTML(message) : '';
    const inner = this.assistantInner(message);
    // A proposal sent with no text shows the card alone.
    const bubble = inner || !card ? `<div class="chat-bubble chat-markdown">${inner}</div>` : '';
    return `<div class="chat-msg chat-msg-assistant" data-id="${id}">${bubble}${card}</div>`;
  }

  // ----------------------------------------------------------- action cards

  // AC-10, AC-13, AC-15: what will run, Run and Cancel while proposed, the
  // typed DELETE for deletes, then live status and a link to the result.
  // Every field is escaped; nothing here is rendered as Markdown.
  cardHTML(message) {
    const action = message.action;
    const status = action.status || 'proposed';
    const { title, listLabel, list } = describeAction(action);
    const isDelete = action.danger === 'delete';
    const busy = Boolean(message.actionBusy);
    const parts = [
      `<div class="chat-action-head"><span class="chat-action-title">${escapeHTML(title)}</span>
        <span class="chat-action-status chat-action-status-${escapeHTML(status)}">${escapeHTML(ACTION_STATUS_TEXT[status] || status)}</span></div>`
    ];
    if (listLabel) parts.push(`<div class="chat-action-label">${escapeHTML(listLabel)}</div>`);
    if (list.length) {
      // A `children` row is a count ("3 scenarios and 12 test cases", "and N
      // more"), not a record, so it carries no ID chip.
      parts.push(`<ul class="chat-action-targets">${list.map(t => {
        const id = t.type === 'children' || !t.id ? '' : ` <code>${escapeHTML(String(t.id).slice(0, 8))}</code>`;
        return `<li><span>${escapeHTML(t.name || '')}</span>${id}</li>`;
      }).join('')}</ul>`);
    }
    if (action.summary) parts.push(`<div class="chat-action-summary">${escapeHTML(action.summary)}</div>`);

    if (status === 'proposed') {
      const typed = message.confirmText || '';
      if (isDelete) {
        parts.push(`<label class="chat-action-confirm-label">Type DELETE to confirm
          <input type="text" class="chat-action-confirm" autocomplete="off" spellcheck="false" value="${escapeHTML(typed)}"></label>`);
      }
      const runDisabled = busy || (isDelete && typed !== 'DELETE');
      parts.push(`<div class="chat-action-buttons">
        <button type="button" class="btn btn-secondary chat-action-btn" data-act="cancel" ${busy ? 'disabled' : ''}>Cancel</button>
        <button type="button" class="btn ${isDelete ? 'btn-danger' : 'btn-primary'} chat-action-btn" data-act="run" ${runDisabled ? 'disabled' : ''}>Run</button>
      </div>`);
    }
    if (status === 'succeeded' && action.result) {
      parts.push(`<div class="chat-action-result">${escapeHTML(action.result.summary || '')}</div>`);
      const label = LINK_LABELS[action.result.link_view];
      if (label) parts.push(`<button type="button" class="chat-link-btn" data-act="open">${escapeHTML(label)}</button>`);
    }
    if (status === 'failed' && action.error) parts.push(`<div class="chat-error">${escapeHTML(action.error)}</div>`);
    if (message.actionError) parts.push(`<div class="chat-error">${escapeHTML(message.actionError)}</div>`);
    return `<div class="chat-action chat-action-${escapeHTML(status)}${isDelete ? ' chat-action-danger' : ''}">${parts.join('')}</div>`;
  }

  messageForEvent(e) {
    const node = e.target.closest('.chat-msg-assistant');
    if (!node) return null;
    return this.messages.find(m => String(m.id) === node.dataset.id && m.action) || null;
  }

  onCardInput(e) {
    if (!e.target.classList.contains('chat-action-confirm')) return;
    const message = this.messageForEvent(e);
    if (!message) return;
    message.confirmText = e.target.value;
    const run = e.target.closest('.chat-action').querySelector('[data-act="run"]');
    if (run) run.disabled = Boolean(message.actionBusy) || message.confirmText !== 'DELETE';
  }

  onCardClick(e) {
    const button = e.target.closest('[data-act]');
    if (button && button.dataset.act === 'retry') {
      const node = button.closest('.chat-msg-assistant');
      const message = this.messages.find(item => String(item.id) === node?.dataset.id);
      if (message) this.retryMessage(message);
      return;
    }
    if (!button) return;
    const message = this.messageForEvent(e);
    if (!message) return;
    const act = button.dataset.act;
    if (act === 'run') this.runAction(message);
    else if (act === 'cancel') this.cancelAction(message);
    else if (act === 'open' && message.action.result && typeof this.app.openChatLink === 'function') {
      this.app.openChatLink(message.action.result.link_view, message.action.result.link_id);
    }
  }

  async runAction(message) {
    if (message.actionBusy || String(message.id).startsWith('local-')) return;
    const projectId = this.projectId;
    message.actionBusy = true;
    message.actionError = null;
    this.updateCard(message);
    try {
      const confirmText = message.action.danger === 'delete' ? (message.confirmText || '') : null;
      const action = await API.confirmChatAction(projectId, message.id, confirmText);
      this.applyAction(message, action);
    } catch (err) {
      await this.handleActionError(message, err, projectId);
    } finally {
      message.actionBusy = false;
      if (projectId === this.projectId) this.updateCard(message);
    }
  }

  async cancelAction(message) {
    if (message.actionBusy || String(message.id).startsWith('local-')) return;
    const projectId = this.projectId;
    message.actionBusy = true;
    message.actionError = null;
    this.updateCard(message);
    try {
      this.applyAction(message, await API.cancelChatAction(projectId, message.id));
    } catch (err) {
      await this.handleActionError(message, err, projectId);
    } finally {
      message.actionBusy = false;
      if (projectId === this.projectId) this.updateCard(message);
    }
  }

  async retryMessage(message) {
    if (this.streaming || !this.projectId || !this.threadId) return;
    const projectId = this.projectId;
    const previousId = message.id;
    message.content = '';
    message.error = null;
    message.interrupted = false;
    message.pending = true;
    message.tool = null;
    this.setStreaming(true);
    this.updateBubble(message);
    const controller = new AbortController();
    this.controller = controller;
    let frame = null;
    try {
      await API.retryChatMessage(projectId, this.threadId, (event, data) => {
        if (event === 'message_start') {
          message.id = data.assistant_message_id;
        } else if (event === 'token') {
          message.content += data.text;
          if (!frame) {
            frame = requestAnimationFrame(() => {
              frame = null;
              this.updateBubble(message);
              this.scrollToBottom(true);
            });
          }
        } else if (event === 'tool') {
          message.tool = data.state === 'start' ? data.name : null;
          this.updateBubble(message);
        } else if (event === 'action') {
          message.action = data.action;
          this.updateBubble(message);
        } else if (event === 'done') {
          message.pending = false;
          message.tool = null;
        } else if (event === 'error') {
          message.pending = false;
          message.interrupted = true;
          message.error = data.message;
        }
      }, controller.signal);
    } catch (err) {
      message.pending = false;
      if (err.name === 'AbortError') message.interrupted = true;
      else message.error = ERROR_TEXT[err.status] || err.message;
    } finally {
      if (frame) cancelAnimationFrame(frame);
      if (this.controller === controller) this.controller = null;
      if (message.id !== previousId) this.updateBubble(message);
      this.setStreaming(false);
      this.render();
      this.scrollToBottom();
    }
  }

  async handleActionError(message, err, projectId) {
    message.actionError = ACTION_ERROR_TEXT[err.status] || err.message;
    if (err.status === 409 && projectId === this.projectId) {
      // Expired, cancelled, or already confirmed elsewhere: show the real status.
      try {
        this.applyAction(message, await API.getChatAction(projectId, message.id));
        if (message.action.status === 'expired') message.actionError = null;
      } catch (e) {}
    }
  }

  // A status change seen by this panel. Reaching `succeeded` tells the app
  // which views to reload (AC-16).
  applyAction(message, action) {
    if (!action) return;
    const before = message.action ? message.action.status : null;
    message.action = action;
    if (action.status === 'succeeded' && before !== 'succeeded') {
      window.dispatchEvent(new CustomEvent('chat:data-changed', { detail: { views: action.affects || [] } }));
    }
    this.ensurePolling();
  }

  // AC-15: poll every 3 s while any card on screen is queued or running.
  ensurePolling() {
    if (this.pollTimer) return;
    if (!this.messages.some(m => m.action && ACTIVE_STATUSES.has(m.action.status))) return;
    this.pollTimer = setTimeout(() => this.pollActions(), ACTION_POLL_MS);
  }

  stopPolling() {
    if (this.pollTimer) clearTimeout(this.pollTimer);
    this.pollTimer = null;
  }

  async pollActions() {
    this.pollTimer = null;
    const projectId = this.projectId;
    const active = this.messages.filter(m => m.action && ACTIVE_STATUSES.has(m.action.status));
    await Promise.all(active.map(async message => {
      try {
        const action = await API.getChatAction(projectId, message.id);
        if (projectId !== this.projectId || !this.messages.includes(message)) return;
        const changed = action.status !== message.action.status;
        this.applyAction(message, action);
        if (changed) this.updateCard(message);
      } catch (err) {
        // A deleted thread or message stops being polled; other errors retry.
        if (err.status === 404) {
          message.action = { ...message.action, status: 'failed', error: 'This conversation no longer exists.' };
          this.updateCard(message);
        }
      }
    }));
    if (projectId === this.projectId) this.ensurePolling();
  }

  updateCard(message) {
    if (!this.root) return;
    const node = Array.from(this.el.messages.querySelectorAll('.chat-msg-assistant'))
      .find(el => el.dataset.id === String(message.id));
    if (!node) return;
    const old = node.querySelector('.chat-action');
    const holder = document.createElement('div');
    holder.innerHTML = message.action ? this.cardHTML(message) : '';
    const card = holder.firstElementChild;
    if (old && card) old.replaceWith(card);
    else if (old) old.remove();
    else if (card) node.appendChild(card);
  }

  assistantInner(message) {
    let html = message.content ? renderMarkdown(message.content) : '';
    if (message.pending && message.tool) {
      const label = TOOL_LABELS[message.tool] || 'project data';
      html += `<div class="chat-tool-status">Looking up ${escapeHTML(label)}…</div>`;
    } else if (message.pending && !message.content) {
      html = '<span class="chat-typing"><span></span><span></span><span></span></span>';
    }
    if (message.error) {
      html += `<div class="chat-error">${escapeHTML(message.error)}</div>`;
      if (this.projectId) html += '<button type="button" class="chat-link-btn" data-act="retry">Retry</button>';
    } else if (message.interrupted) {
      html += '<div class="chat-note">Reply interrupted.</div>';
      if (this.projectId) html += '<button type="button" class="chat-link-btn" data-act="retry">Retry</button>';
    }
    return html;
  }

  updateBubble(message) {
    const node = Array.from(this.el.messages.querySelectorAll('.chat-msg-assistant'))
      .find(el => el.dataset.id === String(message.id));
    if (!node) {
      this.render();
      return;
    }
    const holder = document.createElement('div');
    holder.innerHTML = this.bubbleHTML(message);
    node.replaceWith(holder.firstElementChild);
  }

  showError(text) {
    const div = document.createElement('div');
    div.className = 'chat-error chat-center';
    div.textContent = text;
    this.el.messages.appendChild(div);
    this.scrollToBottom();
  }

  scrollToBottom(onlyIfNear = false) {
    const box = this.el.messages;
    if (onlyIfNear && box.scrollHeight - box.scrollTop - box.clientHeight > 120) return;
    box.scrollTop = box.scrollHeight;
  }
}
