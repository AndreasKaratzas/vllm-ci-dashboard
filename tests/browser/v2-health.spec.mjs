import { expect, test } from '@playwright/test';

import { readGolden } from './golden/read.mjs';

// CI Health "AMD hardware", "AMD mirrors", and the Overview mirror summary
// against the golden page facts of the fixture snapshot.
const golden = readGolden('health');

const squash = (text) => String(text ?? '').replace(/\s+/g, ' ').trim();
const texts = async (locator) => (await locator.allInnerTexts()).map(squash);

// Nested sheets hide the ones below them from the accessibility tree, so close
// them one Escape at a time until none is left.
async function closeSheets(page) {
  const dialogs = page.locator('[data-slot=sheet-content]');
  for (let i = 0; i < 5 && (await dialogs.count()); i += 1) {
    const before = await dialogs.count();
    await page.keyboard.press('Escape');
    await expect(dialogs).toHaveCount(before - 1);
  }
  await expect(dialogs).toHaveCount(0);
}

async function v2TableRows(scope) {
  return scope.locator('tbody tr').evaluateAll((rows) =>
    rows
      .filter((row) => row.querySelectorAll('td').length > 1)
      .map((row) => [...row.querySelectorAll('td')].map((cell) => cell.innerText.replace(/\s+/g, ' ').trim())),
  );
}

