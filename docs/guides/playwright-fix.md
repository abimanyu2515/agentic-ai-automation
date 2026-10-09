# Playwright Test Fix: Select Hotel Test

## Problem
The test is failing with: `Expected element to contain text "Hotel Name", but got " " (timed out after 5000ms)`

## Root Causes
1. **Table selector might be incorrect** - `#select_form table` may not match the actual page structure
2. **Column headers might have different text/casing** - "Hotel Name" might be "Hotel name" or something else
3. **Timing issue** - Table might not be loaded when we check it
4. **No results returned** - Search criteria might not match any hotels

## Recommended Fixes

### 1. Add Screenshot Debugging
```typescript
// After navigation to SelectHotel.php
await page.screenshot({ path: 'debug-select-hotel.png', fullPage: true });
```

### 2. Wait for Table to Load
```typescript
// Wait for the table to be visible first
await page.waitForSelector('#select_form table', { timeout: 10000 });
```

### 3. Inspect Actual Table Structure
```typescript
// Debug: Print actual table content
const tableContent = await page.locator('#select_form').textContent();
console.log('[DEBUG] Table content:', tableContent);
```

### 4. Use More Flexible Header Check
```typescript
// Check for headers case-insensitively
const tableText = await table.textContent();
const expectedColumns = ['Hotel Name', 'Location', 'Rooms', 'Arrival Date'];
for (const col of expectedColumns) {
  if (!tableText?.toLowerCase().includes(col.toLowerCase())) {
    console.error(`Missing column: ${col}`);
    await page.screenshot({ path: `error-missing-${col}.png` });
    throw new Error(`Column "${col}" not found in table`);
  }
}
```

### 5. Alternative: Check for Specific Table Structure
```typescript
// Check for table headers by row
const headerRow = page.locator('#select_form table tr').first();
await expect(headerRow).toBeVisible();

// Verify individual header cells
await expect(page.locator('#select_form table th')).toContainText(['Hotel Name', 'Location']);
```

## Complete Fixed Test

