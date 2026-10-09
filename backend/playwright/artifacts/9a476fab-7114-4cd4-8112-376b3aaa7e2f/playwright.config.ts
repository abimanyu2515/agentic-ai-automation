import { defineConfig } from '@playwright/test';
export default defineConfig({
  use: {
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    storageState: 'E:\\Agentic-AI-Automation\\backend\\playwright\\artifacts\\BATCH-20260715-837\\storage_state.json',
  },
  outputDir: 'E:\Agentic-AI-Automation\backend\playwright\artifacts\9a476fab-7114-4cd4-8112-376b3aaa7e2f\test-results',
  timeout: 60000,
});
