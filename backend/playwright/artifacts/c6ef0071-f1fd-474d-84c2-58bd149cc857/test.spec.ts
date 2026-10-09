import { test, expect } from '@playwright/test';

test('Negative Test: Unsupported Image Format', async ({ page }) => {
  // Step 1: Login to the application with test user credentials
  await page.getByRole('button', { name: 'Log In' }).click();
  await page.fill('[data-test="username"]', 'test-username');
  await page.fill('[data-test="password"]', 'test-password');
  await page.getByText('Log in').click();

  // Step 2: Navigate to profile settings page
  await page.getByRole('link', { name: 'Settings' }).click();
  await page.getByText('Profile Settings').click();

  // Step 3: Click on profile picture upload button and select an image in GIF format
  const gifFile = './path/to/test.gif';
  try {
    await page.locator('[data-test="profile-picture-upload"]').click();
    await page.setInputFiles('[data-test="profile-picture-upload"]', gifFile);
  } catch (error) {
    console.error('Upload button not found or failed to upload file:', error);
    await expect(page).toHaveText('.error-message', 'Unsupported image format');
  }

  // Expected result: The upload fails, displaying a clear error message indicating the unsupported file format, and no changes are made to the previous picture.
  await expect(page.locator('[data-test="profile-picture-upload"]')).toContainText('Unsupported image format');
});