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
// The golden page called the node signal "infra-suspect" under its retired rule; failure attribution renamed it.
const RENAMES = JSON.parse(readFileSync(resolve(fixtures, 'agent_signal_renames.json'), 'utf8'));
const renamed = (value) =>
  typeof value === 'string'
    ? RENAMES.reduce((text, [from, to]) => text.split(from).join(to), value)
    : Array.isArray(value)
      ? value.map(renamed)
      : value && typeof value === 'object'
        ? Object.fromEntries(Object.entries(value).map(([key, item]) => [key, renamed(item)]))
        : value;

async function routeDns(page) {
  const fulfill = (route) => route.fulfill({ body: DNS, contentType: 'application/json', headers: { 'access-control-allow-origin': '*' } });
  await page.route('https://raw.githubusercontent.com/**/dns_failures.json*', fulfill);
  await page.route('**/data/vllm/ci/dns_failures.json*', fulfill);
}

async function routeAgent(page) {
  await routeSection(page, 'amd_agent_health', AGENT);
}

const clean = (s) => String(s ?? '').replace(/\s+/g, ' ').trim();
const escapeRegExp = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
// Under partial coverage the golden page printed "-" for a count with no observation; the page shows a muted 0.
const dash = (s) => s.replace(/^0(?= |$)/, '-');

async function v2Dns(page, query) {
  await page.goto(`/#/analytics?view=dns&${query}`);
  const host = page.getByTestId('analytics-dns');
  await expect(host.getByTestId('dns-summary').or(host.getByTestId('dns-empty'))).toBeVisible();
  return host.evaluate((root) => {
    // A KPI without an established value shows an in-card empty state where the golden page printed "-".
    const kpi = (id) => {
      const card = root.querySelector(`[data-testid="dns-kpi-${id}"]`);
      const value = card.querySelector('[data-slot="kpi-empty"]') ? '-' : card.querySelector('[data-slot="kpi-value"]').textContent.trim();
      return [value, card.querySelector('[data-slot="card-footer"] p')?.textContent.trim() ?? ''];
    };
    const notice = root.querySelector('[data-testid="dns-notice"]');
    const title = notice?.querySelector('p.font-medium')?.textContent.trim() ?? '';
    return {
      summary: root.querySelector('[data-testid="dns-summary"]')
        ? [...['jobs', 'queues', 'nodes', 'hf'].map(kpi), [`${kpi('passed')[0]} / ${kpi('nonpassing')[0]}`, kpi('passed')[1]]]
        : [],
      cards: [...root.querySelectorAll('[data-testid="dns-queue-card"]')].map((card) => ({
        queue: card.querySelector('[data-slot="card-title"]').textContent.trim(),
        stats: [...card.querySelectorAll('[data-testid="dns-queue-stats"] span')].map((s) => s.textContent.trim()),
        bars: [...card.querySelectorAll('[data-testid="dns-node-bar"]')].map((bar) => [
          bar.getAttribute('aria-label'),
          bar.querySelector('[data-testid="dns-node-count"]').textContent.trim(),
          [...bar.querySelectorAll('[data-testid="dns-node-meta"] [data-part]')].map((part) => part.textContent.trim()),
        ]),
      })),
      // One notice carries staleness and partial coverage; its title names both.
      stale: root.querySelector('[data-testid="dns-stale"]') && /stale/i.test(title) ? `DNS observations are stale. ${root.querySelector('[data-testid="dns-stale"]').textContent}` : null,
      coverage: (() => {
        const missing = root.querySelector('[data-testid="dns-coverage"]');
        if (!missing || !/lower bounds/.test(title)) return null;
        const facts = [...notice.querySelectorAll('[data-fact]')].map((fact) => fact.textContent.trim());
        return `Partial coverage - counts are lower bounds. ${facts.length ? facts.join(' - ') + '. ' : ''}${missing.textContent.trim()}`;
      })(),
      empty: root.querySelector('[data-testid="dns-empty"] p')?.textContent ?? null,
    };
  });
}

