"""
Script to update the test case script with the correct selector for login error.
"""

import psycopg2

# Database connection
DB_HOST = "localhost"
DB_PORT = 5432
DB_NAME = "agentic_ai"
DB_USER = "postgres"
DB_PASSWORD = "scoot"

TEST_CASE_ID = "b7169bdf-befe-4883-afdd-e59c581e403b"

# Corrected script with b#loginerror instead of b.loginerror
CORRECTED_SCRIPT = """import { test, expect } from '@playwright/test';

const BASE_URL = process.env.TARGET_URL || 'https://adactinhotelapp.com/';
const USERNAME = process.env.TARGET_USERNAME || 'ADACTINFORQA';

test('Login with incorrect password', async ({ page }) => {
  console.log('[Timeline] Navigate to base URL');
  await page.goto(BASE_URL, { waitUntil: 'networkidle' });

  await page.screenshot({ path: '01-login-page.png' });

  console.log('[Timeline] Enter valid username');
  await page.locator('#username').fill(USERNAME);

  console.log('[Timeline] Enter incorrect password');
  await page.locator('#password').fill('WrongPassword123');

  await page.screenshot({ path: '02-credentials-entered.png' });

  console.log('[Timeline] Click Login button');
  await page.locator('#login').click();

  console.log('[Timeline] Verify invalid login message');

  // Wait for the error message to appear - use ID selector
  await expect(page.locator('b#loginerror')).toBeVisible();
  await expect(page.locator('b#loginerror'))
    .toContainText('Invalid Login details');

  // Verify user is still on the login page
  await expect(page).toHaveURL(/index\\.php/);

  await page.screenshot({ path: '03-invalid-login.png' });

  console.log('[Timeline] Negative login scenario passed');
});"""

def update_test_case_script():
    """Update the test case script with the corrected selector."""
    conn = None
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        cursor = conn.cursor()
        
        # Update the playwright_script for the test case
        update_sql = """
            UPDATE test_cases 
            SET playwright_script = %s, updated_at = NOW()
            WHERE id = %s
        """
        
        cursor.execute(update_sql, (CORRECTED_SCRIPT, TEST_CASE_ID))
        conn.commit()
        
        print(f"Successfully updated test case {TEST_CASE_ID} with corrected script")
        print("Changed selector from 'b.loginerror' to 'b#loginerror'")
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        print(f"Error: {e}")
        if conn:
            conn.rollback()
            conn.close()

if __name__ == "__main__":
    update_test_case_script()
