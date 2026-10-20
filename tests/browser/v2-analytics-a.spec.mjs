import { expect, test } from '@playwright/test';

import { readGolden } from './golden/read.mjs';

// CI Analytics "AMD test health", "AMD nightly latency", and "AMD nightlies"
// against the golden page facts of the fixture snapshot.
const golden = readGolden('analytics-a');

const clean = (text) => (text ?? '').replace(/\s+/g, ' ').trim();

/** [label, value, headline, footnote] of every KPI card under root. */
const kpis = (root) =>
  root.locator('[data-slot="kpi-card"]').evaluateAll((cards) =>
    cards.map((card) =>
      ['kpi-label', 'kpi-value', 'kpi-headline', 'kpi-footnote'].map((slot) => {
        const nodes = card.querySelectorAll(`[data-slot="${slot}"]`);
        return nodes.length ? nodes[nodes.length - 1].textContent.replace(/\s+/g, ' ').trim() : null;
      }),
    ),
  );

const cellTexts = (rows) =>
  rows.evaluateAll((trs) => trs.map((tr) => [...tr.querySelectorAll('td')].map((td) => td.innerText.replace(/\s+/g, ' ').trim()).filter(Boolean)));

// Hovering a bar must show an Unovis tooltip naming the series; the chart wrapper is excluded.
async function hoverBar(page, chart, series) {
  const tooltip = page.locator('[class*="-tooltip"]:not([data-slot="chart"]):visible').filter({ hasText: series[0] });
  const bars = chart.locator('path[class*="-bar"]');
  const count = await bars.count();
  let hovered = null;
  for (let index = 0; index < count && !(await tooltip.last().isVisible()); index += 1) {
    const box = await bars.nth(index).boundingBox();
    if (!box || box.width < 2 || box.height < 2) continue;
    hovered = bars.nth(index);
    await hovered.hover();
    await page.waitForTimeout(100);
  }
  const tip = tooltip.last();
  await expect(tip).toBeVisible();
  for (const name of series) await expect(tip).toContainText(name);
  expect(await hovered.evaluate((el) => getComputedStyle(el).cursor)).toBe('pointer');
  await page.mouse.move(0, 0);
}

/** Picks one entry of a chart's keyboard Inspect list. */
async function inspect(page, chart, entry) {
  await chart.getByRole('button', { name: /^Inspect / }).click();
  await page.getByRole('menuitem').filter({ hasText: entry }).first().click();
  return page.getByRole('dialog').last();
}

async function closeSheets(page) {
  while ((await page.getByRole('dialog').count()) > 0) {
    await page.keyboard.press('Escape');
    await page.waitForTimeout(150);
  }
}

