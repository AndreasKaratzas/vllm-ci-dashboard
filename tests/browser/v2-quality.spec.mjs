import { test } from '@playwright/test';

import { collectErrors, expectQuality, setTheme } from './support/quality.mjs';

// Each area adds its routes (every sub-view) once it is on the design system;
// every listed route is checked light and dark, on desktop and on a phone.
const ROUTES = [];

for (const theme of ['light', 'dark']) {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    for (const route of ROUTES) {
      test(`${route} meets the quality gates (${theme}, ${viewport.width}px)`, async ({ page }) => {
        await setTheme(page, theme);
        await page.setViewportSize(viewport);
        const errors = collectErrors(page);
        await page.goto(route);
        await page.getByRole('heading', { level: 2 }).first().waitFor();
        await expectQuality(page, errors);
      });
    }
  }
}
