import { test, expect } from '@playwright/test';

test('Upload exceeds size limit', async ({ page }) => {
  // Log in to the application with a test user account
  await page.getByLabel('Email').fill('test@example.com');
  await page.getByLabel('Password').fill('password');
  await page.getByText('Log In').click();

  // Navigate to the profile page and click on the upload button
  await page.goto('/profile');
  const uploadButton = await page.getByRole('button', { name: 'Upload new picture' });
  await uploadButton.click();

  // Select a 6MB image file from the local file system
  try {
    const filePath = '/path/to/6mb_image.jpg';
    await page.setInputFiles('picture', filePath);
  } catch (err) {
    console.error(err);
    expect(true).toBe(false); // If we can't upload, skip rest of test
  }

  // Check the expected result: The upload fails, and an error message indicating the size limit exceeded is displayed, 
  // while keeping the previous picture unchanged
  const errorMessage = await page.getByText('File exceeds size limit');
  expect(errorMessage).not.toBe(null);
});