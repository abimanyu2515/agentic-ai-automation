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
  outputDir: 'C:/Users/Sridhar D/agentic-ai-automation/backend/playwrightt/artifacts/a62afb7d-c5b3-41eb-95d4-eadfd29937eb/test-results',
  timeout: 60000,
});
