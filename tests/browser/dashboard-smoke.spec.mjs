import { expect, test } from '@playwright/test';

const PUBLIC_VIEWS = [
  { name: 'trajectory workload', url: '/#ci-hotness', tab: 'ci-hotness', heading: 'CI Workload Trajectory' },
  { name: 'home', url: '/#projects', tab: 'projects', heading: 'Command Center' },
  ...['overview', 'targets', 'gating', 'coverage', 'diagnostics'].map(view => ({
    name: `health ${view}`,
    url: `/?ops_health_view=${view}#ci-health`,
    tab: 'ci-health',
    heading: 'CI Health',
    watchdog: view === 'overview',
  })),
  ...['groups', 'flakes', 'retries', 'latency', 'nightlies', 'agent-health'].map(view => ({
    name: `analytics ${view}`,
    url: `/?ops_analytics_view=${view}#ci-analytics`,
    tab: 'ci-analytics',
    heading: 'CI Analytics',
  })),
  ...['performance', 'accuracy'].map(view => ({
    name: `performance ${view}`,
    url: `/?ops_perf_view=${view}#ci-perf-eval`,
    tab: 'ci-perf-eval',
    heading: 'Performance & Evaluation',
  })),
  ...['current', 'history', 'jobs'].map(view => ({
    name: `queue ${view}`,
    url: `/?ops_queue_view=${view}#ci-queue`,
    tab: 'ci-queue',
    heading: 'Queue Monitor',
  })),
  {
    name: 'trajectory capacity',
    url: '/?ops_trajectory_view=capacity#ci-hotness',
    tab: 'ci-hotness',
    heading: 'CI Workload Trajectory',
  },
  { name: 'omni', url: '/#ci-omni', tab: 'ci-omni', heading: 'Omni CI' },
];

test.describe('public dashboard routes', () => {
  for (const route of PUBLIC_VIEWS) {
    test(route.name, async ({ page }) => {
      const browserErrors = [];
      page.on('pageerror', error => browserErrors.push(`pageerror: ${error.stack || error.message}`));
      page.on('console', message => {
        if (message.type() === 'error') browserErrors.push(`console: ${message.text()}`);
      });

      await page.goto(route.url, { waitUntil: 'domcontentloaded' });

      const panel = page.locator(`#tab-${route.tab}`);
      await expect(panel).toHaveClass(/\bactive\b/);
      await expect(panel.locator('h1.ops-page-title')).toHaveText(route.heading);
      await expect(panel.locator('.ops-loading')).toHaveCount(0);
      await expect(panel.locator('.ops-error')).toHaveCount(0);

      // Deep links defer the Home payload briefly. Let that background work
      // settle so its failures are included in the runtime-error assertion.
      await page.waitForTimeout(route.watchdog ? 12_500 : 2_000);

      await expect(page.locator('#last-updated')).not.toHaveText('Dashboard startup failed');
      expect(browserErrors, browserErrors.join('\n')).toEqual([]);
    });
  }
});
