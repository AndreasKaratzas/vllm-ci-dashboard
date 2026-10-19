import { expect, test } from '@playwright/test';

// Perf Eval reads data/vllm/perf_eval/perf_eval.json, which the fixture site
// copies from the frozen repository snapshot. Every fact below is read from
// classic.html and from v2 on that same file.

const squash = (s) => String(s || '').replace(/\s+/g, ' ').trim();

async function classicPerf(page, query = '') {
  await page.goto(`/classic.html${query}#ci-perf-eval`);
  const host = page.locator('#ci-perf-eval-view[data-render-state="ready"]');
  await host.waitFor();
  return host;
}

async function tableRows(table) {
  return table.locator('tbody tr').evaluateAll((rows) =>
    rows.map((row) => Array.from(row.querySelectorAll('td')).map((td) => td.textContent.replace(/\s+/g, ' ').trim())),
  );
}

async function classicTiles(host) {
  return host.locator('.ops-perf-config').evaluateAll((configs) =>
    configs.map((config) => ({
      label: config.querySelector('h3').textContent.trim(),
      meta: config.querySelector('.ops-panel-meta').textContent.trim(),
      tiles: Array.from(config.querySelectorAll('.ops-perf-metric')).map((tile) =>
        [
          tile.querySelector('.ops-perf-metric-name').textContent,
          tile.querySelector('.ops-perf-delta').textContent,
          tile.querySelector('.ops-perf-metric-value').textContent,
          tile.querySelector('.ops-perf-direction').textContent,
          (tile.className.match(/is-(success|danger|neutral)/) || [])[0],
        ].map((s) => s.trim()).join('|'),
      ),
    })),
  );
}

async function v2Tiles(page) {
  return page.getByTestId('perf-config').evaluateAll((configs) =>
    configs.map((config) => ({
      label: config.querySelector('[data-testid="perf-config-label"]').textContent.trim(),
      meta: config.querySelector('[data-testid="perf-config-meta"]').textContent.trim(),
      tiles: Array.from(config.querySelectorAll('[data-testid="perf-tile"]')).map((tile) =>
        [
          tile.querySelector('[data-testid="perf-tile-label"]').textContent,
          tile.querySelector('[data-testid="perf-tile-delta"]').textContent,
          tile.querySelector('[data-testid="perf-tile-value"]').textContent,
          tile.querySelector('[data-testid="perf-tile-direction"]').textContent,
          tile.dataset.tone,
        ].map((s) => s.trim()).join('|'),
      ),
    })),
  );
}

