import { createHash } from 'node:crypto';

import { expect, test } from '@playwright/test';

import { collectErrors } from './support/quality.mjs';

// Fixture facts (tests/browser/build_fixture_site.py): 33 main builds, the newest #31027 passed;
// #31026 failed Routed Beta and Broken Group hard and soft-failed Attention Kernels Shard 1;
// two hard incidents tracked in issue #512, one pending soft failure; Attention Kernels Shard 2
// flips and Basic Models passes only after a retry.

/** Rewrites the manifest (and optionally serves a main_ci section body with its digest). */
async function routeMainCi(page, { shell, section }) {
  const text = section === undefined ? null : JSON.stringify(section);
  await page.route('**/operations_v2_manifest.json*', async (route) => {
    const response = await route.fetch();
    const manifest = await response.json();
    shell(manifest);
    if (text !== null && manifest.sections.main_ci) {
      manifest.sections.main_ci.sha256 = createHash('sha256').update(text).digest('hex');
      delete manifest.sections.main_ci.generation;
    }
    await route.fulfill({ response, json: manifest });
  });
  if (text !== null) await page.route('**/operations_v2/main_ci.json*', (route) => route.fulfill({ body: text, contentType: 'application/json' }));
}

test.describe('Main CI', () => {
  test('leads with the latest main builds and opens a build with its failing groups', async ({ page }) => {
    const errors = collectErrors(page);
    await page.goto('/#/main');
    await expect(page.getByRole('heading', { name: 'Main CI', level: 1 })).toBeVisible();
    await expect(page.getByTestId('kpi-main-latest').locator('[data-slot=kpi-value]')).toHaveText('#31027');
    await expect(page.getByTestId('kpi-main-incidents').locator('[data-slot=kpi-value]')).toHaveText(/^2\s*groups$/);
    await expect(page.getByTestId('kpi-main-flaky').locator('[data-slot=kpi-value]')).toHaveText(/^1\s*alerting$/);
    await expect(page.getByTestId('main-trend').getByRole('img', { name: 'AMD pass rate on main' })).toBeVisible();

    const tiles = page.locator('[data-slot=main-build-tile]');
    await expect(tiles).toHaveCount(33);
    await expect(tiles.last()).toHaveAttribute('aria-label', /^Build #31027/);
    await expect(tiles.last()).toHaveCSS('cursor', 'pointer');
    await page.getByRole('button', { name: /^Build #31026/ }).click();
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText('Main build #31026');
    await expect(sheet.getByRole('link', { name: 'Open Buildkite build' })).toHaveAttribute('href', 'https://buildkite.com/vllm/ci/builds/31026');
    const failing = sheet.getByTestId('main-build-failing-groups').locator('tbody tr');
    await expect(failing).toHaveCount(3);
    await expect(failing.nth(0)).toContainText('Broken Group');
    await expect(failing.nth(1)).toContainText('Routed Beta');
    await expect(failing.nth(2)).toContainText('Soft fail');
    await expect(failing.nth(0).getByRole('link')).toHaveAttribute('href', /^https:\/\/buildkite\.com\/vllm\/ci\/builds\/31026\/steps\/canvas\?jid=/);
    expect(errors).toEqual([]);
  });

  test('switches the trend window and opens the builds behind a point', async ({ page }) => {
    await page.goto('/#/main');
    const trend = page.getByTestId('main-trend');
    await trend.getByRole('group', { name: 'Main CI window' }).getByRole('button', { name: '24h' }).click();
    await expect(page).toHaveURL(/window=24h/);
    await expect(trend).toContainText('Last 24 hours');
    await trend.getByRole('button', { name: /Inspect/ }).click();
    await page.getByRole('menuitem').last().click();
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText('Main builds ·');
    await sheet.locator('tbody tr').first().click();
    await expect(page.getByRole('dialog').last()).toContainText(/Main build #\d+/);
  });

  test('a shared build link opens its sheet and closing it clears the link', async ({ page }) => {
    await page.goto('/#/main?build=31026');
    await expect(page.getByRole('dialog')).toContainText('Main build #31026');
    await page.keyboard.press('Escape');
    await expect(page).toHaveURL(/#\/main$/);
  });

  test('lists incidents with their commits and the managed issue', async ({ page }) => {
    await page.goto('/#/main?view=incidents');
    await expect(page.getByTestId('main-issue-link')).toHaveAttribute('href', 'https://github.com/AndreasKaratzas/vllm-ci-dashboard/issues/512');
    const rows = page.getByTestId('main-incidents').locator('tbody tr');
    await expect(rows).toHaveCount(2);
    await expect(page.getByTestId('main-pending')).toContainText('Attention Kernels Shard 1');
    await rows.filter({ hasText: 'Routed Beta' }).getByRole('cell').first().click();
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText('Hard fail incident on main');
    await expect(sheet.getByRole('link', { name: 'Compare commits' })).toHaveAttribute('href', /^https:\/\/github\.com\/vllm-project\/vllm\/compare\//);
    await expect(sheet.getByRole('link', { name: 'Managed issue #512' })).toBeVisible();
    await expect(sheet).toContainText('git bisect start');
    await expect(page).toHaveURL(/view=incidents/);
  });

  test('ranks flaky and retry-recovering groups', async ({ page }) => {
    await page.goto('/#/main?view=flaky');
    await expect(page.getByTestId('main-flaky').locator('tbody tr').first()).toContainText('Attention Kernels Shard 2');
    await expect(page.getByTestId('main-flaky').locator('tbody tr').first()).toContainText('Alerting');
    await expect(page.getByTestId('main-retry').locator('tbody tr').first()).toContainText('Basic Models (Other)');
    await page.getByTestId('main-retry').locator('tbody tr').first().click();
    await expect(page.getByRole('dialog')).toContainText('Passes only after a retry on main');
  });

  test('Home leads with main CI and keeps the nightly second', async ({ page }) => {
    await page.goto('/');
    const main = page.getByTestId('home-main');
    await expect(main.getByTestId('kpi-main-latest')).toContainText('#31027');
    await expect(main.getByTestId('main-trend')).toBeVisible();
    const mainTop = (await main.boundingBox()).y;
    const nightlyTop = (await page.getByTestId('home-nightly-heading').boundingBox()).y;
    expect(mainTop).toBeLessThan(nightlyTop);
    await main.getByTestId('kpi-main-incidents').getByRole('link').click();
    await expect(page).toHaveURL(/#\/main\?view=incidents$/);
  });

  test('the command palette opens main builds and incidents', async ({ page }) => {
    await page.goto('/');
    await page.keyboard.press('Control+k');
    await page.getByPlaceholder(/Search views/).fill('#31026');
    await page.getByRole('group', { name: 'Main builds' }).getByRole('option').first().click();
    await expect(page).toHaveURL(/#\/main\?build=31026$/);
    await expect(page.getByRole('dialog')).toContainText('Main build #31026');
    await page.keyboard.press('Escape');

    await page.keyboard.press('Control+k');
    await page.getByPlaceholder(/Search views/).fill('Routed Beta');
    await page.getByRole('group', { name: 'Main incidents' }).getByRole('option').click();
    await expect(page).toHaveURL(/#\/main\?view=incidents&incident=/);
    await expect(page.getByRole('dialog')).toContainText('Routed Beta');
  });

  test('the header dates core data from the main CI clock', async ({ page }) => {
    await page.goto('/');
    const core = page.locator('[data-slot=header-freshness]').first();
    await expect(core).toContainText(/Core data\s*\d+d ago/);
    await expect(core.locator('[data-slot=header-freshness-missing]')).toHaveCount(0);
  });

  test('the header keeps the core age and names a missing main CI clock', async ({ page }) => {
    await routeMainCi(page, {
      shell: (manifest) => {
        manifest.shell.sources.main_ci = { path: 'analytics.json', timestamp: null, unavailable_reason: 'strict_all_main_reliability_unavailable' };
        manifest.shell.main_ci = { available: false, source_generated_at: null };
      },
      section: { main_ci: { available: false, unavailable_reason: 'strict_all_main_reliability_unavailable', summary: {}, builds: [] } },
    });
    await page.goto('/#/main');
    const core = page.locator('[data-slot=header-freshness]').first();
    await expect(core).toHaveAttribute('data-freshness', 'stale');
    await expect(core).toContainText(/Core data\s*\d+d ago/);
    await expect(core.locator('[data-slot=header-freshness-missing]')).toHaveText('· 1 missing');
    await expect(page.getByTestId('main-unavailable')).toContainText('Strict all main reliability unavailable');
    await page.goto('/');
    await expect(page.getByTestId('home-main-unavailable')).toBeVisible();
    await expect(page.getByTestId('kpi-nightly')).toBeVisible();
  });

  test('a manifest without the main CI section shows it as unpublished', async ({ page }) => {
    const errors = collectErrors(page);
    await routeMainCi(page, {
      shell: (manifest) => {
        delete manifest.sections.main_ci;
        delete manifest.shell.main_ci;
        delete manifest.shell.sources.main_ci;
      },
    });
    await page.goto('/#/main');
    await expect(page.getByTestId('main-unpublished')).toBeVisible();
    await page.goto('/');
    await expect(page.getByTestId('kpi-nightly')).toBeVisible();
    await expect(page.getByTestId('kpi-main-latest')).toHaveCount(0);
    expect(errors.filter((error) => !/main_ci/.test(error))).toEqual([]);
  });
});
