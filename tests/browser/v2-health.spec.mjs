import { expect, test } from '@playwright/test';

// CI Health "AMD hardware", "AMD mirrors", and the Overview mirror summary:
// read the same facts from classic.html and from v2 on the fixture snapshot.

const squash = (text) => String(text ?? '').replace(/\s+/g, ' ').trim();
const texts = async (locator) => (await locator.allInnerTexts()).map(squash);

async function classicHealth(page, query) {
  await page.goto(`/classic.html${query}#ci-health`);
  const host = page.locator('#ci-health-view[data-render-state="ready"]');
  await host.waitFor();
  return host;
}

async function classicOverlay(page) {
  const overlay = page.locator('.ops-overlay:not([hidden])').last();
  await overlay.waitFor();
  return overlay;
}

async function closeClassicOverlays(page) {
  while (await page.locator('.ops-overlay').count()) {
    await page.locator('.ops-overlay .ops-overlay-close').last().click();
  }
}

// Nested sheets hide the ones below them from the accessibility tree, so close
// them one Escape at a time until none is left.
async function closeSheets(page) {
  const dialogs = page.locator('[data-slot=sheet-content]');
  for (let i = 0; i < 5 && (await dialogs.count()); i += 1) {
    const before = await dialogs.count();
    await page.keyboard.press('Escape');
    await expect(dialogs).toHaveCount(before - 1);
  }
  await expect(dialogs).toHaveCount(0);
}

async function classicTableRows(scope) {
  return scope.locator('tbody tr').evaluateAll((rows) =>
    rows.map((row) => [...row.querySelectorAll('td')].map((cell) => cell.innerText.replace(/\s+/g, ' ').trim())),
  );
}

async function v2TableRows(scope) {
  return scope.locator('tbody tr').evaluateAll((rows) =>
    rows
      .filter((row) => row.querySelectorAll('td').length > 1)
      .map((row) => [...row.querySelectorAll('td')].map((cell) => cell.innerText.replace(/\s+/g, ' ').trim())),
  );
}

async function classicCoverage(page, sort = '') {
  const host = await classicHealth(page, `?ops_health_view=coverage${sort ? `&ops_health_sort=${sort}` : ''}`);
  const card = host.locator('.ops-unique-health');
  const stats = await card.locator('.ops-unique-health-stat').evaluateAll((nodes) =>
    nodes.map((n) => [n.querySelector('span').textContent.trim(), n.querySelector('strong').textContent.trim()]),
  );
  const architectures = await host.locator('.ops-architecture-row').evaluateAll((nodes) =>
    nodes.map((n) => ({
      label: n.querySelector('.ops-architecture-identity strong').textContent.trim(),
      configured: n.querySelector('.ops-architecture-identity span').textContent.trim(),
      metrics: [...n.querySelectorAll('.ops-architecture-metrics span')].map((s) => s.textContent.trim()),
      rate: n.querySelector('.ops-architecture-rate strong').textContent.trim(),
    })),
  );
  const matrix = host.locator('.ops-panel', { has: page.locator('.ops-panel-title', { hasText: 'Test-group routes by architecture' }) });
  return {
    host,
    meta: squash(await card.locator('.ops-panel-meta').innerText()),
    rate: squash(await card.locator('.ops-unique-health-rate strong').innerText()),
    stats,
    facts: squash(await card.locator('.ops-unique-health-footer span').first().innerText()),
    architectures,
    description: squash(await matrix.locator('.ops-panel-meta').innerText()),
    rows: await classicTableRows(matrix),
  };
}

