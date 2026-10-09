import { test, expect } from '@playwright/test';

test('Happy Path JPG Image', async ({ page }) => {
  // Log in to the system with a valid account.
  await page.getByPlaceholder('Email or username').fill('your-email');
  await page.getByPlaceholder('Password').fill('your-password');
  await page.getByRole('button', { name: 'Login' }).click();

  // Navigate to the profile settings page.
  await page.getByText('Profile Settings').click();
  await page.locator('text="Profile Picture"').first().click(); // best guess, might need adjustment

  // Click on the upload button for the profile picture.
  const oldPicture = await page.getByRole('img');
  try {
    await page.getByText('Upload Picture').click();
    await page.setInputFiles('input[type="file"]', 'path/to/jpg/image.jpg');
  } catch (error) {
    console.error('Failed to upload new profile picture:', error);
  }

  // Check if the old picture is replaced with a new one.
  const newPicture = await page.getByRole('img');
  expect(newPicture).not.toBe(oldPicture);
});