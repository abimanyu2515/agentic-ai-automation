import { defineConfig } from '@playwright/test';
export default defineConfig({
  use: {
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  outputDir: 'E:\Agentic-AI-Automation\backend\playwright\artifacts\7b933794-df73-47cf-bbcd-3d721a7cf526\test-results',
  timeout: 60000,
});