test.describe('v2 CI Health AMD hardware matches the classic page', () => {
  test('policy card, architecture routes, and the sorted route matrix', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const classic = await classicCoverage(page);
    expect(classic.stats.length).toBe(4);
    expect(classic.architectures.length).toBeGreaterThan(0);
    expect(classic.rows.length).toBeGreaterThan(0);

    await page.goto('/#/health?view=coverage');
    await expect(page.getByTestId('hardware-policy-meta')).toHaveText(classic.meta);
    await expect(page.getByTestId('hardware-rate').locator('[data-slot=card-title]')).toHaveText(classic.rate);
    for (const [label, count] of classic.stats) {
      const mode = { 'Test groups': 'all', Passing: 'passing', Failing: 'failing', 'No signal': 'no-signal' }[label];
      const card = page.getByTestId(`hardware-stat-${mode}`);
      await expect(card.locator('[data-slot=card-description]')).toHaveText(label);
      await expect(card.locator('[data-slot=card-title]')).toHaveText(count);
    }
    await expect(page.getByTestId('hardware-distribution')).toContainText(classic.facts);

    const rows = page.getByTestId('architecture-row');
    await expect(rows).toHaveCount(classic.architectures.length);
    expect(await texts(page.getByTestId('architecture-label'))).toEqual(classic.architectures.map((a) => a.label));
    expect(await texts(page.getByTestId('architecture-configured'))).toEqual(classic.architectures.map((a) => a.configured));
    expect(await texts(page.getByTestId('architecture-rate'))).toEqual(classic.architectures.map((a) => a.rate));
    const metrics = await page.getByTestId('architecture-metrics').evaluateAll((nodes) =>
      nodes.map((n) => [...n.querySelectorAll('span')].map((s) => s.textContent.trim())),
    );
    expect(metrics).toEqual(classic.architectures.map((a) => a.metrics));

    const matrix = page.getByTestId('route-matrix');
    await expect(matrix.getByTestId('matrix-sort-description')).toHaveText(classic.description);
    expect(await v2TableRows(matrix)).toEqual(classic.rows);
    expect(errors).toEqual([]);
  });

  for (const sort of ['name', 'area']) {
    test(`route matrix sorted by ${sort} matches classic ops_health_sort=${sort}`, async ({ page }) => {
      const classic = await classicCoverage(page, sort);
      expect(classic.rows.length).toBeGreaterThan(0);
      await page.goto('/#/health?view=coverage');
      const label = { name: 'Test group', area: 'Test area' }[sort];
      await page.getByTestId('route-matrix').getByRole('button', { name: label, exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`sort=${sort}`));
      await expect(page.getByTestId('matrix-sort-description')).toHaveText(classic.description);
      expect(await v2TableRows(page.getByTestId('route-matrix'))).toEqual(classic.rows);
      await page.goto(`/#/health?view=coverage&sort=${sort}`);
      await expect(page.getByTestId('matrix-sort-description')).toHaveText(classic.description);
    });
  }

  test('drill-downs: group browser filters, group evidence, architecture routes, and test-group detail', async ({ page }) => {
    const classic = await classicCoverage(page);
    const facts = {};
    for (const mode of ['is-neutral', 'is-passing', 'is-failing', 'is-unknown']) {
      await classic.host.locator(`.ops-unique-health-stat.${mode}`).click();
      const overlay = await classicOverlay(page);
      facts[mode] = {
        title: squash(await overlay.locator('.ops-overlay-title').innerText()),
        count: squash(await overlay.locator('.ops-browser-count').innerText()),
        rows: (await classicTableRows(overlay)).map((cells) => cells.slice(0, 7)),
      };
      await closeClassicOverlays(page);
    }
    expect(facts['is-neutral'].rows.length).toBeGreaterThan(0);
    // Evidence drawer for the first failing group.
    await classic.host.locator('.ops-unique-health-stat.is-failing').click();
    let overlay = await classicOverlay(page);
    await overlay.locator('tbody tr').first().locator('button').first().click();
    overlay = await classicOverlay(page);
    const evidence = {
      title: squash(await overlay.locator('.ops-overlay-title').innerText()),
      fields: await overlay.locator('.ops-detail-field').evaluateAll((nodes) => nodes.map((n) => [n.querySelector('dt').textContent.trim(), n.querySelector('dd').textContent.trim()])),
      definitions: (await classicTableRows(overlay)).map((cells) => [cells[0], cells[2], cells[3]]),
    };
    expect(evidence.fields.length).toBeGreaterThan(3);
    await closeClassicOverlays(page);
    // Architecture route browser.
    await classic.host.locator('.ops-architecture-row').first().click();
    overlay = await classicOverlay(page);
    const archTitle = squash(await overlay.locator('.ops-overlay-title').innerText());
    const archSubtitle = squash(await overlay.locator('.ops-overlay-subtitle').innerText());
    const archRows = await classicTableRows(overlay);
    expect(archRows.length).toBeGreaterThan(0);
    // Test-group detail from a route.
    await overlay.locator('tbody tr').first().locator('button').first().click();
    overlay = await classicOverlay(page);
    const groupTitle = squash(await overlay.locator('.ops-overlay-title').innerText());
    const groupFields = await overlay.locator('.ops-detail-field').evaluateAll((nodes) => nodes.map((n) => [n.querySelector('dt').textContent.trim(), n.querySelector('dd').textContent.trim()]));
    await expect(overlay.getByRole('button', { name: 'Load 30-day run history' })).toBeVisible();
    await closeClassicOverlays(page);

    await page.goto('/#/health?view=coverage');
    const modes = { 'is-neutral': 'all', 'is-passing': 'passing', 'is-failing': 'failing', 'is-unknown': 'no-signal' };
    for (const [cls, mode] of Object.entries(modes)) {
      await page.getByTestId(`hardware-stat-${mode}`).locator('[data-slot=card-footer] button').click();
      const sheet = page.getByRole('dialog');
      await expect(sheet.getByRole('heading').first()).toHaveText(facts[cls].title);
      await expect(sheet.getByTestId('browser-count')).toHaveText(facts[cls].count);
      expect((await v2TableRows(sheet)).map((cells) => cells.slice(0, 7))).toEqual(facts[cls].rows);
      await closeSheets(page);
    }
    // Changing a filter in place matches the classic browser opened in that mode.
    await page.getByTestId('hardware-stat-failing').locator('[data-slot=card-footer] button').click();
    await page.getByRole('combobox', { name: 'Filter configured test-group status' }).click();
    await page.getByRole('option', { name: 'Passing', exact: true }).click();
    await expect(page.getByRole('dialog').getByTestId('browser-count')).toHaveText(facts['is-passing'].count);
    expect((await v2TableRows(page.getByRole('dialog'))).map((cells) => cells.slice(0, 7))).toEqual(facts['is-passing'].rows);
    await page.getByRole('combobox', { name: 'Filter configured test-group status' }).click();
    await page.getByRole('option', { name: 'Failing', exact: true }).click();
    await expect(page.getByRole('dialog').getByTestId('browser-count')).toHaveText(facts['is-failing'].count);
    await page.getByRole('dialog').locator('tbody tr').first().getByRole('button').first().click();
    const detail = page.getByTestId('matrix-group-detail');
    await expect(detail).toBeVisible();
    await expect(page.getByRole('dialog').last().getByRole('heading').first()).toHaveText(evidence.title);
    const v2Fields = await detail.getByTestId('detail-fields').locator('div').evaluateAll((nodes) =>
      nodes.map((n) => [n.querySelector('dt').textContent.trim(), n.querySelector('dd').textContent.trim()]),
    );
    expect(v2Fields.map(([label, value]) => [label.toLowerCase(), value])).toEqual(evidence.fields.map(([label, value]) => [label.toLowerCase(), value]));
    expect((await v2TableRows(detail)).map((cells) => [cells[0].replace(/ ↗$/, ''), cells[2], cells[3]])).toEqual(evidence.definitions);
    await closeSheets(page);

    await page.getByTestId('architecture-row').first().click();
    let sheet = page.getByRole('dialog');
    await expect(sheet.getByRole('heading').first()).toHaveText(archTitle);
    await expect(sheet).toContainText(archSubtitle);
    expect(await v2TableRows(sheet)).toEqual(archRows);
    await sheet.locator('tbody tr').first().getByRole('button').first().click();
    const group = page.getByTestId('group-detail');
    await expect(group).toBeVisible();
    await expect(page.getByRole('dialog').last().getByRole('heading').first()).toHaveText(groupTitle);
    const v2GroupFields = await group.getByTestId('detail-fields').locator('div').evaluateAll((nodes) =>
      nodes.map((n) => [n.querySelector('dt').textContent.trim(), n.querySelector('dd').textContent.trim()]),
    );
    expect(v2GroupFields.map(([l, v]) => [l.toLowerCase(), v])).toEqual(groupFields.map(([l, v]) => [l.toLowerCase(), v]));
    await group.getByRole('button', { name: 'Load 30-day run history' }).click();
    // The fixture ledger has no retained rows, so classic offers the load again.
    await expect(group.getByRole('button', { name: 'Load 30-day run history' })).toBeVisible();
  });
});

