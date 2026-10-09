import { defineConfig } from '@playwright/test';
export default defineConfig({
  use: {
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  outputDir: 'E:\Agentic-AI-Automation\backend\playwright\artifacts\7f2f789a-f7c4-4ab8-8022-6aa10a420468\test-results',
  timeout: 60000,
});
