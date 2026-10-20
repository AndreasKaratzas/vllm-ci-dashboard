import { expect, test } from '@playwright/test';

import { collectErrors, expectQuality, setTheme } from './support/quality.mjs';

// Each area adds its routes (every sub-view) once it is on the design system;
// every listed route is checked light and dark, on desktop and on a phone.
const ROUTES = [
  '/#/',
  '/#/?work=prs',
  '/#/?range=7',
  '/#/health',
  '/#/health?view=parity',
  '/#/health?view=coverage',
  '/#/health?view=mirrors',
  '/#/analytics?view=dns',
  '/#/analytics?view=dns&dns_window=720h&dns_scope=canonical',
  '/#/analytics?view=agent-health',
  '/#/analytics?view=agent-health&agent_window=14d&agent_signal=all',
  '/#/perf',
  '/#/perf?view=accuracy',
  '/#/perf?device=mi300x',
  '/#/perf?model=MiniMaxAI%2FMiniMax-M2.5',
  '/#/perf?view=accuracy&model=MiniMaxAI%2FMiniMax-M2.5',
  '/#/perf?view=accuracy&device=mi300x',
  '/#/perf?model=zai-org%2FGLM-5.2-FP8&device=mi300x',
];

for (const theme of ['light', 'dark']) {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    for (const route of ROUTES) {
      test(`${route} meets the quality gates (${theme}, ${viewport.width}px)`, async ({ page }) => {
        await setTheme(page, theme);
        await page.setViewportSize(viewport);
        const errors = collectErrors(page);
        await page.goto(route);
        await page.locator('[data-slot="page-header"]').first().waitFor();
        await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
        await expectQuality(page, errors);
      });
    }
  }
}