test.describe('v2 CI Health AMD mirrors matches the classic page', () => {
  test('count hero, configuration shape, areas, inventory, and drawers', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const host = await classicHealth(page, '?ops_health_view=mirrors');
    const classic = await host.evaluate((root) => {
      const t = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : null);
      return {
        total: t(root.querySelector('.ops-mirror-count-ring strong')),
        meta: t(root.querySelector('.ops-mirror-hero-meta')),
        legend: [...root.querySelectorAll('.ops-mirror-hardware-item')].map((n) => [...n.children].slice(1).map(t)),
        modes: [...root.querySelectorAll('.ops-mirror-mode-headline strong')].map(t),
        facts: [...root.querySelectorAll('.ops-mirror-fact strong')].map(t),
        areasSubtitle: t(root.querySelector('.ops-mirror-area-panel .ops-panel-meta')),
        areas: [...root.querySelectorAll('.ops-mirror-area-row')].map((n) => [
          t(n.querySelector('.ops-mirror-area-label strong')),
          t(n.querySelector('.ops-mirror-area-label small')),
          t(n.querySelector('.ops-mirror-area-breakdown')),
          t(n.querySelector('.ops-mirror-area-count')),
          n.getAttribute('href'),
        ]),
        inventorySubtitle: t(root.querySelector('.ops-mirror-inventory-panel .ops-panel-meta')),
        preview: [...root.querySelectorAll('.ops-mirror-preview-row')].map((n) => [
          t(n.querySelector('.ops-mirror-preview-copy strong')),
          t(n.querySelector('.ops-mirror-preview-copy small')),
          t(n.querySelectorAll('.ops-badge')[1]),
        ]),
        browse: [...root.querySelectorAll('.ops-mirror-actions button')].map(t),
        headerAction: [...root.querySelectorAll('.ops-page-header a')].map((a) => [t(a), a.getAttribute('href')]),
        observed: t(root.querySelector('.ops-page-header .ops-panel-meta')).replace(/ - .*$/, ''),
      };
    });
    expect(classic.headerAction).toHaveLength(1);
    expect(classic.observed).toMatch(/^Observed \S/);
    expect(classic.total).toMatch(/^\d+$/);
    expect(classic.preview.length).toBeGreaterThan(0);
    expect(classic.areas.length).toBeGreaterThan(0);
    // Classic drawers and browsers.
    await host.locator('.ops-mirror-preview-row').first().click();
    let overlay = await classicOverlay(page);
    const drawer = {
      title: squash(await overlay.locator('.ops-overlay-title').innerText()),
      fields: await overlay.locator('.ops-detail-field').evaluateAll((nodes) => nodes.map((n) => [n.querySelector('dt').textContent.trim(), n.querySelector('dd').textContent.trim()])),
      sources: await overlay.locator('.ops-source-actions a').evaluateAll((nodes) => nodes.map((n) => n.getAttribute('href'))),
    };
    expect(drawer.fields.length).toBeGreaterThan(3);
    await closeClassicOverlays(page);
    const browsers = [];
    for (const label of classic.browse) {
      await host.getByRole('button', { name: label }).click();
      overlay = await classicOverlay(page);
      browsers.push({
        label,
        title: squash(await overlay.locator('.ops-overlay-title').innerText()),
        count: squash(await overlay.locator('.ops-browser-count').innerText()),
        rows: await classicTableRows(overlay),
      });
      await closeClassicOverlays(page);
    }

    await page.goto('/#/health?view=mirrors');
    await expect(page.getByTestId('mirror-total')).toHaveText(classic.total);
    const [[actionLabel, actionHref]] = classic.headerAction;
    await expect(page.getByRole('link', { name: actionLabel })).toHaveAttribute('href', actionHref);
    await expect(page.getByRole('link', { name: actionLabel })).toHaveCount(1);
    await expect(page.getByText(`${classic.observed} ·`)).toBeVisible();
    await expect(page.getByTestId('mirror-hero-meta')).toHaveText(classic.meta);
    const legend = await page.getByTestId('mirror-hardware').evaluateAll((nodes) => nodes.map((n) => [...n.children].slice(1).map((c) => c.textContent.trim())));
    expect(legend).toEqual(classic.legend);
    expect(await page.getByTestId('mirror-mode').locator('strong').allInnerTexts()).toEqual(classic.modes);
    expect(await page.getByTestId('mirror-fact').locator('strong').allInnerTexts()).toEqual(classic.facts);
    await expect(page.getByTestId('mirror-areas-subtitle')).toHaveText(classic.areasSubtitle);
    const areas = await page.getByTestId('mirror-area').evaluateAll((nodes) =>
      nodes.map((n) => [
        n.querySelector('[data-testid=mirror-area-name]').textContent.trim(),
        n.querySelector('[data-testid=mirror-area-file]').textContent.trim(),
        n.querySelector('[data-testid=mirror-area-breakdown]').textContent.trim(),
        n.querySelector('[data-testid=mirror-area-count]').textContent.trim(),
        n.getAttribute('href'),
      ]),
    );
    expect(areas).toEqual(classic.areas);
    await expect(page.getByTestId('mirror-inventory-subtitle')).toHaveText(classic.inventorySubtitle);
    const preview = await page.getByTestId('mirror-preview').evaluateAll((nodes) =>
      nodes.map((n) => ['mirror-preview-label', 'mirror-preview-meta', 'mirror-preview-mode'].map((id) => n.querySelector(`[data-testid=${id}]`).textContent.replace(/\s+/g, ' ').trim())),
    );
    expect(preview).toEqual(classic.preview);

    await page.getByTestId('mirror-preview').first().click();
    const detail = page.getByTestId('mirror-detail');
    await expect(page.getByRole('dialog').getByRole('heading').first()).toHaveText(drawer.title);
    const fields = await detail.getByTestId('detail-fields').locator('div').evaluateAll((nodes) =>
      nodes.map((n) => [n.querySelector('dt').textContent.trim(), n.querySelector('dd').textContent.trim()]),
    );
    expect(fields.map(([l, v]) => [l.toLowerCase(), v])).toEqual(drawer.fields.map(([l, v]) => [l.toLowerCase(), v]));
    expect(await detail.locator('a').evaluateAll((nodes) => nodes.map((n) => n.getAttribute('href')))).toEqual(drawer.sources);
    await closeSheets(page);

    for (const browser of browsers) {
      await page.getByRole('button', { name: browser.label }).click();
      const sheet = page.getByRole('dialog');
      await expect(sheet.getByRole('heading').first()).toHaveText(browser.title);
      await expect(sheet.getByTestId('browser-count')).toHaveText(browser.count);
      expect(await v2TableRows(sheet)).toEqual(browser.rows);
      await closeSheets(page);
    }
    expect(errors).toEqual([]);
  });

  test('Overview mirror summary card matches classic and opens the mirrors view', async ({ page }) => {
    const host = await classicHealth(page, '');
    const card = host.locator('.ops-health-mirror-summary');
    const classic = {
      count: squash(await card.locator('.ops-health-mirror-title strong').innerText()),
      meta: squash(await card.locator('.ops-health-mirror-meta').innerText()),
      facts: squash(await card.locator('.ops-health-mirror-facts').innerText()),
      label: await card.getAttribute('aria-label'),
    };
    expect(classic.count).toMatch(/^\d+$/);
    await page.goto('/#/health');
    const summary = page.getByTestId('mirror-summary');
    await expect(summary.getByTestId('mirror-summary-count')).toHaveText(classic.count);
    await expect(summary.getByTestId('mirror-summary-meta')).toHaveText(classic.meta);
    await expect(summary.getByTestId('mirror-summary-facts')).toHaveText(classic.facts);
    await expect(summary).toHaveAttribute('aria-label', classic.label);
    await summary.click();
    await expect(page).toHaveURL(/#\/health\?view=mirrors$/);
    await expect(page.getByTestId('mirror-total')).toHaveText(classic.count);
  });

  test('legacy links to AMD hardware and AMD mirrors open v2', async ({ page }) => {
    await page.goto('/?ops_health_view=coverage&ops_health_sort=area#ci-health');
    await expect(page).toHaveURL(/#\/health\?view=coverage&sort=area$/);
    await expect(page.getByTestId('matrix-sort-description')).toHaveText('Test areas sorted alphabetically, then by test-group name');
    await page.goto('/?ops_health_view=mirrors#ci-health');
    await expect(page).toHaveURL(/#\/health\?view=mirrors$/);
    await expect(page.getByTestId('mirror-total')).toBeVisible();
  });
});
