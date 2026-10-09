import { test } from '@playwright/test';

test('Happy Path: Valid JPG Upload', async ({ page }) => {
  // Login to the application with test user credentials
  await page.getByLabel('Email').fill('testuser@email.com');
  await page.getByLabel('Password').fill('password123');
  await page.getByText('Login').click();

  // Navigate to profile settings page
  await page.getByLabel('Settings').click();
  const profilePage = await page.getByRole('page', { name: 'Profile Settings' });
  await profilePage.click();

  // Click on profile picture upload button and select a valid JPG image under 5MB
  const uploadButton = await page.getByPlaceholder('');
  await uploadButton.upload('./assets/profile-picture.jpg');
  try {
    await page.waitLoadState();
    const uploadedPictureLocator = await page.getByRole('img', { name: 'Uploaded Picture' });
    const newPictureUrl = await uploadedPictureLocator.getAttribute('src');
    await expect(newPictureUrl).toContain('/profile-picture.jpg');
  } catch (error) {
    console.error(error);
    throw error;
  }
});