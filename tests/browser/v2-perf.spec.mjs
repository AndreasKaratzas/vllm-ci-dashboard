import { expect, test } from '@playwright/test';

import { readGolden } from './golden/read.mjs';

// Perf Eval reads data/vllm/perf_eval/perf_eval.json, which the fixture site
// copies from the frozen repository snapshot; the goldens were read from it.
const golden = readGolden('perf');
const models = golden.summary.rows.map((row) => row[0]);

const squash = (s) => String(s || '').replace(/\s+/g, ' ').trim();

async function tableRows(table) {
  return table.locator('tbody tr').evaluateAll((rows) =>
    rows.map((row) => Array.from(row.querySelectorAll('td')).map((td) => td.textContent.replace(/\s+/g, ' ').trim())),
  );
}

// Pre-rename domain tones on data-tone attributes, in the golden vocabulary.
const TONES = { 'is-success': 'pass', 'is-danger': 'hard', 'is-neutral': 'neutral' };

async function v2Tiles(page) {
  return page.getByTestId('perf-config').evaluateAll((configs, tones) =>
    configs.map((config) => ({
      label: config.querySelector('[data-testid="perf-config-label"]').textContent.trim(),
      meta: config.querySelector('[data-testid="perf-config-meta"]').textContent.trim(),
      tiles: Array.from(config.querySelectorAll('[data-testid="perf-tile"]')).map((tile) =>
        [
          tile.querySelector('[data-testid="perf-tile-label"]').textContent,
          tile.querySelector('[data-testid="perf-tile-delta"]').textContent,
          tile.querySelector('[data-testid="perf-tile-value"]').textContent,
          tile.querySelector('[data-testid="perf-tile-direction"]').textContent,
          tones[tile.dataset.tone] ?? tile.dataset.tone,
        ].map((s) => s.trim()).join('|'),
      ),
    })),
    TONES,
  );
}

