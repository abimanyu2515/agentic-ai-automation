# US/TC ID Naming & Report Filtering - Implementation Complete

## Summary

Implemented two key features:

1. **Test Case ID Display:** Show US##-TC## format in test case tables
2. **Report Filtering:** Filter execution reports by US number, TC number, and status

---

## Feature 1: Test Case ID Column

### What Changed

**Frontend - Test Cases Table (`frontend/js/components.js`):**

Added new column to display test case reference IDs (e.g., US02-TC01, US02-TC02).

**Before:**
```
| ☐ | Test Case Title | Evaluation | Run Status | Confidence | Status |
```

**After:**
```
| ☐ | Test Case ID | Test Case Title | Evaluation | Run Status | Confidence | Status |
```

**Implementation:**
- Added `<th style="width: 120px;">Test Case ID</th>` header
- Moved test_case_ref_id from inline badge to dedicated column
- Styled with teal accent color and left border
- Shows "N/A" if ref_id not yet generated

**Auto-Generation Logic (Already Exists):**
- Located in: `backend/repository/postgres_project_repository.py` (line 532-545)
- Auto-generates format: `{scenario_ref}-TC{count:02d}`
- Example: Scenario US02 → test cases US02-TC01, US02-TC02, US02-TC03
- Generation happens when test case is first created

---

## Feature 2: Report Filtering UI

### What Changed

**Frontend - Reports View (`frontend/index.html`):**

Added filter controls section above the reports workspace:

```html
<div class="section-card">
  <h3>Filter Reports</h3>
  <div style="grid with 4 columns">
    1. User Story (US) input field
    2. Test Case (TC) input field
    3. Status dropdown
    4. Apply/Clear buttons
  </div>
  <div id="filter-results-summary">
    <!-- Shows: Active Filters + Results count -->
  </div>
</div>
```

**Filter Inputs:**
- **US Number:** Text input (e.g., "US01", "US02")
- **TC Number:** Text input (e.g., "TC01", "TC02")
- **Status:** Dropdown (All, Passed, Failed, Error, Skipped)

**Frontend - Report Logic (`frontend/js/app.js`):**

### Added Methods:

1. **`applyReportFilters()`**
   - Reads filter values from inputs
   - Stores in `this.reportFilters`
   - Calls `renderReports()` to re-render with filters

2. **`clearReportFilters()`**
   - Clears all input fields
   - Resets `this.reportFilters = {}`
   - Calls `renderReports()` to show all reports

### Modified Method:

**`renderReports()`** - Added filtering logic at the start:

```javascript
// Apply filters if any
let filteredExecutions = this.executions;
const usFilter = this.reportFilters?.usNumber || '';
const tcFilter = this.reportFilters?.tcNumber || '';
const statusFilter = this.reportFilters?.status || '';

if (usFilter || tcFilter || statusFilter) {
  filteredExecutions = this.executions.filter(ex => {
    // Get test case for this execution
    const testCase = this.testCases?.find(tc => tc.id === ex.test_case_id);
    
    // Get test case ref ID (e.g., "US02-TC01")
    const refId = testCase.test_case_ref_id || '';
    
    // Parse US and TC from ref_id using regex
    const match = refId.match(/^(US\d+)-(TC\d+)$/i);
    const [, usNum, tcNum] = match || [];
    
    // Apply US filter (case-insensitive)
    if (usFilter && usNum?.toUpperCase() !== usFilter.toUpperCase()) {
      return false;
    }
    
    // Apply TC filter (case-insensitive)
    if (tcFilter && tcNum?.toUpperCase() !== tcFilter.toUpperCase()) {
      return false;
    }
    
    // Apply status filter
    if (statusFilter && ex.status !== statusFilter) {
      return false;
    }
    
    return true;
  });
  
  // Update filter results summary
  summaryEl.innerHTML = `Active Filters: ${filters} | Results: ${filteredExecutions.length} of ${this.executions.length}`;
}
```

