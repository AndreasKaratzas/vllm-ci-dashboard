import { expect, test } from '@playwright/test';

import { collectErrors, expectQuality, setTheme } from './support/quality.mjs';

// Each area adds its routes (every sub-view) once it is on the design system;
// every listed route is checked light and dark, on desktop and on a phone.
const ROUTES = [
  '/#/',
  '/#/?work=prs',
  '/#/?range=7',
  '/#/main',
  '/#/main?window=24h',
  '/#/main?view=incidents',
  '/#/main?view=flaky',
  '/#/health',
  '/#/health?view=parity',
  '/#/health?view=coverage',
  '/#/health?view=mirrors',
  '/#/analytics',
  '/#/analytics?view=latency',
  '/#/analytics?view=slowdowns',
  '/#/analytics?view=nightlies',
  '/#/analytics?view=dns',
  '/#/analytics?view=dns&dns_window=720h&dns_scope=canonical',
  '/#/analytics?view=agent-health',
  '/#/analytics?view=agent-health&agent_window=14d&agent_signal=all',
  '/#/analytics?view=agent-health&agent_window=60d&agent_signal=hard&agent_cofail=1440',
  '/#/analytics?view=agent-health&agent_window=14d&agent_signal=all&agent_node=fixture-3f6181c2',
  '/#/perf',
  '/#/perf?view=accuracy',
  '/#/perf?device=mi300x',
  '/#/perf?model=MiniMaxAI%2FMiniMax-M2.5',
  '/#/perf?view=accuracy&model=MiniMaxAI%2FMiniMax-M2.5',
  '/#/perf?view=accuracy&device=mi300x',
  '/#/perf?model=zai-org%2FGLM-5.2-FP8&device=mi300x',
  '/#/omni',
  '/#/omni?view=live',
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

// Tables on these routes fit a phone: secondary columns hide below sm instead of scrolling.
test('phone tables show every visible column without sideways scrolling', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const [route, ids] of [
    ['/#/', ['nightly-table']],
    ['/#/main', ['main-builds', 'main-incidents-panel', 'main-flaky-panel']],
    ['/#/main?view=incidents', ['main-incidents', 'main-pending', 'main-timeline']],
    ['/#/main?view=flaky', ['main-flaky', 'main-retry']],
    ['/#/analytics', ['priority']],
    ['/#/analytics?view=latency', ['latency-table']],
    ['/#/analytics?view=slowdowns', ['slowdowns-active']],
    ['/#/main', ['main-slowdowns-panel']],
    ['/#/health?view=coverage', ['route-matrix']],
    ['/#/health?view=mirrors', ['mirror-inventory']],
    ['/#/perf', ['perf-overview']],
    ['/#/perf?view=accuracy', ['perf-accuracy']],
    ['/#/omni?view=live', ['omni-live-jobs']],
    ['/#/analytics?view=nightlies', ['nightlies-table']],
  ]) {
    await page.goto(route);
    for (const id of ids) {
      const tables = page.getByTestId(id).locator('table');
      await expect(tables.locator('tbody tr').first()).toBeVisible();
      const overflow = await tables.evaluateAll((nodes) => nodes.map((node) => node.parentElement.scrollWidth - node.parentElement.clientWidth));
      expect(Math.max(...overflow), `${id} on ${route} scrolls sideways`).toBeLessThanOrEqual(0);
    }
  }
});
