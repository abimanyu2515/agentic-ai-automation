import { defineConfig } from '@playwright/test';
export default defineConfig({
  use: {
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    storageState: 'E:\\Agentic-AI-Automation\\backend\\playwright\\artifacts\\BATCH-20260715-413\\storage_state.json',
  },
  outputDir: 'E:\Agentic-AI-Automation\backend\playwright\artifacts\feebefe1-12b6-4be3-8887-aa2affc02ae9\test-results',
  timeout: 60000,
});
