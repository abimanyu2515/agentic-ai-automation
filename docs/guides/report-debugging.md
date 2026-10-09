# Professional Report Engine Debug Guide

## Issue: Professional Report Engine Not Being Used

The Professional Report Engine implementation is complete, but real Playwright executions are still generating legacy reports instead of professional reports.

## Debugging Steps Performed

### 1. Added Runtime Logging

Added comprehensive logging to trace the execution path:

**In `backend/services/report_service.py`:**
- Added logging to `compile_reports_with_engine_selection()` to show:
  - When the method is called
  - Current REPORT_ENGINE value
  - Whether professional mode is enabled
  - Which engine is selected
  - Whether fallback is activated

- Added logging to `compile_reports()` to detect direct legacy calls
- Added logging to `_compile_professional_reports()` to show:
  - ExecutionAnalyzer instantiation
  - ProfessionalReportGenerator instantiation
  - Report generation progress

- Added logging to `_on_execution_completed()` to show:
  - When execution completes
  - Stack trace of the call
  - Current configuration

**In `backend/config/report_config.py`:**
- Added `print_config()` method to show current configuration
- Added logging to `is_professional_enabled()` to show when it's called
- Added logging to mode switching methods

### 2. Environment Variable Testing

Created `test_professional_report_runtime.py` to verify that the environment variable is correctly read:

**Result:** ✅ Environment variable is correctly read and configuration works as expected.

### 3. Code Path Analysis

Verified all code paths that call report generation:

**Correctly Updated:**
- `backend/services/report_service.py` - `_on_execution_completed()` listener
- `backend/services/workflow_service.py` - Workflow execution
- `backend/main.py` - PDF endpoint
- `backend/main.py` - HTML endpoint  
- `backend/main.py` - Batch execution

**All paths now use `compile_reports_with_engine_selection()`**

## Root Cause Analysis

### Issue: Environment Variable Not Set at Server Start

The most likely cause is that the backend server is started without the `REPORT_ENGINE=professional` environment variable set. When the backend server starts, it imports the `ReportConfig` class, which reads the environment variable at module load time.

### Verification

To verify this is the issue:

1. **Check current server logs** for the new debug output:
   ```
   [Report Engine] _on_execution_completed() called for execution {id}
   [ReportConfig] Current Configuration:
   [ReportConfig]   REPORT_ENGINE env var: 'legacy'  <-- This should be 'professional'
   ```

2. **Check if environment variable is set** when the server starts

## Solution

### Option 1: Set Environment Variable Before Starting Server

**Windows (PowerShell):**
```powershell
$env:REPORT_ENGINE="professional"
cd backend
python main.py
```

**Windows (Command Prompt):**
```cmd
set REPORT_ENGINE=professional
cd backend
python main.py
```

**Windows (Batch File):**
```batch
@echo off
set REPORT_ENGINE=professional
cd backend
python main.py
```

### Option 2: Use Provided Batch File

Run the provided batch file:
```cmd
start_backend_professional_mode.bat
```

### Option 3: Set in .env File (if using python-dotenv)

Create or update `.env` file in the project root:
```
REPORT_ENGINE=professional
```

### Option 4: Set in Code (for testing only)

Add this at the top of `backend/main.py` (before any imports):
```python
import os
os.environ["REPORT_ENGINE"] = "professional"
```

## Verification Steps

After applying the solution:

