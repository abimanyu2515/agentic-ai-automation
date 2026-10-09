import { test, expect } from '@playwright/test';

test('Upload invalid PDF format', async ({ page }) => {
  // Log in to the application with a test user account
  await page.getByLabel('Email').fill('test@example.com');
  await page.getByLabel('Password').fill('password');
  await page.getByText('Log in').click();
  
  // Navigate to the profile page and click on the upload button
  await page.waitForNavigation({ url: 'https://example.com/profile' });
  const profilePictureButton = page.getByRole('button', { name: 'Upload picture' });
  await profilePictureButton.click();
  
  // Select a PDF image file from the local file system
  try {
    const filePath = './invalid-pdf.pdf';
    await page.setInputFiles('input[type="file"]', filePath);
  } catch (error) {
    console.error(`Error: Could not select file at path ${filePath}:`, error);
    throw error;
  }
  
  // The upload fails, and an error message indicating the unsupported format is displayed
  const errorMessage = await page.getByText('Invalid file format');
  expect(errorMessage).toBeVisible();
  
  // while keeping the previous picture unchanged
  const oldPicture = await page.getByRole('img', { name: 'Old picture' });
  expect(await oldPicture.getAttribute('alt')).not.toBeUndefined();
});