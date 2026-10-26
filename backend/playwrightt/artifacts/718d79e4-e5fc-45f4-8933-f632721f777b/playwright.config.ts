import { defineConfig } from '@playwright/test';
export default defineConfig({
  use: {
    browserName: 'chromium',
    headless: true,
    launchOptions: {
      slowMo: 1000,
      args: ['--disable-blink-features=AutomationControlled']
    },
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    
  },
  reporter: [
    ['line'],
    ['json', { outputFile: 'report.json' }]
  ],
  outputDir: 'C:/Users/devis/agentic-ai-automation/backend/playwrightt/artifacts/718d79e4-e5fc-45f4-8933-f632721f777b/test-results',
  timeout: 30000,
});
