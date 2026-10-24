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
  outputDir: 'C:/Users/Sridhar D/agentic-ai-automation/backend/playwrightt/artifacts/c414ab46-eeb9-4ed6-bb98-0b557bc1690c/test-results',
  timeout: 60000,
});
