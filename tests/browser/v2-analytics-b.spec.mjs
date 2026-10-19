import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { readGolden } from './golden/read.mjs';
import { collectErrors, expectQuality, setTheme } from './support/quality.mjs';
import { routeSection } from './support/sections.mjs';

// CI Analytics "DNS health" and "CI agent health" against the golden page facts
// of the same routed snapshots: a real (trimmed) DNS aggregate and the live
// amd_agent_health operations section, plus the fixture site's own data.
const golden = readGolden('analytics-b');
const fixtures = resolve(dirname(fileURLToPath(import.meta.url)), '../../web/tests/fixtures');
const DNS = readFileSync(resolve(fixtures, 'dns_pages.json'), 'utf8');
const AGENT = JSON.stringify({ amd_agent_health: JSON.parse(readFileSync(resolve(fixtures, 'agent_health_live.json'), 'utf8')) });

async function routeDns(page) {
  const fulfill = (route) => route.fulfill({ body: DNS, contentType: 'application/json', headers: { 'access-control-allow-origin': '*' } });
  await page.route('https://raw.githubusercontent.com/**/dns_failures.json*', fulfill);
  await page.route('**/data/vllm/ci/dns_failures.json*', fulfill);
}

async function routeAgent(page) {
  await routeSection(page, 'amd_agent_health', AGENT);
}

const clean = (s) => String(s ?? '').replace(/\s+/g, ' ').trim();
// A KPI without an established value renders a muted em dash where the golden page printed text.
const kpiValue = (text, missing) => (text === '—' ? missing : text);

async function v2Dns(page, query) {
  await page.goto(`/#/analytics?view=dns&${query}`);
  const host = page.getByTestId('analytics-dns');
  await expect(host.getByTestId('dns-summary').or(host.getByTestId('dns-empty'))).toBeVisible();
  return host.evaluate((root) => ({
    summary: [...root.querySelectorAll('[data-testid^="dns-kpi-"]')].map((card) => [
      card.querySelector('[data-slot="kpi-value"]').textContent.trim(),
      card.querySelector('[data-slot="card-footer"] p')?.textContent.trim() ?? '',
    ]),
    cards: [...root.querySelectorAll('[data-testid="dns-queue-card"]')].map((card) => ({
      queue: card.querySelector('[data-slot="card-title"]').textContent.trim(),
      stats: [...card.querySelectorAll('[data-testid="dns-queue-stats"] span')].map((s) => s.textContent),
      bars: [...card.querySelectorAll('[data-testid="dns-node-bar"]')].map((bar) => [
        bar.getAttribute('aria-label'),
        bar.querySelector('[data-testid="dns-node-count"]').textContent,
        bar.querySelector('[data-testid="dns-node-meta"]').textContent,
      ]),
    })),
    stale: root.querySelector('[data-testid="dns-stale"]')?.innerText ?? null,
    // The coverage callout shows each fact as a chip under a fixed title.
    coverage: (() => {
      const callout = root.querySelector('[data-testid="dns-coverage"]');
      if (!callout) return null;
      const facts = [...callout.querySelectorAll('[data-fact]')].map((fact) => fact.textContent.trim());
      return `Partial coverage - counts are lower bounds. ${facts.length ? facts.join(' - ') + '. ' : ''}Missing observations never mean zero.`;
    })(),
    empty: root.querySelector('[data-testid="dns-empty"] p')?.textContent ?? null,
  }));
}

function normalizeDns(facts) {
  return {
    ...facts,
    summary: facts.summary.map(([value, meta]) => [kpiValue(value, '-'), meta]),
    stale: facts.stale && clean(facts.stale),
    coverage: facts.coverage && clean(facts.coverage),
    empty: facts.empty && clean(facts.empty),
  };
}

