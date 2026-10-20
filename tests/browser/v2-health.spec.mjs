import { expect, test } from '@playwright/test';

import { readGolden } from './golden/read.mjs';
import { collectErrors, expectQuality, setTheme, settleAnimations } from './support/quality.mjs';

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
  return scope.locator('[data-slot=evidence-fields]').evaluate((dl) => {
    const terms = [...dl.querySelectorAll('dt')].map((n) => n.textContent.trim().toLowerCase());
    const values = [...dl.querySelectorAll('dd')].map((n) => n.textContent.trim());
    return terms.map((term, i) => [term, values[i]]);
  });
}
const lower = (fields) => fields.map(([label, value]) => [label.toLowerCase(), value]);

// The golden facts were captured from the classic page. The design system names
// one result the same way everywhere and mutes a blank cell, so each golden cell
// is compared through that fixed vocabulary; the facts themselves are unchanged.
const RESULT = { passed: 'Passed', failed: 'Hard fail', unknown: 'No signal', unobserved: 'No signal', '-': '—' };
const shown = (cell) =>
  RESULT[cell] ??
  cell
    .replace(/^(\d+) pass - (\d+) non-passing$/, '$1 passing · $2 failing')
    .replace(/^1 jobs$/, '1 job')
    .replace(/^(\d+) non-passing$/, '$1 failing')
    .replace(/^(\d+) unobserved$/, '$1 no signal');
const shownRows = (rows) => rows.map((row) => row.map(shown));

