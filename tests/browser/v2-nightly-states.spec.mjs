import { expect, test } from '@playwright/test';

import { routeNightlyState } from './support/nightlyStates.mjs';
import { collectErrors, expectQuality, setTheme } from './support/quality.mjs';

// Every page with nightly results says the same thing about the newest nightly, in each state the
// collector publishes it in, and keeps counting from the newest nightly with results.
const PAGES = [
  ['Home', '/#/'],
  ['CI Health', '/#/health'],
  ['AMD test health', '/#/analytics'],
  ['AMD nightlies', '/#/analytics?view=nightlies'],
];

const STATES = [
  ['amd-complete', 'Nightly #30005: AMD jobs complete · build still running (1 non-AMD job)', 'Every AMD job is final'],
  ['awaiting', 'Nightly #30006 is still running its AMD jobs', 'with 2 hard failures so far. Counts come from #30005'],
  ['retry', 'Nightly #30006 is re-running an AMD job', 'results return once the retry settles. Counts come from #30005'],
  ['blocked', 'Nightly #30006 has no AMD test results', '6 AMD test jobs never started. Counts come from #30005'],
  ['no-results', 'Nightly #30006 has no AMD test results', 'It finished without parsed AMD test results; 1 job variant failed.'],
];

for (const [kind, title, body] of STATES) {
  test.describe(`latest nightly: ${kind}`, () => {
    test.beforeEach(async ({ page }) => routeNightlyState(page, kind));

    for (const [name, url] of PAGES) {
      test(`${name} shows the shared notice`, async ({ page }) => {
        const errors = collectErrors(page);
        await page.goto(url);
        const notice = page.getByTestId('latest-nightly');
        await expect(notice).toHaveCount(1);
        await expect(notice).toHaveAttribute('data-state', kind);
        await expect(notice).toContainText(title);
        await expect(notice).toContainText(body);
        expect(errors).toEqual([]);
      });
    }

    test('Home keeps its health numbers from the newest nightly with results', async ({ page }) => {
      await page.goto('/#/');
      await expect(page.getByTestId('kpi-pass-rate')).toContainText('66.7%');
      await expect(page.getByTestId('kpi-nightly')).toContainText('#30005 - 4 pass - 0 soft - 2 hard');
      const first = page.getByTestId('nightly-table').locator('tbody tr').first();
      if (kind === 'amd-complete') {
        await expect(page.getByTestId('kpi-pass-rate')).toContainText('AMD jobs complete · build still running (1 non-AMD job)');
        await expect(page.getByTestId('kpi-nightly')).toContainText('AMD jobs complete · build still running');
        await expect(first).toContainText('4 / 6 · AMD jobs complete');
      } else {
        await expect(first).toContainText('#30006');
        await expect(first).toContainText({ awaiting: 'Awaiting results', retry: 'AMD retry running', blocked: 'Infra blocked', 'no-results': 'No test signal' }[kind]);
      }
    });
  });
}

test('the AMD nightlies table marks an AMD-complete row and its sheet keeps the Buildkite state apart', async ({ page }) => {
  await routeNightlyState(page, 'amd-complete');
  await page.goto('/#/analytics?view=nightlies');
  const first = page.getByTestId('nightlies-table').locator('tbody tr').first();
  await expect(first).toContainText('AMD jobs complete · build still running (1 non-AMD job)');
  await first.locator('td').last().click();
  const sheet = page.getByRole('dialog');
  await expect(sheet).toContainText('AMD results are final');
  await expect(sheet).toContainText('Buildkite state');
  await expect(sheet).toContainText('Failing');
});

test('failures so far open the provisional jobs of a running nightly', async ({ page }) => {
  await routeNightlyState(page, 'awaiting');
  await page.goto('/#/');
  await page.getByTestId('latest-nightly').getByRole('button', { name: 'Failures so far' }).click();
  const sheet = page.getByRole('dialog');
  await expect(sheet).toContainText('Nightly #30006 failures so far');
  await expect(sheet).toContainText('Provisional');
  await expect(sheet.locator('tbody tr')).toHaveCount(2);
});

for (const theme of ['light', 'dark']) {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    for (const kind of ['amd-complete', 'awaiting']) {
      test(`Home with a ${kind} nightly meets the quality gates (${theme}, ${viewport.width}px)`, async ({ page }) => {
        await setTheme(page, theme);
        await page.setViewportSize(viewport);
        await routeNightlyState(page, kind);
        const errors = collectErrors(page);
        await page.goto('/#/');
        await expect(page.getByTestId('latest-nightly')).toBeVisible();
        await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
        await expectQuality(page, errors);
      });
    }
  }
}
