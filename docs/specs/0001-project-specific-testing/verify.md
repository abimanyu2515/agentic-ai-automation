# Verify: Project specific testing, spec 0001

## Focused fixture update

1. [x] Run `pytest -q backend/tests/test_api.py backend/tests/test_playwright_agent.py`.
   Expect all focused tests to pass. This checks the required project creation fields and explicit Playwright project context.
   Covers AC 1, AC 3, AC 4, and AC 5.

2. [x] Run `git diff --check`.
   Expect no whitespace errors.

3. [x] Run `python -m compileall -q backend`.
   Expect successful backend compilation.

## Project configuration contract

4. [x] Submit a project creation request without `framework` or `target_url`.
   Expect HTTP 422 and no project creation.
   Covers AC 1.

5. [x] Create a project with `framework: playwright` and a valid project URL, then read it.
   Expect `readiness.is_ready` to be true and the normalized URL to be returned.
   Covers AC 2.

6. [ ] Run Playwright generation without explicit project context.
   Expect an explicit validation error, not selection of the first project or a global URL.
   Covers AC 3 and AC 4.

7. [x] Create two projects with equivalent normalized URLs.
   Expect the second creation to return the project URL conflict response.
   Covers AC 7.
