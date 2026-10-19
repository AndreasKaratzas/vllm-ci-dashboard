import { expect, test } from '@playwright/test';

import { readGolden } from './golden/read.mjs';
import { collectErrors, expectQuality, setTheme } from './support/quality.mjs';

// CI Health against the golden page facts of the fixture snapshot, plus the
// quality gates on its drill-down sheets.
const golden = readGolden('health');
const smoke = readGolden('smoke');

const squash = (text) => String(text ?? '').replace(/\s+/g, ' ').trim();
// External links now carry an icon instead of a trailing arrow glyph.
const unarrow = (text) => squash(text).replace(/ ?↗$/, '');
const texts = async (locator) => (await locator.allInnerTexts()).map(squash);
const sheets = (page) => page.locator('[data-slot=evidence-sheet]');
const topSheet = (page) => sheets(page).last();

async function closeSheets(page) {
  for (let i = 0; i < 5 && (await sheets(page).count()); i += 1) {
    const before = await sheets(page).count();
    await page.keyboard.press('Escape');
    await expect(sheets(page)).toHaveCount(before - 1);
  }
  await expect(sheets(page)).toHaveCount(0);
}

// Data rows of a table, cut to the expected width (clickable rows add a chevron cell).
async function tableRows(scope, width) {
  const rows = await scope.locator('tbody tr').evaluateAll((nodes) =>
    nodes
      .filter((row) => row.querySelectorAll('td').length > 1)
      .map((row) => [...row.querySelectorAll('td')].map((cell) => cell.innerText.replace(/\s+/g, ' ').trim())),
  );
  return rows.map((cells) => cells.slice(0, width).map(unarrow));
}

async function detailFields(scope) {
  return scope.locator('[data-slot=detail-fields]').evaluate((dl) => {
    const terms = [...dl.querySelectorAll('dt')].map((n) => n.textContent.trim().toLowerCase());
    const values = [...dl.querySelectorAll('dd')].map((n) => n.textContent.trim());
    return terms.map((term, i) => [term, values[i]]);
  });
}
const lower = (fields) => fields.map(([label, value]) => [label.toLowerCase(), value]);

const kpiValue = (card) => card.locator('[data-slot=kpi-value]');
const kpiOpen = (card) => card.locator('[data-kpi-action]');

test.describe('CI Health Overview and Upstream parity match the golden page facts', () => {
  test('Overview: logical health, outcomes, failures, and the logical drill-down', async ({ page }) => {
    const errors = collectErrors(page);
    const { rate, ratio, meta, counts, failures } = smoke.overview;
    await page.goto('/#/health');
    const logical = page.getByTestId('logical-ring');
    await expect(logical).toContainText(rate);
    await expect(logical).toContainText(ratio);
    await expect(logical).toContainText(meta);
    const outcomes = page.getByTestId('logical-outcomes');
    for (const [label, count] of Object.entries(counts)) {
      await expect(outcomes.getByRole('button', { name: new RegExp(`^${count}\\s*${label}$`) })).toBeVisible();
    }
    expect(await texts(page.getByTestId('latest-failures').locator('tbody tr [data-slot=cell] button'))).toEqual(failures);

    await kpiOpen(logical).click();
    await expect(topSheet(page).locator('[data-slot=row-count]')).toHaveText(`${ratio.split(' / ')[1]} rows`);
    await topSheet(page).locator('tbody tr').first().getByRole('button').first().click();
    await expect(sheets(page)).toHaveCount(2);
    await expect(topSheet(page)).toContainText('Exact AMD job variant');
    await closeSheets(page);

    await page.getByTestId('latest-failures').locator('tbody tr').first().click();
    await expect(topSheet(page).getByRole('heading').first()).toHaveText(failures[0]);
    await expect(topSheet(page)).toContainText('Latest AMD result');
    await expect(topSheet(page).getByTestId('outcome-strip').locator('a')).not.toHaveCount(0);
    await closeSheets(page);
    expect(errors).toEqual([]);
  });

  test('Upstream parity: coverage, distribution, methodology, and gap areas', async ({ page }) => {
    const { rate, ratio, groups, note, areas } = smoke.parity;
    await page.goto('/#/health?view=parity');
    const ring = page.getByTestId('parity-ring');
    await expect(ring).toContainText(rate);
    await expect(ring).toContainText(ratio);
    await expect(page.getByText(new RegExp(`Current main CI · ${groups} logical groups`, 'i'))).toBeVisible();
    await page.getByRole('button', { name: 'About this view' }).click();
    await expect(page.getByTestId('parity-config-note')).toHaveText(note);
    await page.keyboard.press('Escape');
    const v2Areas = await page.getByTestId('gap-area').evaluateAll((nodes) =>
      nodes.map((n) => `${n.querySelector('[data-testid=gap-area-label]').textContent.trim()}|${n.querySelector('[data-testid=gap-area-count]').textContent.trim()}`),
    );
    expect(v2Areas).toEqual(areas);

    await page.getByTestId('gap-area').first().click();
    await topSheet(page).locator('tbody tr').first().getByRole('button').first().click();
    await expect(sheets(page)).toHaveCount(2);
    await expect(topSheet(page)).toContainText('AMD route mode');
  });
});

