import { Page } from 'playwright';
import * as fs from 'fs/promises';
import * as path from 'path';

export type LogicalState = string;

interface StateConfig {
  name: string;
  url_contains?: string;
  title_contains?: string;
  required_elements?: string[];
}

export class ApplicationStateResolver {
  static async resolve(page: Page): Promise<LogicalState> {
    try {
      const configPath = path.join(process.cwd(), '../config/app_states.json');
      const configRaw = await fs.readFile(configPath, 'utf-8');
      const config = JSON.parse(configRaw);
      const states: StateConfig[] = config.application_states || [];

      const url = page.url();
      const title = await page.title().catch(() => '');

      for (const state of states) {
        // Check URL contains
        if (state.url_contains && !url.includes(state.url_contains)) {
          continue;
        }

        // Check Title contains
        if (state.title_contains && !title.toLowerCase().includes(state.title_contains.toLowerCase())) {
          continue;
        }

        // Check required elements presence
        if (state.required_elements && state.required_elements.length > 0) {
          let allMatch = true;
          for (const selector of state.required_elements) {
            const el = await page.$(selector).catch(() => null);
            if (el === null) {
              allMatch = false;
              break;
            }
          }
          if (!allMatch) {
            continue;
          }
        }

        return state.name;
      }

      return 'UNKNOWN';
    } catch (err) {
      console.error('[StateResolver] TS Resolution error:', err);
      return 'UNKNOWN';
    }
  }
}