test.describe('DNS health matches the golden page facts', () => {
  for (const [windowId, scope] of [['720h', 'amd'], ['720h', 'canonical'], ['168h', 'amd'], ['3h', 'amd']]) {
    test(`summary, queue cards, and node bars for ${windowId} / ${scope}`, async ({ page }) => {
      await routeDns(page);
      const v2 = normalizeDns(await v2Dns(page, `dns_window=${windowId}${scope === 'amd' ? '' : '&dns_scope=canonical'}`));
      const expected = golden[`dns ${windowId}/${scope}`];
      // With no observation in the window the whole summary collapses into the empty state.
      const unobserved = expected.summary.every(([value]) => /^[-\s/]+$/.test(value));
      expect(v2).toEqual(unobserved ? { ...expected, summary: [] } : expected);
    });
  }

  test('node evidence sheet carries the same fields and exact logs as the golden drawer', async ({ page }) => {
    await routeDns(page);
    await v2Dns(page, 'dns_window=168h');
    await page.getByTestId('dns-node-bar').first().click();
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText('DNS observation is not the job outcome');
    // The sheet splits the drawer's fields into a stat strip and a field list.
    const v2 = await sheet.evaluate((root) => ({
      title: root.querySelector('h2').textContent.trim(),
      fields: [
        ...[...root.querySelectorAll('[data-slot="evidence-stats"] > div')].map(
          (s) => `${s.querySelector('dt').textContent.trim()}=${s.querySelector('dd').textContent.trim()}`,
        ),
        ...[...root.querySelectorAll('[data-slot="evidence-fields"] dt')].map((dt) => `${dt.textContent.trim()}=${dt.nextElementSibling.textContent.trim()}`),
      ],
      meta: root.querySelector('[data-slot="evidence-table-meta"]').textContent.trim(),
      links: [...root.querySelectorAll('tbody tr td:nth-child(2) a')].map((a) => a.getAttribute('href')),
    }));
    const expected = golden['dns evidence'];
    expect({ ...v2, fields: [...v2.fields].sort() }).toEqual({ ...expected, fields: [...expected.fields].sort() });
  });

  test('scope and window controls update the route and the view', async ({ page }) => {
    await routeDns(page);
    await v2Dns(page, 'dns_window=720h');
    const scope = page.getByRole('group', { name: 'DNS queue scope' });
    await scope.getByRole('radio', { name: 'Canonical AMD (12)' }).or(scope.getByRole('button', { name: 'Canonical AMD (12)' })).click();
    await expect(page).toHaveURL(/dns_scope=canonical/);
    await expect(page.getByTestId('dns-queue-card')).toHaveCount(golden['dns 720h/canonical'].cards.length);
    const windows = page.getByRole('group', { name: 'DNS observation window' });
    await windows.getByRole('radio', { name: '7d' }).or(windows.getByRole('button', { name: '7d' })).click();
    await expect(page).toHaveURL(/dns_window=168h/);
    // Below sm the same choice is a select.
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('combobox', { name: 'DNS observation window' }).click();
    await page.getByRole('option', { name: 'Last 3 days' }).click();
    await expect(page).toHaveURL(/dns_window=72h/);
  });

  test('legacy DNS links open the v2 view with their state', async ({ page }) => {
    await routeDns(page);
    await page.goto('/?ops_analytics_view=dns&ops_analytics_dns_window=3h&ops_analytics_dns_scope=canonical#ci-analytics');
    await expect(page).toHaveURL(/#\/analytics\?view=dns&dns_window=3h&dns_scope=canonical$/);
    await expect(page.getByTestId('dns-empty')).toHaveText(new RegExp(golden['dns 3h/amd'].empty));
  });
});

// Data cells only: clickable rows end in a chevron cell.
const DATA_CELL = 'td:has(> [data-slot="cell"])';
// The evidence column names how many runs it opens where the golden page printed "runs ↗".
const evidenceCell = (text) => (/^[\d,]+ runs?$/.test(text) ? 'runs ↗' : text);

/** Every v2 table row across all DataTable pages, then back on page 1. */
async function v2AllRows(host) {
  const read = () =>
    host
      .locator('table tbody tr')
      .evaluateAll((trs, sel) => trs.map((tr) => [...tr.querySelectorAll(sel)].map((td) => td.textContent.trim())).filter((r) => r.length > 1), DATA_CELL)
      .then((rows) => rows.map((row) => row.map((cell, i) => (i === row.length - 1 ? evidenceCell(cell) : cell))));
  const next = host.getByRole('button', { name: 'Next page' });
  const prev = host.getByRole('button', { name: 'Previous page' });
  const rows = await read();
  let pages = 1;
  while ((await next.count()) && (await next.isEnabled())) {
    const before = JSON.stringify(await read());
    await next.click();
    await expect.poll(async () => JSON.stringify(await read())).not.toBe(before);
    rows.push(...(await read()));
    pages += 1;
  }
  while ((await prev.count()) && (await prev.isEnabled())) await prev.click();
  return { rows, pages };
}

async function v2Agent(page, query) {
  await page.goto(`/#/analytics?view=agent-health${query ? '&' + query : ''}`);
  const host = page.getByTestId('analytics-agent-health');
  await expect(host.getByTestId('kpi-agent-nodes')).toBeVisible();
  const { rows } = await v2AllRows(host);
  const facts = await host.evaluate((root) => ({
    kpis: ['agent-nodes', 'agent-unreliable', 'agent-coverage', 'agent-cofail'].map((id) => {
      const card = root.querySelector(`[data-testid="kpi-${id}"]`);
      return [card.querySelector('[data-slot="kpi-value"]').textContent.trim(), card.querySelector('[data-slot="card-footer"] p').textContent.trim()];
    }),
    description: root.querySelector('[data-testid="agent-table-description"]').textContent.trim(),
    rows: null,
    selectedNode: (() => {
      const trigger = root.querySelector('[data-testid="agent-node-select"]');
      return trigger.hasAttribute('data-placeholder') ? '' : trigger.textContent.trim();
    })(),
    events: root.querySelector('[data-testid="agent-events-description"]').textContent.trim(),
    cards: [...root.querySelectorAll('[data-testid="cofailure-node"]')].map((b) => b.textContent.trim()),
  }));
  return { ...facts, kpis: facts.kpis.map(([value, meta]) => [kpiValue(value, 'Unavailable'), meta]), rows };
}

test.describe('CI agent health matches the golden page facts', () => {
  const cases = [
    ['live section, default week', true, ''],
    ['live section, 14d all failures', true, 'agent_window=14d&agent_signal=all'],
    ['live section, 3d MI355 nightlies including cancelled', true, 'agent_window=3d&agent_gpu=MI355&agent_nightly=1&agent_excl_cancel=0'],
    ['live section, 30d hard failures with a 1h co-failure window', true, 'agent_window=30d&agent_signal=hard&agent_cofail=60'],
    ['fixture site section', false, 'agent_window=14d'],
  ];
  for (const [name, routed, query] of cases) {
    test(name, async ({ page }) => {
      if (routed) await routeAgent(page);
      expect(await v2Agent(page, query)).toEqual(golden[`agent ${name}`]);
    });
  }

  test('node evidence, node focus, and sorting match the golden page', async ({ page }) => {
    await routeAgent(page);
    const { evidenceTitle, evidenceLinks, sortedNodes, focusedEvents } = golden['agent drill-downs'];
    // More rows than one v2 page, so the comparison walks the DataTable pagination.
    expect(sortedNodes.length).toBeGreaterThan(15);
    await v2Agent(page, 'agent_window=14d');
    const v2host = page.getByTestId('analytics-agent-health');
    await v2host.locator('table tbody tr').first().getByRole('button', { name: /^\d+ runs?$/ }).click();
    const sheet = page.getByRole('dialog');
    await expect(sheet.locator('h2')).toHaveText(evidenceTitle);
    expect(await sheet.locator('tbody tr td:first-child a').evaluateAll((as) => as.map((a) => a.getAttribute('href')))).toEqual(evidenceLinks);
    await page.keyboard.press('Escape');
    await expect(sheet).toHaveCount(0);
    await v2host.locator('thead').getByRole('button', { name: 'Physical node' }).click();
    expect((await v2AllRows(v2host)).rows.map((r) => r[0])).toEqual(sortedNodes);
    await v2host.locator('table tbody tr').first().locator('td').first().getByRole('button').click();
    await expect(v2host.getByTestId('agent-events-description')).toHaveText(focusedEvents);
    await expect(v2host.getByTestId('agent-events-title')).toHaveText('Co-failure events · one node');
    await expect(page).toHaveURL(/agent_node=/);
    await expect(v2host.getByTestId('timeline')).toBeVisible();
  });

  test('rejects the legacy all-branch cohort', async ({ page }) => {
    const packet = JSON.parse(AGENT);
    delete packet.amd_agent_health.branches;
    await routeSection(page, 'amd_agent_health', packet);
    await page.goto('/#/analytics?view=agent-health');
    await expect(page.getByTestId('agent-unavailable')).toHaveText(golden['agent all-branch cohort'].warning);
  });

  test('legacy agent-health links open the v2 view with their state', async ({ page }) => {
    await routeAgent(page);
    await page.goto('/?ops_analytics_view=agent-health&ops_agent_window=30d&ops_agent_signal=hard#ci-analytics');
    await expect(page).toHaveURL(/#\/analytics\?view=agent-health&agent_window=30d&agent_signal=hard$/);
    await expect(page.getByTestId('agent-signal-caption')).toHaveText('Showing every hard failure on the node — not just the infra-suspect subset.');
  });

  test('dragging across the run timeline zooms to the selected range', async ({ page }) => {
    await routeAgent(page);
    await page.goto('/#/analytics?view=agent-health&agent_window=14d');
    const host = page.getByTestId('analytics-agent-health');
    await expect(host.getByText(golden['agent drill-downs'].zoomHint)).toBeVisible();
    const chart = host.getByTestId('timeline');
    await expect(chart.locator('svg')).toBeVisible();
    await chart.scrollIntoViewIfNeeded();
    const ticks = async () => (await chart.locator('svg .tick text').allTextContents()).filter(Boolean);
    await expect.poll(async () => (await ticks()).length).toBeGreaterThan(1);
    const before = await ticks();
    const box = await chart.boundingBox();
    const y = box.y + 30;
    const popups = [];
    page.on('popup', (p) => popups.push(p));
    await page.mouse.move(box.x + box.width * 0.6, y);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.75, y, { steps: 5 });
    await expect(chart.getByTestId('timeline-selection')).toBeVisible();
    await page.mouse.move(box.x + box.width * 0.9, y, { steps: 5 });
    await page.mouse.up();
    await expect(chart.getByTestId('timeline-selection')).toHaveCount(0);
    await expect.poll(ticks).not.toEqual(before);
    expect(popups).toHaveLength(0);
    await host.getByRole('button', { name: 'Reset the timeline to the full window' }).click();
    await expect.poll(ticks).toEqual(before);
  });
});

// The main drill-down sheets meet the same gates as the pages.
const SHEETS = [
  ['DNS node evidence', '/#/analytics?view=dns&dns_window=720h', (page) => page.getByTestId('dns-node-bar').first().click()],
  ['DNS queue nodes', '/#/analytics?view=dns&dns_window=720h', (page) => page.getByRole('button', { name: /open the queue's nodes$/ }).first().click()],
  ['DNS retained logs', '/#/analytics?view=dns&dns_window=720h', (page) => page.getByTestId('dns-kpi-jobs').locator('[data-kpi-action]').click()],
  ['agent node', '/#/analytics?view=agent-health&agent_window=14d', (page) => page.locator('table tbody tr').first().click()],
  ['agent co-failure event', '/#/analytics?view=agent-health&agent_window=14d', (page) => page.getByTestId('cofailure-node').first().click()],
];
test.describe('analytics drill-down sheets meet the quality gates', () => {
  for (const theme of ['light', 'dark']) {
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
      for (const [name, url, open] of SHEETS) {
        test(`${name} (${theme}, ${viewport.width}px)`, async ({ page }) => {
          await setTheme(page, theme);
          await page.setViewportSize(viewport);
          await routeDns(page);
          await routeAgent(page);
          const errors = collectErrors(page);
          await page.goto(url);
          await open(page);
          await expect(page.getByRole('dialog')).toBeVisible();
          // Let the sheet finish sliding in before measuring it.
          await page.waitForTimeout(600);
          await expectQuality(page, errors);
        });
      }
    }
  }
});