test.describe('v2 Perf Eval matches the classic page on the same snapshot', () => {
  test('summary strip and model overview', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));

    const host = await classicPerf(page);
    const strip = await host.locator('.ops-status-item').evaluateAll((items) =>
      items.map((item) => ({
        label: item.querySelector('.ops-stat-label').textContent.trim().toLowerCase(),
        value: item.querySelector('.ops-stat-value').textContent.trim(),
        meta: item.querySelector('.ops-stat-meta').textContent.trim(),
      })),
    );
    const classicRows = await tableRows(host.locator('table.ops-table'));
    expect(strip.length).toBe(4);
    expect(classicRows.length).toBeGreaterThan(0);

    await page.goto('/#/perf');
    const overview = page.getByTestId('perf-overview');
    await expect(overview.locator('tbody tr').first()).toBeVisible();
    for (const [id, item] of [['models', strip[0]], ['nightlies', strip[1]], ['points', strip[2]], ['hardware', strip[3]]]) {
      const card = page.getByTestId(`kpi-perf-${id}`);
      await expect(card).toContainText(new RegExp(item.label, 'i'));
      expect(squash(await card.locator('[data-slot="card-title"]').innerText())).toBe(item.value);
      await expect(card).toContainText(item.meta);
    }
    const v2Rows = await tableRows(overview.locator('table'));
    expect(v2Rows).toEqual(classicRows);
    await expect(overview).toContainText(`${classicRows.length} retained model families`);

    // Drill-down: the AMD MODELS card lists the model families, as classic does (reversed).
    await page.getByTestId('kpi-perf-models').getByRole('button', { name: 'Inspect model families' }).click();
    const sheet = page.getByRole('dialog');
    const families = await sheet.locator('tbody tr td:first-child').allInnerTexts();
    expect(families.map(squash)).toEqual(classicRows.map((row) => row[0]).reverse());
    expect(errors).toEqual([]);
  });

  test('model performance tiles under each hardware filter', async ({ page }) => {
    const host = await classicPerf(page);
    const models = (await tableRows(host.locator('table.ops-table'))).map((row) => row[0]);
    const multi = models.find((_, i) => i === 0);
    expect(multi).toBeTruthy();
    for (const device of ['all', 'mi300x', 'mi355x']) {
      const query = `?ops_perf_model=${encodeURIComponent(multi)}${device === 'all' ? '' : `&ops_perf_device=${device}`}`;
      const classic = await classicPerf(page, query);
      const header = {
        title: squash(await classic.locator('.ops-perf-model-title').innerText()),
        provenance: await classic.locator('.ops-perf-provenance > *').allTextContents(),
        nightlies: squash(await classic.locator('.ops-perf-nightly-count').innerText()),
      };
      const tiles = await classicTiles(classic);
      expect(tiles.flatMap((c) => c.tiles).length).toBeGreaterThan(0);

      await page.goto(`/#/perf?model=${encodeURIComponent(multi)}${device === 'all' ? '' : `&device=${device}`}`);
      await expect(page.getByTestId('perf-tile').first()).toBeVisible();
      expect(squash(await page.getByTestId('perf-model-title').innerText())).toBe(header.title);
      expect(header.provenance.length).toBeGreaterThan(0);
      expect(await page.getByTestId('perf-provenance').locator('> *').allTextContents()).toEqual(header.provenance);
      expect(squash(await page.getByTestId('perf-nightlies').innerText())).toBe(header.nightlies);
      expect(await v2Tiles(page)).toEqual(tiles);
    }
  });

  test('metric history sheet', async ({ page }) => {
    const host = await classicPerf(page);
    const model = (await tableRows(host.locator('table.ops-table')))[1][0];
    const classic = await classicPerf(page, `?ops_perf_model=${encodeURIComponent(model)}`);
    await classic.locator('.ops-perf-metric').nth(2).getByRole('button', { name: 'Inspect history' }).click();
    const overlay = page.locator('.ops-overlay:not([hidden]) [role="dialog"]');
    const title = squash(await overlay.locator('.ops-overlay-title').innerText());
    const stats = await overlay.locator('.ops-evidence-stat').evaluateAll((items) =>
      items.map((item) => `${item.querySelector('.ops-stat-label').textContent.trim()}|${item.querySelector('.ops-stat-value').textContent.trim()}`),
    );
    const caption = squash(await overlay.locator('caption').innerText());
    const rows = (await tableRows(overlay.locator('table'))).slice(0, 15);
    expect(rows.length).toBeGreaterThan(1);

    await page.goto(`/#/perf?model=${encodeURIComponent(model)}`);
    await page.getByTestId('perf-tile').nth(2).getByRole('button', { name: 'Inspect history' }).click();
    const sheet = page.getByTestId('perf-history');
    await expect(sheet).toContainText(title);
    const v2Stats = await sheet.getByTestId('perf-history-stat').evaluateAll((items) =>
      items.map((item) => `${item.children[0].textContent.trim().toUpperCase()}|${item.children[1].textContent.trim()}`),
    );
    expect(v2Stats).toEqual(stats);
    expect(squash(await sheet.getByTestId('perf-history-caption').innerText())).toBe(caption);
    expect(await tableRows(sheet.locator('table'))).toEqual(rows);
    await expect(sheet.getByTestId('perf-chart')).toBeVisible();

    // Runtime image drill-down.
    await sheet.locator('tbody tr').first().locator('td').last().getByRole('button').click();
    await expect(page.getByRole('dialog').last()).toContainText('Runtime image');
    await expect(page.getByRole('dialog').last()).toContainText(rows[0][4]);
  });

  test('accuracy observations and empty filters', async ({ page }) => {
    const host = await classicPerf(page);
    const models = (await tableRows(host.locator('table.ops-table'))).map((row) => ({ name: row[0], hardware: row[1], accuracy: Number(row[4]) }));
    const model = models.find((m) => m.accuracy > 0);
    expect(model).toBeTruthy();
    const classic = await classicPerf(page, `?ops_perf_view=accuracy&ops_perf_model=${encodeURIComponent(model.name)}`);
    const rows = await tableRows(classic.locator('table.ops-table'));
    expect(rows.length).toBe(model.accuracy);

    await page.goto(`/#/perf?view=accuracy&model=${encodeURIComponent(model.name)}`);
    const accuracy = page.getByTestId('perf-accuracy');
    await expect(accuracy.locator('tbody tr').first()).toBeVisible();
    expect(await tableRows(accuracy.locator('table'))).toEqual(rows);
    await accuracy.locator('tbody tr').first().getByRole('button').first().click();
    await expect(page.getByTestId('perf-history')).toContainText(`${model.name} - Accuracy`);

    // A model filtered by hardware it never ran on renders the classic empty state.
    const single = models.find((m) => !m.hardware.includes('MI300X'));
    expect(single).toBeTruthy();
    const empty = await classicPerf(page, `?ops_perf_model=${encodeURIComponent(single.name)}&ops_perf_device=mi300x`);
    await expect(empty.locator('.ops-empty')).toHaveText('No model matches the selected filters.');
    await page.goto(`/#/perf?model=${encodeURIComponent(single.name)}&device=mi300x`);
    await expect(page.getByTestId('perf-empty')).toHaveText('No model matches the selected filters.');
  });

  test('model drawer and legacy links', async ({ page }) => {
    const host = await classicPerf(page);
    const model = (await tableRows(host.locator('table.ops-table')))[0][0];
    const classic = await classicPerf(page, `?ops_perf_model=${encodeURIComponent(model)}`);
    await classic.locator('.ops-perf-model-link').click();
    const fields = await page.locator('.ops-overlay:not([hidden]) .ops-detail-field').evaluateAll((items) =>
      items.map((item) => `${item.querySelector('dt').textContent.trim()}|${item.querySelector('dd').textContent.trim()}`),
    );
    expect(fields.length).toBeGreaterThan(0);

    await page.goto(`/?ops_perf_view=accuracy&ops_perf_model=${encodeURIComponent(model)}&ops_perf_device=mi355x#ci-perf-eval`);
    await expect(page).toHaveURL(new RegExp(`#/perf\\?view=accuracy&model=${encodeURIComponent(model).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}&device=mi355x$`));
    await expect(page.getByTestId('perf-accuracy')).toBeVisible();

    await page.goto(`/#/perf?model=${encodeURIComponent(model)}`);
    await page.getByTestId('perf-model-title').click();
    const sheet = page.getByRole('dialog');
    const v2Fields = await sheet.locator('dl').evaluate((dl) => {
      const cells = Array.from(dl.children);
      const out = [];
      for (let i = 0; i < cells.length; i += 2) out.push(`${cells[i].textContent.trim()}|${cells[i + 1].textContent.trim()}`);
      return out;
    });
    expect(v2Fields).toEqual(fields);
  });
});
