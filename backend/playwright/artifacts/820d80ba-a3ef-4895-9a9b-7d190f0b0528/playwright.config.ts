import { defineConfig } from '@playwright/test';
export default defineConfig({
  use: {
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  outputDir: 'E:\Agentic-AI-Automation\backend\playwright\artifacts\820d80ba-a3ef-4895-9a9b-7d190f0b0528\test-results',
  timeout: 60000,
});
