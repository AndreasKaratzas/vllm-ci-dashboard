import { expect, test } from '@playwright/test';

import { dockerPages, dockerSeed, routeDocker } from './support/docker.mjs';
import { collectErrors, expectQuality, setTheme } from './support/quality.mjs';

// CI Analytics "Docker builds" over the synthetic docker_builds.json of build_fixture_site.py
// (docker_jobs() spells out every job the expected values below derive from).
const VIEW = '/#/analytics?view=docker';
const kpi = (page, id) => page.getByTestId(`docker-kpi-${id}`);
const choose = async (page, group, name) => {
  const toggles = page.getByRole('group', { name: group });
  await toggles.getByRole('radio', { name, exact: true }).or(toggles.getByRole('button', { name, exact: true })).click();
};

async function openView(page, query = '') {
  await routeDocker(page);
  await page.goto(`${VIEW}${query}`);
  await expect(page.getByTestId('docker-kpis')).toBeVisible();
}

async function expectKpis(page, expected) {
  for (const [id, [value, footnote]] of Object.entries(expected)) {
    await expect(kpi(page, id).locator('[data-slot="kpi-value"]')).toHaveText(value);
    if (footnote) await expect(kpi(page, id).locator('[data-slot="kpi-footnote"]')).toHaveText(footnote);
  }
}

