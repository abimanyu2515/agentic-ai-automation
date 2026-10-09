import { defineConfig } from '@playwright/test';
export default defineConfig({
  use: {
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  outputDir: 'E:\Agentic-AI-Automation\backend\playwright\artifacts\5af43842-a9e8-4022-ba03-d8d056b4e8f5\test-results',
  timeout: 60000,
});
