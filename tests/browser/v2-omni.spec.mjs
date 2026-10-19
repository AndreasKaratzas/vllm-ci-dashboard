import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

import { readGolden } from './golden/read.mjs';

// The fixture site's omni section carries no mapping history, so the second
// suite serves the real (trimmed) published snapshot the goldens were read from.
const golden = readGolden('omni');
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

// A fresh load: re-entering the current hash route would keep an open sheet.
async function reload(page, route = '/#/omni') {
  await page.goto('about:blank');
  await page.goto(route);
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
      empties: [...o.querySelectorAll('[data-testid=omni-detail-empty], [data-testid=omni-detail-table-empty]')].map(t),
    };
  });
}

test.describe('Omni CI matches the golden page facts', () => {
  test('fixture site: unavailable mapping history and live queue state', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await isolate(page);
    const expected = golden['fixture site'];
    expect(await v2Facts(page)).toEqual(expected.facts);

    // Active jobs drill-down: the exact job rows.
    await page.getByRole('button', { name: /^Active jobs \(/ }).click();
    const jobs = await v2Drawer(page);
    expect(jobs.title).toBe(expected.jobs.title);
    expect(jobs.firstColumn[0]).toEqual(expected.jobs.firstColumn);

    // With no mapping history the bucket browser is an empty note, not an empty table.
    await reload(page);
    await page.getByRole('button', { name: 'Inspect time buckets' }).click();
    expect(await v2Drawer(page)).toEqual(expected.buckets);
    expect(errors).toEqual([]);
  });

  test('narrow viewport: the window toggles do not scroll the page sideways', async ({ page }) => {
    await isolate(page, MAPPED_OMNI);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/#/omni');
    await page.getByTestId('omni-mapping-range').waitFor();
    await expect(page.getByTestId('omni-range')).toBeVisible();
    const { scrollWidth, clientWidth } = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    expect(clientWidth).toBe(390);
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth);
  });

  test('published snapshot: mapping KPIs, coverage note, charts, comparison, and drawers', async ({ page }) => {
    await isolate(page, MAPPED_OMNI);
    const expected = golden['published snapshot'];
    const v2 = await v2Facts(page);
    expect(v2).toEqual(expected.facts);
    expect(v2.empties).toEqual(['', '']);

    // Every KPI opens its drawer.
    for (const [i, drawer] of expected.kpiDrawers.entries()) {
      await reload(page);
      await page.locator('[data-testid^=omni-kpi-]').nth(i).locator('[data-slot=card-footer] button').click();
      expect(await v2Drawer(page)).toEqual(drawer);
    }

    // Repository comparison row -> workload drawer; queue preview row -> queue impact drawer.
    await reload(page);
    await page.getByTestId('omni-comparison').locator('tbody tr').nth(1).getByRole('button').click();
    expect(await v2Drawer(page)).toEqual(expected.mainWorkload);
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('omni-detail')).toHaveCount(0);
    await page.getByTestId('omni-impact-preview').locator('tbody tr').first().getByRole('button').first().click();
    expect(await v2Drawer(page)).toEqual(expected.queue);
  });

  test('window selections and legacy links keep their windows', async ({ page }) => {
    await isolate(page, MAPPED_OMNI);
    await page.goto('/?ops_omni_mapping_range=1d&ops_omni_range=3h#ci-omni');
    await expect(page).toHaveURL(/#\/omni\?mapping=1d&range=3h$/);
    expect(await v2Facts(page, '/#/omni?mapping=1d&range=3h')).toEqual(golden['mapping 1d, range 3h']);

    // Toggling a window renders the golden facts of that window.
    await page.goto('/#/omni');
    await page.getByTestId('omni-mapping-range').locator('[aria-label="1 month"]').click();
    await expect(page).toHaveURL(/#\/omni\?mapping=1m$/);
    const toggled = await v2Facts(page, '/#/omni?mapping=1m');
    expect(toggled).toEqual(golden['mapping 1m']);
    expect(norm(toggled.panels[0])).toContain('1 month');
  });
});
