"""
Render and strip the injected object repository block (spec 0004, AC-8, AC-9).

Stored scripts hold element keys only, for example
`await orx(page, 'login_page.login_button')`. Before a script is shown or
run, `render_script` inserts a fixed helper plus the current locators of the
referenced keys right after the import lines. `strip_block` removes it again
before a script is saved, so render then strip returns the original script.

The JavaScript block runs inside the execution engine's `new Function`
wrapper, so it must never contain `Function(`, `eval(`, or `require(`.
Locator values are written as JSON with every `(` escaped as `\\u0028`, so
page text can't trip the engine's danger check.
"""

from __future__ import annotations

import json
import re

JS_START = "// <object-repository> generated, do not edit"
JS_END = "// </object-repository>"
PY_START = "# <object-repository> generated, do not edit"
PY_END = "# </object-repository>"
_START_MARKERS = ("// <object-repository>", "# <object-repository>")
_END_MARKERS = (JS_END, PY_END)

# A quoted element key passed to orx, for example orx(page, 'login.submit').
KEY_REFERENCE = re.compile(r"""orx\(\s*\w+\s*,\s*['"]([a-z0-9_]+\.[a-z0-9_]+)['"]""")
# Any orx call, to catch keys built at run time (template strings, variables).
_ANY_REFERENCE = re.compile(r"""\borx\(\s*\w+\s*,\s*([^)\n]*)\)""")
_PLAIN_KEY_ARGUMENT = re.compile(r"""^\s*(['"])[a-z0-9_]+\.[a-z0-9_]+\1\s*$""")

# A whole JavaScript import statement, including ones split over several lines.
_JS_IMPORT = re.compile(r"""^[ \t]*import\b[^;'"]*?(['"])[^'"]+\1[ \t]*;?[ \t]*$""", re.MULTILINE)

_JS_HELPER = """async function orx(page, key) {
  // @ts-ignore addLog exists only inside the execution engine wrapper
  const log = (m) => (typeof addLog === 'function' ? addLog(m) : console.log(m));
  const chain = __OBJREPO[key];
  if (!chain) { log('[OBJREPO] ' + JSON.stringify({ key, index: -1, tried: 0, ok: false })); throw new Error('Object repository element ' + key + ' is not in the repository'); }
  for (let i = 0; i < chain.length; i++) {
    const { s, v, o } = chain[i];
    const loc = (s === 'role' ? page.getByRole(v, o) : s === 'label' ? page.getByLabel(v, o)
      : s === 'placeholder' ? page.getByPlaceholder(v) : s === 'text' ? page.getByText(v, o)
      : s === 'test_id' ? page.getByTestId(v) : s === 'xpath' ? page.locator('xpath=' + v) : page.locator(v)).first();
    try { await loc.waitFor({ state: 'visible', timeout: __OBJREPO_TIMEOUT }); log('[OBJREPO] ' + JSON.stringify({ key, index: i, tried: i + 1, ok: true })); return loc; } catch (e) {}
  }
  log('[OBJREPO] ' + JSON.stringify({ key, index: chain.length, tried: chain.length, ok: false }));
  throw new Error('Object repository element ' + key + ' was not found by any locator');
}"""

_PY_HELPER = """def orx(driver, key):
    from selenium.webdriver.support import expected_conditions as __orx_ec
    from selenium.webdriver.support.ui import WebDriverWait as __OrxWait
    chain = __OBJREPO.get(key)
    if not chain:
        raise LookupError("Object repository element " + key + " is not in the repository")
    for by, selector in chain:
        try:
            return __OrxWait(driver, __OBJREPO_TIMEOUT).until(
                __orx_ec.visibility_of_element_located((by, selector))
            )
        except Exception:
            pass
    raise LookupError("Object repository element " + key + " was not found by any locator")"""

# Native tag for a role when translating role locators to Selenium XPath.
_ROLE_TAGS = {
    "button": "button",
    "link": "a",
    "textbox": "input",
    "combobox": "select",
    "checkbox": "input",
    "radio": "input",
}


def is_python_script(script: str) -> bool:
    return (
        "from selenium" in script
        or "import pytest" in script
        or "webdriver." in script
    )


def is_gherkin_script(script: str) -> bool:
    return "Feature:" in script and "Scenario" in script


def find_references(script: str) -> tuple[list[str], list[str]]:
    """Return (quoted keys, dynamic references) used by orx calls, in order.

    Dynamic references are keys built at run time, such as template strings;
    they are not supported and are reported as unknown keys.
    """
    body = strip_block(script)
    keys = list(dict.fromkeys(KEY_REFERENCE.findall(body)))
    dynamic = []
    for match in _ANY_REFERENCE.finditer(body):
        argument = match.group(1).strip()
        if not _PLAIN_KEY_ARGUMENT.match(argument):
            dynamic.append(argument[:100])
    return keys, list(dict.fromkeys(dynamic))


def _escape_json(value) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
        .replace("(", "\\u0028")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


def _js_chain(locators: list[dict]) -> list[dict]:
    chain = []
    for locator in locators:
        entry = {"s": locator["strategy"], "v": locator["value"]}
        if locator.get("options"):
            entry["o"] = locator["options"]
        chain.append(entry)
    return chain