// Serves a 30-day ledger built from the fixture's exact-job catalog, so the evidence branch renders.
// The manifest's digest of the published (empty) ledger is dropped so the substitute validates.
async function routeLedger(page) {
  await page.route('**/operations_v2_manifest.json?*', async (route) => {
    const manifest = await (await route.fetch()).json();
    delete manifest.sections.reliability.sha256;
    await route.fulfill({ json: manifest });
  });
  await page.route('**/operations_v2/reliability.json?*', async (route) => {
    const response = await page.request.get('/data/vllm/ci/operations_v2/amd_test_health.json');
    const { amd_test_health: amd } = await response.json();
    await route.fulfill({
      json: { reliability: { available: true, source_pipeline: 'ci', job_scope: 'amd_gpu', hardware_scope: 'amd_mi_gpu', group_catalog: amd.group_catalog } },
    });
  });
}

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
    await expect(page.getByTestId('parity-config-note')).toHaveText(note);
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
    const stats = Object.fromEntries(expected.stats);
    // The configured total is the denominator of the pass-rate ring.
    await expect(page.getByTestId('hardware-rate')).toContainText(`${stats.Passing} / ${stats['Test groups']}`);
    for (const [label, count] of expected.stats.filter(([label]) => label !== 'Test groups')) {
      const mode = { Passing: 'passing', Failing: 'failing', 'No signal': 'no-signal' }[label];
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
    expect(metrics).toEqual(expected.architectures.map((a) => a.metrics.map(shown)));

    const matrix = page.getByTestId('route-matrix');
    await expect(matrix.getByTestId('matrix-sort-description')).toHaveText(expected.description);
    expect(await tableRows(matrix, expected.rows[0].length)).toEqual(shownRows(expected.rows));
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
      expect(await tableRows(page.getByTestId('route-matrix'), expected.rows[0].length)).toEqual(shownRows(expected.rows));
      await page.goto(`/#/health?view=coverage&sort=${sort}`);
      await expect(page.getByTestId('matrix-sort-description')).toHaveText(expected.description);
    });
  }

  test('drill-downs: group browser filters, group evidence, architecture routes, and test-group detail', async ({ page }) => {
    const { browsers, evidence, architecture, group } = golden['coverage drill-downs'];
    await page.goto('/#/health?view=coverage');
    const kpiOf = (mode) => page.getByTestId(mode === 'all' ? 'hardware-rate' : `hardware-stat-${mode}`);
    for (const [mode, browser] of Object.entries(browsers)) {
      await kpiOpen(kpiOf(mode)).click();
      const sheet = topSheet(page);
      await expect(sheet.getByRole('heading').first()).toHaveText(browser.title);
      await expect(sheet.locator('[data-slot=row-count]')).toHaveText(browser.count);
      expect(await tableRows(sheet, 7)).toEqual(shownRows(browser.rows));
      await closeSheets(page);
    }
    // Changing a filter in place matches the golden browser of that mode.
    await kpiOpen(page.getByTestId('hardware-stat-failing')).click();
    await page.getByRole('combobox', { name: 'Filter configured test-group status' }).click();
    await page.getByRole('option', { name: 'Passing', exact: true }).click();
    await expect(topSheet(page).locator('[data-slot=row-count]')).toHaveText(browsers.passing.count);
    expect(await tableRows(topSheet(page), 7)).toEqual(shownRows(browsers.passing.rows));
    await page.getByRole('combobox', { name: 'Filter configured test-group status' }).click();
    await page.getByRole('option', { name: 'Failing', exact: true }).click();
    await expect(topSheet(page).locator('[data-slot=row-count]')).toHaveText(browsers.failing.count);
    await topSheet(page).locator('tbody tr').first().getByRole('button').first().click();
    await expect(sheets(page)).toHaveCount(2);
    const detail = topSheet(page);
    await expect(detail.getByRole('heading').first()).toHaveText(evidence.title);
    expect(lower(await detailFields(detail))).toEqual(lower(evidence.fields));
    expect((await tableRows(detail, 4)).map((cells) => [cells[0], cells[2], cells[3]])).toEqual(shownRows(evidence.definitions));
    await closeSheets(page);

    await page.getByTestId('architecture-row').first().click();
    let sheet = topSheet(page);
    await expect(sheet.getByRole('heading').first()).toHaveText(architecture.title);
    await expect(sheet).toContainText(architecture.subtitle);
    expect(await tableRows(sheet, 4)).toEqual(shownRows(architecture.rows));
    await sheet.locator('tbody tr').first().getByRole('button').first().click();
    await expect(sheets(page)).toHaveCount(2);
    sheet = topSheet(page);
    await expect(sheet.getByRole('heading').first()).toHaveText(group.title);
    expect(lower(await detailFields(sheet))).toEqual(lower(group.fields));
    await sheet.getByRole('button', { name: 'Load 30-day run history' }).click();
    // The fixture ledger is unpublished: the sheet says so instead of offering the load again.
    await expect(sheet.getByTestId('group-history-empty')).toContainText('not published in this snapshot');
    await expect(sheet.getByRole('button', { name: 'Load 30-day run history' })).toHaveCount(0);
  });

  test('a loaded 30-day ledger shows the outcome trend, cohort toggle, and filters', async ({ page }) => {
    const errors = collectErrors(page);
    await routeLedger(page);
    await page.goto('/#/health?view=coverage');
    await page.getByTestId('route-matrix').locator('tbody tr').first().click();
    await topSheet(page).getByRole('button', { name: 'Load 30-day run history' }).click();
    const sheet = topSheet(page);
    await expect(sheet.getByTestId('outcome-strip').locator('a')).not.toHaveCount(0);
    await expect(sheet.locator('[data-slot=line-chart]')).not.toHaveCount(0);
    await sheet.getByRole('button', { name: 'Nightly only' }).click();
    await sheet.getByRole('button', { name: 'All main' }).click();
    await expect(sheet.getByRole('button', { name: 'All main' })).toHaveAttribute('aria-pressed', 'true');
    await sheet.getByRole('combobox', { name: 'Result' }).click();
    await page.getByRole('option', { name: 'Failures only' }).click();
    await expect(sheet.locator('[data-slot=row-count]')).toContainText(' of ');
    expect(errors).toEqual([]);
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
    const legend = await page
      .getByTestId('mirror-families')
      .locator('[data-slot=donut-legend-item]')
      .evaluateAll((nodes) => nodes.map((n) => ['legend-value', 'legend-label'].map((slot) => n.querySelector(`[data-slot=${slot}]`).textContent.trim())));
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

    // The golden label "Browse all 1 test-area files" now reads with the right plural.
    await page.getByRole('button', { name: areaBrowser.label.replace(/^Browse all 1 test-area files$/, 'Browse 1 test-area file').replace(/^Browse all /, 'Browse ') }).click();
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
const drillRow = (page) => topSheet(page).locator('tbody tr').first().click();
const SHEETS = [
  ['logical catalog', '/#/health', (page) => kpiOpen(page.getByTestId('logical-ring')).click()],
  ['logical group', '/#/health', async (page) => { await kpiOpen(page.getByTestId('logical-ring')).click(); await drillRow(page); }, 2],
  ['exact job', '/#/health', (page) => page.getByTestId('latest-failures').locator('tbody tr').first().click()],
  ['nightly movement', '/#/health', async (page) => { await page.getByTestId('movement-chart').getByRole('button', { name: /Inspect/ }).click(); await page.getByRole('menuitem').last().click(); }],
  ['parity inventory', '/#/health?view=parity', (page) => kpiOpen(page.getByTestId('parity-ring')).click()],
  ['parity group', '/#/health?view=parity', async (page) => { await page.getByTestId('gap-area').first().click(); await drillRow(page); }, 2],
  ['group browser', '/#/health?view=coverage', (page) => kpiOpen(page.getByTestId('hardware-rate')).click()],
  ['group evidence', '/#/health?view=coverage', async (page) => { await kpiOpen(page.getByTestId('hardware-stat-failing')).click(); await drillRow(page); }, 2],
  ['architecture routes', '/#/health?view=coverage', (page) => page.getByTestId('architecture-row').first().click()],
  ['group history', '/#/health?view=coverage', (page) => page.getByTestId('route-matrix').locator('tbody tr').first().click()],
  ['group history ledger', '/#/health?view=coverage', async (page) => {
    await routeLedger(page);
    await page.reload();
    await page.getByTestId('route-matrix').locator('tbody tr').first().click();
    await topSheet(page).getByRole('button', { name: 'Load 30-day run history' }).click();
    await topSheet(page).locator('[data-slot=line-chart]').first().waitFor();
  }],
  ['complete route matrix', '/#/health?view=coverage', (page) => page.getByRole('button', { name: 'Browse complete route matrix' }).click()],
  ['mirror detail', '/#/health?view=mirrors', (page) => page.getByTestId('mirror-inventory').locator('tbody tr').first().click()],
  ['mirror areas', '/#/health?view=mirrors', (page) => page.getByRole('button', { name: /^Browse .*test-area file/ }).click()],
];

for (const theme of ['light', 'dark']) {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    test(`CI Health sheets meet the quality gates (${theme}, ${viewport.width}px)`, async ({ page }) => {
      test.setTimeout(120_000);
      await setTheme(page, theme);
      await page.setViewportSize(viewport);
      const errors = collectErrors(page);
      for (const [name, route, open, depth = 1] of SHEETS) {
        await page.goto(route);
        await page.getByRole('heading', { level: 2 }).first().waitFor();
        await settleAnimations(page);
        await open(page);
        await expect(sheets(page), name).toHaveCount(depth);
        await topSheet(page).getByRole('heading').first().waitFor();
        await expectQuality(page, errors);
        await closeSheets(page);
      }
    });
  }
}
