import { defineConfig } from '@playwright/test';
export default defineConfig({
  use: {
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  outputDir: 'E:\Agentic-AI-Automation\backend\playwright\artifacts\1ecd2c58-e2c6-4c41-845b-5eaeb11e1f63\test-results',
  timeout: 60000,
});
