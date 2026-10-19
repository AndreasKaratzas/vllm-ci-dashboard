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
