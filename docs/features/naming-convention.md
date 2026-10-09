# Naming Convention Implementation Summary

## Overview
Implemented a hierarchical naming convention for Scenarios (User Stories) and Test Cases to improve traceability and organization.

## Naming Format
- **Scenarios (User Stories)**: `US01`, `US02`, `US03`, etc.
- **Test Cases**: `US01-TC01`, `US01-TC02`, `US02-TC01`, etc.

## Changes Made

### 1. Backend Models
**Files Modified:**
- `backend/models/scenario.py`
- `backend/models/test_case.py`

**Changes:**
- Added `scenario_ref_id` field to Scenario model
- Added `test_case_ref_id` field to TestCase model

### 2. Database Models
**File Modified:** `backend/database/db_models.py`

**Changes:**
- Added `scenario_ref_id` column to `ScenarioDB` table (String(20), nullable, indexed)
- Added `test_case_ref_id` column to `TestCaseDB` table (String(30), nullable, indexed)

### 3. Database Migration
**File Created:** `backend/database/alembic/versions/4e73efadb26f_add_scenario_and_testcase_ref_ids.py`

**Migration Applied:**
```bash
alembic upgrade head
```

**What it does:**
- Adds `scenario_ref_id` column to scenarios table
- Adds `test_case_ref_id` column to test_cases table
- Creates indexes for both new columns

### 4. Repository Layer
**File Modified:** `backend/repository/postgres_project_repository.py`

**Changes:**
- Added import for `func` from SQLAlchemy
- Modified `save_scenarios()` method to auto-generate `scenario_ref_id` (US01, US02, etc.)
- Modified `save_test_cases()` method to auto-generate `test_case_ref_id` (US01-TC01, US01-TC02, etc.)

**Auto-Generation Logic:**
- **Scenarios**: Counts existing scenarios for a requirement and assigns next sequential number
- **Test Cases**: Fetches parent scenario's ref_id and counts existing test cases for that scenario

### 5. API Response DTOs
**Files Modified:**
- `backend/schemas/scenario_dto.py`
- `backend/schemas/testcase_dto.py`

**Changes:**
- Added `scenario_ref_id` field to `ScenarioResponse`
- Added `test_case_ref_id` field to `TestCaseResponse`

### 6. Frontend Display
**File Modified:** `frontend/js/components.js`

**Changes:**
- Updated scenario table rendering to display `scenario_ref_id` badge (blue badge with US01, US02, etc.)
- Updated test case table rendering to display `test_case_ref_id` badge (teal badge with US01-TC01, etc.)

**Visual Design:**
- Scenario badges: Blue background (`rgba(59, 130, 246, 0.1)`), displayed before scenario name
- Test case badges: Teal background (`rgba(13, 148, 136, 0.1)`), displayed before test case title
- Both badges have rounded corners and bold font

## How It Works

### Scenario ID Generation
When a new scenario is saved:
1. Check if `scenario_ref_id` is already set (skip if yes)
2. Count existing scenarios for the same requirement
3. Generate ID: `US{count + 1:02d}` (e.g., US01, US02, US10, etc.)
4. Save to database

### Test Case ID Generation
When a new test case is saved:
1. Check if `test_case_ref_id` is already set (skip if yes)
2. Fetch parent scenario's `scenario_ref_id`
3. Count existing test cases for the same scenario
4. Generate ID: `{scenario_ref_id}-TC{count + 1:02d}` (e.g., US01-TC01, US01-TC02, etc.)
5. Save to database

## Testing

### Manual Testing Steps
1. **Create a new requirement** or select an existing one
2. **Generate scenarios** - verify they show badges like US01, US02, US03
3. **Approve scenarios** and generate test cases
4. **Verify test case IDs** show as US01-TC01, US01-TC02, US02-TC01, etc.
5. **Check database** - verify columns `scenario_ref_id` and `test_case_ref_id` are populated

### Database Verification
```sql
-- Check scenarios
SELECT id, scenario_ref_id, scenario_name FROM scenarios ORDER BY created_at;

-- Check test cases
SELECT id, test_case_ref_id, title, scenario_id FROM test_cases ORDER BY created_at;
```

## Benefits

1. **Improved Traceability**: Easy to track which test cases belong to which scenarios
2. **Better Organization**: Sequential numbering provides clear ordering
3. **Professional Format**: Follows industry-standard naming conventions (similar to JIRA)
4. **Visual Clarity**: Color-coded badges make IDs easy to spot in the UI
5. **Scalability**: Format supports up to 99 scenarios (US01-US99) and 99 test cases per scenario

## Future Enhancements

1. **Custom Prefixes**: Allow projects to customize prefix (e.g., "REQ" instead of "US")
2. **Requirement-Level Prefix**: Include requirement ID in scenario names (e.g., REQ01-US01)
3. **Export to Reports**: Include ref IDs in generated reports and exports
4. **JIRA Integration**: Sync ref IDs with JIRA issue keys
5. **Search by Ref ID**: Add search functionality to find by US01, US01-TC01, etc.

## Rollback Instructions

If needed, rollback the migration:
```bash
alembic downgrade -1
```

This will:
- Drop the `test_case_ref_id` column and index
- Drop the `scenario_ref_id` column and index
- Preserve all other data
