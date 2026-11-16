// Open item bookkeeping for the chat assistant (spec 0005, open item rules).
// Pure state with no DOM access, so the rules can be checked on their own.
//
// Each list view keeps its own ordered list of open rows. A row is open while
// it is expanded or in inline edit. The last entry is the most recent one.

export const ROW_VIEWS = {
  scenarios: 'scenario',
  'scenario-approval': 'scenario',
  testcases: 'test_case',
  'testcase-approval': 'test_case'
};

export class OpenRows {
  constructor() {
    this.views = new Map(); // view -> [{ id, expanded, editing }]
  }

  rows(view) {
    if (!this.views.has(view)) this.views.set(view, []);
    return this.views.get(view);
  }

  find(view, id) {
    return this.rows(view).find(row => row.id === id) || null;
  }

  // Move (or add) a row to the most recent position.
  bump(view, id) {
    const list = this.rows(view);
    let row = list.find(r => r.id === id);
    if (row) list.splice(list.indexOf(row), 1);
    else row = { id, expanded: false, editing: false };
    list.push(row);
    return row;
  }

  prune(view) {
    this.views.set(view, this.rows(view).filter(row => row.expanded || row.editing));
  }

  isExpanded(view, id) {
    const row = this.find(view, id);
    return Boolean(row && row.expanded);
  }

  // A row toggle. Returns the new expanded state.
  toggle(view, id) {
    if (this.isExpanded(view, id)) {
      this.find(view, id).expanded = false;
      this.prune(view);
      return false;
    }
    this.bump(view, id).expanded = true;
    return true;
  }

  // Entering edit makes the row the most recent one.
  startEdit(view, id) {
    const row = this.bump(view, id);
    row.editing = true;
    row.expanded = true;
  }

  // Cancel, a successful save, or a successful delete ends edit.
  endEdit(view, id) {
    const row = this.find(view, id);
    if (!row) return;
    row.editing = false;
    this.prune(view);
  }

  remove(view, id) {
    this.views.set(view, this.rows(view).filter(row => row.id !== id));
  }

  clear(view) {
    this.views.set(view, []);
  }

  clearAll() {
    this.views.clear();
  }

  // The most recently opened row that is still open, or null.
  current(view) {
    const list = this.rows(view);
    return list.length ? list[list.length - 1].id : null;
  }
}
