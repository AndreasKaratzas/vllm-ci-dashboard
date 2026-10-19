import { expect, test } from '@playwright/test';

import { readGolden } from './golden/read.mjs';

// CI Analytics "AMD test health", "AMD nightly latency", and "AMD nightlies"
// against the golden page facts of the fixture snapshot.
const golden = readGolden('analytics-a');

const v2Strip = (root) =>
  root.locator('[data-part="label"]').evaluateAll((labels) =>
    labels.map((label) => {
      const card = label.closest('[data-slot="card"]');
      return [label.textContent.trim(), card.querySelector('[data-part="value"]').textContent.trim(), card.querySelector('[data-part="meta"]').textContent.trim()];
    }),
  );

const cellTexts = (rows) =>
  rows.evaluateAll((trs) => trs.map((tr) => [...tr.querySelectorAll('td')].map((td) => td.innerText.replace(/\s+/g, ' ').trim())));

// Hovering a bar must show an Unovis tooltip naming the hovered row and its series values.
async function hoverBar(page, chart, label, series) {
  await chart.locator('path[class*=bar]').first().hover();
  const tip = page.locator('[class*="-tooltip"]').filter({ hasText: label }).last();
  await expect(tip).toBeVisible();
  for (const name of series) await expect(tip).toContainText(name);
  await page.mouse.move(0, 0);
}

async function chartEvidence(page, root, name) {
  await root.getByRole('button', { name }).click();
  return page.getByRole('dialog').last();
}

