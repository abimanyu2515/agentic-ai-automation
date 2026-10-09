import { defineConfig } from '@playwright/test';
export default defineConfig({
  use: {
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    storageState: 'E:\\Agentic-AI-Automation\\backend\\playwright\\artifacts\\BATCH-20260715-951\\storage_state.json',
  },
  outputDir: 'E:\Agentic-AI-Automation\backend\playwright\artifacts\69fe9b86-2d31-43fa-b37e-586e803e1bec\test-results',
  timeout: 60000,
});
