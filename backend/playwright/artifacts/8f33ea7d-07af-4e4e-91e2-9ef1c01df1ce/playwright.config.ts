import { defineConfig } from '@playwright/test';
export default defineConfig({
  use: {
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  outputDir: 'E:\Agentic-AI-Automation\backend\playwright\artifacts\8f33ea7d-07af-4e4e-91e2-9ef1c01df1ce\test-results',
  timeout: 60000,
});
