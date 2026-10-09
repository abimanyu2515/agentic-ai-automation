import { defineConfig } from '@playwright/test';
export default defineConfig({
  use: {
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  outputDir: 'E:\Agentic-AI-Automation\backend\playwright\artifacts\6bfbb573-7251-49dc-b7c7-a14a92c6f1b5\test-results',
  timeout: 60000,
});
