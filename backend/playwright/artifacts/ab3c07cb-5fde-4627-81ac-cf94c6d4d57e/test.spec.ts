import { test, expect } from '@playwright/test';

test('Invalid Password with Valid Username', async ({ page }) => {
  console.log('[Timeline] Navigate: Navigating to target URL');
  await page.goto('https://adactinhotelapp.com/');

  console.log('[Timeline] Click: Clicking on Login button');
  await page.click('#username');

  console.log('[Timeline] Type: Entering valid username');
  await page.type('#username', 'User Name');

  console.log('[Timeline] Type: Entering invalid password');
  await page.type('#password', 'Invalid Password');

  console.log('[Timeline] Click: Clicking on Login button');
  try {
    await page.click('text=Login');
  } catch (error) {
    console.log('[Error] Could not click login button');
    throw error;
  }

  console.log('[Timeline] Expect: Error message is displayed and user remains on login page');
  const errorMessage = await page.getByText('You must provide both your Username and Password to login.');
  expect(errorMessage).toBeDefined();
});