test.describe('CI Analytics Docker builds', () => {
  test('summarizes the last 14 days of vllm/ci: test image percentiles and base rebuilds', async ({ page }) => {
    const errors = collectErrors(page);
    await openView(page);
    await expectKpis(page, {
      'image-p50': ['25m', '20 finished runs'],
      'image-p90': ['45m', 'Longest 1h'],
      'image-jobs': ['22', '1 retried · 2 failed · 1 unfinished or canceled'],
      'base-p50': ['1m 26s', 'p90 1h 35m · 29 finished runs'],
      'base-share': ['17%', '3 partial, 2 full · 24 reused'],
      'base-full': ['2', 'Median 2h 30m each'],
    });
    // The fixture was generated on 2026-10-08, long past the 6 hour freshness target.
    await expect(page.getByTestId('docker-stale')).toContainText('Docker build jobs are stale.');
    expect(errors).toEqual([]);
  });

  test('charts the daily test image run time and every base rebuild', async ({ page }) => {
    await openView(page);
    await expect(page.getByTestId('docker-daily').locator('[data-slot="area-plot"] svg').first()).toBeVisible();
    const rebuilds = page.getByTestId('docker-rebuilds');
    await expect(rebuilds.getByTestId('docker-reused')).toHaveText('24 of 29 runs reused the cached image and are not drawn.');
    await expect(rebuilds.locator('[data-slot="docker-rebuild-plot"] g[class*="-point"]:not([class*="-point-group"])')).toHaveCount(5);
    await expect(rebuilds).toContainText('Full rebuild from 2 h');
    // Every drawn rebuild opens its job.
    await rebuilds.getByRole('button', { name: 'Inspect Base image rebuilds' }).click();
    await expect(page.getByRole('menuitem')).toHaveCount(5);
    await page.getByRole('menuitem').filter({ hasText: 'Full rebuild, 5h' }).click();
    const sheet = page.getByRole('dialog');
    await expect(sheet.locator('h2')).toHaveText('ROCm stack + runtime (base) · #32050');
    await expect(sheet).toContainText('Full rebuild');
  });

  test('lists running jobs and jobs of at least 10 minutes, newest first, linked to their Buildkite job', async ({ page }) => {
    const errors = collectErrors(page);
    await openView(page);
    const jobs = page.getByTestId('docker-jobs');
    await expect(jobs).toContainText('27 of 53 rows');
    const rows = jobs.locator('tbody tr');
    await expect(rows).toHaveCount(25);
    await expect(rows.first()).toContainText('Test image');
    await expect(rows.first()).toContainText('Running');
    const first = rows.nth(1);
    await expect(first).toContainText('Test image');
    await expect(first).toContainText('32m');
    const payload = dockerPages();
    const id = payload.jobs.id[payload.jobs.build.indexOf(32013)];
    const build = first.getByRole('link', { name: '#32013' });
    await expect(build).toHaveAttribute('href', `https://buildkite.com/vllm/ci/builds/32013#${id}`);
    await expect(build).toHaveAttribute('target', '_blank');
    for (const href of await jobs.locator('tbody a').evaluateAll((links) => links.map((link) => link.getAttribute('href'))))
      expect(href).toMatch(/^https:\/\/buildkite\.com\/vllm\/(ci|amd-ci)\/builds\/\d+#[0-9a-f-]{36}$/);
    // The retired ci_base step is listed though it feeds no KPI.
    await expect(rows.filter({ hasText: 'ci_base (retired)' })).toHaveCount(1);
    expect(errors).toEqual([]);
  });

  test('a job opens its evidence sheet', async ({ page }) => {
    await openView(page);
    await page.getByTestId('docker-jobs').locator('tbody tr').filter({ hasText: '#32013' }).click();
    const sheet = page.getByRole('dialog');
    await expect(sheet.locator('h2')).toHaveText('ROCm test image · #32013');
    await expect(sheet).toContainText('vllm/ci · PR build');
    await expect(sheet).toContainText('Final attempt');
    await expect(sheet.getByRole('link', { name: /Buildkite job/ })).toHaveAttribute('href', /^https:\/\/buildkite\.com\/vllm\/ci\/builds\/32013#[0-9a-f-]{36}$/);
  });

  test('window, builds and pipeline controls round-trip through the URL', async ({ page }) => {
    await openView(page);
    await choose(page, 'Window', '30d');
    await expect(page).toHaveURL(/docker_window=30d/);
    await expectKpis(page, { 'image-p50': ['26m'], 'base-full': ['3', 'Median 4h each'] });
    await choose(page, 'Builds', 'main');
    await expect(page).toHaveURL(/docker_scope=main/);
    await choose(page, 'Pipeline', 'vllm/amd-ci');
    await expect(page).toHaveURL(/docker_window=30d&docker_scope=main&docker_pipeline=amd-ci$/);
    await expectKpis(page, { 'image-p50': ['22m'], 'image-jobs': ['3'], 'base-share': ['25%', '1 partial, 0 full · 3 reused'] });
    // Defaults leave the URL.
    await choose(page, 'Window', '14d');
    await expect(page).not.toHaveURL(/docker_window/);

    await page.goto(`${VIEW}&docker_window=7d&docker_scope=pr&docker_pipeline=all`);
    for (const [group, name] of [['Window', '7d'], ['Builds', 'PRs'], ['Pipeline', 'Both']]) {
      const toggles = page.getByRole('group', { name: group });
      await expect(toggles.getByRole('radio', { name, exact: true }).or(toggles.getByRole('button', { name, exact: true }))).toHaveAttribute('data-state', 'on');
    }
    await expectKpis(page, { 'image-p50': ['18m'], 'image-jobs': ['7'], 'base-full': ['1'] });
    await choose(page, 'Pipeline', 'vllm/amd-ci');
    await expect(page.getByTestId('docker-empty')).toContainText('No docker build jobs in this window.');
  });

  test('fits a phone, with selects for the controls', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openView(page);
    await expect(page.getByTestId('docker-jobs').locator('tbody tr').first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
    await page.getByRole('combobox', { name: 'Window' }).click();
    await page.getByRole('option', { name: 'Last 30 days' }).click();
    await expect(page).toHaveURL(/docker_window=30d/);
    await expect(kpi(page, 'image-p50').locator('[data-slot="kpi-value"]')).toHaveText('26m');
  });

  test('says the jobs are not collected yet while only the seed is published', async ({ page }) => {
    const seed = dockerSeed('2026-10-30T00:00:00Z');
    await routeDocker(page, { live: seed, pages: seed });
    await page.goto(VIEW);
    await expect(page.getByTestId('docker-not-collected')).toContainText('Docker build jobs have not been collected yet.');
    await expect(page.getByTestId('docker-not-collected')).toContainText('Coverage not_collected');
    await expect(page.getByTestId('docker-kpis')).toHaveCount(0);
    await expect(page.getByRole('button', { name: /^Not collected yet/ })).toBeVisible();
  });

  test('the newer of the live branch and Pages is shown', async ({ page }) => {
    await routeDocker(page, { live: dockerSeed('2026-10-01T00:00:00Z') });
    await page.goto(VIEW);
    await expect(kpi(page, 'image-p50').locator('[data-slot="kpi-value"]')).toHaveText('25m');
    await page.unrouteAll();
    await routeDocker(page, { live: dockerSeed('2026-10-30T00:00:00Z') });
    await page.reload();
    await expect(page.getByTestId('docker-not-collected')).toBeVisible();
  });
});

const SHEETS = [
  ['docker job', `${VIEW}`, (page) => page.getByTestId('docker-jobs').locator('tbody tr').first().click()],
  [
    'docker rebuild',
    `${VIEW}&docker_window=30d`,
    async (page) => {
      await page.getByRole('button', { name: 'Inspect Base image rebuilds' }).click();
      await page.getByRole('menuitem').first().click();
    },
  ],
];
test.describe('Docker builds sheets meet the quality gates', () => {
  for (const theme of ['light', 'dark']) {
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
      for (const [name, url, open] of SHEETS) {
        test(`${name} (${theme}, ${viewport.width}px)`, async ({ page }) => {
          await setTheme(page, theme);
          await page.setViewportSize(viewport);
          await routeDocker(page);
          const errors = collectErrors(page);
          await page.goto(url);
          await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
          await open(page);
          await expect(page.getByRole('dialog')).toHaveAttribute('data-state', 'open');
          await expectQuality(page, errors);
        });
      }
    }
  }
});
