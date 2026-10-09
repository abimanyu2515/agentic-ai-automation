import { test, expect } from '@playwright/test';

test('Exceeds Size Limit', async ({ page }) => {
  // Step 1: Login to the user's account
  await page.getByLabel('Username').fill('username');
  await page.getByLabel('Password').fill('password');
  await page.getByText('Login').click();

  // Step 2: Navigate to profile settings page
  const profileLink = await page.getByRole('link', { name: 'Profile' });
  await profileLink.click();
  const settingsButton = await page.getByRole('button', { name: 'Settings' });
  await settingsButton.click();

  // Step 3: Attempt to upload 10MB JPG image
  try {
    await page.getByPlaceholder('Select a file...').setInput('./assets/very_large_image.jpg');
    await page.getByText('Upload').click();
  } catch (error) {
    console.error(error);
    expect(page.locator('text=Exceeds size limit')).toBeVisible();
    expect(await page.locator('#previous-picture-url').textContent()).not.toMatch('https://example.com/new-image.jpg');
  }
});