def _xpath_literal(value: str) -> str:
    if '"' not in value:
        return f'"{value}"'
    if "'" not in value:
        return f"'{value}'"
    parts = value.split('"')
    return "concat(" + ", '\"', ".join(f'"{part}"' for part in parts) + ")"


def selenium_selector(locator: dict) -> tuple[str, str]:
    """Translate a stored locator into a Selenium (By, selector) pair."""
    strategy = locator["strategy"]
    value = locator["value"]
    options = locator.get("options") or {}
    if strategy == "css":
        return "css selector", value
    if strategy == "xpath":
        return "xpath", value
    if strategy == "test_id":
        return "css selector", f"[data-testid={json.dumps(value)}]"
    if strategy == "placeholder":
        return "css selector", f"[placeholder={json.dumps(value)}]"
    if strategy == "text":
        return "xpath", f"//*[normalize-space()={_xpath_literal(value)}]"
    if strategy == "label":
        return (
            "xpath",
            f"//label[normalize-space()={_xpath_literal(value)}]"
            "/following::*[self::input or self::select or self::textarea][1]",
        )
    # role
    tag = _ROLE_TAGS.get(value)
    role_test = f"(@role={_xpath_literal(value)} or self::{tag})" if tag else f"@role={_xpath_literal(value)}"
    name = options.get("name")
    if name:
        literal = _xpath_literal(str(name))
        return "xpath", f"//*[{role_test} and (normalize-space()={literal} or @aria-label={literal})]"
    return "xpath", f"//*[{role_test}]"


def _js_insert_index(lines: list[str], script: str) -> int:
    last_end = None
    for match in _JS_IMPORT.finditer(script):
        last_end = match.end()
    if last_end is None:
        return 0
    return script[:last_end].count("\n") + 1


def _python_insert_index(lines: list[str]) -> int:
    index = 0
    open_paren = False
    for position, line in enumerate(lines):
        stripped = line.strip()
        if open_paren:
            if ")" in stripped:
                open_paren = False
                index = position + 1
            continue
        if stripped.startswith("import ") or (stripped.startswith("from ") and " import " in stripped):
            index = position + 1
            if "(" in stripped and ")" not in stripped:
                open_paren = True
            continue
        if stripped and not stripped.startswith("#") and index:
            break
    return index


def render_script(script: str, locators_by_key: dict[str, list[dict]], timeout_ms: int) -> str:
    """Insert the helper and the locators of every referenced key.

    `locators_by_key` holds the ordered locators of known keys only; a key
    missing from it makes orx fail at run time with a failure marker.
    """
    if not script:
        return script
    base = strip_block(script)
    if is_gherkin_script(base):
        return base
    keys, _ = find_references(base)
    if not keys:
        return base
    lines = base.split("\n")
    if is_python_script(base):
        chains = {
            key: [list(selenium_selector(locator)) for locator in locators_by_key[key]]
            for key in keys if key in locators_by_key
        }
        block = [
            PY_START,
            f"__OBJREPO = __import__('json').loads({json.dumps(_escape_json(chains))})",
            f"__OBJREPO_TIMEOUT = {timeout_ms / 1000:g}",
            *_PY_HELPER.split("\n"),
            PY_END,
        ]
        index = _python_insert_index(lines)
    else:
        chains = {key: _js_chain(locators_by_key[key]) for key in keys if key in locators_by_key}
        block = [
            JS_START,
            f"const __OBJREPO = {_escape_json(chains)};",
            f"const __OBJREPO_TIMEOUT = {int(timeout_ms)};",
            *_JS_HELPER.split("\n"),
            JS_END,
        ]
        index = _js_insert_index(lines, base)
    return "\n".join(lines[:index] + block + lines[index:])


def _helper_end(lines: list[str], start: int) -> int:
    """Index just past the helper definition when the end marker is missing."""
    for position in range(start, len(lines)):
        line = lines[position]
        if line.startswith("async function orx("):
            for end in range(position + 1, len(lines)):
                if lines[end] == "}":
                    return end + 1
            return len(lines)
        if line.startswith("def orx("):
            for end in range(position + 1, len(lines)):
                if lines[end].strip() and not lines[end].startswith((" ", "\t")):
                    return end
            return len(lines)
    # No helper at all: drop the start marker and the generated constant lines.
    end = start + 1
    while end < len(lines) and lines[end].lstrip().startswith(("const __OBJREPO", "__OBJREPO")):
        end += 1
    return end


def strip_block(script: str | None) -> str | None:
    """Remove every injected block (doubled or hand edited blocks included)."""
    if not script:
        return script
    lines = script.split("\n")
    start = next(
        (i for i, line in enumerate(lines) if line.strip().startswith(_START_MARKERS)),
        None,
    )
    if start is None:
        return script
    end = None
    for i in range(len(lines) - 1, start, -1):
        if lines[i].strip() in _END_MARKERS:
            end = i + 1
            break
    if end is None:
        end = _helper_end(lines, start)
    stripped = "\n".join(lines[:start] + lines[end:])
    return strip_block(stripped) if stripped != script else stripped