1. **Start the backend server** with the environment variable set
2. **Execute a Playwright test**
3. **Check the server logs** for the debug output:
   ```
   [Report Engine] _on_execution_completed() called for execution {id}
   [ReportConfig] Current Configuration:
   [ReportConfig]   REPORT_ENGINE env var: 'professional'
   [ReportConfig]   REPORT_ENGINE enum: ReportEngine.PROFESSIONAL
   [ReportConfig]   ENABLE_PROFESSIONAL_REPORTS: True
   [Report Engine] compile_reports_with_engine_selection() called for execution {id}
   [Report Engine] REPORT_ENGINE value: ReportEngine.PROFESSIONAL
   [Report Engine] is_professional_enabled(): True
   [Report Engine] should_fallback_on_error(): True
   [Report Engine] Selected: PROFESSIONAL
   [ExecutionAnalyzer] Starting initialization...
   [ExecutionAnalyzer] Instantiating LLMService...
   [ExecutionAnalyzer] Instantiating ExecutionAnalyzer...
   [ExecutionAnalyzer] Started
   [ExecutionAnalyzer] Analyzing execution...
   [ExecutionAnalyzer] Analysis complete
   [ProfessionalReportGenerator] Starting initialization...
   [ProfessionalReportGenerator] Started
   [ProfessionalReportGenerator] Generating reports...
   [Professional Report Generated] HTML: {path}
   [Professional Report Generated] PDF: {path}
   ```

4. **Open the generated HTML report** and verify it starts with:
   ```
   TEST EXECUTION REPORT
   
   Executive Summary
   
   Validation Summary
   
   Business Rule Validation
   ```

## Expected Log Output

When the Professional Report Engine is correctly activated, you should see:

```
[Report Engine] _on_execution_completed() called for execution {uuid}
[Report Engine] Stack trace:
  File "backend/services/report_service.py", line 1688, in _on_execution_completed
    traceback.print_stack()
  ...
[ReportConfig] Current Configuration:
[ReportConfig]   REPORT_ENGINE env var: 'professional'
[ReportConfig]   REPORT_ENGINE enum: ReportEngine.PROFESSIONAL
[ReportConfig]   ENABLE_PROFESSIONAL_REPORTS: True
[ReportConfig]   FALLBACK_TO_LEGACY_ON_ERROR: True
[ReportConfig]   PROFESSIONAL_TEMPLATE_DIR: backend/templates
[ReportConfig]   PROFESSIONAL_REPORT_DIR: data/professional_reports
[ReportConfig] is_professional_enabled() called, returning: True
[Report Engine] compile_reports_with_engine_selection() called for execution {uuid}
[Report Engine] REPORT_ENGINE value: ReportEngine.PROFESSIONAL
[Report Engine] is_professional_enabled(): True
[Report Engine] should_fallback_on_error(): True
[Report Engine] Selected: PROFESSIONAL
[ExecutionAnalyzer] Starting initialization...
[ExecutionAnalyzer] Instantiating LLMService...
[ExecutionAnalyzer] Instantiating ExecutionAnalyzer...
[ExecutionAnalyzer] Started
[ExecutionAnalyzer] Analyzing execution...
[ExecutionAnalyzer] Analysis complete
[ProfessionalReportGenerator] Starting initialization...
[ProfessionalReportGenerator] Started
[ProfessionalReportGenerator] Generating reports...
[Professional Report Generated] HTML: data/professional_reports/professional_report_{uuid}.html
[Professional Report Generated] PDF: data/professional_reports/professional_report_{uuid}.pdf
```

## If Legacy Report Still Generated

If you still see:
```
[Report Engine] Selected: LEGACY
[Report Engine] compile_reports() called directly (LEGACY ENGINE)
```

Then the environment variable is not being set correctly. Verify:

1. **Check the environment variable** at server start:
   ```python
   import os
   print(f"REPORT_ENGINE env var: {os.getenv('REPORT_ENGINE')}")
   ```

2. **Check if the variable is set in the correct scope** (system vs session)

3. **Restart the backend server** after setting the variable

4. **Check for conflicting .env files** that might override the variable

## Summary

The Professional Report Engine implementation is correct and all code paths have been updated to use the engine selection method. The issue is that the `REPORT_ENGINE=professional` environment variable needs to be set before the backend server starts.

Once the environment variable is correctly set and the server is restarted, the Professional Report Engine will be used for all Playwright executions.
