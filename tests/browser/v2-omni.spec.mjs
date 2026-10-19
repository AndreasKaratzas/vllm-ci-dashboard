import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

import { readGolden } from './golden/read.mjs';
import { collectErrors, expectQuality, setTheme } from './support/quality.mjs';
import { routeSection } from './support/sections.mjs';

// The fixture site's omni section carries no mapping history, so most cases
// serve the real (trimmed) published snapshot the goldens were read from.
const golden = readGolden('omni');
const here = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(resolve(here, '../../web/tests/fixtures/omni_sections.json'), 'utf8'));
const MAPPED_OMNI = JSON.stringify({ omni: fixture.omni });

// KPI values tween on load; reduced motion shows the final value at once.
test.use({ reducedMotion: 'reduce' });

async function isolate(page, omniBody) {
  // Both pages race the live queue branch against the published section; keep it offline.
  await page.route('https://raw.githubusercontent.com/**', (route) => route.abort());
  if (omniBody) {
    await routeSection(page, 'omni', omniBody);
  }
}

const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
// The design system shows a missing headline value as a muted em dash and labels in sentence case.
const MISSING = new Set(['-', 'Unavailable']);
function headline(fact) {
  const [label, value, meta, tone] = fact.split(' | ');
  return [label.toLowerCase(), MISSING.has(value) ? '—' : value, meta, tone].join(' | ');
}
const row = (cells) => cells.map((cell) => (cell === '—' ? '-' : cell)).join(' | ');

// A fresh load: re-entering the current hash route would keep an open sheet.
async function reload(page, route = '/#/omni') {
  await page.goto('about:blank');
  await page.goto(route);
}

async function settled(page) {
  await page.locator('[data-slot=page-header]').waitFor();
  await expect(page.locator('[data-slot=async-loading]')).toHaveCount(0);
}

function readKpis(root, selector) {
  const t = (el) => (el?.textContent || '').replace(/\s+/g, ' ').trim();
  return [...root.querySelectorAll(selector)].map((card) => {
    const action = card.querySelector('[data-kpi-action]').cloneNode(true);
    action.querySelectorAll('.sr-only').forEach((el) => el.remove());
    return [
      t(action).toLowerCase(),
      t(card.querySelector('[data-slot=kpi-value]')),
      t(card.querySelector('[data-slot=card-footer] p')),
      card.dataset.tone || '',
    ].join(' | ');
  });
}

