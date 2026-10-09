import { test, expect } from '@playwright/test';

test('Happy Path JPG Upload', async ({ page }) => {
  // Navigate to user profile page
  await page.goto('/profile');

  // Click on edit profile picture button
  const editProfilePictureButton = page.getByRole('button', { name: 'Edit Profile Picture' });
  await editProfilePictureButton.click();

  // Select the valid JPG file from local storage
  try {
    const fileInput = page.getByPlaceholder('No file chosen...');
    const filePath = '/path/to/image.jpg';
    await page.setFileInputFile(fileInput, filePath);
  } catch (error) {
    console.error('Failed to upload image:', error);
  }

  // The uploaded picture should be displayed on the user's profile page,
  // and its size and format should be verified as within the limits (5MB, JPG)
  const uploadedPicture = await page.getByText('Uploaded Picture');
  expect(uploadedPicture).not.toBeNull();
  const pictureDimensions = await page.evaluate(() => {
    const img = document.querySelector('img.uploaded-picture');
    return { width: img.width, height: img.height };
  });
  expect(pictureDimensions).not.toBe(null);
  const pictureFileSize = await page.evaluate(() => {
    const img = document.querySelector('img.uploaded-picture');
    return img filesize;
  });
  expect(pictureFileSize).toBeLessThanOrEqual(5 * 1024 * 1024);
});