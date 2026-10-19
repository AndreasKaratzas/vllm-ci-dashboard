import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// v2 CI Analytics "DNS health" and "CI agent health" against classic.html on
// the same routed snapshots: a real (trimmed) DNS aggregate and the live
// amd_agent_health operations section, plus the fixture site's own data.
const fixtures = resolve(dirname(fileURLToPath(import.meta.url)), '../../web/tests/fixtures');
const DNS = readFileSync(resolve(fixtures, 'dns_pages.json'), 'utf8');
const AGENT = JSON.stringify({ amd_agent_health: JSON.parse(readFileSync(resolve(fixtures, 'agent_health_live.json'), 'utf8')) });

async function routeDns(page) {
  const fulfill = (route) => route.fulfill({ body: DNS, contentType: 'application/json', headers: { 'access-control-allow-origin': '*' } });
  await page.route('https://raw.githubusercontent.com/**/dns_failures.json*', fulfill);
  await page.route('**/data/vllm/ci/dns_failures.json*', fulfill);
}

async function routeAgent(page) {
  await page.route('**/operations_v2/amd_agent_health.json*', (route) => route.fulfill({ body: AGENT, contentType: 'application/json' }));
}

const clean = (s) => String(s ?? '').replace(/\s+/g, ' ').trim();

async function classicDns(page, query) {
  await page.goto(`/classic.html?ops_analytics_view=dns&${query}#ci-analytics`);
  const host = page.locator('#tab-ci-analytics');
  await expect(host.locator('.ops-dns-summary-item').first()).toBeVisible();
  return host.evaluate((root) => ({
    summary: [...root.querySelectorAll('.ops-dns-summary-item')].map((item) => [
      item.querySelector('.ops-dns-summary-value').textContent,
      item.querySelector('.ops-dns-summary-meta')?.textContent ?? '',
    ]),
    cards: [...root.querySelectorAll('article.ops-dns-queue-card')].map((card) => ({
      queue: card.querySelector('.ops-dns-queue-card-title').textContent,
      stats: [...card.querySelectorAll('.ops-dns-queue-card-stats span')].map((s) => s.textContent),
      bars: [...card.querySelectorAll('.ops-dns-node-bar')].map((bar) => [
        bar.getAttribute('aria-label'),
        bar.querySelector('.ops-dns-node-count').textContent,
        bar.querySelector('.ops-dns-node-meta').textContent,
      ]),
    })),
    stale: root.querySelector('.ops-dns-stale-warning')?.textContent ?? null,
    coverage: root.querySelector('.ops-dns-coverage-note')?.textContent ?? null,
    empty: root.querySelector('.ops-empty')?.textContent ?? null,
  }));
}

