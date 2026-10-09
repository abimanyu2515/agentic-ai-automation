import { test, expect } from '@playwright/test';

test('Forgot Password - Registered Email Required', async ({ page }) => {
  console.log('[Timeline] Navigate: Navigating to target URL');
  await page.goto('https://sampleapp.tricentis.com/101/app.php');

  console.log('[Timeline] Click: Clicking Forgot Password link');
  const forgotPasswordLink = page.getByRole('link', { name: 'Forgot your password?' });
  await forgotPasswordLink.click();

  console.log('[Timeline] Enter Email: Entering registered email address in the email field');
  const emailField = page.getByPlaceholder('Email Address');
  await emailField.type('test@sampleapp.com');

  console.log('[Timeline] Submit: Submitting the forgot password request');
  const submitButton = page.getByRole('button', { name: 'Submit' });
  try {
    await submitButton.click();
    await page.waitForLoadState('networkidle2');
    expect(await page.getByText('An email with a verification link has been sent to your email address')).toBeVisible();
  } catch (error) {
    console.error(error);
    throw new Error('Forgot password request failed');
  }
});
// comment