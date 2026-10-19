import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

// The fixture site's omni section carries no mapping history, so the second
// suite serves both pages the same real (trimmed) published snapshot.
const here = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(resolve(here, '../../web/tests/fixtures/omni_sections.json'), 'utf8'));
const MAPPED_OMNI = JSON.stringify({ omni: fixture.omni });

async function isolate(page, omniBody) {
  // Both pages race the live queue branch against the published section; keep it offline.
  await page.route('https://raw.githubusercontent.com/**', (route) => route.abort());
  if (omniBody) {
    await page.route(/operations_v2\/omni\.json/, (route) => route.fulfill({ body: omniBody, contentType: 'application/json' }));
  }
}

const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();

async function classicFacts(page, query = '') {
  await page.goto(`/classic.html${query}#ci-omni`);
  const host = page.locator('#ci-omni-view[data-render-state="ready"]');
  await host.waitFor();
  return host.evaluate((root) => {
    const t = (el) => (el?.textContent || '').replace(/\s+/g, ' ').trim();
    const tone = (el) => [...el.classList].find((c) => /^is-(success|danger|warning|info|neutral)$/.test(c)) || '';
    const grid = [...root.querySelectorAll('.ops-omni-overview-grid > section')];
    const comparison = root.querySelector('.ops-omni-comparison');
    return {
      description: t(root.querySelector('.ops-page-description')),
      kpis: [...root.querySelectorAll('.ops-status-strip')][0]
        ? [...root.querySelectorAll('.ops-status-strip')[0].querySelectorAll('.ops-status-item')].map((k) =>
            [t(k.querySelector('.ops-stat-label')), t(k.querySelector('.ops-stat-value')), t(k.querySelector('.ops-stat-meta')), tone(k)].join(' | '),
          )
        : [],
      noteHeading: t(root.querySelector('.ops-omni-coverage-note strong')),
      noteText: t(root.querySelector('.ops-omni-coverage-note span')),
      noteTone: tone(root.querySelector('.ops-omni-coverage-note')),
      panels: grid.map((p) => `${t(p.querySelector('.ops-panel-title'))} | ${t(p.querySelector('.ops-panel-meta'))}`),
      empties: grid.map((p) => t(p.querySelector('.ops-empty'))),
      impactRows: grid[1] ? [...grid[1].querySelectorAll('tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map(t).join(' | ')) : [],
      comparison: [...comparison.querySelectorAll('tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map(t).join(' | ')),
      facts: [...root.querySelectorAll('.ops-omni-live-fact')].map((f) =>
        [t(f.querySelector('.ops-stat-label')), t(f.querySelector('.ops-omni-live-value')), t(f.querySelector('.ops-stat-meta')), tone(f)].join(' | '),
      ),
      liveMeta: t(root.querySelector('.ops-omni-live .ops-panel-meta')),
      liveActions: [...root.querySelectorAll('.ops-omni-live-actions > button')].map(t),
      activeMapping: t(root.querySelector('.ops-omni-mapping-toolbar .ops-segment.is-active')),
      activeRange: t(root.querySelector('.ops-omni-live .ops-segment.is-active')),
    };
  });
}

async function v2Facts(page, route = '/#/omni') {
  await page.goto(route);
  await page.getByTestId('omni-live').waitFor();
  return page.locator('main, body').first().evaluate((root) => {
    const t = (el) => (el?.textContent || '').replace(/\s+/g, ' ').trim();
    const panel = (card) => `${t(card.querySelector('[data-slot=card-title]'))} | ${t(card.querySelector('[data-slot=card-description]'))}`;
    const rows = (scope) => (scope ? [...scope.querySelectorAll('tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map(t).join(' | ')) : []);
    const volume = root.querySelector('[data-testid=omni-volume]');
    const impact = root.querySelector('[data-testid=omni-impact]');
    return {
      description: t(root.querySelector('[data-testid=omni-description]')),
      kpis: [...root.querySelectorAll('[data-testid^=omni-kpi-]')].map((k) =>
        [
          t(k.querySelector('[data-slot=card-description]')),
          t(k.querySelector('[data-slot=card-title]')),
          t(k.querySelector('[data-slot=card-footer] div')),
          k.dataset.tone || '',
        ].join(' | '),
      ),
      noteHeading: t(root.querySelector('[data-testid=omni-scope-note] strong')),
      noteText: t(root.querySelector('[data-testid=omni-scope-note] span')),
      noteTone: root.querySelector('[data-testid=omni-scope-note]').dataset.tone,
      panels: [panel(volume), panel(impact)],
      empties: [t(root.querySelector('[data-testid=omni-volume-empty]')), t(root.querySelector('[data-testid=omni-impact-empty]'))],
      impactRows: rows(root.querySelector('[data-testid=omni-impact-preview]')),
      comparison: rows(root.querySelector('[data-testid=omni-comparison]')),
      facts: [...root.querySelectorAll('[data-testid=omni-live-fact]')].map((f) =>
        [...f.querySelectorAll('span, strong')].map(t).filter((_, i) => i < 3).concat(f.dataset.tone || '').join(' | '),
      ),
      liveMeta: t(root.querySelector('[data-testid=omni-live] [data-slot=card-description]')),
      liveActions: [...root.querySelectorAll('[data-testid=omni-live-actions] button')].map(t),
      activeMapping: t(root.querySelector('[data-testid=omni-mapping-range] [data-state=on]')),
      activeRange: t(root.querySelector('[data-testid=omni-range] [data-state=on]')),
    };
  });
}

async function classicDrawer(page) {
  return page.locator('.ops-overlay:not([hidden])').last().evaluate((o) => {
    const t = (el) => (el?.textContent || '').replace(/\s+/g, ' ').trim();
    return {
      title: t(o.querySelector('.ops-overlay-title')),
      subtitle: t(o.querySelector('.ops-overlay-subtitle')),
      fields: [...o.querySelectorAll('.ops-detail-fields .ops-detail-field')].map((f) => `${t(f.querySelector('dt'))}=${t(f.querySelector('dd'))}`),
      strip: [...o.querySelectorAll('.ops-status-strip .ops-stat-value')].map(t),
      firstColumn: [...o.querySelectorAll('table.ops-table')].map((table) => [...table.querySelectorAll('tbody tr')].map((tr) => t(tr.querySelector('td')))),
    };
  });
}

async function v2Drawer(page) {
  const sheet = page.getByTestId('omni-detail').last();
  await sheet.waitFor();
  return sheet.evaluate((o) => {
    const t = (el) => (el?.textContent || '').replace(/\s+/g, ' ').trim();
    return {
      title: t(o.querySelector('[data-slot=sheet-title]')),
      subtitle: t(o.querySelector('[data-slot=sheet-description]')),
      fields: [...o.querySelectorAll('dl dt')].map((dt) => `${t(dt)}=${t(dt.nextElementSibling)}`),
      strip: [...o.querySelectorAll('[data-slot=card-title]')].map(t),
      firstColumn: [...o.querySelectorAll('table')].map((table) => [...table.querySelectorAll('tbody tr')].map((tr) => t(tr.querySelector('td')))),
    };
  });
}

function expectSameFacts(classic, v2) {
  // Guard against both sides extracting nothing.
  expect(classic.kpis).toHaveLength(4);
  expect(classic.facts).toHaveLength(4);
  expect(classic.comparison).toHaveLength(2);
  expect(classic.noteHeading).not.toBe('');
  expect(v2.kpis).toEqual(classic.kpis);
  expect({ ...v2, kpis: undefined }).toEqual({ ...classic, kpis: undefined });
}

test.describe('v2 Omni CI matches the classic page on the same snapshot', () => {
  test('fixture site: unavailable mapping history and live queue state', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await isolate(page);
    const classic = await classicFacts(page);
    const v2 = await v2Facts(page);
    expectSameFacts(classic, v2);
    expect(classic.empties.filter(Boolean)).toHaveLength(2);

    // Active jobs drill-down: the exact job rows, then a queue drawer from the jobs table.
    await page.getByRole('button', { name: /^Active jobs \(/ }).click();
    const jobs = await v2Drawer(page);
    await page.goto('/classic.html#ci-omni');
    await page.locator('#ci-omni-view[data-render-state="ready"]').waitFor();
    await page.locator('.ops-omni-live-actions > button', { hasText: /^Active jobs \(/ }).click();
    const classicJobs = await classicDrawer(page);
    expect(classicJobs.firstColumn[0].length).toBeGreaterThan(0);
    expect(jobs.title).toBe(classicJobs.title);
    expect(jobs.firstColumn[0]).toEqual(classicJobs.firstColumn[0]);
    expect(errors).toEqual([]);
  });

  test('published snapshot: mapping KPIs, coverage note, charts, comparison, and drawers', async ({ page }) => {
    await isolate(page, MAPPED_OMNI);
    const classic = await classicFacts(page);
    const v2 = await v2Facts(page);
    expectSameFacts(classic, v2);
    expect(classic.impactRows.length).toBeGreaterThan(0);
    expect(v2.empties).toEqual(['', '']);

    // Every KPI opens the same drawer.
    for (let i = 0; i < 4; i += 1) {
      await page.goto('/classic.html#ci-omni');
      await page.locator('#ci-omni-view[data-render-state="ready"]').waitFor();
      await page.locator('.ops-status-strip').first().locator('.ops-status-item').nth(i).click();
      const expected = await classicDrawer(page);
      await page.goto('/#/omni');
      await page.locator('[data-testid^=omni-kpi-]').nth(i).locator('[data-slot=card-footer] button').click();
      const actual = await v2Drawer(page);
      expect(expected.title).not.toBe('');
      expect(actual).toEqual(expected);
    }

    // Repository comparison row -> workload drawer; queue preview row -> queue impact drawer.
    await page.goto('/classic.html#ci-omni');
    await page.locator('#ci-omni-view[data-render-state="ready"]').waitFor();
    await page.locator('.ops-omni-comparison tbody tr').nth(1).locator('button').click();
    const classicMain = await classicDrawer(page);
    await page.locator('.ops-overlay:not([hidden]) .ops-overlay-close').last().click();
    await page.locator('.ops-omni-overview-grid > section').nth(1).locator('tbody tr').first().locator('button').first().click();
    const classicQueue = await classicDrawer(page);

    await page.goto('/#/omni');
    await page.getByTestId('omni-comparison').locator('tbody tr').nth(1).getByRole('button').click();
    expect(await v2Drawer(page)).toEqual(classicMain);
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('omni-detail')).toHaveCount(0);
    await page.getByTestId('omni-impact-preview').locator('tbody tr').first().getByRole('button').first().click();
    expect(await v2Drawer(page)).toEqual(classicQueue);
  });

  test('window selections and legacy links keep the classic windows', async ({ page }) => {
    await isolate(page, MAPPED_OMNI);
    const classic = await classicFacts(page, '?ops_omni_mapping_range=1d&ops_omni_range=3h');
    expect(classic.activeMapping).toBe('1 day');
    expect(classic.activeRange).toBe('3 hours');

    await page.goto('/?ops_omni_mapping_range=1d&ops_omni_range=3h#ci-omni');
    await expect(page).toHaveURL(/#\/omni\?mapping=1d&range=3h$/);
    const v2 = await v2Facts(page, '/#/omni?mapping=1d&range=3h');
    expectSameFacts(classic, v2);

    // Toggling a window in v2 matches classic with the same window.
    const monthly = await classicFacts(page, '?ops_omni_mapping_range=1m');
    await page.goto('/#/omni');
    await page.getByTestId('omni-mapping-range').locator('[aria-label="1 month"]').click();
    await expect(page).toHaveURL(/#\/omni\?mapping=1m$/);
    const toggled = await v2Facts(page, '/#/omni?mapping=1m');
    expectSameFacts(monthly, toggled);
    expect(norm(toggled.panels[0])).toContain('1 month');
  });
});
