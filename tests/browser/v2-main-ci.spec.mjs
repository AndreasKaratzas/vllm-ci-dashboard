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
    const failing = sheet.locator('[data-slot=evidence-table]').filter({ has: page.getByRole('heading', { name: 'Failing AMD groups' }) }).locator('tbody tr');
    await expect(failing).toHaveCount(3);
    await expect(failing.nth(0)).toContainText('Broken Group');
    await expect(failing.nth(1)).toContainText('Routed Beta');
    await expect(failing.nth(2)).toContainText('Soft fail');
    await expect(failing.nth(0).getByRole('link')).toHaveAttribute('href', /^https:\/\/buildkite\.com\/vllm\/ci\/builds\/31026\/steps\/canvas\?jid=/);
    expect(errors).toEqual([]);
  });

  test('compares the 24-hour rate with the 24 hours before and opens the builds behind it', async ({ page }) => {
    await page.goto('/#/main');
    const rate = page.getByTestId('kpi-main-rate');
    await expect(rate.locator('[data-slot=kpi-value]')).toHaveText('66.7%');
    await expect(rate).toContainText('Below 95%');
    await expect(rate).toContainText('-4.2 pts');
    await rate.locator('[data-kpi-action]').click();
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText('Main builds · last 24 hours');
    await expect(sheet.locator('[data-slot=evidence-stat-value]').first()).toHaveText('66.7%');
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

  test('dates incidents, marks new ones and rolls up a shared first bad commit', async ({ page }) => {
    const section = await (await page.request.get('/data/vllm/ci/operations_v2/main_ci.json')).json();
    const active = section.main_ci.incidents.active;
    const routed = active.find((row) => row.name.endsWith('Routed Beta'));
    const broken = active.find((row) => row.name.endsWith('Broken Group'));
    Object.assign(broken, { bad_commit: routed.bad_commit, bad_build_number: routed.bad_build_number, good_commit: 'a'.repeat(40), good_build_number: 1, commit_range_status: 'candidate', compare_url: `https://github.com/vllm-project/vllm/compare/${'a'.repeat(40)}...${routed.bad_commit}` });
    await routeMainCi(page, { shell: () => {}, section });
    await page.goto('/#/main?view=incidents');
    const incidents = page.getByTestId('main-incidents');
    await expect(incidents.locator('thead')).toContainText('Opened');
    await expect(incidents.locator('thead')).not.toContainText('Severity');
    const ranges = page.getByTestId('main-suspect-ranges');
    await expect(ranges).toContainText(`2 incidents first failed at 1 commit`);
    await expect(ranges.locator('tbody tr')).toHaveCount(1);
    await ranges.locator('tbody tr td').nth(1).click();
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText(`2 incidents first failed at #${routed.bad_build_number}`);
    await expect(sheet.getByRole('link', { name: 'Compare commits' })).toHaveAttribute('href', routed.compare_url);
    await sheet.locator('tbody tr').first().click();
    await expect(page.getByRole('dialog').last()).toContainText('incident on main');
  });

  test('marks an incident opened in the last 24 hours as new', async ({ page }) => {
    const section = await (await page.request.get('/data/vllm/ci/operations_v2/main_ci.json')).json();
    // New means opened in the last 24 hours of wall-clock time, so a stale snapshot shows nothing new.
    const opened = new Date(Date.now() - 3 * 3_600_000).toISOString();
    const routed = section.main_ci.incidents.active.find((row) => row.name.endsWith('Routed Beta'));
    section.main_ci.timeline.unshift({ event: 'opened', group_id: routed.group_id, name: routed.name, build_number: routed.bad_build_number, at: opened, severity: 'hard' });
    await routeMainCi(page, { shell: () => {}, section });
    await page.goto('/#/main?view=incidents');
    await expect(page.getByTestId('main-incident-summary')).toContainText('1 new in the last 24 hours');
    const row = page.getByTestId('main-incidents').locator('tbody tr').first();
    await expect(row).toContainText('Routed Beta');
    await expect(row).toContainText('New · 3h ago');
    await expect(page.getByTestId('main-incidents').locator('tbody tr').nth(1)).not.toContainText('New');
  });

  test('says when the build list is partial', async ({ page }) => {
    const section = await (await page.request.get('/data/vllm/ci/operations_v2/main_ci.json')).json();
    section.main_ci.cohort.build_count = 40;
    section.main_ci.publication_retention = { builds: { source: 40, published: 33, omitted: 7 } };
    await routeMainCi(page, { shell: () => {}, section });
    await page.goto('/#/main');
    await expect(page.getByText('The section keeps the newest 33 of 40 builds')).toBeVisible();
  });

  test('ranks flaky and retry-recovering groups, each alerting on its own criterion', async ({ page }) => {
    await page.goto('/#/main?view=flaky');
    await expect(page.getByTestId('main-flaky').locator('tbody tr').first()).toContainText('Attention Kernels Shard 2');
    await expect(page.getByTestId('main-flaky').locator('tbody tr').first()).toContainText('Alerting');
    await expect(page.getByTestId('main-flaky')).toContainText('1 alerting at 3 flips');
    // One flip is below the flips threshold whatever the group's retries do.
    await expect(page.getByTestId('main-flaky').locator('tbody tr').filter({ hasText: 'Attention Kernels Shard 1' })).toContainText('Below threshold');
    await expect(page.getByTestId('main-retry')).toContainText('1 alerting at 3 recoveries');
    await expect(page.getByTestId('main-retry').locator('tbody tr').first()).toContainText('Alerting');
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

  test('Home keeps main CI when the nightly section fails', async ({ page }) => {
    await page.route('**/operations_v2/nightly.json*', (route) => route.fulfill({ status: 500, body: 'down' }));
    await page.goto('/');
    await expect(page.getByTestId('home-main').getByTestId('kpi-main-latest')).toContainText('#31027');
    await expect(page.getByTestId('main-trend')).toBeVisible();
    await expect(page.locator('[data-slot=async-error]')).toBeVisible();
    await expect(page.getByTestId('kpi-nightly')).toHaveCount(0);
  });

  test('CI Health leads with the main CI headline and links to it', async ({ page }) => {
    await page.goto('/#/health');
    const signal = page.getByTestId('main-ci-signal');
    await expect(signal).toContainText('66.7%');
    await expect(signal).toContainText('Below 95%');
    await expect(signal).toContainText('#31027');
    await signal.getByRole('link', { name: /Open Main CI/ }).click();
    await expect(page).toHaveURL(/#\/main$/);
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
