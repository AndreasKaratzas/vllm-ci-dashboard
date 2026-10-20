import { expect, test } from '@playwright/test';

import { collectErrors, expectQuality, setTheme, settleAnimations } from './support/quality.mjs';

const HOME = { name: 'Home', level: 1 };

test.describe('Home', () => {
  test('renders the fixture nightlies and opens build evidence', async ({ page }) => {
    const errors = collectErrors(page);

    await page.goto('/');
    await expect(page.getByRole('heading', HOME)).toBeVisible();
    await expect(page.getByTestId('kpi-nightly').locator('[data-slot=kpi-value]')).toHaveText(/^2\s*failing$/);
    await expect(page.getByTestId('kpi-nightly')).toContainText('Hard failures');
    await expect(page.getByTestId('kpi-nightly')).toContainText('No net change');
    await expect(page.getByTestId('kpi-nightly')).toContainText('#30005 - 4 pass - 0 soft - 2 hard');
    await expect(page.getByTestId('kpi-pass-rate')).toContainText('66.7%');
    await expect(page.getByTestId('kpi-parity')).toContainText('90.9%');
    await expect(page.getByTestId('kpi-parity')).toContainText('1 missing');
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
    await expect(sheet).toContainText('AMD nightly #30005');
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
    await expect(page.getByRole('dialog')).toContainText('AMD nightly #30003');
  });

  test('the pass-rate range control offers only windows that hide some nightlies', async ({ page }) => {
    // The fixture retains 5 nightlies, so Last 30/14/7 would all show every one of them.
    await page.goto('/#/?range=7');
    const trend = page.getByTestId('nightly-trend');
    await expect(trend).toContainText('All 5 nightlies (build summaries)');
    await expect(trend.getByRole('group', { name: 'Nightly range' })).toHaveCount(0);
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
    await expect(page.getByRole('heading', HOME)).toBeVisible();
    await page.keyboard.press('Control+k');
    await page.getByPlaceholder(/Search views/).fill('analytics');
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
    await page.getByRole('group', { name: 'AMD nightlies' }).getByRole('option', { name: /#30004/ }).click();
    await expect(page).toHaveURL(/#\/\?build=30004$/);
    await expect(page.getByRole('dialog')).toContainText('AMD nightly #30004');
    await page.keyboard.press('Escape');
    await expect(page).toHaveURL(/#\/$/);
  });

  test('the command palette finds test groups and perf models', async ({ page }) => {
    await page.goto('/');
    await page.keyboard.press('Control+k');
    const palette = page.getByRole('dialog');
    await palette.getByPlaceholder(/Search views/).fill('broken group');
    await expect(palette.getByRole('group', { name: 'AMD test groups' }).getByRole('option')).toHaveCount(1);
    await palette.getByRole('group', { name: 'AMD test groups' }).getByRole('option', { name: /broken group/i }).click();
    await expect(page.getByRole('dialog')).toContainText('Exact AMD job variants');
    await page.keyboard.press('Escape');

    await page.keyboard.press('Control+k');
    await page.getByPlaceholder(/Search views/).fill('MiniMax');
    await page.getByRole('group', { name: 'Perf models' }).getByRole('option').first().click();
    await expect(page).toHaveURL(/#\/perf\?model=MiniMaxAI/);
  });

  test('Omni sub-views appear in the breadcrumb, sidebar and command palette', async ({ page }) => {
    await page.goto('/#/omni?view=live');
    await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toContainText('Live queues');
    const sidebar = page.locator('[data-sidebar=sidebar]').first();
    await expect(sidebar.getByRole('link', { name: 'Live queues' })).toHaveAttribute('data-active', 'true');
    await page.keyboard.press('Control+k');
    await page.getByRole('option', { name: /Omni CI\s*Incoming workload/ }).click();
    await expect(page).toHaveURL(/#\/omni$/);
    await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toContainText('Incoming workload');
  });

  test('the phone sidebar closes on Escape and after following a link', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');
    const sheet = page.locator('[data-sidebar=sidebar][data-mobile=true]');
    await page.getByRole('button', { name: 'Toggle Sidebar' }).click();
    await expect(sheet).toHaveAttribute('data-state', 'open');
    await page.keyboard.press('Escape');
    await expect(sheet).toHaveCount(0);
    await page.getByRole('button', { name: 'Toggle Sidebar' }).click();
    await sheet.getByRole('link', { name: 'CI Analytics' }).click();
    await expect(page).toHaveURL(/#\/analytics$/);
    await expect(sheet).toHaveCount(0);
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
  'data freshness': async (page) => page.locator('[data-slot^=header-freshness]:visible').first().click(),
  'hard-count evidence': async (page) => page.getByTestId('nightly-table').locator('tbody tr').first().getByRole('button', { name: '2' }).first().click(),
  'command palette': async (page) => page.keyboard.press('Control+k'),
};

for (const theme of ['light', 'dark']) {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    for (const [name, open] of Object.entries(SHEETS)) {
      test(`the ${name} sheet meets the quality gates (${theme}, ${viewport.width}px)`, async ({ page }) => {
        await setTheme(page, theme);
        await page.setViewportSize(viewport);
        const errors = collectErrors(page);
        await page.goto('/');
        await expect(page.getByRole('heading', HOME)).toBeVisible();
        await open(page);
        await expect(page.getByRole('dialog')).toBeVisible();
        await page.waitForTimeout(400);
        await expectQuality(page, errors);
      });
    }
  }
}

test.describe('v2 is the root page and keeps old links working', () => {
  test('legacy links to ported views open v2', async ({ page }) => {
    await page.goto('/?ops_health_view=parity#ci-health');
    await expect(page).toHaveURL(/#\/health\?view=parity$/);
    await expect(page.getByTestId('parity-ring')).toBeVisible();
    await page.goto('/#projects');
    await expect(page.getByRole('heading', HOME)).toBeVisible();
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
  const sections = page.getByRole('navigation', { name: 'CI Health sections' });
  expect(await cursor(sections.getByRole('link', { name: 'Upstream parity' }))).toBe('pointer');
  expect(await cursor(page.getByRole('button', { name: /theme/ }))).toBe('pointer');
});

// KPI cards are stretched links; scrolled under the sticky header they must not swallow its controls.
for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
  test(`the sticky header stays clickable above scrolled KPI cards (${viewport.width}px)`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto('/#/health');
    const toggle = page.getByRole('button', { name: /Switch to (dark|light) theme/ });
    const kpi = page.locator('[data-kpi-action]').first();
    await kpi.waitFor();
    // An entering card is its own stacking context only while its slide-in transform runs.
    await settleAnimations(page);
    const header = await page.locator('header').first().boundingBox();
    // Scroll so the first KPI card sits under the header controls.
    const card = await page.locator('[data-slot=kpi-card]').first().boundingBox();
    await page.mouse.move(viewport.width / 2, viewport.height / 2);
    await page.mouse.wheel(0, card.y - header.y - 4);
    await expect.poll(async () => (await page.locator('[data-slot=kpi-card]').first().boundingBox()).y).toBeLessThan(header.y + header.height);
    // The control itself, not a card's overlay, is on top at its center.
    const hit = async (control) => {
      const box = await control.boundingBox();
      return control.evaluate((el, [x, y]) => el.contains(document.elementFromPoint(x, y)), [box.x + box.width / 2, box.y + box.height / 2]);
    };
    expect(await hit(page.locator('header [data-sidebar=trigger]'))).toBe(true);
    expect(await hit(toggle)).toBe(true);
    const box = await toggle.boundingBox();
    const before = await page.evaluate(() => document.documentElement.classList.contains('dark'));
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await expect.poll(() => page.evaluate(() => document.documentElement.classList.contains('dark'))).toBe(!before);
    await expect(page).toHaveURL(/#\/health$/);
  });
}
