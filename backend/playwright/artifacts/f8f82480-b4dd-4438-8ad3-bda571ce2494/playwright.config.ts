import { defineConfig } from '@playwright/test';
export default defineConfig({
  use: {
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  outputDir: 'E:\Agentic-AI-Automation\backend\playwright\artifacts\f8f82480-b4dd-4438-8ad3-bda571ce2494\test-results',
  timeout: 60000,
});
