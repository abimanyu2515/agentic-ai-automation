import { test, expect } from '@playwright/test';

test('Valid Credentials Login', async ({ page }) => {
  console.log('[Timeline] Navigate: Navigating to Banking Login page');
  await page.goto('https://sampleapp.tricentis.com/101/app.php');
  console.log('[Timeline] Success');
});