test.describe('CI Health AMD hardware matches the golden page facts', () => {
  test('policy card, architecture routes, and the sorted route matrix', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const expected = golden.coverage;

    await page.goto('/#/health?view=coverage');
    await expect(page.getByTestId('hardware-policy-meta')).toHaveText(expected.meta);
    await expect(page.getByTestId('hardware-rate').locator('[data-slot=card-title]')).toHaveText(expected.rate);
    for (const [label, count] of expected.stats) {
      const mode = { 'Test groups': 'all', Passing: 'passing', Failing: 'failing', 'No signal': 'no-signal' }[label];
      const card = page.getByTestId(`hardware-stat-${mode}`);
      await expect(card.locator('[data-slot=card-description]')).toHaveText(label);
      await expect(card.locator('[data-slot=card-title]')).toHaveText(count);
    }
    await expect(page.getByTestId('hardware-distribution')).toContainText(expected.facts);

    const rows = page.getByTestId('architecture-row');
    await expect(rows).toHaveCount(expected.architectures.length);
    expect(await texts(page.getByTestId('architecture-label'))).toEqual(expected.architectures.map((a) => a.label));
    expect(await texts(page.getByTestId('architecture-configured'))).toEqual(expected.architectures.map((a) => a.configured));
    expect(await texts(page.getByTestId('architecture-rate'))).toEqual(expected.architectures.map((a) => a.rate));
    const metrics = await page.getByTestId('architecture-metrics').evaluateAll((nodes) =>
      nodes.map((n) => [...n.querySelectorAll('span')].map((s) => s.textContent.trim())),
    );
    expect(metrics).toEqual(expected.architectures.map((a) => a.metrics));

    const matrix = page.getByTestId('route-matrix');
    await expect(matrix.getByTestId('matrix-sort-description')).toHaveText(expected.description);
    expect(await v2TableRows(matrix)).toEqual(expected.rows);
    expect(errors).toEqual([]);
  });

  for (const sort of ['name', 'area']) {
    test(`route matrix sorted by ${sort} matches the golden sort`, async ({ page }) => {
      const expected = golden[`coverage sorted by ${sort}`];
      await page.goto('/#/health?view=coverage');
      const label = { name: 'Test group', area: 'Test area' }[sort];
      await page.getByTestId('route-matrix').getByRole('button', { name: label, exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`sort=${sort}`));
      await expect(page.getByTestId('matrix-sort-description')).toHaveText(expected.description);
      expect(await v2TableRows(page.getByTestId('route-matrix'))).toEqual(expected.rows);
      await page.goto(`/#/health?view=coverage&sort=${sort}`);
      await expect(page.getByTestId('matrix-sort-description')).toHaveText(expected.description);
    });
  }

  test('drill-downs: group browser filters, group evidence, architecture routes, and test-group detail', async ({ page }) => {
    const { browsers, evidence, architecture, group } = golden['coverage drill-downs'];
    await page.goto('/#/health?view=coverage');
    for (const [mode, browser] of Object.entries(browsers)) {
      await page.getByTestId(`hardware-stat-${mode}`).locator('[data-slot=card-footer] button').click();
      const sheet = page.getByRole('dialog');
      await expect(sheet.getByRole('heading').first()).toHaveText(browser.title);
      await expect(sheet.getByTestId('browser-count')).toHaveText(browser.count);
      expect((await v2TableRows(sheet)).map((cells) => cells.slice(0, 7))).toEqual(browser.rows);
      await closeSheets(page);
    }
    // Changing a filter in place matches the golden browser of that mode.
    await page.getByTestId('hardware-stat-failing').locator('[data-slot=card-footer] button').click();
    await page.getByRole('combobox', { name: 'Filter configured test-group status' }).click();
    await page.getByRole('option', { name: 'Passing', exact: true }).click();
    await expect(page.getByRole('dialog').getByTestId('browser-count')).toHaveText(browsers.passing.count);
    expect((await v2TableRows(page.getByRole('dialog'))).map((cells) => cells.slice(0, 7))).toEqual(browsers.passing.rows);
    await page.getByRole('combobox', { name: 'Filter configured test-group status' }).click();
    await page.getByRole('option', { name: 'Failing', exact: true }).click();
    await expect(page.getByRole('dialog').getByTestId('browser-count')).toHaveText(browsers.failing.count);
    await page.getByRole('dialog').locator('tbody tr').first().getByRole('button').first().click();
    const detail = page.getByTestId('matrix-group-detail');
    await expect(detail).toBeVisible();
    await expect(page.getByRole('dialog').last().getByRole('heading').first()).toHaveText(evidence.title);
    const v2Fields = await detail.getByTestId('detail-fields').locator('div').evaluateAll((nodes) =>
      nodes.map((n) => [n.querySelector('dt').textContent.trim(), n.querySelector('dd').textContent.trim()]),
    );
    expect(v2Fields.map(([label, value]) => [label.toLowerCase(), value])).toEqual(evidence.fields.map(([label, value]) => [label.toLowerCase(), value]));
    expect((await v2TableRows(detail)).map((cells) => [cells[0].replace(/ ↗$/, ''), cells[2], cells[3]])).toEqual(evidence.definitions);
    await closeSheets(page);

    await page.getByTestId('architecture-row').first().click();
    let sheet = page.getByRole('dialog');
    await expect(sheet.getByRole('heading').first()).toHaveText(architecture.title);
    await expect(sheet).toContainText(architecture.subtitle);
    expect(await v2TableRows(sheet)).toEqual(architecture.rows);
    await sheet.locator('tbody tr').first().getByRole('button').first().click();
    const groupDetail = page.getByTestId('group-detail');
    await expect(groupDetail).toBeVisible();
    await expect(page.getByRole('dialog').last().getByRole('heading').first()).toHaveText(group.title);
    const v2GroupFields = await groupDetail.getByTestId('detail-fields').locator('div').evaluateAll((nodes) =>
      nodes.map((n) => [n.querySelector('dt').textContent.trim(), n.querySelector('dd').textContent.trim()]),
    );
    expect(v2GroupFields.map(([l, v]) => [l.toLowerCase(), v])).toEqual(group.fields.map(([l, v]) => [l.toLowerCase(), v]));
    await groupDetail.getByRole('button', { name: 'Load 30-day run history' }).click();
    // The fixture ledger has no retained rows, so the load is offered again.
    await expect(groupDetail.getByRole('button', { name: 'Load 30-day run history' })).toBeVisible();
  });
});

