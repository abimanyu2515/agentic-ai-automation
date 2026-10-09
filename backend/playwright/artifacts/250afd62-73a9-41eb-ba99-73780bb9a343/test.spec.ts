import { test, expect } from '@playwright/test';

test('Size Limit Exceeded Error Handling', async ({ page }) => {
  // Navigate to user profile page
  await page.goto('/user/profile');

  // Click on edit profile picture button
  const editButton = page.getByRole('button', { name: 'Edit Profile Picture' });
  await editButton.click();

  // Select the large JPG file from local storage
  const inputField = page.getByPlaceholder('No file chosen');
  try {
    await inputField.setInputFiles('/path/to/6MB.jpg');
  } catch (error) {
    console.error(error);
    throw new Error('Failed to upload file: ' + error.message);
  }

  // The upload should fail, displaying an error message indicating that the file size exceeds the limit
  const errorMessage = await page.getByText('File size exceeds the limit').textContent();
  expect(errorMessage).toContain('File size exceeds the limit');

  // and the previous picture should remain unchanged on the user's profile page
  const oldPictureUrl = 'previous-picture-url'; // Assume this is already stored somewhere or retrieved from DB
  await page.getByText(oldPictureUrl).expect(toBeDisplayed());
});