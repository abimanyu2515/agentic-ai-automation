# TestDataAgent follow-up fix

## Findings from the latest screenshots

The screenshots show three distinct conditions:

1. Generating test cases for the 20-row TestDataAgent run used one large structured LLM response. The configured model timed out, and larger responses also sometimes encoded test-case objects as JSON strings (or nested JSON strings), which did not validate as objects.
2. A separate test-case generation request still received entries as strings instead of objects. This is an LLM output-shape issue; it is separate from whether the generated data rows themselves are valid.
3. A selected project using a framework other than Playwright is rejected by the automatic TestDataAgent route. This is an existing guard: that route launches the Playwright execution workspace, so the guard remains to avoid generating tests that the selected execution path cannot run.

The latest timeout screenshot shows the plain `LLMService.generate()` error without the structured-response validation/retry message. This locates the timeout in Playwright script generation, not in the deterministic TestDataAgent row generation.

## Changes

- Split test-case generation into batches of at most five scenarios, reducing the size and latency of each structured response.
- Retry each batch at most once after the initial attempt, with a 30-second per-call timeout.
- Decode up to three levels of JSON-string encoding for test-case entries before schema validation.
- Ensure all requested scenario numbers occur exactly once across each batch.
- Increase the Playwright script-generation LLM timeout to 120 seconds; this provider/model can exceed the previous 30-second default.
- Added regression coverage for double-encoded case entries and multi-batch scenario correlation.
- Added a regression test confirming Playwright script generation passes the extended timeout.

## Validation

The focused test run passed: **15 passed, 1 warning**.

```powershell
.\.venv\Scripts\python.exe -m pytest -q backend\tests\test_playwright_agent.py backend\tests\test_test_case_agent.py backend\tests\test_test_data_workflow.py
```
