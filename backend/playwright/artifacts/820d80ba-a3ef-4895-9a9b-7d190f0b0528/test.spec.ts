import { test, expect } from '@playwright/test';

test('Reject Oversized PNG Profile Picture Upload', async ({ page }) => {
  // Log in to the system as the user with an active account
  await page.getByLabel('Email').fill('user@example.com');
  await page.getByLabel('Password').fill('password');
  await page.getByText('Login').click();

  // Navigate to the settings page where the profile picture can be updated
  const url = new URL(page.url());
  url.hash = 'settings';
  await page.goto(url.href);

  // Click on the 'Upload' button and select an 8MB PNG file from local storage
  try {
    const uploadButton = page.getByRole('button', { name: 'Upload' });
    await uploadButton.click();
    const inputField = page.getByPlaceholder('Select a file');
    await inputField.setInputFiles('./path/to/image.png');

    // Check the expected result (error message displayed)
    expect(await page.locator('text=Image size exceeds the limit').isVisible()).toBe(true);
  } catch (err) {
    console.error(err);
    throw err;
  }
});