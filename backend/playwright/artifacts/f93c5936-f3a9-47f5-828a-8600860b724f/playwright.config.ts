import { defineConfig } from '@playwright/test';
export default defineConfig({
  use: {
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    storageState: 'E:\\Agentic-AI-Automation\\backend\\playwright\\artifacts\\BATCH-20260715-248\\storage_state.json',
  },
  outputDir: 'E:\Agentic-AI-Automation\backend\playwright\artifacts\f93c5936-f3a9-47f5-828a-8600860b724f\test-results',
  timeout: 60000,
});
