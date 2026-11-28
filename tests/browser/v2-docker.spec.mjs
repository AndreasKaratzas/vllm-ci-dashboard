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
  test('summarizes the last 24 hours of vllm/ci: base cache hits, rebuild cost and test image time', async ({ page }) => {
    const errors = collectErrors(page);
    await openView(page);
    const headline = page.getByTestId('docker-headline');
    await expect(headline.locator('[data-slot="kpi-value"]')).toHaveText('50%');
    await expect(headline.locator('[data-slot="proportion-bar"] li')).toHaveText([/^5\s*Reused$/, /^2\s*Partial rebuild/, /^3\s*Full rebuild/]);
    await expect(page.getByTestId('docker-headline-story')).toContainText('1 of the 10 was still rebuilding at collection');
    await expect(headline.locator('[data-slot="kpi-footnote"]')).toHaveText('Too few runs to compare');
    // Day 0 only: the previous 24 hours hold 3 decided base runs, too few for a delta. The one failed job is
    // the retried test image attempt of #32000.
    await expectKpis(page, {
      'base-rebuilds': ['5', '1 partial · 3 full · 1 still running · Too few runs to compare'],
      'rebuild-time': ['8.5 h', /^Median full rebuild 2h 40m · 1 still running \(44m \d\ds so far\), not counted · Too few runs to compare$/],
      'image-p50': ['12m', 'p90 32m · 4 finished builds · Too few runs to compare'],
      failed: ['1', '1 retried · 1 failed · 2 unfinished or canceled · Too few runs to compare'],
    });
    await expect(page.locator('[data-testid="analytics-docker"] [data-direction]')).toHaveCount(0);
    // The fixture was generated on 2026-10-08, long past the 6 hour freshness target.
    await expect(page.getByTestId('docker-stale')).toContainText('Docker build jobs are stale.');
    expect(errors).toEqual([]);
  });

  test('the last hour ends at the collection and counts the running rebuild as a miss', async ({ page }) => {
    await openView(page, '&docker_window=1h');
    await expect(page.getByTestId('docker-window-span')).toContainText('Oct 8, 19:00 – Oct 8, 20:00 UTC');
    await expect(page.getByTestId('docker-headline').locator('[data-slot="kpi-value"]')).toHaveText('67%');
    await expect(page.getByTestId('docker-headline-story')).toContainText('2 of 3');
    await expect(page.getByTestId('docker-stale')).toContainText('Short windows end at the last collection');
    await expect(page.getByTestId('docker-outcomes').locator('[data-slot="bar-plot"] g[class*="-barGroup"]')).toHaveCount(12);
  });

  test('charts base outcomes per bucket, the run time spread and the test image time', async ({ page }) => {
    await openView(page);
    const outcomes = page.getByTestId('docker-outcomes');
    await expect(outcomes.locator('[data-slot="chart-legend"] li')).toHaveCount(3);
    await expect(outcomes.locator('[data-slot="bar-plot"] g[class*="-barGroup"]')).toHaveCount(24);
    const tooltip = page.locator('[class*="-tooltip"]:not([data-slot="chart"]):visible').filter({ hasText: /Oct 8, \d\d:00–\d\d:00 UTC · \d+ runs/ });
    const bars = outcomes.locator('path[class*="-bar"]');
    for (let index = 0; index < (await bars.count()) && !(await tooltip.last().isVisible()); index += 1) {
      const box = await bars.nth(index).boundingBox();
      if (!box || box.width < 2 || box.height < 2) continue;
      await bars.nth(index).hover();
      await page.waitForTimeout(100);
    }
    await expect(tooltip.last()).toBeVisible();
    await page.mouse.move(0, 0);

    const spread = page.getByTestId('docker-spread');
    await expect(page.getByTestId('docker-spread-reused')).toHaveText('n 5 · min 1m 12s · p25 1m 24s · median 1m 24s · p75 1m 24s · p90 1m 26s · max 1m 26s');
    await expect(page.getByTestId('docker-spread-rebuilt')).toHaveText('4 runs: 30m, 2h 30m, 2h 40m, 2h 50m · 1 still rebuilding, not timed');
    await expect(page.getByTestId('docker-spread-image')).toHaveText('4 runs: 10m, 12m, 14m, 32m');
    await expect(spread).toContainText('cache hit under 10 min');
    await expect(spread).toContainText('full rebuild from 2 h');
    // Rows under 5 runs draw each run as a dot.
    await expect(spread.locator('[data-slot="docker-spread-dot"]')).toHaveCount(8);
    await expect(page.getByTestId('docker-daily').locator('[data-slot="area-plot"] svg').first()).toBeVisible();
  });

  test('lists rebuild episodes newest first, each opening its rebuild jobs', async ({ page }) => {
    await openView(page);
    const rows = page.getByTestId('docker-episodes').locator('tbody tr');
    await expect(rows).toHaveCount(2);
    await expect(rows.first()).toContainText('Oct 8, 19:15');
    await expect(rows.first()).toContainText('1 running');
    await expect(rows.first()).toContainText(/44m \d\ds so far/);
    await expect(rows.nth(1)).toContainText('Oct 8, 12:00');
    await expect(rows.nth(1)).toContainText('1h 12m');
    await expect(rows.nth(1).locator('td').nth(2)).toHaveText('4');
    await expect(rows.nth(1).locator('td').nth(3)).toHaveText('3');
    await rows.nth(1).click();
    const sheet = page.getByRole('dialog').last();
    await expect(sheet.locator('h2')).toHaveText('Base rebuild episode · Oct 8, 12:00 UTC');
    await sheet.locator('tbody tr').first().click();
    await expect(page.getByRole('dialog').last().locator('h2')).toHaveText(/^ROCm stack \+ runtime \(base\) · #320\d\d$/);

    // The last hour of main builds only reused the cache.
    await openView(page, '&docker_window=1h&docker_scope=main');
    await expect(page.getByTestId('docker-episodes-empty')).toHaveText('No base rebuilds in the last hour. Every finished run reused the cached image.');
  });

  test('lists running jobs and jobs of at least 10 minutes, newest first, linked to their Buildkite job', async ({ page }) => {
    const errors = collectErrors(page);
    await openView(page, '&docker_window=14d');
    const jobs = page.getByTestId('docker-jobs');
    await expect(jobs).toContainText('34 of 63 rows');
    const rows = jobs.locator('tbody tr');
    await expect(rows).toHaveCount(25);
    await expect(rows.first()).toContainText('Test image');
    await expect(rows.first()).toContainText('Running');
    const first = rows.nth(1);
    await expect(first).toContainText('Test image');
    await expect(first).toContainText('14m');
    const payload = dockerPages();
    const id = payload.jobs.id[payload.jobs.build.indexOf(32077)];
    const build = first.getByRole('link', { name: '#32077' });
    await expect(build).toHaveAttribute('href', `https://buildkite.com/vllm/ci/builds/32077#${id}`);
    await expect(build).toHaveAttribute('target', '_blank');
    for (const href of await jobs.locator('tbody a').evaluateAll((links) => links.map((link) => link.getAttribute('href'))))
      expect(href).toMatch(/^https:\/\/buildkite\.com\/vllm\/(ci|amd-ci)\/builds\/\d+#[0-9a-f-]{36}$/);
    // The retired ci_base step is listed though it feeds no KPI.
    await jobs.getByPlaceholder('Filter step, build or job id…').fill('ensure-ci-base');
    await expect(rows.filter({ hasText: 'ci_base (retired)' })).toHaveCount(1);
    expect(errors).toEqual([]);
  });

  test('a job opens its evidence sheet', async ({ page }) => {
    await openView(page, '&docker_window=14d');
    await page.getByTestId('docker-jobs').locator('tbody tr').filter({ hasText: '#32013' }).click();
    const sheet = page.getByRole('dialog');
    await expect(sheet.locator('h2')).toHaveText('ROCm test image · #32013');
    await expect(sheet).toContainText('vllm/ci · PR build');
    await expect(sheet).toContainText('Final attempt');
    await expect(sheet.getByRole('link', { name: /Buildkite job/ })).toHaveAttribute('href', /^https:\/\/buildkite\.com\/vllm\/ci\/builds\/32013#[0-9a-f-]{36}$/);
  });

  test('window, builds and pipeline controls round-trip through the URL', async ({ page }) => {
    await openView(page);
    for (const id of ['1h', '3h', '6h', '3d', '7d', '14d', '30d']) {
      await choose(page, 'Window', id);
      await expect(page).toHaveURL(new RegExp(`docker_window=${id}$`));
    }
    await expectKpis(page, { 'image-p50': ['25m'], 'base-rebuilds': ['11', '4 partial · 6 full · 1 still running · No earlier period in the 30-day data'] });
    await expect(page.locator('[data-testid="analytics-docker"] [data-direction]')).toHaveCount(0);
    await choose(page, 'Builds', 'main');
    await expect(page).toHaveURL(/docker_scope=main/);
    await choose(page, 'Pipeline', 'vllm/amd-ci');
    await expect(page).toHaveURL(/docker_window=30d&docker_scope=main&docker_pipeline=amd-ci$/);
    await expectKpis(page, { 'image-p50': ['22m'], 'base-rebuilds': ['1', '1 partial · 0 full · No earlier period in the 30-day data'] });
    await expect(page.getByTestId('docker-headline').locator('[data-slot="kpi-value"]')).toHaveText('75%');
    // Defaults leave the URL.
    await choose(page, 'Window', '1d');
    await expect(page).not.toHaveURL(/docker_window/);

    await page.goto(`${VIEW}&docker_window=7d&docker_scope=pr&docker_pipeline=all`);
    for (const [group, name] of [['Window', '7d'], ['Builds', 'PRs'], ['Pipeline', 'Both']]) {
      const toggles = page.getByRole('group', { name: group });
      await expect(toggles.getByRole('radio', { name, exact: true }).or(toggles.getByRole('button', { name, exact: true }))).toHaveAttribute('data-state', 'on');
    }
    await expectKpis(page, { 'image-p50': ['18m'], 'base-rebuilds': ['7'] });
    await choose(page, 'Pipeline', 'vllm/amd-ci');
    await expect(page.getByTestId('docker-empty')).toContainText('No docker build jobs in this window.');

    // An unknown window reads as the default and is not written back.
    await page.goto(`${VIEW}&docker_window=2h`);
    const toggles = page.getByRole('group', { name: 'Window' });
    await expect(toggles.getByRole('radio', { name: '1d', exact: true }).or(toggles.getByRole('button', { name: '1d', exact: true }))).toHaveAttribute('data-state', 'on');
    await expect(page.getByTestId('docker-headline').locator('[data-slot="kpi-value"]')).toHaveText('50%');
    await expect(page).not.toHaveURL(/docker_window=1d/);
  });

  test('fits a phone, with selects for the controls', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openView(page);
    await expect(page.getByTestId('docker-jobs').locator('tbody tr').first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
    await page.getByRole('combobox', { name: 'Window' }).click();
    await expect(page.getByRole('option')).toHaveText(['Last hour', 'Last 3 hours', 'Last 6 hours', 'Last 24 hours', 'Last 3 days', 'Last 7 days', 'Last 14 days', 'Last 30 days']);
    await page.getByRole('option', { name: 'Last hour' }).click();
    await expect(page).toHaveURL(/docker_window=1h/);
    await expect(page.getByTestId('docker-headline').locator('[data-slot="kpi-value"]')).toHaveText('67%');
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
    await expect(kpi(page, 'image-p50').locator('[data-slot="kpi-value"]')).toHaveText('12m');
    await page.unrouteAll();
    await routeDocker(page, { live: dockerSeed('2026-10-30T00:00:00Z') });
    await page.reload();
    await expect(page.getByTestId('docker-not-collected')).toBeVisible();
  });
});

const SHEETS = [
  ['docker job', `${VIEW}`, (page) => page.getByTestId('docker-jobs').locator('tbody tr').first().click()],
  ['docker episode', `${VIEW}&docker_window=30d`, (page) => page.getByTestId('docker-episodes').locator('tbody tr').first().click()],
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
