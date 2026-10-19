import { expect, test } from '@playwright/test';

test.describe('v2 dashboard', () => {
  test('Home renders the fixture nightlies and opens build evidence', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => message.type() === 'error' && errors.push(message.text()));

    await page.goto('/v2/');
    await expect(page.getByRole('heading', { name: 'Command Center' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Latest AMD nightly' })).toBeVisible();

    const rows = page.getByTestId('nightly-row');
    await expect(rows.first()).toBeVisible();
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

  test('unported views link to the classic dashboard', async ({ page }) => {
    await page.goto('/v2/#/analytics');
    await expect(page.getByRole('link', { name: 'Open CI Analytics on the classic dashboard' })).toHaveAttribute('href', '../#ci-analytics');
  });
});