test.describe('CI Analytics matches the golden page facts', () => {
  test('AMD test health: KPIs, clusters, failures, catalog, and charts', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const { strip, clusters, browseAll, priority, priorityMeta, attentionRows, attentionCaption, searchedRows, hardwareEvidence, logicalCount } = golden.groups;

    await page.goto('/#/analytics');
    const view = page.getByTestId('analytics-groups');
    await expect(view.getByTestId('groups-kpis')).toBeVisible();
    expect(await v2Strip(view.getByTestId('groups-kpis'))).toEqual(strip);
    const v2Clusters = await view.getByTestId('cluster').evaluateAll((tiles) =>
      tiles.map((t) => ['label', 'count', 'description', 'meta'].map((p) => t.querySelector(`[data-part="${p}"]`).textContent.trim())),
    );
    expect(v2Clusters).toEqual(clusters);
    await expect(view.getByTestId('browse-all')).toHaveText(browseAll);
    await expect(view.getByTestId('priority')).toContainText(priorityMeta);
    const v2Priority = await cellTexts(view.getByTestId('priority').locator('tbody tr'));
    expect(v2Priority.map((r) => r.map((c) => c.replace(/ ↗$/, '')))).toEqual(priority);

    await view.getByTestId('cluster').first().click();
    const catalog = page.getByTestId('amd-catalog');
    await expect(catalog).toBeVisible();
    await expect(catalog.getByTestId('catalog-caption')).toHaveText(attentionCaption);
    const v2Attention = await cellTexts(catalog.getByTestId('catalog-row'));
    expect(v2Attention.map((r) => r.map((c) => c.replace(/ ↗$/, '')))).toEqual(attentionRows);
    await catalog.getByRole('combobox').click();
    await page.getByRole('option', { name: 'All retained variants' }).click();
    await catalog.getByPlaceholder('Search AMD job variant, hardware, or queue').fill('routed');
    const v2Searched = await cellTexts(catalog.getByTestId('catalog-row'));
    expect(v2Searched.map((r) => r.map((c) => c.replace(/ ↗$/, '')))).toEqual(searchedRows);
    await catalog.getByTestId('catalog-row').first().getByRole('button').first().click();
    await expect(page.getByRole('dialog').last()).toContainText('Main CI AMD gating-job outcomes');
    await page.getByRole('dialog').last().getByRole('button', { name: 'Close' }).click();
    await expect(page.getByRole('dialog')).toHaveCount(1);
    await page.getByRole('dialog').getByRole('button', { name: 'Close' }).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);

    const evidence = await chartEvidence(page, view.getByTestId('hardware-health-chart'), /Inspect 2 observations/);
    const v2Hardware = await cellTexts(evidence.locator('tbody tr'));
    expect(v2Hardware.map((r) => r.slice(0, 3))).toEqual(hardwareEvidence.map((r) => r.slice(0, 3)));
    await page.keyboard.press('Escape');
    await hoverBar(page, view.getByTestId('hardware-health-chart'), v2Hardware[0][0], ['Passing', 'Hard fail', 'Not in latest']);

    await view.getByTestId('kpi-amd-health-test-groups').click();
    await expect(page.getByRole('dialog')).toContainText(`${logicalCount.split(' of ')[0]} rows`);
    expect(errors).toEqual([]);
  });

  test('AMD nightly latency: cohort, KPIs, table, and nightly evidence', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const { cohort, strip, meta, rows, evidenceStrip, evidenceRows, chartRows } = golden.latency;

    await page.goto('/#/analytics?view=latency');
    const view = page.getByTestId('analytics-latency');
    await expect(view.getByTestId('latency-cohort')).toHaveText(cohort);
    expect(await v2Strip(view.getByTestId('latency-kpis'))).toEqual(strip);
    const table = view.getByTestId('latency-table');
    await expect(table).toContainText(meta);
    expect(await cellTexts(table.locator('tbody tr'))).toEqual(rows.slice(0, 12));

    await table.locator('tbody tr').first().getByRole('button').first().click();
    const sheet = page.getByTestId('latency-evidence');
    await expect(sheet.getByTestId('latency-evidence-value')).toHaveText(evidenceStrip[0][1]);
    await expect(sheet.getByTestId('latency-evidence-meta')).toHaveText(evidenceStrip[0][2]);
    expect(await cellTexts(sheet.getByTestId('latency-evidence-row'))).toEqual(evidenceRows);
    await page.keyboard.press('Escape');

    const evidence = await chartEvidence(page, view.getByTestId('latency-chart'), /Inspect \d+ observations/);
    expect((await cellTexts(evidence.locator('tbody tr'))).map((r) => [r[0], r[2]])).toEqual(chartRows.map((r) => [r[0], r[2]]));
    await page.keyboard.press('Escape');
    await hoverBar(page, view.getByTestId('latency-chart'), chartRows[0][0], ['AMD']);
    expect(errors).toEqual([]);
  });

  test('AMD nightlies: KPIs, movement table, chart evidence, and build detail', async ({ page }) => {
    const { strip, note, rows, chartRows, buildTitle } = golden.nightlies;

    await page.goto('/#/analytics?view=nightlies');
    const view = page.getByTestId('analytics-nightlies');
    expect(await v2Strip(view.getByTestId('nightlies-kpis'))).toEqual(strip);
    await expect(view.getByTestId('nightlies-note')).toHaveText(note.replace(/\s+/g, ' '));
    const table = view.getByTestId('nightlies-table');
    expect(await cellTexts(table.locator('tbody tr'))).toEqual(rows);

    const evidence = await chartEvidence(page, view.getByTestId('nightlies-chart'), /Inspect \d+ observations/);
    expect((await cellTexts(evidence.locator('tbody tr'))).map((r) => [r[0], r[1], r[2]])).toEqual(chartRows.map((r) => [r[0], r[1], r[2]]));
    await page.keyboard.press('Escape');

    await table.locator('tbody tr').first().locator('td').nth(4).getByRole('button').click();
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText(buildTitle);
    await expect(sheet).toContainText('Recurring failures');
  });

  test('legacy CI Analytics links open the ported v2 views', async ({ page }) => {
    await page.goto('/?ops_analytics_view=nightlies#ci-analytics');
    await expect(page).toHaveURL(/#\/analytics\?view=nightlies$/);
    await expect(page.getByTestId('analytics-nightlies')).toBeVisible();
    await page.goto('/?ops_analytics_view=groups&ops_analytics_pipeline=ci#ci-analytics');
    await expect(page).toHaveURL(/#\/analytics$/);
    await expect(page.getByTestId('analytics-groups')).toBeVisible();
  });
});
