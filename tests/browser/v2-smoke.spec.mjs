import { expect, test } from '@playwright/test';

import { readGolden } from './golden/read.mjs';

import { collectErrors, expectQuality, setTheme } from './support/quality.mjs';

const COMMAND_CENTER = { name: 'Command Center', level: 1 };

test.describe('Home', () => {
  test('renders the fixture nightlies and opens build evidence', async ({ page }) => {
    const errors = collectErrors(page);

    await page.goto('/');
    await expect(page.getByRole('heading', COMMAND_CENTER)).toBeVisible();
    await expect(page.getByTestId('kpi-nightly')).toContainText('Hard failures');
    await expect(page.getByTestId('kpi-nightly')).toContainText('#30005 - 4 pass - 0 soft - 2 hard');
    await expect(page.getByTestId('kpi-pass-rate')).toContainText('66.7%');
    await expect(page.getByTestId('kpi-parity')).toContainText('90.9%');
    await expect(page.getByTestId('kpi-attention')).toContainText('2');
    await expect(page.getByTestId('nightly-trend')).toContainText('AMD nightly pass rate');
    await expect(page.getByTestId('nightly-trend').getByRole('img', { name: 'AMD nightly pass rate' })).toBeVisible();
    await expect(page.getByTestId('nightly-movement-chart')).toBeVisible();

    const rows = page.getByTestId('nightly-table').locator('tbody tr');
    await expect(rows).toHaveCount(5);
    await expect(rows.first()).toContainText('#30005');
    await expect(rows.first().getByRole('link', { name: '#30005' })).toHaveAttribute(
      'href',
      /^https:\/\/buildkite\.com\/vllm\/ci\/builds\/30005$/,
    );

    await rows.first().getByRole('cell').nth(1).click();
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText('AMD build #30005');
    await expect(sheet).toContainText('Job variants observed');
    await expect(sheet.locator('tbody tr')).toHaveCount(2);

    expect(errors).toEqual([]);
  });

  test('a count opens just its job variants', async ({ page }) => {
    await page.goto('/');
    const first = page.getByTestId('nightly-table').locator('tbody tr').first();
    await first.getByRole('button', { name: '2' }).first().click();
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText('Current hard failures evidence');
    await expect(sheet.locator('tbody tr')).toHaveCount(2);
  });

  test('needs-attention signals drill into the latest nightly or route to their view', async ({ page }) => {
    await page.goto('/');
    const signals = page.getByTestId('attention-row');
    await expect(signals).toHaveCount(2);
    await signals.filter({ hasText: 'Hard-failed groups' }).click();
    await expect(page.getByRole('dialog')).toContainText('Hard-failed groups in the latest AMD nightly');
    await page.keyboard.press('Escape');
    await signals.filter({ hasText: 'not passing every route' }).click();
    await expect(page).toHaveURL(/#\/health$/);
  });

  test('the pass-rate chart has a keyboard Inspect path to build evidence', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Inspect AMD nightly pass rate' }).click();
    await page.getByRole('menuitem', { name: /Nightly #30003/ }).click();
    await expect(page.getByRole('dialog')).toContainText('AMD build #30003');
  });

  test('the engineering workbench switches between issues and pull requests in the URL', async ({ page }) => {
    await page.goto('/');
    const workbench = page.getByTestId('workbench');
    await expect(workbench.locator('tbody tr')).toHaveCount(3);
    await workbench.getByRole('button', { name: /^PRs/ }).click();
    await expect(page).toHaveURL(/work=prs/);
    await expect(workbench).toContainText('No open ROCm CI pull requests.');
  });
});

test.describe('App shell', () => {
  test('the command palette jumps between views', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', COMMAND_CENTER)).toBeVisible();
    await page.keyboard.press('Control+k');
    await page.getByPlaceholder(/Type a view/).fill('analytics');
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/#\/analytics$/);
  });

  test("the command palette opens a sub-view and a nightly's evidence", async ({ page }) => {
    await page.goto('/#/analytics');
    await page.keyboard.press('Control+k');
    await page.getByRole('option', { name: /Upstream parity/ }).click();
    await expect(page).toHaveURL(/#\/health\?view=parity$/);
    await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toContainText('CI Health');
    await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toContainText('Upstream parity');

    await page.keyboard.press('Control+k');
    await page.getByRole('option', { name: /#30004/ }).click();
    await expect(page).toHaveURL(/#\/\?build=30004$/);
    await expect(page.getByRole('dialog')).toContainText('AMD build #30004');
    await page.keyboard.press('Escape');
    await expect(page).toHaveURL(/#\/$/);
  });

  test('the sidebar marks the page, lists its sections and collapses to icons', async ({ page }) => {
    await page.goto('/#/analytics?view=dns');
    const sidebar = page.locator('[data-sidebar=sidebar]').first();
    await expect(sidebar.getByRole('link', { name: 'CI Analytics' })).toHaveAttribute('aria-current', 'page');
    await expect(sidebar.getByRole('link', { name: 'DNS health' })).toHaveAttribute('data-active', 'true');
    await expect(sidebar).not.toContainText('Classic dashboard');
    await page.getByRole('button', { name: 'Toggle Sidebar' }).first().click();
    await expect(page.locator('[data-collapsible="icon"]')).toHaveCount(1);
  });

  test('the header freshness chips open the Data freshness sheet', async ({ page }) => {
    await page.goto('/');
    const chips = page.locator('[data-slot=header-freshness]');
    await expect(chips).toHaveCount(2);
    await expect(chips.first()).toContainText('Core data');
    await expect(chips.first()).toHaveAttribute('data-freshness', 'stale');
    await chips.first().click();
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText('Data freshness');
    await expect(sheet).toContainText('CI analytics');
    await expect(sheet).toContainText('Perf eval');
  });
});

const SHEETS = {
  'build evidence': async (page) => page.getByTestId('nightly-table').locator('tbody tr').first().getByRole('cell').nth(1).click(),
  'attention evidence': async (page) => page.getByTestId('attention-row').first().click(),
  'data freshness': async (page) => page.locator('[data-slot=header-freshness]').first().click(),
};

for (const theme of ['light', 'dark']) {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    for (const [name, open] of Object.entries(SHEETS)) {
      test(`the ${name} sheet meets the quality gates (${theme}, ${viewport.width}px)`, async ({ page }) => {
        await setTheme(page, theme);
        await page.setViewportSize(viewport);
        const errors = collectErrors(page);
        await page.goto('/');
        await expect(page.getByRole('heading', COMMAND_CENTER)).toBeVisible();
        await open(page);
        await expect(page.getByRole('dialog')).toBeVisible();
        await page.waitForTimeout(400);
        await expectQuality(page, errors);
      });
    }
  }
}

const golden = readGolden('smoke');

test.describe('CI Health matches the golden page facts', () => {
  test('Overview: logical health, outcomes, and failure observations', async ({ page }) => {
    const { rate, ratio, meta, counts, failures } = golden.overview;
    await page.goto('/#/health');
    const logical = page.getByTestId('logical-ring');
    await expect(logical).toContainText(rate);
    await expect(logical).toContainText(ratio);
    await expect(logical).toContainText(meta);
    for (const [label, count] of Object.entries(counts)) {
      await expect(page.getByRole('button', { name: new RegExp(`^${count}\\s*${label}$`) })).toBeVisible();
    }
    const v2Failures = await page.locator('table').last().locator('tbody tr button').allInnerTexts();
    expect(v2Failures.map((t) => t.trim())).toEqual(failures);

    await logical.getByRole('button', { name: /Inspect all logical test groups/ }).click();
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText(`${ratio.split(' / ')[1]} rows`);
    await sheet.locator('tbody tr').first().getByRole('button').first().click();
    await expect(page.getByRole('dialog').last()).toContainText('Exact AMD job variant');
  });

  test('Upstream parity: coverage, distribution, config note, and gap areas', async ({ page }) => {
    const { rate, ratio, groups, note, areas } = golden.parity;
    await page.goto('/#/health?view=parity');
    const parityRing = page.getByTestId('parity-ring');
    await expect(parityRing).toContainText(rate);
    await expect(parityRing).toContainText(ratio);
    await expect(page.getByText(new RegExp(`Current main CI · ${groups} logical groups`, 'i'))).toBeVisible();
    await expect(page.getByText(note)).toBeVisible();
    const v2Areas = await page.getByTestId('gap-area').evaluateAll((nodes) =>
      nodes.map((n) => `${n.querySelector('strong').textContent.trim()}|${n.querySelector('span').textContent.trim()}`),
    );
    expect(v2Areas).toEqual(areas);

    await page.getByTestId('gap-area').first().click();
    const sheet = page.getByRole('dialog');
    await sheet.locator('tbody tr').first().getByRole('button').first().click();
    await expect(page.getByRole('dialog').last()).toContainText('AMD route mode');
  });
});

test.describe('v2 is the root page and keeps old links working', () => {
  test('legacy links to ported views open v2', async ({ page }) => {
    await page.goto('/?ops_health_view=parity#ci-health');
    await expect(page).toHaveURL(/#\/health\?view=parity$/);
    await expect(page.getByTestId('parity-ring')).toBeVisible();
    await page.goto('/#projects');
    await expect(page.getByRole('heading', COMMAND_CENTER)).toBeVisible();
  });

  test('old /v2/ bookmarks redirect to the root with their route', async ({ page }) => {
    await page.goto('/v2/#/health');
    await expect(page).toHaveURL(/\/#\/health$/);
    await expect(page.getByTestId('logical-ring')).toBeVisible();
  });
});

test('clickable controls show a pointer cursor', async ({ page }) => {
  await page.goto('/#/health');
  const cursor = (locator) => locator.evaluate((el) => getComputedStyle(el).cursor);
  expect(await cursor(page.getByRole('tab', { name: 'Upstream parity' }))).toBe('pointer');
  expect(await cursor(page.getByRole('button', { name: /theme/ }))).toBe('pointer');
});
