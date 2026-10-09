import { test, expect } from '@playwright/test';

test('Invalid Username and Password', async ({ page }) => {
  console.log('[Timeline] Navigate: Navigating to target URL');
  await page.goto('https://sampleapp.tricentis.com/101/app.php');

  // Login button
  const loginButton = page.getByRole('button', { name: 'Login' });
  console.log('[Timeline] Click: Clicked login button');
  await loginButton.click();

  // Username field (best guess)
  const usernameField = page.getByPlaceholder('User Name');
  console.log('[Timeline] Fill: Entering invalid username');
  await usernameField.fill('invalid-username');

  // Password field
  const passwordField = page.getByPlaceholder('Password');
  console.log('[Timeline] Fill: Entering invalid password');
  await passwordField.fill('invalid-password');

  // Submit button
  const submitButton = page.getByRole('button', { name: 'Login' });
  console.log('[Timeline] Click: Clicked submit button');
  try {
    await submitButton.click();
    const errorMessage = page.locator('text=Invalid User ID or Password').first();
    expect(await errorMessage.innerText()).toBe('Invalid User ID or Password');
  } catch (error) {
    console.error(error);
    throw error;
  }
});