import { test, expect } from '@playwright/test';

test('Unsupported Image Format', async ({ page }) => {
  // Step 1: Login to the user's account
  await page.goto('/login');
  const loginForm = await page.getByLabel('Email or username');
  await loginForm.fill('your-email@example.com');
  const passwordInput = await page.getByPlaceholder('Password');
  await passwordInput.fill('your-password');
  await page.getByText('Log in').click();

  // Step 2: Navigate to profile settings page
  const profileSettingsLink = await page.getByRole('link', { name: 'Profile Settings' });
  await profileSettingsLink.click();

  // Step 3: Click upload button and select BMP image
  try {
    const uploadButton = await page.getByText('Change picture');
    await uploadButton.click();
    const fileInput = await page.getByPlaceholder('No file chosen');
    const bmpFile = await page.uploadedFiles().createFile({
      filePath: 'path/to/image.bmp',
      mimeType: 'image/bmp'
    });
    await fileInput.setFiles([bmpFile]);
  } catch (error) {
    console.error(error);
    throw error;
  }

  // Expected result
  const errorMessage = await page.getByText('Unsupported image format');
  expect(errorMessage).not.toBeUndefined();
});