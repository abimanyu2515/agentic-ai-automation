import { test, expect } from '@playwright/test';

test('Valid JPG Upload', async ({ page }) => {
    // Step 1: Login to the user's account
    await page.getByLabel('Email').fill('user@example.com');
    await page.getByLabel('Password').fill('password');
    await page.getByText('Log in').click();
    
    // Step 2: Navigate to profile settings page
    await page.getByText('Profile Settings').click();

    // Step 3: Click upload button and select 4MB JPG image
    try {
        const [fileChooser] = await Promise.all([page.getByName('profilePicture'), page.getByRole('button', { name: 'Upload' })]);
        await fileChooser.setInputFiles('/path/to/4mb.jpg');
        await page.getByText('Upload').click();
    } catch (e) {
        console.error(e);
        throw e;
    }

    // Expected result
    await expect(page.getByPlaceholder('Preview')).toBeVisible();
});