test.describe('CI Health AMD hardware matches the golden page facts', () => {
  test('policy, architecture routes, and the sorted route matrix', async ({ page }) => {
    const errors = collectErrors(page);
    const expected = golden.coverage;

    await page.goto('/#/health?view=coverage');
    await expect(page.getByTestId('hardware-policy-meta')).toHaveText(expected.meta);
    await expect(kpiValue(page.getByTestId('hardware-rate'))).toHaveText(expected.rate);
    for (const [label, count] of expected.stats) {
      const mode = { 'Test groups': 'all', Passing: 'passing', Failing: 'failing', 'No signal': 'no-signal' }[label];
      const card = page.getByTestId(`hardware-stat-${mode}`);
      await expect(card.locator('[data-slot=card-description]')).toContainText(label);
      await expect(kpiValue(card)).toHaveText(count);
    }
    await expect(page.getByTestId('hardware-distribution')).toContainText(expected.facts);

    await expect(page.getByTestId('architecture-row')).toHaveCount(expected.architectures.length);
    expect(await texts(page.getByTestId('architecture-label'))).toEqual(expected.architectures.map((a) => a.label));
    expect(await texts(page.getByTestId('architecture-configured'))).toEqual(expected.architectures.map((a) => a.configured));
    expect(await texts(page.getByTestId('architecture-rate'))).toEqual(expected.architectures.map((a) => a.rate));
    const metrics = await page.getByTestId('architecture-metrics').evaluateAll((nodes) =>
      nodes.map((n) => [...n.querySelectorAll('span')].map((s) => s.textContent.trim())),
    );
    expect(metrics).toEqual(expected.architectures.map((a) => a.metrics));

    const matrix = page.getByTestId('route-matrix');
    await expect(matrix.getByTestId('matrix-sort-description')).toHaveText(expected.description);
    expect(await tableRows(matrix, expected.rows[0].length)).toEqual(expected.rows);
    expect(errors).toEqual([]);
  });

  for (const sort of ['name', 'area']) {
    test(`route matrix sorted by ${sort} matches the golden sort`, async ({ page }) => {
      const expected = golden[`coverage sorted by ${sort}`];
      await page.goto('/#/health?view=coverage');
      const label = { name: 'Test group', area: 'Test area' }[sort];
      await page.getByTestId('route-matrix').getByRole('radio', { name: label, exact: true }).or(page.getByTestId('route-matrix').getByRole('button', { name: label, exact: true })).click();
      await expect(page).toHaveURL(new RegExp(`sort=${sort}`));
      await expect(page.getByTestId('matrix-sort-description')).toHaveText(expected.description);
      expect(await tableRows(page.getByTestId('route-matrix'), expected.rows[0].length)).toEqual(expected.rows);
      await page.goto(`/#/health?view=coverage&sort=${sort}`);
      await expect(page.getByTestId('matrix-sort-description')).toHaveText(expected.description);
    });
  }

  test('drill-downs: group browser filters, group evidence, architecture routes, and test-group detail', async ({ page }) => {
    const { browsers, evidence, architecture, group } = golden['coverage drill-downs'];
    await page.goto('/#/health?view=coverage');
    for (const [mode, browser] of Object.entries(browsers)) {
      await kpiOpen(page.getByTestId(`hardware-stat-${mode}`)).click();
      const sheet = topSheet(page);
      await expect(sheet.getByRole('heading').first()).toHaveText(browser.title);
      await expect(sheet.locator('[data-slot=row-count]')).toHaveText(browser.count);
      expect(await tableRows(sheet, 7)).toEqual(browser.rows);
      await closeSheets(page);
    }
    // Changing a filter in place matches the golden browser of that mode.
    await kpiOpen(page.getByTestId('hardware-stat-failing')).click();
    await page.getByRole('combobox', { name: 'Filter configured test-group status' }).click();
    await page.getByRole('option', { name: 'Passing', exact: true }).click();
    await expect(topSheet(page).locator('[data-slot=row-count]')).toHaveText(browsers.passing.count);
    expect(await tableRows(topSheet(page), 7)).toEqual(browsers.passing.rows);
    await page.getByRole('combobox', { name: 'Filter configured test-group status' }).click();
    await page.getByRole('option', { name: 'Failing', exact: true }).click();
    await expect(topSheet(page).locator('[data-slot=row-count]')).toHaveText(browsers.failing.count);
    await topSheet(page).locator('tbody tr').first().getByRole('button').first().click();
    await expect(sheets(page)).toHaveCount(2);
    const detail = topSheet(page);
    await expect(detail.getByRole('heading').first()).toHaveText(evidence.title);
    expect(lower(await detailFields(detail))).toEqual(lower(evidence.fields));
    expect((await tableRows(detail, 4)).map((cells) => [cells[0], cells[2], cells[3]])).toEqual(evidence.definitions);
    await closeSheets(page);

    await page.getByTestId('architecture-row').first().click();
    let sheet = topSheet(page);
    await expect(sheet.getByRole('heading').first()).toHaveText(architecture.title);
    await expect(sheet).toContainText(architecture.subtitle);
    expect(await tableRows(sheet, 4)).toEqual(architecture.rows);
    await sheet.locator('tbody tr').first().getByRole('button').first().click();
    await expect(sheets(page)).toHaveCount(2);
    sheet = topSheet(page);
    await expect(sheet.getByRole('heading').first()).toHaveText(group.title);
    expect(lower(await detailFields(sheet))).toEqual(lower(group.fields));
    await sheet.getByRole('button', { name: 'Load 30-day run history' }).click();
    // The fixture ledger has no retained rows, so the load is offered again.
    await expect(sheet.getByRole('button', { name: 'Load 30-day run history' })).toBeVisible();
  });
});

