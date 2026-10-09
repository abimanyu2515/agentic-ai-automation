import { defineConfig } from '@playwright/test';
export default defineConfig({
  use: {
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  outputDir: 'E:\Agentic-AI-Automation\backend\playwright\artifacts\e4ca7ec9-041f-4228-bd7d-6fc1e923d42d\test-results',
  timeout: 60000,
});
