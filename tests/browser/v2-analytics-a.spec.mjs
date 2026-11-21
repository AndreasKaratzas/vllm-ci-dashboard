import { expect, test } from '@playwright/test';

import { readGolden } from './golden/read.mjs';
import { collectErrors, expectQuality, setTheme } from './support/quality.mjs';
import { routeSection } from './support/sections.mjs';

// CI Analytics "AMD test health", "AMD nightly latency", "Slowdowns" and "AMD nightlies"
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
    // The job-variant count names the nightly it comes from where classic said "the latest completed nightly".
    const facts = strip.map((item, index) => [item[1], index === 1 ? `exact jobs in ${strip[0][1]}` : item[2].toLowerCase()]);
    expect(cards.map((card) => [card[1], card[3].toLowerCase()])).toEqual(facts);
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

test.describe('CI Analytics AMD tabs behave consistently', () => {
  test('a nightly has one evidence sheet whichever tab or control opens it', async ({ page }) => {
    const sheetText = async () => {
      const dialog = page.getByRole('dialog');
      await expect(dialog.locator('[data-slot="evidence-stats"]')).toBeVisible();
      const text = clean(await dialog.innerText());
      await closeSheets(page);
      return text;
    };
    await page.goto('/#/analytics?view=nightlies');
    const view = page.getByTestId('analytics-nightlies');
    const number = clean(await view.getByTestId('kpi-nightly-latest').locator('[data-slot="kpi-value"]').innerText());
    await view.getByTestId('kpi-nightly-latest').click();
    const fromKpi = await sheetText();
    expect(fromKpi).toContain(`AMD nightly ${number}`);
    await view.getByTestId('nightlies-table').locator('tbody tr').first().locator('td').nth(1).click();
    expect(await sheetText()).toBe(fromKpi);
    const chart = view.getByTestId('nightlies-chart');
    await chart.getByRole('button', { name: /^Inspect / }).click();
    await page.getByRole('menuitem').filter({ hasText: number }).click();
    expect(await sheetText()).toBe(fromKpi);

    await page.goto('/#/analytics');
    const groups = page.getByTestId('analytics-groups');
    if (clean(await groups.getByTestId('kpi-amd-health-build').locator('[data-slot="kpi-value"]').innerText()) === number) {
      await groups.getByTestId('kpi-amd-health-build').click();
      expect(await sheetText()).toBe(fromKpi);
    }
  });

  test('every page counts from the newest nightly with results and lists the running one\'s failures so far', async ({ page }) => {
    const body = await (await page.request.get('/data/vllm/ci/operations_v2/nightly.json')).json();
    const cohort = body.nightly.pipelines[0];
    const previous = cohort.builds[0];
    const { failure_movement: _movement, failed_groups: failed, soft_failed_groups: _soft, ...base } = previous;
    const running = previous.number + 1;
    const early = { ...failed[0], build_number: running, url: failed[0].url.replace(`/builds/${previous.number}/`, `/builds/${running}/`) };
    cohort.builds.unshift({ ...base, number: running, state: 'failing', has_test_results: false, total_groups: 0, failed_groups: [early], soft_failed_groups: [] });
    await routeSection(page, 'nightly', body);
    const callout = `Nightly #${running} is still running its AMD jobs`;
    const source = `Counts come from #${previous.number}, the newest nightly with test results.`;

    // Every page counts from the newest nightly with results and says so the same way.
    await page.goto('/#/');
    await expect(page.getByTestId('latest-nightly')).toContainText(callout);
    await expect(page.getByTestId('latest-nightly')).toContainText(source);
    await expect(page.getByTestId('kpi-nightly')).toContainText(`#${previous.number}`);
    await expect(page.getByTestId('kpi-pass-rate')).toContainText(`in #${previous.number}`);
    await page.getByTestId('latest-nightly').getByRole('button', { name: 'Failures so far' }).click();
    const earlySheet = page.getByRole('dialog');
    await expect(earlySheet).toContainText('Provisional');
    await expect(earlySheet.locator('tbody tr')).toHaveCount(1);
    await expect(earlySheet.locator('tbody tr').getByRole('link')).toHaveAttribute('href', early.url);
    await closeSheets(page);
    await page.goto('/#/health');
    await expect(page.getByTestId('latest-nightly')).toContainText(source);
    await expect(page.getByRole('link', { name: `Open AMD nightly #${previous.number}` })).toBeVisible();
    await page.goto('/#/analytics?view=nightlies');
    const view = page.getByTestId('analytics-nightlies');
    await expect(view.getByTestId('latest-nightly')).toContainText(source);
    const latest = view.getByTestId('kpi-nightly-latest');
    await expect(latest.locator('[data-slot="kpi-value"]')).toHaveText(`#${running}`);
    await expect(latest).toContainText('Awaiting results');
    await expect(view.getByTestId('nightlies-note')).toHaveCount(0);
    const variants = view.getByTestId('kpi-nightly-variants');
    await expect(variants.locator('[data-slot="kpi-value"]')).toHaveText(String(previous.total_groups));
    const recurring = view.getByTestId('kpi-nightly-recurring');
    await expect(recurring.locator('[data-slot="kpi-value"]')).toHaveText(String(previous.failure_movement.recurring.length));
    await recurring.click();
    await expect(page.getByRole('dialog')).toContainText(`AMD nightly #${previous.number}`);
  });

  test('zero movement counts are plain text and only real counts open evidence', async ({ page }) => {
    await page.goto('/#/analytics?view=nightlies');
    const rows = page.getByTestId('nightlies-table').locator('tbody tr');
    await expect(rows.first()).toBeVisible();
    const cells = await rows.evaluateAll((trs) =>
      trs.flatMap((tr) => [...tr.querySelectorAll('td')].slice(3, 6).map((td) => [td.innerText.trim(), td.querySelectorAll('button').length])),
    );
    expect(cells.length).toBeGreaterThan(0);
    for (const [text, buttons] of cells) expect([text, buttons]).toEqual([text, /^[1-9]/.test(text) ? 1 : 0]);
    for (const key of ['new', 'recurring', 'fixed']) {
      const card = page.getByTestId(`kpi-nightly-${key}`);
      const value = clean(await card.locator('[data-slot="kpi-value"]').innerText());
      await expect(card.locator('[data-kpi-action]')).toHaveCount(/^[1-9]/.test(value) ? 1 : 0);
    }
  });

  test('the nightly movement axis thins its date ticks so labels never overlap', async ({ page }) => {
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto('/#/analytics?view=nightlies');
      const ticks = page.getByTestId('nightlies-chart').locator('[class*="-axis"] .tick text');
      await expect(ticks.first()).toBeVisible();
      const boxes = (await ticks.evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect())))
        .filter((box) => box.width > 0 && box.height < 30)
        .map((box) => ({ left: box.left, right: box.right, top: Math.round(box.top) }));
      const rows = Object.values(Object.groupBy(boxes, (box) => box.top)).filter((row) => row.length > 1);
      for (const row of rows) {
        row.sort((a, b) => a.left - b.left);
        for (let index = 1; index < row.length; index += 1) expect(row[index].left).toBeGreaterThanOrEqual(row[index - 1].right);
      }
    }
  });
});