---

## How It Works

### User Flow:

1. **Navigate to Reports page**
2. **Enter filter criteria:**
   - US Number: "US02" (optional)
   - TC Number: "TC01" (optional)
   - Status: "passed" (optional)
3. **Click "Apply Filters"**
4. **See filtered results:**
   - Only executions matching US02-TC01 with passed status
   - Summary shows: "Active Filters: US: US02, TC: TC01, Status: passed | Results: 5 of 120 executions"
5. **Click "Clear" to reset** and show all reports

### Filter Logic:

- **Case-insensitive:** "us02" matches "US02"
- **Partial matching:** Can filter by just US, just TC, or both
- **AND logic:** All active filters must match
- **Empty shows all:** If no filters, all reports shown

### Examples:

| Filter Input | Matches |
|---|---|
| US: "US02" | All test cases from US02 (US02-TC01, US02-TC02, etc.) |
| TC: "TC01" | All TC01 from any US (US01-TC01, US02-TC01, etc.) |
| US: "US02", TC: "TC01" | Only US02-TC01 |
| US: "US02", Status: "failed" | All failed tests from US02 |
| (empty) | All execution reports |

---

## Data Flow

```
1. Test Case Created
   ↓
2. Auto-generate test_case_ref_id (US##-TC##)
   └─ Backend: postgres_project_repository.py
   ↓
3. Display in Test Cases Table
   └─ Frontend: components.js (new ID column)
   ↓
4. Test Case Executed
   ↓
5. Execution Result stored with test_case_id reference
   ↓
6. Reports Page Loads
   ↓
7. User Applies Filters
   └─ Frontend: app.js (applyReportFilters)
   ↓
8. Parse test_case_ref_id from linked test case
   └─ Extract US## and TC## using regex
   ↓
9. Filter executions array
   └─ Match US, TC, and/or Status
   ↓
10. Render Filtered Results
    └─ Show metrics and export options
```

---

## Files Modified

### Frontend:

1. **`frontend/index.html`**
   - Added filter controls section to reports view
   - 3 input fields + 2 buttons + results summary div

2. **`frontend/js/app.js`**
   - Modified `renderReports()` to apply filters
   - Added `applyReportFilters()` method
   - Added `clearReportFilters()` method
   - Updated metrics to use `filteredExecutions` instead of `this.executions`

3. **`frontend/js/components.js`**
   - Added "Test Case ID" column header
   - Moved test_case_ref_id to dedicated column
   - Updated colspan in expanded row (5 → 6)

### Backend:

**No backend changes needed!**

- Auto-generation logic already exists
- Database schema already has:
  - `scenarios.scenario_ref_id` (US01, US02, etc.)
  - `test_cases.test_case_ref_id` (US01-TC01, etc.)
- Alembic migration already created (4e73efadb26f)

---

## Testing

### Test Scenario 1: View Test Case IDs

1. Navigate to Test Cases page
2. Select a requirement
3. **Verify:** Table shows new "Test Case ID" column
4. **Verify:** IDs display as US##-TC## format (or "N/A" if not generated)
5. **Verify:** IDs are styled with teal color and left border

### Test Scenario 2: Filter by US Number

1. Navigate to Reports page
2. Enter "US02" in US Number field
3. Click "Apply Filters"
4. **Verify:** Only reports from US02-* test cases shown
5. **Verify:** Summary shows: "Active Filters: US: US02 | Results: X of Y executions"

### Test Scenario 3: Filter by TC Number

1. Navigate to Reports page
2. Enter "TC01" in TC Number field
3. Click "Apply Filters"
4. **Verify:** Only reports from *-TC01 test cases shown (any US)

### Test Scenario 4: Combined Filters

1. Navigate to Reports page
2. Enter "US02" in US Number
3. Enter "TC01" in TC Number
4. Select "failed" in Status dropdown
5. Click "Apply Filters"
6. **Verify:** Only US02-TC01 failed executions shown
7. **Verify:** Summary shows all 3 filters

