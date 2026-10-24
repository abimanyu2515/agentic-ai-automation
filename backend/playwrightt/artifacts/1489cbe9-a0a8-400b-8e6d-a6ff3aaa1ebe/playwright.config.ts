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
  outputDir: 'C:/Users/Sridhar D/agentic-ai-automation/backend/playwrightt/artifacts/1489cbe9-a0a8-400b-8e6d-a6ff3aaa1ebe/test-results',
  timeout: 60000,
});