test.describe('Perf Eval matches the golden page facts', () => {
  test('summary strip and model overview', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const { strip, rows: expectedRows } = golden.summary;

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
    expect(v2Rows).toEqual(expectedRows);
    await expect(overview).toContainText(`${expectedRows.length} retained model families`);

    // Drill-down: the AMD MODELS card lists the model families, newest first.
    await page.getByTestId('kpi-perf-models').getByRole('button', { name: 'Inspect model families' }).click();
    const sheet = page.getByRole('dialog');
    const families = await sheet.locator('tbody tr td:first-child').allInnerTexts();
    expect(families.map(squash)).toEqual([...models].reverse());
    expect(errors).toEqual([]);
  });

  test('model performance tiles under each hardware filter', async ({ page }) => {
    for (const [device, expected] of Object.entries(golden.tiles)) {
      await page.goto(`/#/perf?model=${encodeURIComponent(models[0])}${device === 'all' ? '' : `&device=${device}`}`);
      await expect(page.getByTestId('perf-tile').first()).toBeVisible();
      expect(squash(await page.getByTestId('perf-model-title').innerText())).toBe(expected.title);
      expect(await page.getByTestId('perf-provenance').locator('> *').allTextContents()).toEqual(expected.provenance);
      expect(squash(await page.getByTestId('perf-nightlies').innerText())).toBe(expected.nightlies);
      expect(await v2Tiles(page)).toEqual(expected.configs);
    }
  });

  test('metric history sheet', async ({ page }) => {
    const { title, stats, caption, rows } = golden.history;
    await page.goto(`/#/perf?model=${encodeURIComponent(models[1])}`);
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

    // A point on the history chart opens that observation.
    await sheet.evaluate((el) => Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished)));
    const chartBox = await sheet.getByTestId('perf-chart').boundingBox();
    await page.mouse.move(chartBox.x + chartBox.width / 2, chartBox.y + chartBox.height / 2);
    await page.mouse.click(chartBox.x + chartBox.width / 2, chartBox.y + chartBox.height / 2);
    await expect(page.getByRole('dialog').last()).toContainText(/Build #\d+|chart observation/);
    await expect(page.getByRole('dialog').last()).toContainText('Open exact source');
    await page.keyboard.press('Escape');

    // Runtime image drill-down.
    await sheet.locator('tbody tr').first().locator('td').last().getByRole('button').click();
    await expect(page.getByRole('dialog').last()).toContainText('Runtime image');
    await expect(page.getByRole('dialog').last()).toContainText(rows[0][4]);
  });

  test('accuracy observations and empty filters', async ({ page }) => {
    const families = golden.summary.rows.map((row) => ({ name: row[0], hardware: row[1], accuracy: Number(row[4]) }));
    const model = families.find((m) => m.accuracy > 0);
    const { rows, empty } = golden.accuracy;
    expect(rows.length).toBe(model.accuracy);

    await page.goto(`/#/perf?view=accuracy&model=${encodeURIComponent(model.name)}`);
    const accuracy = page.getByTestId('perf-accuracy');
    await expect(accuracy.locator('tbody tr').first()).toBeVisible();
    expect(await tableRows(accuracy.locator('table'))).toEqual(rows);
    await accuracy.locator('tbody tr').first().getByRole('button').first().click();
    await expect(page.getByTestId('perf-history')).toContainText(`${model.name} - Accuracy`);

    // A model filtered by hardware it never ran on renders the empty state.
    const single = families.find((m) => !m.hardware.includes('MI300X'));
    await page.goto(`/#/perf?model=${encodeURIComponent(single.name)}&device=mi300x`);
    await expect(page.getByTestId('perf-empty')).toHaveText(empty);
    // The v2-only regression badge counts the filtered models.
    await expect(page.getByTestId('kpi-perf-models')).toContainText('0 regressed in filter');
  });

  test('model drawer and legacy links', async ({ page }) => {
    const model = models[0];
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
    expect(v2Fields).toEqual(golden['model drawer'].fields);
  });

  test('sparkline points open the same observation drawer', async ({ page }) => {
    const model = models[0];
    for (const [i, keys] of [['Enter'], ['ArrowLeft', 'ArrowLeft', 'Enter']].entries()) {
      await page.goto('about:blank');
      await page.goto(`/#/perf?model=${encodeURIComponent(model)}`);
      const chart = page.getByTestId('perf-tile').first().getByTestId('perf-chart');
      await chart.focus();
      for (const key of keys) await chart.press(key);
      const sheet = page.getByRole('dialog');
      await expect(sheet).toBeVisible();
      const v2 = {
        title: squash(await sheet.locator('[data-slot="sheet-title"]').innerText()),
        subtitle: squash(await sheet.locator('[data-slot="sheet-description"]').innerText()),
        fields: await sheet.locator('dl').evaluate((dl) => {
          const cells = Array.from(dl.children);
          const out = [];
          for (let j = 0; j < cells.length; j += 2) out.push(`${cells[j].textContent.trim().toLowerCase()}|${cells[j + 1].textContent.trim()}`);
          return out;
        }),
        sources: await sheet.locator('a').evaluateAll((links) => links.map((a) => `${a.textContent.replace('↗', '').trim()}|${a.getAttribute('href')}`)),
      };
      expect(v2).toEqual(golden['sparkline points'][i]);
    }

    // A mouse click on the sparkline opens a point, not the full history.
    await page.goto('about:blank');
    await page.goto(`/#/perf?model=${encodeURIComponent(model)}`);
    const chart = page.getByTestId('perf-tile').first().getByTestId('perf-chart');
    const box = await chart.boundingBox();
    await page.mouse.move(box.x + box.width - 3, box.y + box.height / 2);
    await page.mouse.click(box.x + box.width - 3, box.y + box.height / 2);
    await expect(page.getByRole('dialog')).toContainText(/chart observation/);
    await expect(page.getByTestId('perf-history')).toHaveCount(0);
  });

  test('view and hardware changes push history entries', async ({ page }) => {
    await page.goto(`/#/perf?model=${encodeURIComponent(models[0])}`);
    await expect(page.getByTestId('perf-tile').first()).toBeVisible();
    await page.getByRole('tab', { name: 'Accuracy' }).click();
    await expect(page.getByTestId('perf-accuracy')).toBeVisible();
    await page.goBack();
    await expect(page.getByTestId('perf-tile').first()).toBeVisible();
    await expect(page).not.toHaveURL(/view=accuracy/);
  });
});