function normalizeDns(facts) {
  const partial = facts.coverage !== null;
  const count = (s) => (partial ? dash(s) : s);
  return {
    ...facts,
    summary: facts.summary.map(([value, meta]) => [value.split(' / ').map(count).join(' / '), meta]),
    cards: facts.cards.map((card) => ({
      ...card,
      stats: card.stats.map((stat) => count(stat).replace(/ Hugging Face$/, ' HF')),
      // The meta reads "a passed · b nonpassing · c episodes"; the golden page joined it as "a passed, b nonpassing - c episodes".
      bars: card.bars.map(([label, value, parts]) => {
        const shown = parts.map(count);
        return [label, value, shown.length === 3 ? `${shown[0]}, ${shown[1]} - ${shown[2]}` : shown.join(' - ')];
      }),
    })),
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
    const counts = ['Affected jobs', 'DNS episodes', 'Hugging Face affected jobs', 'Passed after observation', 'Soft-failed after observation', 'Hard-failed after observation'];
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
    v2.fields = v2.fields.map((field) => {
      const [label, value] = field.split('=');
      return counts.includes(label) ? `${label}=${dash(value)}` : field;
    });
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
    await expect(page.getByTestId('dns-empty')).toHaveText(new RegExp(escapeRegExp(golden['dns 3h/amd'].empty)));
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

/** The text a card's About popover shows, read by opening it. */
async function popoverText(page, label, testId) {
  await page.getByRole('button', { name: label }).click();
  const text = (await page.getByTestId(testId).textContent()).trim();
  await page.keyboard.press('Escape');
  await expect(page.getByTestId(testId)).toHaveCount(0);
  return text;
}

async function v2Agent(page, query) {
  await page.goto(`/#/analytics?view=agent-health${query ? '&' + query : ''}`);
  const host = page.getByTestId('analytics-agent-health');
  await expect(host.getByTestId('kpi-agent-nodes')).toBeVisible();
  const { rows } = await v2AllRows(host);
  const more = host.getByTestId('agent-events-more');
  if (await more.count()) await more.click();
  const facts = await host.evaluate((root) => ({
    // A KPI without an established value shows an in-card empty state where the golden page printed "Unavailable".
    kpis: ['agent-nodes', 'agent-unreliable', 'agent-coverage', 'agent-cofail'].map((id) => {
      const card = root.querySelector(`[data-testid="kpi-${id}"]`);
      const value = card.querySelector('[data-slot="kpi-empty"]') ? 'Unavailable' : card.querySelector('[data-slot="kpi-value"]').textContent.trim();
      return [value, card.querySelector('[data-slot="card-footer"] p').textContent.trim()];
    }),
    failRate: [...root.querySelectorAll('thead th')].some((th) => th.textContent.trim() === 'Fail %'),
    cards: [...root.querySelectorAll('[data-testid="cofailure-node"]')].map((b) => b.textContent.trim()),
  }));
  const description = await popoverText(page, 'About the node table', 'agent-table-description');
  const events = await popoverText(page, 'About co-failure events', 'agent-events-description');
  const { failRate, ...rest } = facts;
  return { failRate, facts: { kpis: rest.kpis, description, rows: rows.map(nodeLabel), events, cards: rest.cards } };
}

// The node column shows the physical node beside its GPU column where the golden page printed "node (GPU)".
function nodeLabel(row) {
  const [node, gpu] = row;
  return [node !== '(unidentified)' && gpu !== '—' ? `${node} (${gpu})` : node, ...row.slice(1)];
}

// Without node-specific accounting every golden Fail % cell read "Unavailable"; the page leaves that column out.
// The golden page's Evidence column is the row itself (a row opens the node), and its node select moved into the node sheet.
function expectedAgent(expected, failRate) {
  const { selectedNode, ...rest } = expected;
  expect(typeof selectedNode).toBe('string');
  const rows = rest.rows.map((row) => row.slice(0, -1));
  if (failRate) return { ...rest, rows };
  expect(rows.every((row) => row[3] === 'Unavailable')).toBe(true);
  return { ...rest, rows: rows.map((row) => row.filter((_, i) => i !== 3)) };
}

const agentHost = (page) => page.getByTestId('analytics-agent-health');

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
      const { failRate, facts } = await v2Agent(page, query);
      expect(facts).toEqual(expectedAgent(renamed(golden[`agent ${name}`]), failRate));
    });
  }

  test('node evidence and sorting match the golden page', async ({ page }) => {
    await routeAgent(page);
    const { evidenceTitle, evidenceLinks, sortedNodes } = renamed(golden['agent drill-downs']);
    // More rows than one v2 page, so the comparison walks the DataTable pagination.
    expect(sortedNodes.length).toBeGreaterThan(15);
    await v2Agent(page, 'agent_window=14d');
    const v2host = agentHost(page);
    await v2host.locator('table tbody tr').first().click();
    const sheet = page.getByRole('dialog');
    // The sheet is titled by the node; the golden drawer title opens its subtitle.
    const [, node] = evidenceTitle.match(/ failures on (.+)$/);
    await expect(sheet.locator('h2')).toHaveText(node);
    await expect(sheet.getByText(evidenceTitle.charAt(0).toUpperCase() + evidenceTitle.slice(1))).toBeVisible();
    const runs = sheet.locator('[data-slot="evidence-table"]').filter({ hasText: 'Retained node-suspect failing runs' });
    expect(await runs.locator('tbody tr td:first-child a').evaluateAll((as) => as.map((a) => a.getAttribute('href')))).toEqual(evidenceLinks);
    await page.keyboard.press('Escape');
    await expect(sheet).toHaveCount(0);
    await v2host.locator('thead').getByRole('button', { name: 'Physical node' }).click();
    expect((await v2AllRows(v2host)).rows.map(nodeLabel).map((r) => r[0])).toEqual(sortedNodes);
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
    await expect(page.getByTestId('agent-signal-caption')).toHaveText('Showing every hard failure on the node — not just the node-suspect subset.');
  });

  test('dragging across a node run timeline zooms to the selected range', async ({ page }) => {
    await routeAgent(page);
    // The golden page's default timeline node, whose runs span several days.
    const node = golden['agent live section, default week'].selectedNode.split(' ')[0];
    await page.goto(`/#/analytics?view=agent-health&agent_window=14d&agent_node=${node}`);
    const sheet = page.getByRole('dialog');
    await expect(sheet.locator('h2')).toHaveText(new RegExp(`^${escapeRegExp(node)}`));
    await expect(sheet.getByText(golden['agent drill-downs'].zoomHint)).toBeVisible();
    const chart = sheet.getByTestId('timeline');
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
    await sheet.getByRole('button', { name: 'Reset the timeline to the full window' }).click();
    await expect.poll(ticks).toEqual(before);
  });
});

