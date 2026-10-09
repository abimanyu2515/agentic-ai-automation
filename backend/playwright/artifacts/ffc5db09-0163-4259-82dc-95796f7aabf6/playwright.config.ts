import { defineConfig } from '@playwright/test';
export default defineConfig({
  use: {
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  outputDir: 'E:\Agentic-AI-Automation\backend\playwright\artifacts\ffc5db09-0163-4259-82dc-95796f7aabf6\test-results',
  timeout: 60000,
});