```typescript
import { test, expect } from '@playwright/test';

const BASE_URL = process.env.TARGET_URL || 'https://adactinhotelapp.com/';
const USERNAME = process.env.TARGET_USERNAME || 'ADACTINFORQA';
const PASSWORD = process.env.TARGET_PASSWORD || '3U0515';

test('Select matching hotel and continue', async ({ page }) => {
  // Navigate to the application
  console.log('[Timeline] Navigate: Go to base URL');
  await page.goto(BASE_URL);

  // Login
  console.log('[Timeline] Type: Enter username');
  await page.locator('input#username').fill(USERNAME);
  console.log('[Timeline] Type: Enter password');
  await page.locator('input#password').fill(PASSWORD);
  console.log('[Timeline] Click: Login button');
  await page.locator('input#login').click();

  // Wait for SearchHotel.php
  await expect(page).toHaveURL(/SearchHotel\.php/, { timeout: 10000 });

  // Perform search
  console.log('[Timeline] Select: Location - Sydney');
  await page.locator('select#location').selectOption({ label: 'Sydney' });
  
  console.log('[Timeline] Select: Hotels - Hotel Creek');
  await page.locator('select#hotels').selectOption({ label: 'Hotel Creek' });
  
  console.log('[Timeline] Select: Room Type - Standard');
  await page.locator('select#room_type').selectOption({ label: 'Standard' });
  
  console.log('[Timeline] Select: Number of Rooms - 1 - One');
  await page.locator('select#room_nos').selectOption({ label: '1 - One' });
  
  console.log('[Timeline] Type: Check-in Date');
  await page.locator('input#datepick_in').fill('20/08/2026');
  
  console.log('[Timeline] Type: Check-out Date');
  await page.locator('input#datepick_out').fill('25/08/2026');
  
  console.log('[Timeline] Select: Adults per Room - 1');
  await page.locator('select#adult_room').selectOption({ label: '1' });
  
  console.log('[Timeline] Select: Children per Room - 0');
  await page.locator('select#child_room').selectOption({ label: '0' });
  
  console.log('[Timeline] Click: Search button');
  await page.locator('input#Submit').click();

  // Wait for SelectHotel.php with longer timeout
  await expect(page).toHaveURL(/SelectHotel\.php/, { timeout: 10000 });
  
  // FIXED: Wait for table to be present
  console.log('[Timeline] Wait: For results table to load');
  await page.waitForSelector('#select_form', { timeout: 10000 });
  
  // FIXED: Take screenshot for debugging
  await page.screenshot({ path: 'debug-select-hotel-page.png', fullPage: true });
  
  // FIXED: Get table locator with better selector
  console.log('[Timeline] Verify: Results table displayed');
  const selectForm = page.locator('#select_form');
  await expect(selectForm).toBeVisible();
  
  // FIXED: Debug - print actual table content
  const formContent = await selectForm.textContent();
  console.log('[DEBUG] Form content preview:', formContent?.substring(0, 200));
  
  // FIXED: More flexible column verification
  const expectedColumns = [
    'Hotel Name',
    'Location',
    'Rooms',
    'Arrival Date',
    'Departure Date',
    'No. of Days',
    'Room Type',
    'Price per Night',
    'Total Price'
  ];
  
  // Check for each column with case-insensitive matching
  for (const col of expectedColumns) {
    const found = formContent?.toLowerCase().includes(col.toLowerCase());
    if (!found) {
      console.error(`[ERROR] Column not found: "${col}"`);
      await page.screenshot({ path: `error-missing-column-${col.replace(/\s/g, '-')}.png` });
    }
    // Still assert, but we've already logged the issue
    await expect(selectForm).toContainText(col, { timeout: 5000 });
  }
  
  // FIXED: Wait for radio button to be available
  console.log('[Timeline] Wait: For hotel results to load');
  await page.waitForSelector('input#radiobutton_0', { timeout: 10000 });
  
  // Check radio button
  console.log('[Timeline] Check: Radio button for first result');
  await page.locator('input#radiobutton_0').check();
  
  // Verify Continue button is enabled
  console.log('[Timeline] Verify: Continue button enabled');
  await expect(page.locator('input#continue')).toBeEnabled({ timeout: 5000 });
  
  // Click Continue
  console.log('[Timeline] Click: Continue button');
  await page.locator('input#continue').click();
  
  // Wait for BookHotel.php
  await expect(page).toHaveURL(/BookHotel\.php/, { timeout: 10000 });
  
  // Verify hotel details on booking page
  console.log('[Timeline] Verify: Selected hotel details on booking page');
  await expect(page.locator('body')).toContainText('Hotel Creek', { timeout: 5000 });
  await expect(page.locator('body')).toContainText('Sydney', { timeout: 5000 });
  await expect(page.locator('body')).toContainText('Standard', { timeout: 5000 });
});
```

## Quick Debug Steps

### Step 1: Check if the page loads at all
```typescript
await page.goto('https://adactinhotelapp.com/SelectHotel.php');
await page.screenshot({ path: 'select-hotel-direct.png', fullPage: true });
const content = await page.content();
console.log('Page HTML:', content);
```

### Step 2: Inspect the actual table selector
```typescript
// Try different selectors
const selectors = [
  '#select_form table',
  'table',
  'form table',
  '#select_form'
];

for (const sel of selectors) {
  const exists = await page.locator(sel).count() > 0;
  console.log(`Selector "${sel}" exists: ${exists}`);
}
```

### Step 3: Check for dynamic content
```typescript
// Wait for network idle (all AJAX calls complete)
await page.goto(url, { waitUntil: 'networkidle' });
```

## Alternative Approach: Use Data-Driven Selectors

If the table structure is completely different, inspect the actual HTML and update selectors accordingly.

```typescript
// Example: If headers are in <td> instead of <th>
const headers = await page.locator('#select_form table tr:first-child td').allTextContents();
console.log('Actual headers:', headers);
```

## Next Steps

1. **Run the fixed test** with screenshots enabled
2. **Check the debug screenshot** to see actual page structure
3. **Update selectors** based on actual HTML structure
4. **Add error handling** for cases where no results are found
5. **Consider using different test data** if Hotel Creek doesn't exist