async function v2Dns(page, query) {
  await page.goto(`/#/analytics?view=dns&${query}`);
  const host = page.getByTestId('analytics-dns');
  await expect(host.getByTestId('dns-summary')).toBeVisible();
  return host.evaluate((root) => ({
    summary: [...root.querySelectorAll('[data-testid^="dns-kpi-"]')].map((card) => [
      card.querySelector('[data-slot="card-title"]').textContent.trim(),
      card.querySelector('[data-slot="card-footer"] .text-muted-foreground')?.textContent.trim() ?? '',
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
    stale: root.querySelector('[data-testid="dns-stale"]')?.textContent ?? null,
    coverage: root.querySelector('[data-testid="dns-coverage"]')?.textContent ?? null,
    empty: root.querySelector('[data-testid="dns-empty"]')?.textContent ?? null,
  }));
}

function normalizeDns(facts) {
  return { ...facts, stale: facts.stale && clean(facts.stale), coverage: facts.coverage && clean(facts.coverage), empty: facts.empty && clean(facts.empty) };
}

test.describe('v2 DNS health matches the classic page on the same snapshot', () => {
  for (const [windowId, scope] of [['720h', 'amd'], ['720h', 'canonical'], ['168h', 'amd'], ['3h', 'amd']]) {
    test(`summary, queue cards, and node bars for ${windowId} / ${scope}`, async ({ page }) => {
      await routeDns(page);
      const query = `ops_analytics_dns_window=${windowId}&ops_analytics_dns_scope=${scope}`;
      const classic = normalizeDns(await classicDns(page, query));
      const v2 = normalizeDns(await v2Dns(page, `dns_window=${windowId}${scope === 'amd' ? '' : '&dns_scope=canonical'}`));
      expect(classic.summary).toHaveLength(5);
      expect(classic.summary.every(([value]) => value)).toBe(true);
      if (windowId !== '3h') expect(classic.cards.length).toBeGreaterThan(0);
      else expect(classic.empty).toBeTruthy();
      expect(v2).toEqual(classic);
    });
  }

  test('node evidence sheet carries the same fields and exact logs as the classic drawer', async ({ page }) => {
    await routeDns(page);
    await classicDns(page, 'ops_analytics_dns_window=168h');
    await page.locator('#tab-ci-analytics .ops-dns-node-bar').first().click();
    const drawer = page.getByRole('dialog');
    const classic = await drawer.evaluate((root) => ({
      title: root.querySelector('.ops-overlay-title').textContent,
      fields: [...root.querySelectorAll('.ops-detail-field')].map((f) => `${f.querySelector('dt').textContent}=${f.querySelector('dd').textContent}`),
      meta: root.querySelector('.ops-dns-evidence .ops-panel-meta').textContent,
      links: [...root.querySelectorAll('.ops-dns-evidence tbody tr td:nth-child(2) a')].map((a) => a.getAttribute('href')),
    }));
    expect(classic.fields.length).toBeGreaterThan(5);

    await v2Dns(page, 'dns_window=168h');
    await page.getByTestId('dns-node-bar').first().click();
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText('DNS observation is not the job outcome');
    const v2 = await sheet.evaluate((root) => ({
      title: root.querySelector('h2').textContent.trim(),
      fields: [...root.querySelectorAll('[data-testid="dns-evidence-fields"] > div')].map(
        (f) => `${f.querySelector('dt').textContent.trim()}=${f.querySelector('dd').textContent.trim()}`,
      ),
      meta: root.querySelector('[data-testid="dns-evidence-meta"]').textContent.trim(),
      links: [...root.querySelectorAll('tbody tr td:nth-child(2) a')].map((a) => a.getAttribute('href')),
    }));
    expect(v2).toEqual(classic);
  });

  test('scope and window controls update the route and the view', async ({ page }) => {
    await routeDns(page);
    const classic = normalizeDns(await classicDns(page, 'ops_analytics_dns_window=720h&ops_analytics_dns_scope=canonical'));
    await v2Dns(page, 'dns_window=720h');
    const scope = page.getByRole('group', { name: 'DNS queue scope' });
    await scope.getByRole('radio', { name: 'Canonical AMD (12)' }).or(scope.getByRole('button', { name: 'Canonical AMD (12)' })).click();
    await expect(page).toHaveURL(/dns_scope=canonical/);
    await expect(page.getByTestId('dns-queue-card')).toHaveCount(classic.cards.length);
    await page.getByTestId('dns-window').click();
    await page.getByRole('option', { name: 'Last 7 days' }).click();
    await expect(page).toHaveURL(/dns_window=168h/);
  });

  test('legacy DNS links open the v2 view with their state', async ({ page }) => {
    await routeDns(page);
    await page.goto('/?ops_analytics_view=dns&ops_analytics_dns_window=3h&ops_analytics_dns_scope=canonical#ci-analytics');
    await expect(page).toHaveURL(/#\/analytics\?view=dns&dns_window=3h&dns_scope=canonical$/);
    await expect(page.getByTestId('dns-summary')).toBeVisible();
  });
});

async function classicAgent(page, query) {
  await page.goto(`/classic.html?ops_analytics_view=agent-health&${query}#ci-analytics`);
  const host = page.locator('#tab-ci-analytics');
  await expect(host.locator('.ops-status-item').first()).toBeVisible();
  return host.evaluate((root) => ({
    kpis: [...root.querySelectorAll('.ops-status-item')].map((k) => [k.querySelector('.ops-stat-value').textContent, k.querySelector('.ops-stat-meta').textContent]),
    description: root.querySelector('.ops-agent-table').closest('.ops-panel').querySelector('.ops-panel-meta').textContent,
    rows: [...root.querySelectorAll('.ops-agent-table tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map((td) => td.textContent)),
    selectedNode: (() => {
      const select = root.querySelector('.ops-agent-toolbar select');
      return select.selectedOptions[0]?.textContent ?? '';
    })(),
    events: root.querySelector('.ops-cluster-section .ops-section-description').textContent,
    cards: [...root.querySelectorAll('.ops-cofailure-card .ops-cofailure-node')].map((b) => b.textContent),
  }));
}

/** Every v2 table row across all DataTable pages, then back on page 1. */
async function v2AllRows(host, cells = 'td') {
  const read = () =>
    host.locator('table tbody tr').evaluateAll(
      (trs, sel) => trs.map((tr) => [...tr.querySelectorAll(sel)].map((td) => td.textContent.trim())).filter((r) => r.length > 1 || sel !== 'td'),
      cells,
    );
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
      return [card.querySelector('[data-slot="card-title"]').textContent.trim(), card.querySelector('[data-slot="card-footer"] div.text-muted-foreground').textContent.trim()];
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
  return { ...facts, rows };
}

test.describe('v2 CI agent health matches the classic page on the same snapshot', () => {
  const cases = [
    ['live section, default week', 'events', '', ''],
    ['live section, 14d all failures', 'events', 'ops_agent_window=14d&ops_agent_signal=all', 'agent_window=14d&agent_signal=all'],
    ['live section, 3d MI355 nightlies including cancelled', true, 'ops_agent_window=3d&ops_agent_gpu=MI355&ops_agent_nightly=1&ops_agent_excl_cancel=0', 'agent_window=3d&agent_gpu=MI355&agent_nightly=1&agent_excl_cancel=0'],
    ['live section, 30d hard failures with a 1h co-failure window', true, 'ops_agent_window=30d&ops_agent_signal=hard&ops_agent_cofail=60', 'agent_window=30d&agent_signal=hard&agent_cofail=60'],
    ['fixture site section', false, 'ops_agent_window=14d', 'agent_window=14d'],
  ];
  for (const [name, routed, classicQuery, v2Query] of cases) {
    test(name, async ({ page }) => {
      if (routed) await routeAgent(page);
      const classic = await classicAgent(page, classicQuery);
      const v2 = await v2Agent(page, v2Query);
      expect(classic.rows.length).toBeGreaterThan(0);
      expect(classic.kpis).toHaveLength(4);
      if (routed === 'events') expect(classic.cards.length).toBeGreaterThan(0);
      expect(v2).toEqual(classic);
    });
  }

  test('node evidence, node focus, and sorting follow classic', async ({ page }) => {
    await routeAgent(page);
    await classicAgent(page, 'ops_agent_window=14d');
    const host = page.locator('#tab-ci-analytics');
    await host.locator('.ops-agent-table tbody tr').first().getByRole('button', { name: /^Open retained/ }).click();
    const dialog = page.getByRole('dialog');
    const classicTitle = await dialog.locator('.ops-overlay-title').textContent();
    const classicLinks = await dialog.locator('tbody tr td:first-child a').evaluateAll((as) => as.map((a) => a.getAttribute('href')));
    expect(classicLinks.length).toBeGreaterThan(0);
    await page.keyboard.press('Escape');
    await host.getByRole('button', { name: 'Sort by Physical node' }).click();
    const classicSorted = await host.locator('.ops-agent-table tbody tr td:first-child').evaluateAll((tds) => tds.map((td) => td.textContent));
    // More rows than one v2 page, so the comparison walks the DataTable pagination.
    expect(classicSorted.length).toBeGreaterThan(15);
    await host.locator('.ops-agent-table tbody tr').first().locator('td').first().getByRole('button').click();
    const classicScoped = await host.locator('.ops-cluster-section .ops-section-description').textContent();

    await v2Agent(page, 'agent_window=14d');
    const v2host = page.getByTestId('analytics-agent-health');
    await v2host.locator('table tbody tr').first().getByRole('button', { name: /^Open retained/ }).click();
    const sheet = page.getByRole('dialog');
    await expect(sheet.locator('h2')).toHaveText(classicTitle);
    expect(await sheet.locator('tbody tr td:first-child a').evaluateAll((as) => as.map((a) => a.getAttribute('href')))).toEqual(classicLinks);
    await page.keyboard.press('Escape');
    await expect(sheet).toHaveCount(0);
    await v2host.getByRole('button', { name: 'Sort by Physical node' }).click();
    expect((await v2AllRows(v2host)).rows.map((r) => r[0])).toEqual(classicSorted);
    await v2host.locator('table tbody tr').first().locator('td').first().getByRole('button').click();
    await expect(v2host.getByTestId('agent-events-description')).toHaveText(classicScoped);
    await expect(v2host.getByTestId('agent-events-title')).toHaveText('Co-failure events · one node');
    await expect(page).toHaveURL(/agent_node=/);
    await expect(v2host.getByTestId('agent-timeline')).toBeVisible();
  });

  test('rejects the legacy all-branch cohort like classic', async ({ page }) => {
    await page.route('**/operations_v2/amd_agent_health.json*', async (route) => {
      const packet = JSON.parse(AGENT);
      delete packet.amd_agent_health.branches;
      await route.fulfill({ json: packet });
    });
    await page.goto('/classic.html?ops_analytics_view=agent-health#ci-analytics');
    const classic = await page.locator('#tab-ci-analytics .ops-evidence-note.is-warning').first().textContent();
    expect(classic).toContain('Current ci/main MI GPU cohort observations are required');
    await page.goto('/#/analytics?view=agent-health');
    await expect(page.getByTestId('agent-unavailable')).toHaveText(classic);
  });

  test('legacy agent-health links open the v2 view with their state', async ({ page }) => {
    await routeAgent(page);
    await page.goto('/?ops_analytics_view=agent-health&ops_agent_window=30d&ops_agent_signal=hard#ci-analytics');
    await expect(page).toHaveURL(/#\/analytics\?view=agent-health&agent_window=30d&agent_signal=hard$/);
    await expect(page.getByTestId('agent-signal-caption')).toHaveText('Showing every hard failure on the node — not just the infra-suspect subset.');
  });

  test('dragging across the run timeline zooms to the selected range, like classic', async ({ page }) => {
    await routeAgent(page);
    await page.goto('/classic.html?ops_analytics_view=agent-health&ops_agent_window=14d#ci-analytics');
    await expect(page.locator('#tab-ci-analytics .ops-zoom-hint')).toHaveText('drag to select a range');
    await page.goto('/#/analytics?view=agent-health&agent_window=14d');
    const host = page.getByTestId('analytics-agent-health');
    await expect(host.getByText('drag to select a range')).toBeVisible();
    const chart = host.getByTestId('agent-timeline');
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
    await expect(chart.getByTestId('agent-timeline-selection')).toBeVisible();
    await page.mouse.move(box.x + box.width * 0.9, y, { steps: 5 });
    await page.mouse.up();
    await expect(chart.getByTestId('agent-timeline-selection')).toHaveCount(0);
    await expect.poll(ticks).not.toEqual(before);
    expect(popups).toHaveLength(0);
    await host.getByRole('button', { name: 'Reset the timeline to the full window' }).click();
    await expect.poll(ticks).toEqual(before);
  });
});