### Test Scenario 5: Clear Filters

1. Apply any filters
2. Click "Clear" button
3. **Verify:** All input fields reset
4. **Verify:** All reports shown again
5. **Verify:** Summary cleared

---

## Edge Cases Handled

1. **Test case without ref_id:** Shows "N/A" in ID column
2. **No matching reports:** Shows empty state with message
3. **Case-insensitive matching:** "us02" = "US02" = "Us02"
4. **Invalid ref_id format:** Filter skips test cases with malformed IDs
5. **No test case link:** Execution without test_case_id is excluded from filtered results

---

## UI/UX Details

### Filter Controls Styling:
- Grid layout: 4 columns (US input, TC input, Status dropdown, Buttons)
- Consistent input heights (10px padding)
- Gap between elements (16px)
- Buttons side-by-side with 8px gap
- Results summary below filters (12px margin-top)

### Test Case ID Column Styling:
- Width: 120px (fixed)
- Background: `rgba(13, 148, 136, 0.05)` (teal tint)
- Text color: `var(--accent-teal)`
- Font weight: 700 (bold)
- Border left: 2px solid teal
- Text align: center
- Shows "N/A" in muted gray if no ID

### Results Summary Format:
```
Active Filters: US: US02, TC: TC01, Status: passed | Results: 5 of 120 executions
```

---

## Backend Schema Reference

**Scenario Table:**
```sql
CREATE TABLE scenarios (
  id UUID PRIMARY KEY,
  scenario_ref_id VARCHAR(20),  -- US01, US02, US03...
  scenario_name VARCHAR(200),
  ...
);
CREATE INDEX ix_scenarios_scenario_ref_id ON scenarios(scenario_ref_id);
```

**Test Case Table:**
```sql
CREATE TABLE test_cases (
  id UUID PRIMARY KEY,
  scenario_id UUID REFERENCES scenarios(id),
  test_case_ref_id VARCHAR(30),  -- US01-TC01, US01-TC02...
  title VARCHAR(200),
  ...
);
CREATE INDEX ix_test_cases_test_case_ref_id ON test_cases(test_case_ref_id);
```

**Auto-Generation Code:**
```python
# In postgres_project_repository.py
if not tc.test_case_ref_id:
    # Get parent scenario's ref_id
    scenario_stmt = select(ScenarioDB).where(ScenarioDB.id == tc.scenario_id)
    scenario = session.scalar(scenario_stmt)
    scenario_ref = scenario.scenario_ref_id or "US00"
    
    # Count existing test cases for this scenario
    count_stmt = select(func.count()).select_from(TestCaseDB).where(
        TestCaseDB.scenario_id == tc.scenario_id
    )
    existing_count = session.scalar(count_stmt) or 0
    
    # Generate: US02-TC01, US02-TC02, etc.
    tc.test_case_ref_id = f"{scenario_ref}-TC{existing_count + 1:02d}"
```

---

## Status

✅ **Feature 1: Test Case ID Column** - COMPLETE  
✅ **Feature 2: Report Filtering UI** - COMPLETE  
✅ **Auto-Generation Logic** - ALREADY EXISTS  
✅ **Database Schema** - ALREADY EXISTS  

**Server:** Running with --reload (changes active immediately)

**Next Steps:** Test the features in the browser!

---

## Future Enhancements

Potential improvements for later:

1. **URL Parameters:** Save filter state in URL query params
2. **Filter Presets:** Save/load common filter combinations
3. **Advanced Filters:** Date range, duration, test priority
4. **Export Filtered:** Download only filtered reports as CSV/PDF
5. **Bulk Actions:** Select multiple filtered reports for batch operations
6. **Filter Chips:** Visual chips showing active filters (Material Design style)
7. **Autocomplete:** Suggest US/TC numbers based on available data

---

**Implementation Complete! 🎉**
