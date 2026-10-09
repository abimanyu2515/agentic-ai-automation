import { test, expect } from '@playwright/test';

test('Edge Case: Large File Size', async ({ page }) => {
    // 1. Login to the application with test user credentials
    await page.getByRole('button', { name: 'Login' }).click();
    await page.getByLabel('Email').fill('testuser@example.com');
    await page.getByLabel('Password').fill('password123');
    await page.getByText('Submit').click();

    // 2. Navigate to profile settings page
    await page.getByRole('button', { name: 'Settings' }).click();
    await page.getByLabel('Profile picture').click();

    // 3. Click on profile picture upload button and select an image larger than 5MB
    try {
        const largeFile = new File(['large file content'], 'largefile.png');
        page.setStorageState({
            storageState: {
                cookies: []
            }
        });
        await page.getByPlaceholder('Upload Profile Picture').setInput(largeFile);
        await page.getByRole('button', { name: 'Upload' }).click();
    } catch (error) {
        console.error(error);
    }

    // Expected result
    const errorMessage = await page.getByText(/file size limit/i).textContent();
    expect(errorMessage).toContain('5MB');
});