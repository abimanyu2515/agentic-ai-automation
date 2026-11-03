# 0004. Object repository: rationale

Build spec: [index.md](index.md).

## Context

Generated Playwright scripts get their locators (rules for finding an element on a page) from the model on every generation. The model sees reconnaissance evidence or DOM context, but it still retypes or invents selectors per test case. When the application under test changes, every script that used the old selector breaks, and the only fix is to generate each one again. Nothing records which locators are decaying.

The codebase already has pieces nearby but no shared store. Reconnaissance (`ApplicationReconnaissanceAgent`) captures roles, labels, placeholders, and one "best selector" per element, but only as throwaway evidence for one generation. `LocatorResolver` builds fallback chains as code, but nothing calls it. The imported framework path indexes POM class and method names, not locators.

Forces that shaped this decision:
- Persistence must go through the repository layer, with the JSON store as the default and PostgreSQL optional (`AGENTS.md`). The engineer asked for locators in their own PostgreSQL table.
- There are two execution paths: the Python runner, which streams stdout, and the Next.js engine, which strips imports, runs plain JS through `new Function`, and reports through webhooks. The engine only captures browser console output, and its `addLog` webhooks are fire and forget.
- Reconnaissance only runs for projects marked as safe test environments.
- Selenium scripts are generated but can't be executed.

## Options considered

### Option 1: Model writes the full locator chains
The prompt lists stored elements with every locator, and the model copies the chains into each step.

**Pros**: no backend rendering. Scripts are complete on their own.
**Cons**: long, error prone output. Locators are frozen at generation, so a fix needs regeneration. The model can still drift from what is stored.

### Option 2: Named keys, locators injected at read and dispatch time (chosen)
The model writes `orx(page, 'page.element')`. The backend inserts a fixed helper and the current locators whenever the script is read or run.

**Pros**: one fix reaches every script. Short, checkable output (unknown keys are easy to detect). The runner needs no new concepts.
**Cons**: stored scripts depend on rendering. Every read and dispatch path must call the renderer. Renames have to rewrite stored scripts.

### Option 3: Generated locator module imported by scripts
Write an `elements.ts` file per execution and have scripts import elements from it.

**Pros**: closest to the classic POM style. Scripts stay readable.
**Cons**: the engine strips imports and runs one script string, so it would need artifact layout and loader changes. It doesn't work with the current engine.

### Option 4: Runtime lookup through the API
The helper fetches locators from the backend while the script runs.

**Pros**: always current, even mid run.
**Cons**: adds a network call and an auth token to every run, and a backend outage fails every test.

## Rationale

Option 2 is the only one that meets the core job the engineer chose (fix a locator once, and every script benefits) while working with how the engine actually runs scripts (one plain JS string with imports stripped). Option 1 freezes locators at generation. Option 3 fights the engine's single string model. Option 4 makes every run depend on the backend being reachable. Rendering at read and dispatch time costs some discipline (one `render_script` and one `strip_block`, called at a known, short list of points), and that is cheaper than either regeneration or engine changes.

Feedback rides on log markers because both paths already move log lines. The Python path gets them through `on_log_callback`. For the workspace path, the only change is attaching the existing `artifacts.logs` to the `completed` payload, since the separate `addLog` webhooks are not awaited and can arrive after completion.

Locators get their own table, per the engineer, which also enables per locator counters. That lets testers see which backup actually works before promoting it. Soft delete goes against the general advice to avoid it, but it matches every other entity in this codebase and keeps deleted fingerprints so reconnaissance doesn't recreate elements a tester removed on purpose. Partial unique indexes handle the uniqueness problem soft delete usually causes.

