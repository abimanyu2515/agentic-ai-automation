import { test, expect } from '@playwright/test';

test('Edge Case Large PNG File', async ({ page }) => {
  // Step 1: Log in to the system with a valid account.
  await page.goto('/login');
  await page.getByLabel('Email').fill('valid@email.com');
  await page.getByLabel('Password').fill('password');
  const loginButton = page.locator('#login-button'); // best guess
  await loginButton.click();

  // Step 2: Navigate to the profile settings page.
  await page.goto('/settings');

  // Step 3: Attempt to upload an oversized PNG file exceeding the 5MB limit.
  const largeImage = await fetch('large_image.png');
  try {
    const response = await fetch('/upload', {
      method: 'POST',
      headers: { 'Content-Type': 'image/png' },
      body: new ReadableStream({
        async pull(controller) {
          const chunk = await largeImage.arrayBuffer();
          controller.enqueue(chunk);
          controller.close();
        }
      })
    });
    await expect(response.ok).toBe(false);
  } catch (error) {
    console.error('Error uploading image:', error);
  }

  // Check the previous profile picture remains unchanged with an error message.
  const prevProfilePicture = page.getByText(/previous profile picture/); // best guess
  const errorMessage = page.locator('.error-message'); // best guess
  await expect(prevProfilePicture).toBeVisible();
  await expect(errorMessage).toContainText('File size exceeds the limit');
});