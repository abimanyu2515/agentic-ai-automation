import { defineConfig } from '@playwright/test';
export default defineConfig({
  use: {
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  outputDir: 'E:\Agentic-AI-Automation\backend\playwright\artifacts\d2331ead-489c-407d-a2a4-6e0a65d6eef1\test-results',
  timeout: 60000,
});
