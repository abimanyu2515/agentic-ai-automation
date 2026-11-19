// Sidebar open/close toggle. The collapsed state is a per-browser convenience kept in
// localStorage; it never affects which views or data are available.
const STORAGE_KEY = 'sidebar_collapsed';

function readCollapsed() {
  try { return localStorage.getItem(STORAGE_KEY) === '1'; } catch { return false; }
}

function saveCollapsed(collapsed) {
  try { localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0'); } catch { /* storage unavailable */ }
}

export function initSidebarToggle() {
  const button = document.getElementById('sidebar-toggle');
  const sidebar = document.getElementById('sidebar-container');
  if (!button || !sidebar) return;

  const apply = (collapsed) => {
    document.body.classList.toggle('sidebar-collapsed', collapsed);
    sidebar.setAttribute('aria-hidden', String(collapsed));
    sidebar.inert = collapsed;
    button.setAttribute('aria-expanded', String(!collapsed));
    const label = collapsed ? 'Open sidebar (Ctrl+B)' : 'Close sidebar (Ctrl+B)';
    button.setAttribute('aria-label', label);
    button.title = label;
  };

  const toggle = () => {
    const collapsed = !document.body.classList.contains('sidebar-collapsed');
    apply(collapsed);
    saveCollapsed(collapsed);
  };

  apply(readCollapsed());
  button.addEventListener('click', toggle);

  document.addEventListener('keydown', (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || e.key.toLowerCase() !== 'b') return;
    const t = e.target;
    if (t instanceof HTMLElement && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    if (sidebar.style.display === 'none') return; // signed out
    e.preventDefault();
    toggle();
  });
}