test.describe('CI agent health tells the fleet story', () => {
  test.beforeEach(async ({ page }) => {
    await routeAgent(page);
  });

  test('the verdict names the hurting nodes, and each opens its sheet', async ({ page }) => {
    await page.goto('/#/analytics?view=agent-health&agent_window=14d');
    const verdict = page.getByTestId('agent-verdict');
    await expect(verdict.getByTestId('agent-verdict-headline')).toHaveText(/^(\d+ nodes? (is|are) failing jobs themselves|Node failures (confined to \S+ #\d+|each confined to one build)|No node is failing jobs repeatedly|No node is hurting CI right now)$/);
    const tone = await verdict.getAttribute('data-tone');
    await expect(verdict.getByTestId('agent-verdict-badge')).toHaveText({ hard: 'Hurting', soft: 'Watch', pass: 'Healthy' }[tone]);
    const hot = verdict.getByTestId('agent-hot-nodes');
    await hot.getByRole('button', { name: /^Inspect / }).click();
    const first = page.getByRole('menuitem').first();
    const node = (await first.locator('span').first().textContent()).trim();
    await first.click();
    const sheet = page.getByRole('dialog');
    await expect(sheet.locator('h2')).toHaveText(new RegExp(`^${escapeRegExp(node)}`));
    await expect(page).toHaveURL(new RegExp(`agent_node=${escapeRegExp(encodeURIComponent(node))}`));
    await page.keyboard.press('Escape');
    await expect(sheet).toHaveCount(0);
    await expect(page).not.toHaveURL(/agent_node=/);
  });

  test('the attribution card says which failures were not node faults and opens each cause', async ({ page }) => {
    await page.goto('/#/analytics?view=agent-health&agent_window=14d');
    const card = page.getByTestId('agent-attribution');
    await expect(card.getByTestId('agent-attribution-summary')).toHaveText(
      /^≥?[\d,]+ of ≥?[\d,]+ failures in 14d were build-wide, test or shared-infrastructure failures, not node faults; ≥?[\d,]+ (is|are) attributed to a node\.$/,
    );
    await card.getByRole('button', { name: /Test or code/ }).first().click();
    const sheet = page.getByRole('dialog');
    await expect(sheet.locator('h2')).toHaveText('Test or code failures');
    await expect(sheet.getByText('The test group also failed on other nodes within a day.')).toBeVisible();
  });

  test('agent_node follows a node sheet opened from inside another sheet', async ({ page }) => {
    await page.goto('/#/analytics?view=agent-health&agent_window=14d');
    await page.getByTestId('agent-load').getByRole('button', { name: /^Inspect / }).click();
    await page.getByRole('menuitem').first().click();
    const sheet = page.getByRole('dialog');
    const nodeRow = sheet.locator('[data-slot="evidence-table"]').first().locator('tbody tr').first();
    const node = (await nodeRow.locator('td').first().textContent()).trim();
    await expect(page).not.toHaveURL(/agent_node=/);
    await nodeRow.click();
    await expect(page).toHaveURL(new RegExp(`agent_node=${escapeRegExp(encodeURIComponent(node))}`));
    await page.getByRole('button', { name: 'Back' }).click();
    await expect(page).not.toHaveURL(/agent_node=/);
  });

  test('the node and day grid ranks the worst nodes and drills into node, day, and node-day', async ({ page }) => {
    await page.goto('/#/analytics?view=agent-health&agent_window=14d');
    const grid = page.getByTestId('agent-heatmap');
    const rows = grid.locator('[data-slot="heatmap-row"]');
    await expect(rows.first()).toBeVisible();
    // Rows are ranked worst first by their window total.
    const totals = (await grid.locator('[data-slot="heatmap-total"]').allTextContents()).slice(1).map((t) => Number(t.replace(/\D/g, '')));
    expect(totals.length).toBe(await rows.count());
    expect(totals).toEqual([...totals].sort((a, b) => b - a));
    // Every cell with failures is a button that names its node, day, and count, with a hover tooltip.
    const cell = grid.locator('button[data-slot="heatmap-cell"]').nth(1);
    await expect(cell).toHaveAttribute('aria-label', /^.+ · [A-Z][a-z]{2} \d+: \d+ .*failures?.*, open the runs$/);
    await cell.hover();
    await expect(grid.locator('[data-slot="heatmap-tooltip"]')).toBeVisible();
    expect(await cell.evaluate((el) => getComputedStyle(el).cursor)).toBe('pointer');
    await cell.click();
    const sheet = page.getByRole('dialog');
    await expect(sheet.locator('h2')).toHaveText(/ on [A-Z][a-z]{2} \d+$/);
    await page.keyboard.press('Escape');
    // A day header opens the day with the nodes that failed on it.
    await grid.locator('[data-slot="heatmap-column"]').last().click();
    await expect(sheet.getByText('Nodes failing on this day')).toBeVisible();
    await page.keyboard.press('Escape');
    // A row label opens the node.
    const name = (await rows.first().getAttribute('aria-label')).split(':')[0];
    await rows.first().click();
    await expect(sheet.locator('h2')).toHaveText(new RegExp(`^${escapeRegExp(name)}`));
    await expect(sheet.getByText('Test groups failing on this node')).toBeVisible();
    await page.keyboard.press('Escape');
    // The grid shows the top nodes and expands to all of them.
    const more = page.getByTestId('agent-heatmap-more');
    if (await more.count()) {
      const shown = await rows.count();
      await more.click();
      await expect.poll(() => rows.count()).toBeGreaterThan(shown);
      await expect(more).toHaveAttribute('aria-expanded', 'true');
    }
  });

  test('on a phone a long window opens the grid on its newest days', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/#/analytics?view=agent-health&agent_window=60d');
    const scroller = page.getByTestId('agent-heatmap').locator('[data-slot="heatmap-scroll"]');
    await expect(scroller).toBeVisible();
    const position = () => scroller.evaluate((el) => ({ left: el.scrollLeft, max: el.scrollWidth - el.clientWidth }));
    const { left, max } = await position();
    expect(max).toBeGreaterThan(0);
    expect(left).toBeGreaterThanOrEqual(max - 1);
    await expect(page.getByTestId('agent-heatmap').locator('[data-slot="heatmap-older"]')).toBeVisible();
    await scroller.evaluate((el) => el.scrollTo({ left: 0 }));
    await expect(page.getByTestId('agent-heatmap').locator('[data-slot="heatmap-older"]')).toHaveCount(0);
  });

  test('a link with agent_node reopens that node, and the signal filter reshapes the grid', async ({ page }) => {
    await page.goto('/#/analytics?view=agent-health&agent_window=14d');
    const first = page.getByTestId('agent-heatmap').locator('[data-slot="heatmap-row"]').first();
    const node = (await first.getAttribute('aria-label')).split(':')[0];
    await page.goto(`/#/analytics?view=agent-health&agent_window=14d&agent_node=${encodeURIComponent(node)}`);
    await expect(page.getByRole('dialog').locator('h2')).toHaveText(new RegExp(`^${escapeRegExp(node)}`));
    await page.keyboard.press('Escape');
    const total = async () => Number((await page.getByTestId('agent-heatmap').locator('[data-slot="heatmap-total"]').first().textContent()).replace(/\D/g, ''));
    const infra = await total();
    const signals = page.getByRole('group', { name: 'Failure signal' });
    await signals.getByRole('radio', { name: 'All' }).or(signals.getByRole('button', { name: 'All' })).click();
    await expect(page).toHaveURL(/agent_signal=all/);
    await expect.poll(total).toBeGreaterThanOrEqual(infra);
    await expect(page.getByTestId('agent-heatmap-card')).toContainText('Failures by node and day');
  });

  test('queues and test groups rank their failures and open their sheets', async ({ page }) => {
    await page.goto('/#/analytics?view=agent-health&agent_window=14d');
    const card = page.getByTestId('agent-load');
    for (const [kind, id, table] of [['Queue', 'agent-queues', 'Test groups'], ['Test group', 'agent-groups', 'Nodes']]) {
      await card.getByRole('radio', { name: kind, exact: true }).or(card.getByRole('button', { name: kind, exact: true })).click();
      await expect(card.getByTestId(id)).toBeVisible();
      await card.getByRole('button', { name: /^Inspect / }).click();
      const items = page.getByRole('menuitem');
      expect(await items.count()).toBeGreaterThan(0);
      // Each entry reads "hard · soft"; the bars rank by their sum.
      const totals = (await items.locator('span:last-child').allTextContents()).map((t) => t.split(' · ').reduce((sum, v) => sum + Number(v), 0));
      expect(totals).toEqual([...totals].sort((a, b) => b - a));
      await items.first().click();
      const sheet = page.getByRole('dialog');
      await expect(sheet.locator('[data-slot="evidence-table"]').filter({ hasText: table }).first()).toBeVisible();
      // A node row inside opens that node above the list; Back returns to it.
      const title = await sheet.locator('h2').textContent();
      const nodeRow = sheet.locator('[data-slot="evidence-table"]').first().locator('tbody tr').first();
      const node = await nodeRow.locator('td').first().textContent();
      await nodeRow.click();
      await expect(page.getByRole('dialog').locator('h2')).toHaveText(new RegExp(`^${escapeRegExp(node.trim())}`));
      await page.getByRole('button', { name: 'Back' }).click();
      await expect(page.getByRole('dialog').locator('h2')).toHaveText(title);
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog')).toHaveCount(0);
    }
  });

  test('co-failure events show a few and expand to all', async ({ page }) => {
    await page.goto('/#/analytics?view=agent-health&agent_window=14d&agent_signal=all');
    const cards = page.getByTestId('cofailure-card');
    await expect(cards.first()).toBeVisible();
    const more = page.getByTestId('agent-events-more');
    await expect(cards).toHaveCount(6);
    await more.click();
    expect(await cards.count()).toBeGreaterThan(6);
    await page.getByTestId('cofailure-node').first().click();
    // The event sheet carries its node's timeline, zoomed to the event.
    const sheet = page.getByRole('dialog');
    await expect(sheet).toContainText('co-failure');
    await expect(sheet.getByText('Node timeline around the event')).toBeVisible();
    await expect(sheet.getByTestId('agent-node-timeline')).toBeVisible();
  });
});

test.describe('chart marks look clickable', () => {
  for (const [name, url, chart] of [
    ['DNS queue bars', '/#/analytics?view=dns&dns_window=720h', 'DNS observations by queue and final job outcome'],
    ['Agent queue bars', '/#/analytics?view=agent-health&agent_window=14d', 'Retained node-suspect failures by queue'],
  ]) {
    test(`${name} show a pointer, a tooltip and a hover state, and open a sheet`, async ({ page }) => {
      const errors = collectErrors(page);
      await routeDns(page);
      await routeAgent(page);
      await page.goto(url);
      const plot = page.getByRole('img', { name: chart, exact: true });
      const groups = plot.locator('g[class*="-barGroup"]');
      expect(await groups.count()).toBeGreaterThan(1);
      const bar = groups.first().locator('path[class*="-bar"]').first();
      await bar.scrollIntoViewIfNeeded();
      await bar.hover({ force: true });
      expect(await bar.evaluate((el) => getComputedStyle(el).cursor)).toBe('pointer');
      await expect(plot.locator('[class*="-tooltip"]')).toBeVisible();
      await expect.poll(() => groups.nth(1).evaluate((el) => Number(getComputedStyle(el).opacity))).toBeLessThan(1);
      expect(Number(await groups.first().evaluate((el) => getComputedStyle(el).opacity))).toBe(1);
      await bar.click({ force: true });
      await expect(page.getByRole('dialog')).toBeVisible();
      expect(errors).toEqual([]);
    });
  }
});

/** Opens a chart's keyboard Inspect list and picks one entry. */
const inspect = (label, index) => async (page) => {
  await page.getByRole('button', { name: `Inspect ${label}`, exact: true }).click();
  await page.getByRole('menuitem').nth(index).click();
};

// The drill-down sheets meet the same gates as the pages.
const SHEETS = [
  ['DNS node evidence', '/#/analytics?view=dns&dns_window=720h', (page) => page.getByTestId('dns-node-bar').first().click()],
  ['DNS queue nodes', '/#/analytics?view=dns&dns_window=720h', (page) => page.getByRole('button', { name: /open the queue's nodes$/ }).first().click()],
  ['DNS retained logs', '/#/analytics?view=dns&dns_window=720h', (page) => page.getByTestId('dns-kpi-jobs').locator('[data-kpi-action]').click()],
  ['DNS all nodes', '/#/analytics?view=dns&dns_window=720h', (page) => page.getByTestId('dns-kpi-nodes').locator('[data-kpi-action]').click()],
  ['DNS interval logs', '/#/analytics?view=dns&dns_window=720h', inspect('DNS observations over time', 4)],
  ['agent node', '/#/analytics?view=agent-health&agent_window=14d', (page) => page.locator('table tbody tr').first().click()],
  ['agent failing nodes', '/#/analytics?view=agent-health&agent_window=3d&agent_gpu=MI355&agent_nightly=1&agent_excl_cancel=0', (page) => page.getByTestId('kpi-agent-unreliable').locator('[data-kpi-action]').click()],
  ['agent day', '/#/analytics?view=agent-health&agent_window=14d', (page) => page.locator('[data-slot="heatmap-column"]').nth(8).click()],
  ['agent node day', '/#/analytics?view=agent-health&agent_window=14d', (page) => page.locator('[data-slot="heatmap-row"] ~ button[data-slot="heatmap-cell"]').first().click()],
  ['agent queue', '/#/analytics?view=agent-health&agent_window=14d', inspect('Retained node-suspect failures by queue', 0)],
  ['agent attribution', '/#/analytics?view=agent-health&agent_window=14d', (page) => page.getByTestId('agent-attribution').getByRole('button', { name: /Unexplained/ }).first().click()],
  [
    'agent test group',
    '/#/analytics?view=agent-health&agent_window=14d',
    async (page) => {
      const card = page.getByTestId('agent-load');
      if (page.viewportSize().width < 640) {
        await card.getByRole('combobox').click();
        await page.getByRole('option', { name: 'Test group' }).click();
      } else await card.getByRole('radio', { name: 'Test group' }).or(card.getByRole('button', { name: 'Test group', exact: true })).click();
      await inspect('Retained node-suspect failures by test group', 0)(page);
    },
  ],
  [
    'agent run',
    '/#/analytics?view=agent-health&agent_window=14d',
    async (page) => {
      await page.locator('table tbody tr').first().click();
      const sheet = page.getByRole('dialog');
      // Below sm the node's timeline is a list of runs.
      if (page.viewportSize().width < 640) await sheet.getByTestId('agent-run-list').getByRole('button').first().click();
      else {
        await sheet.getByRole('button', { name: /^Inspect Failing runs on / }).click();
        await page.getByRole('menuitem').first().click();
      }
      await expect(page.getByRole('button', { name: 'Back' })).toBeVisible();
    },
  ],
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
          await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
          await open(page);
          await expect(page.getByRole('dialog')).toHaveAttribute('data-state', 'open');
          await expectQuality(page, errors);
        });
      }
    }
  }
});