test.describe('CI Health AMD mirrors matches the golden page facts', () => {
  test('totals, families, areas, inventory, and drawers', async ({ page }) => {
    const errors = collectErrors(page);
    const expected = golden.mirrors;
    const { drawer, browsers } = expected;
    const [areaBrowser, inventoryBrowser] = browsers;

    await page.goto('/#/health?view=mirrors');
    await expect(kpiValue(page.getByTestId('mirror-hero'))).toHaveText(expected.total);
    const [[actionLabel, actionHref]] = expected.headerAction;
    const action = page.getByRole('link', { name: unarrow(actionLabel) });
    await expect(action).toHaveAttribute('href', actionHref);
    await expect(action).toHaveCount(1);
    await page.locator('[data-slot=freshness]').click();
    await expect(page.locator('[data-slot=freshness-detail]')).toContainText(`${expected.observed} ·`);
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('mirror-hero')).toContainText(expected.meta);
    const legend = await page.getByTestId('mirror-hardware').evaluateAll((nodes) => nodes.map((n) => [...n.children].slice(1).map((c) => c.textContent.trim())));
    expect(legend).toEqual(expected.legend);
    expect(await texts(page.getByTestId('mirror-mode').locator('[data-slot=kpi-value]'))).toEqual(expected.modes);
    expect(await texts(page.getByTestId('mirror-fact').locator('strong'))).toEqual(expected.facts);
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

    // The inventory browser is now the page's inventory table.
    const inventory = page.getByTestId('mirror-inventory');
    await expect(inventory.getByRole('heading', { level: 2 })).toHaveText(inventoryBrowser.title);
    await expect(inventory.locator('[data-slot=row-count]')).toHaveText(inventoryBrowser.count);
    expect(await tableRows(inventory, 6)).toEqual(inventoryBrowser.rows.map((row) => row.map(unarrow)));

    await inventory.locator('tbody tr').first().click();
    const detail = topSheet(page);
    await expect(detail.getByRole('heading').first()).toHaveText(drawer.title);
    expect(lower(await detailFields(detail))).toEqual(lower(drawer.fields));
    expect(await detail.locator('a').evaluateAll((nodes) => nodes.map((n) => n.getAttribute('href')))).toEqual(drawer.sources);
    await closeSheets(page);

    await page.getByRole('button', { name: areaBrowser.label }).click();
    const sheet = topSheet(page);
    await expect(sheet.getByRole('heading').first()).toHaveText(areaBrowser.title);
    await expect(sheet.locator('[data-slot=row-count]')).toHaveText(areaBrowser.count);
    expect(await tableRows(sheet, 7)).toEqual(areaBrowser.rows.map((row) => row.map(unarrow)));
    await closeSheets(page);
    expect(errors).toEqual([]);
  });

  test('Overview mirror summary matches the golden card and opens the mirrors view', async ({ page }) => {
    const expected = golden['mirror summary'];
    await page.goto('/#/health');
    const summary = page.getByTestId('mirror-summary');
    await expect(kpiValue(summary)).toHaveText(expected.count);
    await expect(summary).toContainText(expected.meta);
    await expect(summary.getByTestId('mirror-summary-facts')).toHaveText(expected.facts);
    await expect(kpiOpen(summary)).toHaveAccessibleName(new RegExp(`${expected.label.replace(/[.]/g, '\\.')}$`));
    await kpiOpen(summary).click();
    await expect(page).toHaveURL(/#\/health\?view=mirrors$/);
    await expect(kpiValue(page.getByTestId('mirror-hero'))).toHaveText(expected.count);
  });

  test('legacy links to AMD hardware and AMD mirrors open the views', async ({ page }) => {
    await page.goto('/?ops_health_view=coverage&ops_health_sort=area#ci-health');
    await expect(page).toHaveURL(/#\/health\?view=coverage&sort=area$/);
    await expect(page.getByTestId('matrix-sort-description')).toHaveText('Test areas sorted alphabetically, then by test-group name');
    await page.goto('/?ops_health_view=mirrors#ci-health');
    await expect(page).toHaveURL(/#\/health\?view=mirrors$/);
    await expect(page.getByTestId('mirror-hero')).toBeVisible();
  });
});

// Every main drill-down sheet passes the same gates as the pages.
const SHEETS = [
  ['logical catalog', '/#/health', (page) => kpiOpen(page.getByTestId('logical-ring')).click()],
  ['exact job', '/#/health', (page) => page.getByTestId('latest-failures').locator('tbody tr').first().click()],
  ['parity inventory', '/#/health?view=parity', (page) => kpiOpen(page.getByTestId('parity-ring')).click()],
  ['group browser', '/#/health?view=coverage', (page) => kpiOpen(page.getByTestId('hardware-stat-all')).click()],
  ['architecture routes', '/#/health?view=coverage', (page) => page.getByTestId('architecture-row').first().click()],
  ['group history', '/#/health?view=coverage', (page) => page.getByTestId('route-matrix').locator('tbody tr').first().click()],
  ['mirror detail', '/#/health?view=mirrors', (page) => page.getByTestId('mirror-inventory').locator('tbody tr').first().click()],
];

for (const theme of ['light', 'dark']) {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    test(`CI Health sheets meet the quality gates (${theme}, ${viewport.width}px)`, async ({ page }) => {
      await setTheme(page, theme);
      await page.setViewportSize(viewport);
      const errors = collectErrors(page);
      for (const [name, route, open] of SHEETS) {
        await page.goto(route);
        await page.getByRole('heading', { level: 2 }).first().waitFor();
        await open(page);
        await expect(sheets(page), name).toHaveCount(1);
        await topSheet(page).getByRole('heading').first().waitFor();
        await page.waitForTimeout(300);
        await expectQuality(page, errors);
        await closeSheets(page);
      }
    });
  }
}
