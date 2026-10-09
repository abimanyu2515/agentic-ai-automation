import { test } from '@playwright/test';

test('Valid Login with Correct Credentials', async ({ page }) => {
  // Navigate: Navigating to target URL
  console.log("[Timeline] Navigate: Navigating to target URL");
  await page.goto("https://adactinhotelapp.com/");

  // Locate: Clicking the Login link
  console.log("[Timeline] Click: Clicking the Login link");
  const loginLink = page.getByText('LOGIN');
  if (!await loginLink.exists()) {
    throw new Error('Login link not found!');
  }
  await loginLink.click();

  // Locate: Entering valid username into Username field
  console.log("[Timeline] Type: Entering valid username into Username field");
  const usernameField = page.getByPlaceholder('Username');
  if (!await usernameField.exists()) {
    throw new Error('Username field not found!');
  }
  await usernameField.fill("username");

  // Locate: Entering valid password into Password field
  console.log("[Timeline] Type: Entering valid password into Password field");
  const passwordField = page.getByPlaceholder('Password');
  if (!await passwordField.exists()) {
    throw new Error('Password field not found!');
  }
  await passwordField.fill("password");

  // Locate: Clicking the Login button
  console.log("[Timeline] Click: Clicking the Login button");
  const loginButton = page.getByText('Login');
  if (!await loginButton.exists()) {
    throw new Error('Login button not found!');
  }

  try {
    await loginButton.click();
    // Locate: Welcome message in Welcome Dashboard
    console.log("[Timeline] Expect: Checking for welcome message");
    const welcomeMessage = page.getByText('Hello username!');
    if (!await welcomeMessage.exists()) {
      throw new Error("Invalid Login Credentials!");
    }
  } catch (error) {
    console.error(error);
    await page.screenshot({ path: 'screenshot.png' });
    throw error;
  }
});