
import { test, expect } from '@playwright/test';
import * as path from 'path';
import * as fs from 'fs';

test.beforeEach(async ({ page }) => {
  const screenshotsDir = path.join(__dirname, 'screenshots');
  if (!fs.existsSync(screenshotsDir)) {
    fs.mkdirSync(screenshotsDir, { recursive: true });
  }

  let stepIndex = 0;
  let lastTimelineMessage = '';

  const origConsoleLog = console.log;
  console.log = function(...args) {
    origConsoleLog(...args);
    const msg = args.join(' ');
    if (msg.startsWith('[Timeline]')) {
      lastTimelineMessage = msg.replace('[Timeline]', '').trim();
    }
  };

  const sanitizeFilename = (str) => {
    return str.toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '');
  };

  const takeStepScreenshot = async (actionName: string) => {
    stepIndex++;
    const cleanDesc = sanitizeFilename(lastTimelineMessage || actionName);
    const filename = `step-${String(stepIndex).padStart(2, '0')}-${cleanDesc}.png`;
    const filepath = path.join(screenshotsDir, filename);
    try {
      await page.waitForTimeout(300);
      await page.screenshot({ path: filepath });
      console.log(`[Timeline] Screenshot: ${filename}`);
    } catch (e) {
      // Ignore screenshot errors
    }
  };

  const origGoto = page.goto.bind(page);
  page.goto = async (url, options) => {
    const res = await origGoto(url, options);
    await takeStepScreenshot('goto');
    return res;
  };

  const LocatorProto = Object.getPrototypeOf(page.locator('body'));

  const origLocClick = LocatorProto.click;
  LocatorProto.click = async function(options) {
    const res = await origLocClick.call(this, options);
    await takeStepScreenshot('click');
    return res;
  };

  const origLocFill = LocatorProto.fill;
  LocatorProto.fill = async function(value, options) {
    const res = await origLocFill.call(this, value, options);
    await takeStepScreenshot('fill');
    return res;
  };

  const origLocSelectOption = LocatorProto.selectOption;
  LocatorProto.selectOption = async function(values, options) {
    const res = await origLocSelectOption.call(this, values, options);
    await takeStepScreenshot('selectOption');
    return res;
  };
});

await page.goto('https://google.com');

test.afterEach(async ({ page }) => {
  try {
    await page.context().storageState({ path: 'C:/Users/devis/agentic-ai-automation/backend/playwright/artifacts/b7324c11-34cf-48e9-92c6-97ad366f5151/storage_state.json' });
  } catch (e) {
    console.error('Failed to save storage state:', e);
  }
});