test.describe('CI Analytics matches the golden page facts', () => {
  test('AMD test health: KPIs, catalog, failures, and charts', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const { strip, clusters, browseAll, priority, priorityMeta, attentionRows, hardwareEvidence, logicalCount } = golden.groups;
    const name = (row) => row[0].replace(/ \S+ - \S+$/, '');

    await page.goto('/#/analytics');
    const view = page.getByTestId('analytics-groups');
    const cards = await kpis(view.getByTestId('groups-kpis'));
    expect(cards.map((card) => card[3])).toEqual(strip.map((item) => item[2]));
    expect([cards[0][1], cards[2][1], cards[3][1]]).toEqual([strip[0][1], strip[2][1], strip[3][1]]);
    await expect(view.getByTestId('kpi-amd-health-test-groups')).toContainText(strip[1][1].replace(' passing', ''));

    const catalog = await kpis(view.getByTestId('catalog'));
    expect(catalog.map(([label, value, headline, footnote]) => [label, value, headline, footnote])).toEqual(clusters);
    await expect(view.getByTestId('browse-all')).toHaveText(browseAll);

    const failures = view.getByTestId('priority');
    await expect(failures).toContainText(priorityMeta);
    const rows = await cellTexts(failures.locator('tbody tr'));
    expect(rows.map((row) => [row[0].startsWith(name(priority[rows.indexOf(row)])), row[2], row[3], row[4]])).toEqual(
      priority.map((row) => [true, row[3], row[5], row[2]]),
    );
    // Every row opens its exact job evidence.
    await failures.locator('tbody tr').first().locator('td').nth(2).click();
    await expect(page.getByRole('dialog')).toContainText('Main CI AMD gating-job outcomes');
    await closeSheets(page);

    await view.getByTestId('cluster').first().click();
    const sheet = page.getByRole('dialog');
    const attention = await cellTexts(sheet.locator('tbody tr'));
    expect(attention.map((row) => row[0])).toEqual(attentionRows.map((row) => `${name(row)} ${row[6]}`));
    await sheet.getByRole('combobox', { name: 'Result' }).click();
    await page.getByRole('option', { name: 'All retained variants' }).click();
    await expect(sheet.locator('[data-slot="row-count"]')).toHaveText(`${browseAll.match(/\d+/)[0]} of ${browseAll.match(/\d+/)[0]} rows`);
    await sheet.locator('tbody tr').first().locator('td').nth(2).click();
    await expect(page.getByRole('dialog').last()).toContainText('Main CI AMD gating-job outcomes');
    await page.getByRole('dialog').last().getByRole('button', { name: 'Back' }).click();
    await expect(page.getByRole('dialog')).toHaveCount(1);
    await closeSheets(page);

    const hardware = view.getByTestId('hardware-health-chart');
    await hoverBar(page, hardware, ['Passing', 'Hard fail', 'Not in latest']);
    const hardwareSheet = await inspect(page, hardware, hardwareEvidence[0][0]);
    await expect(hardwareSheet).toContainText(`${hardwareEvidence[0][0]} AMD job variants`);
    await closeSheets(page);

    const builds = view.getByTestId('build-health-chart');
    await hoverBar(page, builds, ['Passing', 'Soft fail', 'Hard fail', 'Unknown']);
    await expect(await inspect(page, builds, strip[0][1])).toContainText(`AMD nightly ${strip[0][1]}`);
    await closeSheets(page);

    await view.getByTestId('kpi-amd-health-test-groups').click();
    await expect(page.getByRole('dialog')).toContainText(`${logicalCount.split(' of ')[0]} rows`);
    await closeSheets(page);

    await page.getByRole('button', { name: 'About this view' }).click();
    await expect(page.getByRole('dialog')).toContainText('Upstream results are never used as AMD passes.');
    expect(errors).toEqual([]);
  });

  test('AMD nightly latency: cohort, KPIs, table, and nightly evidence', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const { cohort, strip, meta, rows, evidenceStrip, evidenceRows, chartRows } = golden.latency;

    await page.goto('/#/analytics?view=latency');
    const view = page.getByTestId('analytics-latency');
    await expect(view.getByTestId('latency-cohort')).toHaveText(cohort);
    const cards = await kpis(view.getByTestId('latency-kpis'));
    expect(cards.slice(0, 2).map((card) => [card[1], card[3]])).toEqual(strip.map((item) => [item[1], item[2]]));
    const table = view.getByTestId('latency-table');
    await expect(table).toContainText(meta);
    expect(await cellTexts(table.locator('tbody tr'))).toEqual(rows.slice(0, 12));

    await table.locator('tbody tr').first().locator('td').nth(1).click();
    const sheet = page.getByRole('dialog');
    await expect(sheet.locator('[data-slot="evidence-stat-value"]')).toHaveText(evidenceStrip[0][1]);
    await expect(sheet.locator('[data-slot="evidence-stats"]')).toContainText(evidenceStrip[0][2]);
    expect(await cellTexts(sheet.locator('tbody tr'))).toEqual(evidenceRows);
    await closeSheets(page);

    const chart = view.getByTestId('latency-chart');
    await hoverBar(page, chart, ['AMD MI median', 'min']);
    const slowest = chartRows.at(-1)[0];
    await expect(await inspect(page, chart, slowest)).toContainText(slowest.charAt(0).toUpperCase() + slowest.slice(1));
    expect(errors).toEqual([]);
  });

  test('AMD nightlies: KPIs, movement table, chart, and build evidence', async ({ page }) => {
    const { strip, note, rows, chartRows } = golden.nightlies;

    await page.goto('/#/analytics?view=nightlies');
    const view = page.getByTestId('analytics-nightlies');
    const cards = await kpis(view.getByTestId('nightlies-kpis'));
    expect(cards.map((card) => [card[1], card[3].toLowerCase()])).toEqual(strip.map((item) => [item[1], item[2].toLowerCase()]));
    await expect(view.getByTestId('nightlies-note')).toHaveCount(0);
    await page.getByRole('button', { name: 'About this view' }).click();
    await expect(page.getByRole('dialog')).toContainText(note.split('. ').at(-2));
    await page.keyboard.press('Escape');

    const table = view.getByTestId('nightlies-table');
    const shown = (await cellTexts(table.locator('tbody tr'))).map((row) => row.map((cell) => cell.toLowerCase()));
    expect(shown).toEqual(rows.map((row) => row.map((cell) => (cell === '-' ? '—' : cell.toLowerCase()))));

    const chart = view.getByTestId('nightlies-chart');
    await hoverBar(page, chart, ['Recurring failure']);
    await chart.getByRole('button', { name: /^Inspect / }).click();
    await expect(page.getByRole('menuitem')).toHaveCount(chartRows.length);
    await page.getByRole('menuitem').filter({ hasText: chartRows[0][0] }).click();
    await expect(page.getByRole('dialog')).toContainText(`AMD nightly ${chartRows[0][0]}`);
    await closeSheets(page);

    await table.locator('tbody tr').first().locator('td').nth(4).getByRole('button').click();
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText(`AMD nightly ${rows[0][0]}`);
    await expect(sheet).toContainText('Recurring failures evidence');
  });

  test('old CI Analytics links open the routed views', async ({ page }) => {
    await page.goto('/?ops_analytics_view=nightlies#ci-analytics');
    await expect(page).toHaveURL(/#\/analytics\?view=nightlies$/);
    await expect(page.getByTestId('analytics-nightlies')).toBeVisible();
    await page.goto('/?ops_analytics_view=groups&ops_analytics_pipeline=ci#ci-analytics');
    await expect(page).toHaveURL(/#\/analytics$/);
    await expect(page.getByTestId('analytics-groups')).toBeVisible();
  });
});
