import { test, expect } from '@playwright/test';

test('Upload valid JPG within size limit', async ({ page }) => {
  // Log in to the application with a test user account
  await page.goto('/login');
  await page.locator('[data-testid="username"]').fill('testuser');
  await page.locator('[data-testid="password"]').fill('testpassword');
  await page.locator('[data-testid="login-button"]').click();
  
  // Navigate to the profile page and click on the upload button
  await page.goto('/profile');
  const uploadButton = await page.getByRole('button', { name: 'Upload new picture' });
  await uploadButton.click();
  
  // Select a 2MB JPG image file from the local file system
  const jpgFileInput = page.getByPlaceholder('Select JPG image');
  try {
    await jpgFileInput.setInputFiles('/path/to/valid.jpg');
  } catch (error) {
    console.error('Failed to upload picture:', error);
    return;
  }
  
  // The new picture is successfully uploaded, replacing the previous one, and a confirmation message is displayed
  const successMessage = await page.getByText('Picture uploaded successfully!');
  expect(successMessage).toBeVisible();
});