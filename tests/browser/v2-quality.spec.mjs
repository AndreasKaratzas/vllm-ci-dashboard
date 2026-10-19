import { expect, test } from '@playwright/test';

import { collectErrors, expectQuality, setTheme } from './support/quality.mjs';

// Each area adds its routes (every sub-view) once it is on the design system;
// every listed route is checked light and dark, on desktop and on a phone.
const ROUTES = [
  '/#/perf',
  '/#/perf?view=accuracy',
  '/#/perf?device=mi300x',
  '/#/perf?model=MiniMaxAI%2FMiniMax-M2.5',
  '/#/perf?view=accuracy&model=MiniMaxAI%2FMiniMax-M2.5',
];

for (const theme of ['light', 'dark']) {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    for (const route of ROUTES) {
      test(`${route} meets the quality gates (${theme}, ${viewport.width}px)`, async ({ page }) => {
        await setTheme(page, theme);
        await page.setViewportSize(viewport);
        const errors = collectErrors(page);
        await page.goto(route);
        // Pages title themselves with PageHeader (h1) and load inside AsyncBoundary.
        await page.locator('[data-slot="page-header"] h1').waitFor();
        await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
        await expectQuality(page, errors);
      });
    }
  }
}
