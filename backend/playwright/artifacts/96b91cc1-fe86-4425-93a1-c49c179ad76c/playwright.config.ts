import { defineConfig } from '@playwright/test';
export default defineConfig({
  use: {
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  outputDir: 'E:\Agentic-AI-Automation\backend\playwright\artifacts\96b91cc1-fe86-4425-93a1-c49c179ad76c\test-results',
  timeout: 60000,
});