test.describe('CI Health AMD mirrors matches the golden page facts', () => {
  test('count hero, configuration shape, areas, inventory, and drawers', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const expected = golden.mirrors;
    const { drawer, browsers } = expected;

    await page.goto('/#/health?view=mirrors');
    await expect(page.getByTestId('mirror-total')).toHaveText(expected.total);
    const [[actionLabel, actionHref]] = expected.headerAction;
    await expect(page.getByRole('link', { name: actionLabel })).toHaveAttribute('href', actionHref);
    await expect(page.getByRole('link', { name: actionLabel })).toHaveCount(1);
    await expect(page.getByText(`${expected.observed} ·`)).toBeVisible();
    await expect(page.getByTestId('mirror-hero-meta')).toHaveText(expected.meta);
    const legend = await page.getByTestId('mirror-hardware').evaluateAll((nodes) => nodes.map((n) => [...n.children].slice(1).map((c) => c.textContent.trim())));
    expect(legend).toEqual(expected.legend);
    expect(await page.getByTestId('mirror-mode').locator('strong').allInnerTexts()).toEqual(expected.modes);
    expect(await page.getByTestId('mirror-fact').locator('strong').allInnerTexts()).toEqual(expected.facts);
    await expect(page.getByTestId('mirror-areas-subtitle')).toHaveText(expected.areasSubtitle);
    const areas = await page.getByTestId('mirror-area').evaluateAll((nodes) =>
      nodes.map((n) => [
        n.querySelector('[data-testid=mirror-area-name]').textContent.trim(),
        n.querySelector('[data-testid=mirror-area-file]').textContent.trim(),
        n.querySelector('[data-testid=mirror-area-breakdown]').textContent.trim(),
        n.querySelector('[data-testid=mirror-area-count]').textContent.trim(),
        n.getAttribute('href'),
      ]),
    );
    expect(areas).toEqual(expected.areas);
    await expect(page.getByTestId('mirror-inventory-subtitle')).toHaveText(expected.inventorySubtitle);
    const preview = await page.getByTestId('mirror-preview').evaluateAll((nodes) =>
      nodes.map((n) => ['mirror-preview-label', 'mirror-preview-meta', 'mirror-preview-mode'].map((id) => n.querySelector(`[data-testid=${id}]`).textContent.replace(/\s+/g, ' ').trim())),
    );
    expect(preview).toEqual(expected.preview);

    await page.getByTestId('mirror-preview').first().click();
    const detail = page.getByTestId('mirror-detail');
    await expect(page.getByRole('dialog').getByRole('heading').first()).toHaveText(drawer.title);
    const fields = await detail.getByTestId('detail-fields').locator('div').evaluateAll((nodes) =>
      nodes.map((n) => [n.querySelector('dt').textContent.trim(), n.querySelector('dd').textContent.trim()]),
    );
    expect(fields.map(([l, v]) => [l.toLowerCase(), v])).toEqual(drawer.fields.map(([l, v]) => [l.toLowerCase(), v]));
    expect(await detail.locator('a').evaluateAll((nodes) => nodes.map((n) => n.getAttribute('href')))).toEqual(drawer.sources);
    await closeSheets(page);

    for (const browser of browsers) {
      await page.getByRole('button', { name: browser.label }).click();
      const sheet = page.getByRole('dialog');
      await expect(sheet.getByRole('heading').first()).toHaveText(browser.title);
      await expect(sheet.getByTestId('browser-count')).toHaveText(browser.count);
      expect(await v2TableRows(sheet)).toEqual(browser.rows);
      await closeSheets(page);
    }
    expect(errors).toEqual([]);
  });

  test('Overview mirror summary card matches the golden card and opens the mirrors view', async ({ page }) => {
    const expected = golden['mirror summary'];
    await page.goto('/#/health');
    const summary = page.getByTestId('mirror-summary');
    await expect(summary.getByTestId('mirror-summary-count')).toHaveText(expected.count);
    await expect(summary.getByTestId('mirror-summary-meta')).toHaveText(expected.meta);
    await expect(summary.getByTestId('mirror-summary-facts')).toHaveText(expected.facts);
    await expect(summary).toHaveAttribute('aria-label', expected.label);
    await summary.click();
    await expect(page).toHaveURL(/#\/health\?view=mirrors$/);
    await expect(page.getByTestId('mirror-total')).toHaveText(expected.count);
  });

  test('legacy links to AMD hardware and AMD mirrors open v2', async ({ page }) => {
    await page.goto('/?ops_health_view=coverage&ops_health_sort=area#ci-health');
    await expect(page).toHaveURL(/#\/health\?view=coverage&sort=area$/);
    await expect(page.getByTestId('matrix-sort-description')).toHaveText('Test areas sorted alphabetically, then by test-group name');
    await page.goto('/?ops_health_view=mirrors#ci-health');
    await expect(page).toHaveURL(/#\/health\?view=mirrors$/);
    await expect(page.getByTestId('mirror-total')).toBeVisible();
  });
});
