import { expect, test } from '@playwright/test';

import { readGolden } from './golden/read.mjs';
import { collectErrors, expectQuality, setTheme } from './support/quality.mjs';

// Perf Eval reads data/vllm/perf_eval/perf_eval.json, which the fixture site
// copies from the frozen repository snapshot; the goldens were read from it.
const golden = readGolden('perf');
const models = golden.summary.rows.map((row) => row[0]);
const OVERVIEW_COLUMNS = ['Model', 'Hardware', 'Nightlies', 'Perf metrics', 'Accuracy', 'Regressions', 'Improvements', 'Latest build'];

const squash = (s) => String(s || '').replace(/\s+/g, ' ').trim();
const sheets = (page) => page.locator('[data-slot="evidence-sheet"]');

async function tableRows(table) {
  return table.locator('tbody tr').evaluateAll((rows) =>
    rows.map((row) => Array.from(row.querySelectorAll('td')).map((td) => td.textContent.replace(/\s+/g, ' ').trim())),
  );
}

/** Rows restricted to the named columns, in that order. */
async function columnRows(table, names) {
  const headers = (await table.locator('thead th').allTextContents()).map(squash);
  const indexes = names.map((name) => headers.indexOf(name));
  expect(indexes, `columns ${names} in ${headers}`).not.toContain(-1);
  return (await tableRows(table)).map((row) => indexes.map((i) => row[i]));
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

async function fieldPairs(sheet, lower = false) {
  return sheet.locator('dl').last().evaluate((dl, lowerCase) => {
    const cells = Array.from(dl.children);
    const out = [];
    for (let i = 0; i < cells.length; i += 2) {
      const label = cells[i].textContent.trim();
      out.push(`${lowerCase ? label.toLowerCase() : label}|${cells[i + 1].textContent.trim()}`);
    }
    return out;
  }, lower);
}

async function openFirstHistory(page) {
  await page.getByTestId('perf-config').first().getByRole('button', { name: 'Open history' }).click();
  const sheet = sheets(page).last();
  await expect(sheet.getByTestId('perf-chart')).toBeVisible();
  return sheet;
}

test.describe('Perf Eval matches the golden page facts', () => {
  test('summary strip and model overview', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const { strip, rows: expectedRows } = golden.summary;

    await page.goto('/#/perf');
    const overview = page.getByTestId('perf-overview');
    await expect(overview.locator('tbody tr').first()).toBeVisible();
    for (const [id, item] of [['models', strip[0]], ['nightlies', strip[1]], ['hardware', strip[3]]]) {
      const card = page.getByTestId(`kpi-perf-${id}`);
      await expect(card).toContainText(new RegExp(item.label, 'i'));
      await expect(card.locator('[data-slot="kpi-value"]')).toHaveText(item.value);
      await expect(card).toContainText(item.meta);
    }
    // Metric points are the Nightlies card's headline.
    await expect(page.getByTestId('kpi-perf-nightlies')).toContainText(`${strip[2].value} ${strip[2].label}`);
    await expect(page.getByTestId('kpi-perf-nightlies')).toContainText(strip[2].meta);

    expect(await columnRows(overview.locator('table'), OVERVIEW_COLUMNS)).toEqual(expectedRows);
    await expect(overview).toContainText(`${expectedRows.length} retained model families`);

    // Drill-down: the AMD MODELS card lists the model families, newest first.
    await page.getByTestId('kpi-perf-models').getByRole('button', { name: 'Inspect model families' }).click();
    const sheet = sheets(page);
    await expect(sheet.locator('tbody tr').first()).toBeVisible();
    const families = await sheet.locator('tbody tr td:first-child').allInnerTexts();
    expect(families.map(squash)).toEqual([...models].reverse());

    // A family opens its model.
    await sheet.getByRole('button', { name: models[2] }).click();
    await expect(page.getByTestId('perf-model-title')).toHaveText(models[2]);
    expect(errors).toEqual([]);
  });

  test('regressions, improvements and the per-model chart agree with the overview', async ({ page }) => {
    const rows = golden.summary.rows;
    const regressions = rows.reduce((sum, row) => sum + Number(row[5]), 0);
    const improvements = rows.reduce((sum, row) => sum + Number(row[6]), 0);
    await page.goto('/#/perf');
    await expect(page.getByTestId('kpi-perf-regressions').locator('[data-slot="kpi-value"]')).toHaveText(String(regressions));
    await expect(page.getByTestId('kpi-perf-improvements').locator('[data-slot="kpi-value"]')).toHaveText(String(improvements));
    await expect(page.getByTestId('perf-changes-chart')).toContainText(`${improvements} improved and ${regressions} regressed`);

    await page.getByTestId('kpi-perf-regressions').getByRole('button').click();
    const sheet = sheets(page);
    await expect(sheet).toContainText(`${regressions} metrics`);
    await expect(sheet.locator('tbody tr')).toHaveCount(regressions);
    // A listed metric drills into its history, with Back to the list.
    await sheet.locator('tbody tr').first().getByRole('button').first().click();
    await expect(sheets(page).last().getByTestId('perf-chart')).toBeVisible();
    await sheets(page).last().getByRole('button', { name: 'Back' }).click();
    await expect(sheets(page)).toHaveCount(1);

    // The chart's keyboard path opens a model.
    await page.keyboard.press('Escape');
    await page.getByTestId('perf-changes-chart').getByRole('button', { name: /^Inspect/ }).click();
    await page.getByRole('menuitem').first().click();
    await expect(page.getByTestId('perf-model-section')).toBeVisible();
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
    // A tile selects the metric its configuration charts; the chart opens the history.
    const tile = page.getByTestId('perf-tile').nth(2);
    await tile.click();
    await expect(tile).toHaveAttribute('aria-pressed', 'true');
    const sheet = await openFirstHistory(page);
    await expect(sheet).toContainText(title);
    const v2Stats = await sheet.locator('dl').first().locator('> div').evaluateAll((items) =>
      items.map((item) => `${item.children[0].textContent.trim().toUpperCase()}|${item.children[1].querySelector('span').textContent.trim()}`),
    );
    expect(v2Stats).toEqual(stats);
    expect(squash(await sheet.locator('section h3').innerText())).toBe(caption);
    expect(await tableRows(sheet.locator('table'))).toEqual(rows);

    // A point on the history chart opens that observation.
    await sheet.evaluate((el) => Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished)));
    await sheet.getByTestId('perf-chart').scrollIntoViewIfNeeded();
    const chartBox = await sheet.getByTestId('perf-chart').locator('[role="img"]').boundingBox();
    await page.mouse.move(chartBox.x + chartBox.width / 2, chartBox.y + chartBox.height / 2);
    await page.mouse.click(chartBox.x + chartBox.width / 2, chartBox.y + chartBox.height / 2);
    await expect(sheets(page).last()).toContainText(/Build #\d+|chart observation/);
    await expect(sheets(page).last()).toContainText('Open exact source');
    await page.keyboard.press('Escape');
    await expect(sheets(page)).toHaveCount(1);

    // Runtime image drill-down.
    await sheet.locator('tbody tr').first().locator('td').last().getByRole('button').click();
    await expect(sheets(page).last()).toContainText('Runtime image');
    await expect(sheets(page).last()).toContainText(rows[0][4]);
  });

  test('accuracy observations and empty filters', async ({ page }) => {
    const families = golden.summary.rows.map((row) => ({ name: row[0], hardware: row[1], accuracy: Number(row[4]) }));
    const model = families.find((m) => m.accuracy > 0);
    const { rows, empty } = golden.accuracy;
    expect(rows.length).toBe(model.accuracy);

    await page.goto(`/#/perf?view=accuracy&model=${encodeURIComponent(model.name)}`);
    const accuracy = page.getByTestId('perf-accuracy');
    await expect(accuracy.locator('tbody tr').first()).toBeVisible();
    expect((await tableRows(accuracy.locator('table'))).map((row) => row.slice(0, 8))).toEqual(rows);
    await accuracy.locator('tbody tr').first().click();
    await expect(sheets(page)).toContainText(`${model.name} - Accuracy`);

    // A model filtered by hardware it never ran on renders the empty state.
    const single = families.find((m) => !m.hardware.includes('MI300X'));
    await page.goto(`/#/perf?model=${encodeURIComponent(single.name)}&device=mi300x`);
    await expect(page.getByTestId('perf-empty')).toContainText(empty);
    // The regression count covers the filtered models only.
    await expect(page.getByTestId('kpi-perf-regressions').locator('[data-slot="kpi-value"]')).toHaveText('0');
    await expect(page.getByTestId('kpi-perf-regressions')).toContainText('in filter');
  });

  test('model drawer and old links', async ({ page }) => {
    const model = models[0];
    await page.goto(`/?ops_perf_view=accuracy&ops_perf_model=${encodeURIComponent(model)}&ops_perf_device=mi355x#ci-perf-eval`);
    await expect(page).toHaveURL(new RegExp(`#/perf\\?view=accuracy&model=${encodeURIComponent(model).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}&device=mi355x$`));
    await expect(page.getByTestId('perf-accuracy')).toBeVisible();

    await page.goto(`/#/perf?model=${encodeURIComponent(model)}`);
    await page.getByTestId('perf-model-title').click();
    expect(await fieldPairs(sheets(page))).toEqual(golden['model drawer'].fields);
  });

  test('chart points open the same observation drawer', async ({ page }) => {
    const model = models[0];
    // The newest point, then the third newest, through the chart's keyboard Inspect menu.
    for (const [i, back] of [0, 2].entries()) {
      await page.goto('about:blank');
      await page.goto(`/#/perf?model=${encodeURIComponent(model)}`);
      const chart = page.getByTestId('perf-config').first().getByTestId('perf-chart');
      await chart.getByRole('button', { name: /^Inspect/ }).click();
      const items = page.getByRole('menuitem');
      await items.nth((await items.count()) - 1 - back).click();
      const sheet = sheets(page);
      await expect(sheet).toBeVisible();
      const v2 = {
        title: squash(await sheet.locator('[data-slot="sheet-title"]').innerText()),
        subtitle: squash(await sheet.locator('[data-slot="sheet-description"]').innerText()),
        fields: await fieldPairs(sheet, true),
        sources: await sheet.locator('a').evaluateAll((links) => links.map((a) => `${a.textContent.trim()}|${a.getAttribute('href')}`)),
      };
      expect(v2).toEqual(golden['sparkline points'][i]);
    }

    // A mouse click on the chart opens a point, not the full history.
    await page.goto('about:blank');
    await page.goto(`/#/perf?model=${encodeURIComponent(model)}`);
    const plot = page.getByTestId('perf-config').first().getByTestId('perf-chart').locator('[role="img"]');
    await plot.scrollIntoViewIfNeeded();
    const box = await plot.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await expect(sheets(page)).toContainText(/chart observation/);
    await expect(sheets(page)).toHaveCount(1);
  });

  test('view and hardware changes push history entries', async ({ page }) => {
    await page.goto(`/#/perf?model=${encodeURIComponent(models[0])}`);
    await expect(page.getByTestId('perf-tile').first()).toBeVisible();
    await page.getByRole('navigation', { name: 'Performance views' }).getByRole('link', { name: 'Accuracy' }).click();
    await expect(page.getByTestId('perf-accuracy')).toBeVisible();
    await page.goBack();
    await expect(page.getByTestId('perf-tile').first()).toBeVisible();
    await expect(page).not.toHaveURL(/view=accuracy/);

    await page.locator('[data-slot="toggle-group-item"]', { hasText: 'MI300X' }).click();
    await expect(page).toHaveURL(/device=mi300x/);
    await expect(page.getByTestId('perf-config')).toHaveCount(golden.tiles.mi300x.configs.length);
    await page.goBack();
    await expect(page.getByTestId('perf-config')).toHaveCount(golden.tiles.all.configs.length);
  });
});

