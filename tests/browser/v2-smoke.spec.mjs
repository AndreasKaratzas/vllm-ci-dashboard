import { expect, test } from '@playwright/test';

test.describe('v2 dashboard', () => {
  test('Home renders the fixture nightlies and opens build evidence', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => message.type() === 'error' && errors.push(message.text()));

    await page.goto('/v2/');
    await expect(page.getByRole('heading', { name: 'Command Center' })).toBeVisible();
    await expect(page.getByTestId('kpi-nightly')).toContainText('Hard failures');
    await expect(page.getByTestId('kpi-nightly')).toContainText('#30005 - 4 pass - 0 soft - 2 hard');
    await expect(page.getByText('AMD nightly trend')).toBeVisible();

    const rows = page.getByTestId('nightly-row');
    await expect(rows.first()).toContainText('#30005');
    await expect(rows.first().getByRole('link', { name: '#30005' })).toHaveAttribute(
      'href',
      /^https:\/\/buildkite\.com\/vllm\/ci\/builds\/30005$/,
    );

    await rows.first().getByRole('button').first().click();
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText('AMD build #30005');
    await expect(sheet).toContainText('Job variants observed');

    expect(errors).toEqual([]);
  });

  test('the command palette jumps between views', async ({ page }) => {
    await page.goto('/v2/');
    await expect(page.getByRole('heading', { name: 'Command Center' })).toBeVisible();
    await page.keyboard.press('Control+k');
    await page.getByPlaceholder(/Type a view/).fill('analytics');
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/#\/analytics$/);
  });

  test('the sidebar collapses to icons', async ({ page }) => {
    await page.goto('/v2/');
    await page.getByRole('button', { name: 'Toggle Sidebar' }).first().click();
    await expect(page.locator('[data-collapsible="icon"]')).toHaveCount(1);
  });

  test('unported views link to the classic dashboard', async ({ page }) => {
    await page.goto('/v2/#/analytics');
    await expect(page.getByRole('link', { name: 'Open CI Analytics on the classic dashboard' })).toHaveAttribute('href', '../#ci-analytics');
  });
});

async function classicHealthText(page, query) {
  await page.goto(`/${query}#ci-health`);
  const host = page.locator('#ci-health-view[data-render-state="ready"]');
  await host.waitFor();
  return host.innerText();
}

test.describe('v2 CI Health matches the classic page on the same snapshot', () => {
  test('Overview: logical health, outcomes, and failure observations', async ({ page }) => {
    const classic = await classicHealthText(page, '');
    const ring = classic.match(/(\d+\.\d%)\s+(\d+ \/ \d+)/);
    const meta = classic.match(/\d+ pass on every route · \d+ pass on some hardware only · \d+ non-passing everywhere/)[0];
    const failures = [...classic.matchAll(/\n([^\n]+)\nmi\d+_\d+ - amd_mi\d+_\d+/g)].map((m) => m[1].trim());

    expect(failures.length).toBeGreaterThan(0);
    await page.goto('/v2/#/health');
    const logical = page.getByTestId('logical-ring');
    await expect(logical).toContainText(ring[1]);
    await expect(logical).toContainText(ring[2]);
    await expect(logical).toContainText(meta);
    for (const label of ['pass every route', 'pass some routes', 'non-passing']) {
      const count = classic.match(new RegExp(`\\n(\\d+)\\n${label}\\n`))[1];
      await expect(page.getByRole('button', { name: new RegExp(`^${count}\\s*${label}$`) })).toBeVisible();
    }
    const v2Failures = await page.locator('table').last().locator('tbody tr button').allInnerTexts();
    expect(v2Failures.map((t) => t.trim())).toEqual(failures);

    await logical.getByRole('button', { name: /Inspect all logical test groups/ }).click();
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText(`${ring[2].split(' / ')[1]} rows`);
    await sheet.locator('tbody tr').first().getByRole('button').first().click();
    await expect(page.getByRole('dialog').last()).toContainText('Exact AMD job variant');
  });

  test('Upstream parity: coverage, distribution, config note, and gap areas', async ({ page }) => {
    const classic = await classicHealthText(page, '?ops_health_view=parity');
    const ring = classic.match(/(\d+\.\d%)\s+(\d+ \/ \d+)/);
    const groups = classic.match(/CURRENT MAIN CI · (\d+) LOGICAL GROUPS/)[1];
    const note = classic.match(/Configured AMD routes cover[^\n]+/)[0];
    const areas = [...classic.matchAll(/\n([^\n]+)\n(\d+ potential open gaps?)\n/g)].map((m) => `${m[1]}|${m[2]}`);

    expect(areas.length).toBeGreaterThan(0);
    await page.goto('/v2/#/health?view=parity');
    const parityRing = page.getByTestId('parity-ring');
    await expect(parityRing).toContainText(ring[1]);
    await expect(parityRing).toContainText(ring[2]);
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

  test('unported CI Health views link to the matching classic view', async ({ page }) => {
    await page.goto('/v2/#/health?view=mirrors');
    await expect(page.getByRole('link', { name: 'Open AMD mirrors on the classic dashboard' })).toHaveAttribute(
      'href',
      '../?ops_health_view=mirrors#ci-health',
    );
  });
});
