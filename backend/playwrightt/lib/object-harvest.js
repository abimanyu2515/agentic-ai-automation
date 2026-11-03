// Object repository extractor (spec 0004, AC-1, AC-16, AC-21).
//
// One function expression, no import, export, or globals: reconnaissance runs
// this file's text with page.evaluate, the workspace engine reads it with
// fs.readFileSync, and the Python runner embeds it in the generated spec.
// Keep it that way, and keep the engine's dangerous patterns (dynamic code
// and module loading) out of it.
//
// options: { maxElements } (optional, never above 300), or { count: locator }
// to return one locator's match count instead (the parity test uses it).
//
// Run in the page, it lists visible buttons, links, form fields, and
// navigation links in the main frame and returns, per element, the shape
// ObjectRepositoryService.ingest_recon takes, with a match count for every
// candidate locator. The counts mirror how `orx` resolves each strategy
// (Playwright getByRole, getByLabel, getByPlaceholder, getByText, getByTestId,
// CSS, XPath); the parity test in backend/tests guards that.
//
// It never reads the value of an input, select, or textarea.
(options) => {
  const opts = options || {};
  const MAX_ELEMENTS = Math.min(300, opts.maxElements || 300);
  const MAX_TEXT = 100;
  // 32 or more characters of only hex or base64 characters look like a token.
  const TOKEN = /[A-Za-z0-9+/=]{32,}/;
  const FIELD_SELECTOR =
    "input:not([type='hidden']):not([type='submit']):not([type='button']):not([type='image']), select, textarea";
  const TEXT_INPUT_TYPES = ['', 'text', 'email', 'password', 'search', 'number', 'tel', 'url', 'date'];
  const GROUPS = [
    ['nav', "nav a[href], [role='navigation'] a[href]"],
    ['button', "button, input[type='button'], input[type='submit'], [role='button']"],
    ['link', 'a[href]'],
    ['field', FIELD_SELECTOR],
  ];

  const normalize = (text) => String(text || '').replace(/\s+/g, ' ').trim();
  const cut = (text) => normalize(text).slice(0, MAX_TEXT);
  const attr = (el, name) => el.getAttribute(name) || '';
  const lower = (text) => normalize(text).toLowerCase();
  const textMatches = (actual, expected, exact) =>
    exact ? normalize(actual) === normalize(expected) : lower(actual).includes(lower(expected));

  // ---------------------------------------------------------- visibility

  // Playwright's isVisible: a non empty box and visibility: visible.
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    if (style.visibility !== 'visible') return false;
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  };

  // Hidden from the accessibility tree, which getByRole leaves out.
  const hiddenSubtree = new Map();
  const inHiddenSubtree = (node) => {
    if (!node || node.nodeType !== 1) return false;
    if (!hiddenSubtree.has(node)) {
      hiddenSubtree.set(
        node,
        node.getAttribute('aria-hidden') === 'true' ||
          window.getComputedStyle(node).display === 'none' ||
          inHiddenSubtree(node.parentElement)
      );
    }
    return hiddenSubtree.get(node);
  };
  const hiddenForAria = (el) => {
    if (inHiddenSubtree(el)) return true;
    const visibility = window.getComputedStyle(el).visibility;
    return visibility === 'hidden' || visibility === 'collapse';
  };

  // ----------------------------------------------------------- roles

  const implicitRole = (el) => {
    const tag = el.tagName.toLowerCase();
    const type = attr(el, 'type').toLowerCase();
    if ((tag === 'a' || tag === 'area') && el.hasAttribute('href')) return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'input') {
      if (['button', 'submit', 'reset', 'image'].includes(type)) return 'button';
      if (type === 'checkbox' || type === 'radio') return type;
      if (type === 'range') return 'slider';
      if (type === 'number') return 'spinbutton';
      if (el.hasAttribute('list')) return 'combobox';
      if (type === 'search') return 'searchbox';
      // Playwright counts password fields as textboxes.
      if (['', 'text', 'email', 'tel', 'url', 'password'].includes(type)) return 'textbox';
      return '';
    }
    if (tag === 'select') return el.multiple || el.size > 1 ? 'listbox' : 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'nav') return 'navigation';
    if (/^h[1-6]$/.test(tag)) return 'heading';
    if (tag === 'img') return attr(el, 'alt') === '' && el.hasAttribute('alt') ? 'presentation' : 'img';
    if (tag === 'option') return 'option';
    if (tag === 'ul' || tag === 'ol') return 'list';
    if (tag === 'li') return 'listitem';
    if (tag === 'dialog') return 'dialog';
    if (tag === 'main') return 'main';
    return '';
  };

  const roleOf = (el) => {
    const explicit = attr(el, 'role').trim().split(/\s+/)[0];
    return explicit || implicitRole(el);
  };

  const NAME_FROM_CONTENT = new Set([
    'button', 'link', 'checkbox', 'radio', 'heading', 'option', 'tab', 'menuitem',
    'menuitemcheckbox', 'menuitemradio', 'treeitem', 'cell', 'columnheader', 'rowheader',
    'switch', 'tooltip', 'gridcell',
  ]);

  const labelledByText = (el) => {
    const ids = attr(el, 'aria-labelledby').trim();
    if (!ids) return null;
    const parts = ids.split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean);
    return parts.length ? normalize(parts.map((part) => part.textContent).join(' ')) : null;
  };

  const labelsText = (el) =>
    normalize(Array.from(el.labels || []).map((label) => label.textContent).join(' '));

  const nameCache = new Map();
  const accessibleName = (el) => {
    if (nameCache.has(el)) return nameCache.get(el);
    const tag = el.tagName.toLowerCase();
    const type = attr(el, 'type').toLowerCase();
    let name = labelledByText(el);
    if (!name) name = normalize(attr(el, 'aria-label'));
    if (!name && (tag === 'input' || tag === 'select' || tag === 'textarea')) {
      if (tag === 'input' && ['button', 'submit', 'reset'].includes(type)) {
        name = normalize(attr(el, 'value')) || (type === 'submit' ? 'Submit' : type === 'reset' ? 'Reset' : '');
      } else if (tag === 'input' && type === 'image') {
        name = normalize(attr(el, 'alt')) || 'Submit';
      } else {
        name = labelsText(el);
      }
    }
    if (!name && tag === 'img') name = normalize(attr(el, 'alt'));
    if (!name && NAME_FROM_CONTENT.has(roleOf(el))) name = normalize(el.textContent);
    if (!name) name = normalize(attr(el, 'title'));
    if (!name && (tag === 'input' || tag === 'textarea')) name = normalize(attr(el, 'placeholder'));
    nameCache.set(el, name);
    return name;
  };

  // --------------------------------------- strategy to match rule table

  const all = () => Array.from(document.querySelectorAll('*'));
  let roleIndex = null;
  const byRole = (role) => {
    if (!roleIndex) {
      roleIndex = new Map();
      for (const el of all()) {
        const elRole = roleOf(el);
        if (!elRole || hiddenForAria(el)) continue;
        if (!roleIndex.has(elRole)) roleIndex.set(elRole, []);
        roleIndex.get(elRole).push(el);
      }
    }
    return roleIndex.get(role) || [];
  };

  let labelIndex = null;
  const labelled = () => {
    if (!labelIndex) {
      labelIndex = [];
      for (const el of all()) {
        const texts = [labelledByText(el), normalize(attr(el, 'aria-label')), el.labels ? labelsText(el) : '']
          .filter(Boolean);
        if (texts.length) labelIndex.push(texts);
      }
    }
    return labelIndex;
  };

  const SKIP_TEXT = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'HEAD', 'TEMPLATE']);
  const countText = (value, exact) => {
    let count = 0;
    for (const el of all()) {
      if (SKIP_TEXT.has(el.tagName) || !textMatches(el.textContent, value, exact)) continue;
      // The deepest element with the text, as getByText returns.
      const childMatches = Array.from(el.children).some(
        (child) => !SKIP_TEXT.has(child.tagName) && textMatches(child.textContent, value, exact)
      );
      if (!childMatches) count++;
    }
    return count;
  };

  const MATCHERS = {
    role: (value, options) =>
      byRole(value).filter((el) => options.name === undefined || textMatches(accessibleName(el), options.name, options.exact))
        .length,
    label: (value, options) => labelled().filter((texts) => texts.some((text) => textMatches(text, value, options.exact))).length,
    placeholder: (value) =>
      Array.from(document.querySelectorAll('[placeholder]')).filter((el) => textMatches(attr(el, 'placeholder'), value, false))
        .length,
    text: (value, options) => countText(value, options.exact),
    test_id: (value) => Array.from(document.querySelectorAll('[data-testid]')).filter((el) => attr(el, 'data-testid') === value).length,
    css: (value) => document.querySelectorAll(value).length,
    xpath: (value) => document.evaluate(value, document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null).snapshotLength,
  };

  const countMatches = (strategy, value, options) => {
    try {
      return MATCHERS[strategy] ? MATCHERS[strategy](value, options || {}) : 0;
    } catch (e) {
      return 0;
    }
  };

  if (opts.count) return countMatches(opts.count.strategy, opts.count.value, opts.count.options);

  // ------------------------------------------------------ collection

  const cssString = (value) => JSON.stringify(value);

  // The reconnaissance selector: data-testid, id, name, aria-label,
  // placeholder, then a structural path.
  const bestSelector = (el) => {
    if (attr(el, 'data-testid')) return `[data-testid=${cssString(attr(el, 'data-testid'))}]`;
    if (el.id) return `#${CSS.escape(el.id)}`;
    if (attr(el, 'name')) return `[name=${cssString(attr(el, 'name'))}]`;
    if (attr(el, 'aria-label')) return `[aria-label=${cssString(attr(el, 'aria-label'))}]`;
    if (attr(el, 'placeholder')) return `[placeholder=${cssString(attr(el, 'placeholder'))}]`;
    const parts = [];
    for (let node = el; node && node.nodeType === 1 && node.tagName !== 'HTML'; node = node.parentElement) {
      let index = 1;
      for (let sibling = node.previousElementSibling; sibling; sibling = sibling.previousElementSibling) {
        if (sibling.tagName === node.tagName) index++;
      }
      parts.unshift(`${node.tagName.toLowerCase()}:nth-of-type(${index})`);
    }
    return parts.join(' > ');
  };

  const describe = (el, kind) => {
    const tag = el.tagName.toLowerCase();
    const type = attr(el, 'type').toLowerCase();
    const aria = normalize(attr(el, 'aria-label'));
    const labels = labelsText(el);
    // Button text: its rendered text, or the value attribute of an input
    // button (an attribute, never what someone typed).
    const text = tag === 'input' ? normalize(attr(el, 'value')) : normalize(el.innerText);
    let role;
    let elementType;
    let name;
    if (kind === 'nav' || kind === 'link') {
      [role, elementType, name] = ['link', kind, aria || text];
    } else if (kind === 'button') {
      [role, elementType, name] = [attr(el, 'role') || 'button', 'button', aria || text];
    } else {
      if (tag === 'select') [role, elementType] = ['combobox', 'select'];
      else if (tag === 'textarea') [role, elementType] = ['textbox', 'textarea'];
      else if (type === 'checkbox' || type === 'radio') [role, elementType] = [type, type];
      else if (TEXT_INPUT_TYPES.includes(type)) [role, elementType] = ['textbox', 'input'];
      else return null;
      name = labels || aria;
    }
    name = cut(name);
    const label = kind === 'field' ? cut(labels || aria) : '';
    const placeholder = cut(attr(el, 'placeholder'));
    const attributes = {
      'data-testid': cut(attr(el, 'data-testid')),
      id: cut(el.id),
      name: cut(attr(el, 'name')),
      tag: tag.slice(0, MAX_TEXT),
      type: type.slice(0, MAX_TEXT),
    };
    for (const key of ['data-testid', 'id', 'name']) {
      if (TOKEN.test(attributes[key])) attributes[key] = '';
    }

    const candidates = [];
    if (attributes['data-testid']) candidates.push({ strategy: 'test_id', value: attr(el, 'data-testid'), options: {} });
    if (name) candidates.push({ strategy: 'role', value: role, options: { name } });
    if (label) candidates.push({ strategy: 'label', value: label, options: {} });
    if (placeholder) candidates.push({ strategy: 'placeholder', value: placeholder, options: {} });
    if (el.id && attributes.id === el.id) candidates.push({ strategy: 'css', value: `#${CSS.escape(el.id)}`, options: {} });
    else if (attributes.name && attributes.name === attr(el, 'name'))
      candidates.push({ strategy: 'css', value: `[name=${cssString(attr(el, 'name'))}]`, options: {} });
    const selector = bestSelector(el);
    if (selector) {
      const isXpath = selector.startsWith('/') || selector.startsWith('xpath=');
      candidates.push({ strategy: isXpath ? 'xpath' : 'css', value: selector.replace(/^xpath=/, ''), options: {} });
    }
    const locators = candidates
      .filter((c) => !TOKEN.test(c.value) && !TOKEN.test(c.options.name || ''))
      .map((c) => ({ ...c, match_count: countMatches(c.strategy, c.value, c.options) }));

    return { element_type: elementType, role, label: name, placeholder, attributes, locators };
  };

  const isNavLink = (el) => Boolean(el.closest("nav, [role='navigation']"));
  const seen = new Set();
  const elements = [];
  for (const [kind, selector] of GROUPS) {
    for (const el of Array.from(document.querySelectorAll(selector))) {
      if (elements.length >= MAX_ELEMENTS) break;
      if (seen.has(el) || (kind === 'link' && isNavLink(el))) continue;
      seen.add(el);
      try {
        if (!isVisible(el)) continue;
        const item = describe(el, kind);
        if (item) elements.push(item);
      } catch (e) {
        // One unreadable element never fails the snapshot.
      }
    }
  }
  return { url: location.pathname || '/', elements };
}