const MODEL = encodeURIComponent(models[0]);
const SHEETS = [
  ['metric history', `/#/perf?model=${MODEL}`, (page) => openFirstHistory(page)],
  ['chart observation', `/#/perf?model=${MODEL}`, async (page) => {
    const sheet = await openFirstHistory(page);
    await sheet.getByRole('button', { name: /^Inspect/ }).click();
    await page.getByRole('menuitem').last().click();
    await expect(sheets(page)).toHaveCount(2);
  }],
  ['model families', '/#/perf', (page) => page.getByTestId('kpi-perf-models').getByRole('button').click()],
  ['regressed metrics', '/#/perf', (page) => page.getByTestId('kpi-perf-regressions').getByRole('button').click()],
  ['hardware', '/#/perf', (page) => page.getByTestId('kpi-perf-hardware').getByRole('button').click()],
  ['model drawer', `/#/perf?model=${MODEL}`, (page) => page.getByTestId('perf-model-title').click()],
];

for (const theme of ['light', 'dark']) {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    for (const [name, route, open] of SHEETS) {
      test(`${name} sheet meets the quality gates (${theme}, ${viewport.width}px)`, async ({ page }) => {
        await setTheme(page, theme);
        await page.setViewportSize(viewport);
        const errors = collectErrors(page);
        await page.goto(route);
        await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
        await page.locator('[data-slot="kpi-card"]').first().waitFor();
        await open(page);
        await sheets(page).last().waitFor();
        await sheets(page).last().evaluate((el) => Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished)));
        await expectQuality(page, errors);
      });
    }
  }
}
