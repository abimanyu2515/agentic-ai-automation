import { defineConfig } from '@playwright/test';
export default defineConfig({
  use: {
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    storageState: 'E:\\Agentic-AI-Automation\\backend\\playwright\\artifacts\\BATCH-20260715-782\\storage_state.json',
  },
  outputDir: 'E:\Agentic-AI-Automation\backend\playwright\artifacts\193e406b-3129-489c-9d89-5a9c53a02de9\test-results',
  timeout: 60000,
});
