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
  outputDir: 'C:/Users/Sridhar D/agentic-ai-automation/backend/playwrightt/artifacts/06a8ceb6-f4c2-42f1-8954-f153b0eb6aba/test-results',
  timeout: 60000,
});
