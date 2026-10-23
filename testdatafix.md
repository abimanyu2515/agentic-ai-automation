# TestDataAgent execution fix

## Root cause

The LLM may return entries in `test_cases` as JSON-encoded strings instead of JSON objects. The shared `LLMService` parser assumed every entry was a dictionary and called `.get()` on it, which raised `AttributeError: 'str' object has no attribute 'get'`. This caused all response retries to fail and the TestDataAgent endpoint to report an error.

## Fix

- Decode stringified test-case entries when they contain a JSON object.
- Only normalize fields on dictionary entries; leave other shapes for Pydantic schema validation to reject with a structured validation error rather than an `AttributeError`.
- Guard scenario normalization against non-object entries as well.
- Add a regression test for a response containing both ordinary objects and a stringified test case.

## Validation

The focused regression suite passed:

```powershell
6 passed, 1 warning in 0.30s
```
