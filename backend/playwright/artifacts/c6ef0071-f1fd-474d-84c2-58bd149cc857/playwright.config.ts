import { defineConfig } from '@playwright/test';
export default defineConfig({
  use: {
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  outputDir: 'E:\Agentic-AI-Automation\backend\playwright\artifacts\c6ef0071-f1fd-474d-84c2-58bd149cc857\test-results',
  timeout: 60000,
});
