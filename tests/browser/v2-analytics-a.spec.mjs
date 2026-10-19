import { expect, test } from '@playwright/test';

// CI Analytics "AMD test health", "AMD nightly latency", and "AMD nightlies":
// the same facts must be read from classic.html and from v2 on one snapshot.

async function openClassic(page, view) {
  await page.goto(`/classic.html?ops_analytics_view=${view}#ci-analytics`);
  const host = page.locator('#ci-analytics-view[data-render-state="ready"]');
  await host.waitFor();
  return host;
}

const classicStrip = (root) =>
  root.locator('.ops-status-item').evaluateAll((items) =>
    items.map((el) => [
      el.querySelector('.ops-stat-label').textContent.trim(),
      el.querySelector('.ops-stat-value').textContent.trim(),
      (el.querySelector('.ops-stat-meta')?.textContent ?? '').trim(),
    ]),
  );

const v2Strip = (root) =>
  root.locator('[data-part="label"]').evaluateAll((labels) =>
    labels.map((label) => {
      const card = label.closest('[data-slot="card"]');
      return [label.textContent.trim(), card.querySelector('[data-part="value"]').textContent.trim(), card.querySelector('[data-part="meta"]').textContent.trim()];
    }),
  );

const classicOverlay = (page) => page.locator('.ops-overlay:not([hidden])').last();

const cellTexts = (rows) =>
  rows.evaluateAll((trs) => trs.map((tr) => [...tr.querySelectorAll('td')].map((td) => td.innerText.replace(/\s+/g, ' ').trim())));

async function chartEvidence(page, root, name) {
  await root.getByRole('button', { name }).click();
  return page.getByRole('dialog').last();
}

test.describe('v2 CI Analytics matches the classic page on the same snapshot', () => {
  test('AMD test health: KPIs, clusters, failures, catalog, and charts', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));

    const classic = await openClassic(page, 'groups');
    const strip = await classicStrip(classic);
    const clusters = await classic.locator('.ops-cluster-tile').evaluateAll((tiles) =>
      tiles.map((t) => ['.ops-cluster-label', '.ops-cluster-count', '.ops-cluster-description', '.ops-cluster-meta'].map((s) => t.querySelector(s).textContent.trim())),
    );
    const browseAll = (await classic.locator('.ops-cluster-section header button').innerText()).trim();
    const priority = await cellTexts(classic.locator('.ops-amd-priority tbody tr'));
    const priorityMeta = (await classic.locator('.ops-amd-priority .ops-panel-meta').innerText()).trim();

    await classic.locator('.ops-cluster-tile').first().click();
    const attentionRows = await cellTexts(classicOverlay(page).locator('tbody tr'));
    const attentionCaption = (await classicOverlay(page).locator('.ops-table-caption').innerText()).trim();
    await classicOverlay(page).locator('select').selectOption('all');
    await classicOverlay(page).locator('input[type="search"]').fill('routed');
    const searchedRows = await cellTexts(classicOverlay(page).locator('tbody tr'));
    await page.keyboard.press('Escape');

    await classic.locator('canvas[data-chart-key="analytics-amd-hardware-health"]').locator('xpath=ancestor::*[contains(@class,"ops-chart-stage")]').locator('.ops-chart-evidence-action').click();
    const hardwareEvidence = await cellTexts(classicOverlay(page).locator('tbody tr'));
    await page.keyboard.press('Escape');
    await classic.locator('.ops-status-item', { hasText: 'LATEST AMD TEST GROUPS' }).click();
    const logicalCount = (await classicOverlay(page).locator('.ops-browser-count').innerText()).trim();

    expect(strip.length).toBe(4);
    expect(clusters.length).toBe(4);
    expect(priority.length).toBeGreaterThan(0);
    expect(attentionRows.length).toBeGreaterThan(0);
    expect(searchedRows.length).toBeGreaterThan(0);
    expect(hardwareEvidence.length).toBeGreaterThan(0);

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

    await view.getByTestId('kpi-amd-health-test-groups').click();
    await expect(page.getByRole('dialog')).toContainText(`${logicalCount.split(' of ')[0]} rows`);
    expect(errors).toEqual([]);
  });

  test('AMD nightly latency: cohort, KPIs, table, and nightly evidence', async ({ page }) => {
    const classic = await openClassic(page, 'latency');
    const cohort = (await classic.locator('.ops-window-context').innerText()).trim();
    const strip = await classicStrip(classic);
    const panel = classic.locator('.ops-panel', { hasText: 'Recent AMD MI nightly latency' });
    const meta = (await panel.locator('.ops-panel-meta').innerText()).trim();
    const rows = await cellTexts(panel.locator('tbody tr'));
    await panel.locator('tbody tr').first().getByRole('button').first().click();
    const evidenceStrip = await classicStrip(classicOverlay(page));
    const evidenceRows = await cellTexts(classicOverlay(page).locator('tbody tr'));
    await page.keyboard.press('Escape');
    await classic.locator('.ops-chart-evidence-action').first().click();
    const chartRows = await cellTexts(classicOverlay(page).locator('tbody tr'));

    expect(rows.length).toBeGreaterThan(0);
    expect(evidenceRows.length).toBeGreaterThan(0);
    expect(chartRows.length).toBeGreaterThan(0);

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
  });

  test('AMD nightlies: KPIs, movement table, chart evidence, and build detail', async ({ page }) => {
    const classic = await openClassic(page, 'nightlies');
    const strip = await classicStrip(classic);
    const note = (await classic.locator('.ops-evidence-note').first().innerText()).trim();
    const rows = await cellTexts(classic.locator('table.ops-table tbody tr'));
    await classic.locator('.ops-chart-evidence-action').first().click();
    const chartRows = await cellTexts(classicOverlay(page).locator('tbody tr'));
    await page.keyboard.press('Escape');
    await classic.locator('table.ops-table tbody tr').first().locator('td').nth(4).getByRole('button').click();
    const buildTitle = (await classicOverlay(page).locator('.ops-overlay-title').innerText()).trim();

    expect(strip.length).toBe(5);
    expect(rows.length).toBeGreaterThan(0);
    expect(chartRows.length).toBeGreaterThan(0);

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
