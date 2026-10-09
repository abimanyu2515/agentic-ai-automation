import { test } from '@playwright/test';

test('Login with Empty Username Field', async ({ page }) => {
  console.log("[Timeline] Navigate: Navigating to target URL")
  await page.goto("https://adactinhotelapp.com/")

  console.log("[Timeline] Click: Clicking login link in top navigation menu")
  const loginLink = page.getByRole('link', { name: 'Login' })
  await loginLink.click()

  console.log("[Timeline] Select: Focusing on username input field")
  const usernameField = page.locator('#username')
  await usernameField.focus()

  console.log("[Timeline] Fill: Leaving username field blank")
  try {
    // Intentionally leaving username field blank
  } catch (e) {
    console.error("Username field was filled in unexpectedly!")
    throw e
  }

  console.log("[Timeline] Type: Entering valid password into password input field")
  const passwordField = page.locator('#password')
  await passwordField.fill('your_valid_password')

  console.log("[Timeline] Click: Clicking login button")
  const loginButton = page.getByText('Login')
  await loginButton.click()

  console.log("[Timeline] Assertion: Verifying that error message is displayed on the login page")
  const errorMessage = page.locator('.error')
  expect(errorMessage).toBeVisible()
});