async function workloadFacts(page, route) {
  await page.goto(route);
  await settled(page);
  await page.locator('[data-testid=omni-workload], [data-testid=omni-mapping-empty]').first().waitFor();
  const facts = await page.locator('body').evaluate((root, readKpisSource) => {
    const t = (el) => (el?.textContent || '').replace(/\s+/g, ' ').trim();
    const read = new Function(`return ${readKpisSource}`)();
    const panel = (card) => (card ? `${t(card.querySelector('[data-slot=card-title]'))} | ${t(card.querySelector('[data-slot=card-description]'))}` : '');
    const rows = (scope) =>
      scope ? [...scope.querySelectorAll('tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map(t).filter((cell, i, all) => cell || i < all.length - 1)) : [];
    const note = root.querySelector('[data-testid=omni-scope-note]');
    return {
      description: t(root.querySelector('[data-slot=page-header] p')),
      kpis: read(root, '[data-testid^=omni-kpi-]'),
      noteHeading: t(note?.querySelector('p')),
      noteTone: note?.dataset.tone,
      panels: [panel(root.querySelector('[data-testid=omni-volume]')), panel(root.querySelector('[data-testid=omni-impact]'))],
      empties: [t(root.querySelector('[data-testid=omni-volume-empty]')), t(root.querySelector('[data-testid=omni-impact-empty]'))],
      impactRows: rows(root.querySelector('[data-testid=omni-impact-preview]')),
      comparison: rows(root.querySelector('[data-testid=omni-comparison]')),
      activeMapping: t(root.querySelector('[data-testid=omni-mapping-range] [data-state=on]')),
      emptyMapping: t(root.querySelector('[data-testid=omni-mapping-empty]')),
    };
  }, readKpis.toString());
  if (facts.noteHeading) {
    await page.getByRole('button', { name: 'Coverage details' }).click();
    facts.noteText = norm(await page.getByTestId('omni-scope-text').textContent());
    await page.keyboard.press('Escape');
  }
  facts.impactRows = facts.impactRows.map(row);
  facts.comparison = facts.comparison.map(row);
  return facts;
}

async function liveFacts(page, route) {
  await page.goto(route);
  await settled(page);
  await page.getByTestId('omni-live').waitFor();
  return page.locator('body').evaluate((root, readKpisSource) => {
    const t = (el) => (el?.textContent || '').replace(/\s+/g, ' ').trim();
    const read = new Function(`return ${readKpisSource}`)();
    return {
      facts: read(root, '[data-testid=omni-live-fact]'),
      liveMeta: t(root.querySelector('[data-testid=omni-live] [data-slot=card-description]')),
      liveActions: [...root.querySelectorAll('[data-testid=omni-live-actions] button')].map(t),
      activeRange: t(root.querySelector('[data-testid=omni-range] [data-state=on]')),
    };
  }, readKpis.toString());
}

const withView = (route, view) => `${route}${route.includes('?') ? '&' : '?'}view=${view}`;

/** Every golden fact of one case, read across the workload and live views. */
async function v2Facts(page, route = '/#/omni') {
  const workload = await workloadFacts(page, route);
  const live = await liveFacts(page, withView(route, 'live'));
  return { ...workload, ...live };
}

function expectWorkload(actual, expected) {
  expect(actual.description).toBe(expected.description);
  expect(actual.kpis).toEqual(expected.kpis.map(headline));
  expect(actual.noteHeading).toBe(expected.noteHeading);
  expect(actual.noteText).toBe(expected.noteText);
  expect(actual.noteTone).toBe(expected.noteTone);
  expect(actual.panels).toEqual(expected.panels);
  expect(actual.empties).toEqual(expected.empties);
  expect(actual.impactRows).toEqual(expected.impactRows);
  expect(actual.comparison).toEqual(expected.comparison);
  expect(actual.activeMapping).toBe(expected.activeMapping);
}

function expectLive(actual, expected) {
  expect(actual.facts).toEqual(expected.facts.map(headline));
  expect(actual.liveMeta).toBe(expected.liveMeta);
  expect(actual.liveActions).toEqual(expected.liveActions);
  expect(actual.activeRange).toBe(expected.activeRange);
}

async function v2Drawer(page) {
  const sheet = page.locator('[data-slot=evidence-sheet][data-state=open]').last();
  await sheet.waitFor();
  return sheet.evaluate((o) => {
    const t = (el) => (el?.textContent || '').replace(/\s+/g, ' ').trim();
    return {
      title: t(o.querySelector('[data-slot=sheet-title]')),
      subtitle: t(o.querySelector('[data-slot=sheet-description]')),
      fields: [...o.querySelectorAll('[data-slot=evidence-fields] dt')].map((dt) => `${t(dt)}=${t(dt.nextElementSibling)}`),
      strip: [...o.querySelectorAll('[data-slot=evidence-stat-value]')].map(t),
      firstColumn: [...o.querySelectorAll('table')].map((table) => [...table.querySelectorAll('tbody tr')].map((tr) => t(tr.querySelector('td')))),
      empties: [...o.querySelectorAll('[data-slot=empty-state]')].map(t),
    };
  });
}

test.describe('Omni CI matches the golden page facts', () => {
  test('fixture site: missing mapping history and live queue state', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await isolate(page);
    const expected = golden['fixture site'];
    const v2 = await v2Facts(page);
    expect(v2.description).toBe(expected.facts.description);
    expect(v2.activeMapping).toBe(expected.facts.activeMapping);
    // Without mapping history the KPI row, charts, and comparison collapse into one
    // empty state that carries the reason every one of them reported.
    expect(new Set(expected.facts.empties)).toEqual(new Set([expected.facts.kpis[0].split(' | ')[2]]));
    expect(v2.emptyMapping).toContain(expected.facts.empties[0]);
    expect(v2.kpis).toEqual([]);
    expectLive(v2, expected.facts);

    // Active jobs drill-down: the exact job rows.
    await page.getByRole('button', { name: /^Active jobs \(/ }).click();
    const jobs = await v2Drawer(page);
    expect(jobs.title).toBe(expected.jobs.title);
    expect(jobs.firstColumn[0]).toEqual(expected.jobs.firstColumn);
    expect(errors).toEqual([]);
  });

  test('narrow viewport: the window controls do not scroll the page sideways', async ({ page }) => {
    await isolate(page, MAPPED_OMNI);
    await page.setViewportSize({ width: 390, height: 844 });
    for (const [route, control] of [
      ['/#/omni', 'omni-mapping-range'],
      ['/#/omni?view=live', 'omni-range'],
    ]) {
      await reload(page, route);
      await settled(page);
      await expect(page.getByTestId(control)).toBeVisible();
      const { scrollWidth, clientWidth } = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));
      expect(clientWidth).toBe(390);
      expect(scrollWidth).toBeLessThanOrEqual(clientWidth);
    }
  });

  test('published snapshot: mapping KPIs, coverage note, charts, comparison, and drawers', async ({ page }) => {
    await isolate(page, MAPPED_OMNI);
    const expected = golden['published snapshot'];
    const v2 = await v2Facts(page);
    expectWorkload(v2, expected.facts);
    expectLive(v2, expected.facts);

    // Every KPI opens its drawer.
    for (const [i, drawer] of expected.kpiDrawers.entries()) {
      await reload(page);
      await page.locator('[data-testid^=omni-kpi-]').nth(i).locator('[data-kpi-action]').click();
      expect(await v2Drawer(page)).toEqual(drawer);
    }

    // Repository comparison row -> workload drawer; queue preview row -> queue impact drawer.
    await reload(page);
    await page.getByTestId('omni-comparison').locator('tbody tr').nth(1).getByRole('button').click();
    expect(await v2Drawer(page)).toEqual(expected.mainWorkload);
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-slot=evidence-sheet][data-state=open]')).toHaveCount(0);
    await page.getByTestId('omni-impact-preview').locator('tbody tr').first().getByRole('button').first().click();
    expect(await v2Drawer(page)).toEqual(expected.queue);
  });

  test('window selections and old links keep their windows', async ({ page }) => {
    await isolate(page, MAPPED_OMNI);
    await page.goto('/?ops_omni_mapping_range=1d&ops_omni_range=3h#ci-omni');
    await expect(page).toHaveURL(/#\/omni\?mapping=1d&range=3h$/);
    const linked = await v2Facts(page, '/#/omni?mapping=1d&range=3h');
    expectWorkload(linked, golden['mapping 1d, range 3h']);
    expectLive(linked, golden['mapping 1d, range 3h']);

    // Toggling a window renders the golden facts of that window.
    await reload(page);
    await page.getByTestId('omni-mapping-range').locator('[aria-label="1 month"]').click();
    await expect(page).toHaveURL(/#\/omni\?mapping=1m$/);
    const toggled = await v2Facts(page, '/#/omni?mapping=1m');
    expectWorkload(toggled, golden['mapping 1m']);
    expectLive(toggled, golden['mapping 1m']);
    expect(norm(toggled.panels[0])).toContain('1 month');

    // The live view keeps its own window in the URL.
    await reload(page, '/#/omni?view=live');
    await page.getByTestId('omni-range').locator('[aria-label="3 hours"]').click();
    await expect(page).toHaveURL(/#\/omni\?view=live&range=3h$/);
  });
});

// The published snapshot exercises every chart and table the fixture site leaves empty.
const SHEETS = [
  ['mapping breakdown', '/#/omni', (page) => page.locator('[data-testid=omni-kpi-omni-mapped-jobs] [data-kpi-action]').click()],
  ['time buckets', '/#/omni', (page) => page.getByRole('button', { name: 'Time buckets' }).click()],
  ['queue impact', '/#/omni', (page) => page.getByTestId('omni-impact-preview').locator('tbody tr').first().click()],
  ['active jobs', '/#/omni?view=live', (page) => page.getByRole('button', { name: /^Active jobs \(/ }).click()],
];

for (const theme of ['light', 'dark']) {
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    test.describe(`Omni CI quality (${theme}, ${viewport.width}px)`, () => {
      test.beforeEach(async ({ page }) => {
        await setTheme(page, theme);
        await page.setViewportSize(viewport);
        await isolate(page, MAPPED_OMNI);
      });

      // Aborted raw.githubusercontent requests (see isolate) log net::ERR_FAILED by design.
      for (const route of ['/#/omni', '/#/omni?view=live']) {
        test(`published snapshot ${route} meets the quality gates`, async ({ page }) => {
          const errors = collectErrors(page);
          await page.goto(route);
          await settled(page);
          await expectQuality(page, errors.filter((e) => !e.includes('net::ERR_FAILED')));
        });
      }

      for (const [name, route, open] of SHEETS) {
        test(`${name} sheet meets the quality gates`, async ({ page }) => {
          const errors = collectErrors(page);
          await page.goto(route);
          await settled(page);
          await open(page);
          await page.locator('[data-slot=evidence-sheet][data-state=open]').waitFor();
          await expectQuality(page, errors.filter((e) => !e.includes('net::ERR_FAILED')));
        });
      }
    });
  }
}