test.describe('CI Analytics Slowdowns', () => {
  test('lists active slowdowns with amber increases, external links and a collapsed settled table', async ({ page }) => {
    const errors = collectErrors(page);
    await page.goto('/#/analytics?view=slowdowns');
    await expect(page.getByTestId('kpi-slowdowns-active').locator('[data-slot=kpi-value]')).toHaveText(/^2\s*steps$/);
    await expect(page.getByTestId('kpi-slowdowns-largest')).toContainText('(MI300) Routed Alpha');
    const rows = page.getByTestId('slowdowns-active').locator('tbody tr');
    await expect(rows).toHaveCount(2);
    expect((await cellTexts(rows)).map((cells) => cells[0])).toEqual(['(MI300) Routed Alpha amd_mi300_1', '(MI300) Attention Kernels (2 shards) amd_mi300_1']);
    // Increases are attention, never failures.
    const badges = rows.locator('[data-tone]');
    await expect(badges).toHaveCount(2);
    for (const tone of await badges.evaluateAll((nodes) => nodes.map((node) => node.dataset.tone))) expect(tone).toBe('soft');
    await expect(page.getByTestId('slowdowns-active').locator('[data-tone="hard"]')).toHaveCount(0);
    const since = rows.first().getByRole('link', { name: /#31013 → #31014/ });
    await expect(since).toHaveAttribute('href', 'https://buildkite.com/vllm/ci/builds/31014');
    await expect(since).toHaveAttribute('target', '_blank');
    const compare = rows.first().getByRole('link', { name: /^[0-9a-f]{12}…[0-9a-f]{12}$/ });
    await expect(compare).toHaveAttribute('href', /^https:\/\/github\.com\/vllm-project\/vllm\/compare\/[0-9a-f]{40}\.\.\.[0-9a-f]{40}$/);
    await expect(compare).toHaveAttribute('target', '_blank');

    const settled = page.getByTestId('slowdowns-settled');
    await expect(settled.locator('table')).toHaveCount(0);
    await settled.getByRole('button', { name: /Settled · 1/ }).click();
    await expect(settled.locator('tbody tr')).toHaveCount(1);
    await expect(settled).toContainText('Environment');
    expect(errors).toEqual([]);
  });

  test('a slowdown opens its builds before and since, with the onset marked', async ({ page }) => {
    await page.goto('/#/analytics?view=slowdowns');
    await page.getByTestId('slowdowns-active').locator('tbody tr').first().locator('td').last().click();
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText('(MI300) Routed Alpha');
    await expect(sheet.locator('[data-slot="evidence-stats"]')).toContainText('Increase');
    await expect(sheet.locator('[data-slot="slowdown-plot"]')).toBeVisible();
    await expect(sheet.locator('[data-slot="slowdown-plot"] g[class*="-point"]:not([class*="-point-group"])')).toHaveCount(28);
    await expect(sheet).toContainText('Slower from here');
    // Routed Alpha rose twice: the latest rise is a field and a second marker.
    await expect(sheet.locator('[data-slot="slowdown-plot"]')).toContainText('Latest rise');
    await expect(sheet.getByRole('link', { name: /^#31018 · / })).toHaveAttribute('href', 'https://buildkite.com/vllm/ci/builds/31018');
    await expect(sheet.getByRole('link', { name: /^[0-9a-f]{12}…[0-9a-f]{12}$/ })).toHaveAttribute('href', /vllm-project\/vllm\/compare/);
    await expect(sheet.getByRole('link', { name: /First slow build/ })).toHaveAttribute('href', 'https://buildkite.com/vllm/ci/builds/31014');
    await expect(sheet.getByRole('link', { name: /Compare vLLM commits/ })).toHaveAttribute('href', /vllm-project\/vllm\/compare/);
    await sheet.getByRole('button', { name: 'Inspect Sampled builds' }).click();
    await expect(page.getByRole('menuitem')).toHaveCount(28);
  });

  test('the latency tab, Main CI and Home point to slowdowns', async ({ page }) => {
    await page.goto('/#/analytics?view=latency');
    await page.getByTestId('slowdowns-notice').getByRole('link', { name: /Open slowdowns/ }).click();
    await expect(page).toHaveURL(/view=slowdowns/);
    await page.goto('/#/main');
    await expect(page.getByTestId('main-slowdowns-panel').locator('tbody tr')).toHaveCount(2);
    // On Home the slowdowns are a main CI signal, so they sit with main and not in the nightly's attention list.
    await page.goto('/#/');
    await expect(page.getByTestId('home-main').getByTestId('slowdowns-notice')).toContainText('2 AMD test steps on main are running slower than before');
    await expect(page.getByTestId('attention-row').filter({ hasText: 'running slower' })).toHaveCount(0);
    await page.getByTestId('home-main').getByRole('link', { name: /Open slowdowns/ }).click();
    await expect(page).toHaveURL(/#\/analytics\?view=slowdowns$/);
  });

  test('says why slowdowns are unavailable', async ({ page }) => {
    const main = await (await page.request.get('/data/vllm/ci/operations_v2/main_ci.json')).json();
    main.main_ci.slowdowns = { available: false, reason: 'duration_watcher_state_stale', rows: [] };
    await routeSection(page, 'main_ci', main);
    await page.goto('/#/analytics?view=slowdowns');
    await expect(page.getByTestId('slowdowns-unavailable')).toContainText('did not evaluate the builds of this collection');
    await expect(page.getByTestId('slowdowns-unavailable')).toContainText('duration_watcher_state_stale');
    await expect(page.getByTestId('slowdowns-unavailable').getByRole('link', { name: /Data Collection runs/ })).toHaveAttribute(
      'href',
      'https://github.com/AndreasKaratzas/vllm-ci-dashboard/actions/workflows/hourly-master.yml',
    );
  });

  test('fits a phone without sideways scrolling', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/#/analytics?view=slowdowns');
    await expect(page.getByTestId('slowdowns-active').locator('tbody tr')).toHaveCount(2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
    // The onset age stays visible with the increase, and wrapped step names stay clear of the minute axis.
    await expect(page.getByTestId('slowdowns-active').locator('tbody tr').first()).toContainText(/since \d+d ago/);
    const plot = page.getByTestId('slowdowns-chart').locator('[data-slot="bar-plot"]');
    await expect(plot.locator('text').filter({ hasText: /^\d+m$/ }).first()).toBeVisible();
    const boxes = await plot.locator('text').evaluateAll((nodes) => nodes.map((node) => ({ text: node.textContent, ...node.getBoundingClientRect().toJSON() })));
    const axisTop = Math.min(...boxes.filter((box) => /^\d+m$/.test(box.text)).map((box) => box.top));
    const labelBottom = Math.max(...boxes.filter((box) => /Attention|Routed/.test(box.text)).map((box) => box.bottom));
    expect(labelBottom).toBeLessThanOrEqual(axisTop);
  });
});

// Every drill-down of the three AMD tabs, light and dark, desktop and phone.
const SHEETS = [
  ['latest AMD nightly', '/#/analytics', (page) => page.getByTestId('kpi-amd-health-build').click()],
  ['logical test groups', '/#/analytics', (page) => page.getByTestId('kpi-amd-health-test-groups').click()],
  ['job-variant catalog', '/#/analytics', (page) => page.getByTestId('browse-all').click()],
  ['exact job variant', '/#/analytics', (page) => page.getByTestId('priority').locator('tbody tr').first().click()],
  ['hardware variant', '/#/analytics', (page) => inspect(page, page.getByTestId('hardware-health-chart'), '')],
  ['latency group', '/#/analytics?view=latency', (page) => page.getByTestId('latency-table').locator('tbody tr').first().click()],
  ['slowdown', '/#/analytics?view=slowdowns', (page) => page.getByTestId('slowdowns-active').locator('tbody tr').first().locator('td').last().click()],
  ['nightly evidence', '/#/analytics?view=nightlies', (page) => page.getByTestId('nightlies-table').locator('tbody tr').first().click()],
  [
    'nightly movement scope',
    '/#/analytics?view=nightlies',
    (page) => page.getByTestId('nightlies-table').locator('tbody td button').first().click(),
  ],
];
test.describe('CI Analytics AMD drill-down sheets meet the quality gates', () => {
  for (const theme of ['light', 'dark']) {
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
      for (const [name, url, open] of SHEETS) {
        test(`${name} (${theme}, ${viewport.width}px)`, async ({ page }) => {
          await setTheme(page, theme);
          await page.setViewportSize(viewport);
          const errors = collectErrors(page);
          await page.goto(url);
          await page.locator('[data-slot="page-header"]').first().waitFor();
          await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
          await open(page);
          await expect(page.getByRole('dialog').last()).toHaveAttribute('data-state', 'open');
          await expectQuality(page, errors);
        });
      }
    }
  }
});