Choices made while writing, each with the runner up:
- **Glob matcher**: a small custom matcher where `*` is one path segment and `**` is any number of segments. Runner up: `fnmatch`, rejected because its `*` crosses `/`, so `/booking/*` would also match `/booking/1/edit`.
- **Fingerprint**: a hash of role, type, accessible name, and first stable attribute. Runner up: matching by element name, rejected because names change when labels change.
- **Rename**: rewrite references in the same transaction. Runner up: blocking renames of referenced elements, rejected because it makes cleanup painful.
- **Tester delete permission**: left out, matching how Tester is set up for every other module. Runner up: giving Tester all four actions.
- **Helper name**: `orx` (object repository locate), short enough that the model uses it consistently. Runner up: `objRepo`.

## Update 2026-11-03: harvest elements during test execution

### Context

After the first build, a Scan now on a real target (saucedemo) filled only the login page. Reconnaissance never logs in and never submits forms, so every page behind the login stays empty unless a tester types its elements by hand. A literal pattern such as `/inventory.html` doesn't help: a logged out visit lands back on the login page. Test runs, by contrast, already log in with the vault credentials and walk the real flow through inventory, cart, and checkout. The question is how to get those pages into the repository without weakening the rule that inspection only reads.

### Options considered

**Option A: harvest during test execution (chosen).** Both runners take one snapshot per page a run visits, using reconnaissance's extractor, and the backend ingests it with the scan rules minus marking missing.
- Pros: reaches every page a test reaches, with no new login logic; follows real user flows and real states; reuses `ingest_recon`, fingerprints, and the tester wins rule.
- Cons: two runners to wire; adds time on each new page; captures mid flow states that a run can never clean up; page text passes through the runner, so redaction must be strict.

**Option B: let reconnaissance log in with vault credentials.** A scan fills a login form first, then visits inner pages.
- Pros: one code path (reconnaissance); scans can mark missing on inner pages too.
- Cons: every target needs its login located and submitted generically, which is fragile across sites; it breaks the "inspection never submits forms" rule; scans still don't know how to reach pages that need a cart item or a prior step.

**Option C: manual entry only for pages behind login.** Keep today's behavior.
- Pros: no new code, no new data paths.
- Cons: the repository stays empty exactly where locators break most, and keeping it current is all manual work.

### Rationale

Runs are the only part of the system that already reaches inner pages in the right state, so collecting from them is the cheapest way to cover them (Option A). Option B would turn reconnaissance into a second, weaker test runner. The engineer chose to harvest on every project, not only safe test environments: harvesting only reads, and the runs already happen regardless of the flag. A global `harvest_enabled` switch stays as the rollback lever.

Choices made while writing, each with the runner up:
- **Never mark missing from a run**: a run sees pages mid flow and often in a narrower state, so absence proves nothing. Runner up: mark missing only when the same page was seen fully loaded, rejected as hard to define across targets.
- **Shared extractor file**: keeps fingerprints identical between scans and runs, so they merge. Runner up: separate Python and JS copies, rejected because any drift creates duplicate elements.
- **Settled after 1000 ms with no navigation, once per path**: cheap, works for client side routing, and bounds the cost. Runner up: snapshot after every action, rejected as too slow and noisy.
- **Transport**: a payload field for the workspace (it already posts `completed`), and a `harvest.json` file in the Python run directory (the runner already writes its report there). Runner up: stdout lines, rejected after the cross check because large JSON on one line can be truncated or interleaved and batch attribution would depend on which item looks "running".
- **Counting in the page**: one `evaluate` call per snapshot computes every match count. Runner up: a Playwright `count()` per locator like reconnaissance, rejected because the Python runner's `slowMo: 1000` makes each call take a second.
- **Backend dedupe and cap**: the backend keeps the first snapshot per path and enforces the page cap, because Playwright restarts a worker after a failed test and resets the runner's in memory set.
- **Redact twice**: the runner knows the credentials, and the backend knows the test data. Runner up: backend only, rejected because credentials would then travel in the payload.
- **No audit entry per harvest**: a system write at run frequency would flood the audit log; reconnaissance during generation writes none either. Runner up: one summary entry per execution.
