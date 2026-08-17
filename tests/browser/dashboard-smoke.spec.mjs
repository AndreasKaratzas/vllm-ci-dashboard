import { expect, test } from '@playwright/test';
import { createHash } from 'node:crypto';

const DNS_JOB_ID = '01a00c92-9cab-4dd2-9a75-32210e739d02';
const DNS_LOG_URL = `https://buildkite.com/vllm/amd-ci/builds/12112/list?jid=${DNS_JOB_ID}&tab=output`;
const DNS_EVIDENCE_ID = createHash('sha256')
  .update(`dns-evidence-v1\0amd-ci\0${DNS_JOB_ID}`)
  .digest('hex');
const DNS_LONG_JOB_ID = '11a00c92-9cab-4dd2-9a75-32210e739d02';
const DNS_LONG_LOG_URL = `https://buildkite.com/vllm/amd-ci/builds/12113/list?jid=${DNS_LONG_JOB_ID}&tab=output`;
const DNS_LONG_EVIDENCE_ID = createHash('sha256')
  .update(`dns-evidence-v1\0amd-ci\0${DNS_LONG_JOB_ID}`)
  .digest('hex');
const DNS_GENERATED_AT = '2026-08-16T10:00:00Z';
const DNS_WINDOW_OPTIONS = [
  { id: '1h', label: 'Last hour', hours: 1 },
  { id: '3h', label: 'Last 3 hours', hours: 3 },
  { id: '12h', label: 'Last 12 hours', hours: 12 },
  { id: '24h', label: 'Last day', hours: 24 },
  { id: '72h', label: 'Last 3 days', hours: 72 },
  { id: '168h', label: 'Last 7 days', hours: 168 },
  { id: '720h', label: 'Last 30 days', hours: 720 },
];
const DNS_COVERAGE = {
  status: 'complete',
  complete: true,
  discovery_complete: true,
  eligible_jobs: 10,
  scanned_jobs: 10,
  positive_jobs: 4,
  negative_jobs: 6,
  pending_jobs: 0,
  unavailable_jobs: 0,
  oversize_jobs: 0,
};
const DNS_BASE_ROWS = [
  {
    queue: 'amd_mi300_1',
    node: 'node-a',
    hardware: 'MI300',
    affected_jobs: 2,
    episodes: 3,
    huggingface_affected_jobs: 1,
    evidence_total: 2,
    passed_jobs: 1,
    soft_failed_jobs: 0,
    hard_failed_jobs: 1,
  },
  {
    queue: 'amd_mi300_1',
    node: 'unidentified',
    hardware: 'MI300',
    affected_jobs: 1,
    episodes: 1,
    huggingface_affected_jobs: 0,
    evidence_total: 1,
    passed_jobs: 0,
    soft_failed_jobs: 1,
    hard_failed_jobs: 0,
  },
];
function dnsEvidenceMetric(firstAt, lastAt, episodes, matchCount, signatureIds, targetCategories) {
  return {
    first_at: firstAt,
    last_at: lastAt,
    episodes,
    match_count: matchCount,
    signature_ids: [...signatureIds],
    target_categories: [...targetCategories],
  };
}
function cloneDnsEvidenceMetric(metric) {
  return {
    ...metric,
    signature_ids: [...metric.signature_ids],
    target_categories: [...metric.target_categories],
  };
}
const DNS_SMOKE_METRIC = dnsEvidenceMetric(
  '2026-08-16T09:30:00Z',
  '2026-08-16T09:30:00Z',
  1,
  9,
  ['temporary_name_resolution'],
  ['huggingface_hub'],
);
const DNS_LONG_RECENT_METRIC = dnsEvidenceMetric(
  '2026-08-16T09:35:00Z',
  '2026-08-16T09:36:00Z',
  1,
  4,
  ['name_or_service_unknown'],
  ['github'],
);
const DNS_LONG_RETAINED_METRIC = dnsEvidenceMetric(
  '2026-08-15T08:30:00Z',
  '2026-08-16T09:36:00Z',
  2,
  9,
  ['name_or_service_unknown', 'temporary_name_resolution'],
  ['huggingface_hub', 'github'],
);
const DNS_WINDOWS = Object.fromEntries(DNS_WINDOW_OPTIONS.map(option => [
  option.id,
  (() => {
    const includesOldLongEpisode = option.hours >= 72;
    const rows = [
      ...DNS_BASE_ROWS.map(row => ({ ...row })),
      {
        queue: 'amd_mi300_1',
        node: 'node-long',
        hardware: 'MI300',
        affected_jobs: 1,
        episodes: includesOldLongEpisode ? 2 : 1,
        huggingface_affected_jobs: includesOldLongEpisode ? 1 : 0,
        evidence_total: 1,
        passed_jobs: 0,
        soft_failed_jobs: 0,
        hard_failed_jobs: 1,
      },
    ].sort((left, right) => (
      left.queue.localeCompare(right.queue) || left.node.localeCompare(right.node)
    ));
    return {
      start: new Date(
        Date.parse(DNS_GENERATED_AT) - option.hours * 60 * 60 * 1000,
      ).toISOString().replace('.000Z', 'Z'),
      end_exclusive: DNS_GENERATED_AT,
      coverage: { ...DNS_COVERAGE },
      totals: {
        affected_jobs: rows.reduce((sum, row) => sum + row.affected_jobs, 0),
        episodes: rows.reduce((sum, row) => sum + row.episodes, 0),
        huggingface_affected_jobs: rows.reduce(
          (sum, row) => sum + row.huggingface_affected_jobs,
          0,
        ),
        passed_jobs: rows.reduce((sum, row) => sum + row.passed_jobs, 0),
        soft_failed_jobs: rows.reduce((sum, row) => sum + row.soft_failed_jobs, 0),
        hard_failed_jobs: rows.reduce((sum, row) => sum + row.hard_failed_jobs, 0),
        queues: new Set(rows.map(row => row.queue)).size,
        nodes: new Set(rows.map(row => row.node)).size,
        evidence_total: rows.reduce((sum, row) => sum + row.evidence_total, 0),
      },
      rows,
    };
  })(),
]));
const DNS_FIXTURE = {
  schema_version: 1,
  outcome_contract: 'dns-job-outcomes-v1',
  generated_at: DNS_GENERATED_AT,
  retention: {
    start: '2026-07-17T10:00:00Z',
    end_exclusive: '2026-08-16T10:00:00Z',
    hours: 720,
  },
  default_window: '24h',
  window_options: DNS_WINDOW_OPTIONS,
  count_basis: 'distinct_buildkite_job_attempts_with_strong_dns_evidence',
  scope: {
    organization: 'vllm',
    pipelines: ['amd-ci', 'ci'],
    branches: 'all',
    job_types: ['script'],
    states: ['passed', 'soft', 'hard'],
    queue_scope: 'active_amd_gpu',
    retried_jobs: 'included',
  },
  classifier: {
    id: 'dns-v1',
    episode_gap_seconds: 5,
    max_log_bytes: 16 * 1024 * 1024,
    target_categories: [
      'huggingface_hub',
      'vllm_public_assets',
      'aws_s3',
      'github',
      'pypi',
      'other_public',
      'unknown',
    ],
  },
  coverage: {
    ...DNS_COVERAGE,
    discovery_start: '2026-07-17T10:00:00Z',
    discovery_end_exclusive: DNS_GENERATED_AT,
  },
  windows: DNS_WINDOWS,
  evidence: {
    evidence_total: 4,
    shown: 2,
    truncated: true,
    items: [{
      id: DNS_EVIDENCE_ID,
      first_at: '2026-08-16T09:30:00Z',
      last_at: '2026-08-16T09:30:00Z',
      time_basis: 'job_finished_at',
      pipeline: 'amd-ci',
      queue: 'amd_mi300_1',
      node: 'node-a',
      hardware: 'MI300',
      build_number: 12112,
      job_id: DNS_JOB_ID,
      state: 'passed',
      episodes: 1,
      match_count: 9,
      signature_ids: ['temporary_name_resolution'],
      target_categories: ['huggingface_hub'],
      window_ids: DNS_WINDOW_OPTIONS.map(option => option.id),
      window_metrics: Object.fromEntries(DNS_WINDOW_OPTIONS.map(option => [
        option.id,
        cloneDnsEvidenceMetric(DNS_SMOKE_METRIC),
      ])),
    }, {
      id: DNS_LONG_EVIDENCE_ID,
      first_at: DNS_LONG_RETAINED_METRIC.first_at,
      last_at: DNS_LONG_RETAINED_METRIC.last_at,
      time_basis: 'log_timestamp',
      pipeline: 'amd-ci',
      queue: 'amd_mi300_1',
      node: 'node-long',
      hardware: 'MI300',
      build_number: 12113,
      job_id: DNS_LONG_JOB_ID,
      state: 'hard',
      episodes: DNS_LONG_RETAINED_METRIC.episodes,
      match_count: DNS_LONG_RETAINED_METRIC.match_count,
      signature_ids: [...DNS_LONG_RETAINED_METRIC.signature_ids],
      target_categories: [...DNS_LONG_RETAINED_METRIC.target_categories],
      window_ids: DNS_WINDOW_OPTIONS.map(option => option.id),
      window_metrics: Object.fromEntries(DNS_WINDOW_OPTIONS.map(option => [
        option.id,
        cloneDnsEvidenceMetric(
          option.hours >= 72 ? DNS_LONG_RETAINED_METRIC : DNS_LONG_RECENT_METRIC,
        ),
      ])),
    }].sort((left, right) => Date.parse(right.last_at) - Date.parse(left.last_at)),
  },
};

async function routeDnsFixture(page, fixture = DNS_FIXTURE, delayMs = 0) {
  const fulfill = async route => {
    if (delayMs) await new Promise(resolve => setTimeout(resolve, delayMs));
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'access-control-allow-origin': '*' },
      body: JSON.stringify(fixture),
    });
  };
  await page.route('https://raw.githubusercontent.com/**/dns_failures.json*', fulfill);
  await page.route('http://127.0.0.1:4173/data/vllm/ci/dns_failures.json*', fulfill);
}

const PUBLIC_VIEWS = [
  { name: 'trajectory workload', url: '/#ci-hotness', tab: 'ci-hotness', heading: 'CI Workload Trajectory' },
  { name: 'home', url: '/#projects', tab: 'projects', heading: 'Command Center' },
  ...['overview', 'targets', 'gating', 'coverage', 'diagnostics'].map(view => ({
    name: `health ${view}`,
    url: `/?ops_health_view=${view}#ci-health`,
    tab: 'ci-health',
    heading: 'CI Health',
    watchdog: view === 'overview',
  })),
  ...['groups', 'flakes', 'retries', 'latency', 'nightlies', 'dns', 'agent-health'].map(view => ({
    name: `analytics ${view}`,
    url: `/?ops_analytics_view=${view}#ci-analytics`,
    tab: 'ci-analytics',
    heading: 'CI Analytics',
    dnsFixture: view === 'dns',
  })),
  ...['performance', 'accuracy'].map(view => ({
    name: `performance ${view}`,
    url: `/?ops_perf_view=${view}#ci-perf-eval`,
    tab: 'ci-perf-eval',
    heading: 'Performance & Evaluation',
  })),
  ...['current', 'lifecycle', 'history', 'jobs'].map(view => ({
    name: `queue ${view}`,
    url: `/?ops_queue_view=${view}#ci-queue`,
    tab: 'ci-queue',
    heading: 'Queue Monitor',
    lifecycleFallback: view === 'lifecycle',
  })),
  {
    name: 'trajectory capacity',
    url: '/?ops_trajectory_view=capacity#ci-hotness',
    tab: 'ci-hotness',
    heading: 'CI Workload Trajectory',
  },
  { name: 'omni', url: '/#ci-omni', tab: 'ci-omni', heading: 'Omni CI' },
];

test.describe('public dashboard routes', () => {
  for (const route of PUBLIC_VIEWS) {
    test(route.name, async ({ page }) => {
      const browserErrors = [];
      page.on('pageerror', error => browserErrors.push(`pageerror: ${error.stack || error.message}`));
      page.on('console', message => {
        if (message.type() === 'error') browserErrors.push(`console: ${message.text()}`);
      });

      if (route.lifecycleFallback) {
        // Make the live raw candidate intentionally unusable without creating
        // a browser network error. This locks the assembled Pages fallback path.
        await page.route('https://raw.githubusercontent.com/**/queue_lifecycle.json*', request => request.fulfill({
          status: 200,
          contentType: 'application/json',
          body: 'null',
        }));
      }
      if (route.dnsFixture) await routeDnsFixture(page);

      await page.goto(route.url, { waitUntil: 'domcontentloaded' });

      const panel = page.locator(`#tab-${route.tab}`);
      await expect(panel).toHaveClass(/\bactive\b/);
      await expect(panel.locator('h1.ops-page-title')).toHaveText(route.heading);
      await expect(panel.locator('.ops-loading')).toHaveCount(0);
      await expect(panel.locator('.ops-error')).toHaveCount(0);
      if (route.lifecycleFallback) {
        await expect(
          panel.getByRole('link', { name: 'Open Pages lifecycle fallback' }),
        ).toHaveAttribute('href', 'data/vllm/ci/queue_lifecycle.json');
      }

      // Deep links defer the Home payload briefly. Let that background work
      // settle so its failures are included in the runtime-error assertion.
      await page.waitForTimeout(route.watchdog ? 12_500 : 2_000);

      await expect(page.locator('#last-updated')).not.toHaveText('Dashboard startup failed');
      expect(browserErrors, browserErrors.join('\n')).toEqual([]);
    });
  }
});

test('analytics DNS bars are compact, outcome-first, and open sanitized evidence', async ({ page }) => {
  await routeDnsFixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/?ops_analytics_view=dns&ops_analytics_dns_window=3h#ci-analytics', {
    waitUntil: 'domcontentloaded',
  });

  const panel = page.locator('#tab-ci-analytics');
  await expect(panel.locator('h1.ops-page-title')).toHaveText('CI Analytics');
  await expect(panel.getByRole('alert')).toContainText('DNS observations are stale');
  await expect(panel.getByRole('alert')).toContainText('Treat these as historical observations');
  await expect(panel.getByRole('combobox', { name: 'DNS observation window' })).toHaveValue('3h');

  const affectedJobs = panel.locator('.ops-dns-summary-item')
    .filter({ hasText: 'JOBS WITH DNS OBSERVATIONS' });
  await expect(affectedJobs.locator('.ops-dns-summary-value')).toHaveText('4');
  const outcomeSummary = panel.locator('.ops-dns-summary-item')
    .filter({ hasText: 'PASSED / NONPASSING' });
  await expect(outcomeSummary.locator('.ops-dns-summary-value')).toHaveText('1 / 3');
  await expect(outcomeSummary).toHaveClass(/\bis-danger\b/);
  await expect(panel.getByText('Passed after observation')).toBeVisible();
  await expect(panel.getByText('Outcome is correlation, not proof DNS caused the result.')).toBeVisible();

  const queue = panel.locator('article.ops-dns-queue-card')
    .filter({ hasText: 'amd_mi300_1' });
  await expect(queue).toBeVisible();
  await expect(queue.locator('.ops-dns-queue-card-stats')).toContainText('4 jobs');
  await expect(queue.locator('.ops-dns-node-bar')).toHaveCount(3);
  await expect(panel.getByText('amd_mi250_1', { exact: true })).toHaveCount(0);
  await expect(queue.locator('.ops-dns-bar-segment.is-passed')).toHaveCount(1);
  await expect(queue.locator('.ops-dns-bar-segment.is-soft')).toHaveCount(1);
  await expect(queue.locator('.ops-dns-bar-segment.is-hard')).toHaveCount(2);

  const nodeAction = queue.getByRole('button', { name: /node-a: 2 jobs with DNS observations/ });
  await nodeAction.click();
  const drawer = page.getByRole('dialog');
  await expect(drawer.getByRole('heading', { name: 'node-a' })).toBeVisible();
  await expect(drawer).toContainText('DNS observation is not the job outcome');
  await expect(drawer).toContainText('Passed means the final Buildkite job outcome was passed after a resolver signature was observed');
  await expect(drawer).toContainText('Exact links are retained for 1 of 2 affected jobs');
  const evidenceRow = drawer.locator('table[data-geometry="queue-dns-evidence"] tbody tr');
  await expect(evidenceRow.locator('td').nth(0)).toHaveText('Passed after observation');
  await expect(evidenceRow.locator('td').nth(6)).toHaveText('1');
  await expect(evidenceRow.locator('td').nth(7)).toHaveText('9');
  await expect(evidenceRow).toContainText('MI300');
  await expect(evidenceRow).toContainText('job finished at');
  await expect(evidenceRow).toContainText('Hugging Face Hub');
  await expect(evidenceRow).toContainText('temporary name resolution');
  const exactLog = drawer.locator(`a[href="${DNS_LOG_URL}"]`).first();
  await expect(exactLog).toBeVisible();
  await expect(exactLog).toHaveAttribute('target', '_blank');
  await expect(exactLog).toHaveAttribute('rel', 'noopener');

  await page.keyboard.press('Escape');
  await expect(drawer).toHaveCount(0);
  await expect(nodeAction).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('analytics DNS drawer projects long-job evidence into the selected window', async ({ page }) => {
  await routeDnsFixture(page);
  await page.goto('/?ops_analytics_view=dns&ops_analytics_dns_window=1h#ci-analytics', {
    waitUntil: 'domcontentloaded',
  });

  const panel = page.locator('#tab-ci-analytics');
  const dnsScope = panel.getByRole('group', { name: 'DNS queue scope' });
  await expect(dnsScope.getByRole('button', { name: 'All AMD GPU' }))
    .toHaveAttribute('aria-pressed', 'true');
  await expect(dnsScope.getByRole('button', { name: 'All queues' })).toHaveCount(0);

  const queue = panel.locator('article.ops-dns-queue-card')
    .filter({ hasText: 'amd_mi300_1' });
  await queue.getByRole('button', { name: /node-long: 1 jobs with DNS observations/ }).click();

  const drawer = page.getByRole('dialog');
  const evidenceRow = drawer.locator('table[data-geometry="queue-dns-evidence"] tbody tr');
  await expect(evidenceRow).toHaveCount(1);
  await expect(evidenceRow.locator('td').nth(0)).toHaveText('Hard-failed');
  await expect(evidenceRow.locator('td').nth(6)).toHaveText('1');
  await expect(evidenceRow.locator('td').nth(7)).toHaveText('4');
  await expect(evidenceRow).toContainText('GitHub');
  await expect(evidenceRow).toContainText('name or service unknown');
  await expect(evidenceRow).not.toContainText('Hugging Face Hub');
  await expect(evidenceRow).not.toContainText('temporary name resolution');
  await expect(evidenceRow.locator(`a[href="${DNS_LONG_LOG_URL}"]`).first()).toBeVisible();
});

test('analytics DNS partial coverage renders native bars as lower bounds', async ({ page }) => {
  const partialFixture = JSON.parse(JSON.stringify(DNS_FIXTURE));
  const partialCoverage = {
    status: 'partial',
    complete: false,
    discovery_complete: true,
    eligible_jobs: 10,
    scanned_jobs: 9,
    positive_jobs: 3,
    negative_jobs: 6,
    pending_jobs: 1,
    unavailable_jobs: 0,
    oversize_jobs: 0,
  };
  partialFixture.coverage = {
    ...partialCoverage,
    discovery_start: partialFixture.retention.start,
    discovery_end_exclusive: partialFixture.generated_at,
  };
  Object.values(partialFixture.windows).forEach(windowBlock => {
    windowBlock.coverage = { ...partialCoverage };
  });
  await routeDnsFixture(page, partialFixture);
  await page.goto('/?ops_analytics_view=dns&ops_analytics_dns_window=3h#ci-analytics', {
    waitUntil: 'domcontentloaded',
  });

  const panel = page.locator('#tab-ci-analytics');
  await expect(panel.getByRole('status')).toContainText('Partial coverage - counts are lower bounds');
  const queue = panel.locator('article.ops-dns-queue-card')
    .filter({ hasText: 'amd_mi300_1' });
  const nodeA = queue.getByRole('button', { name: /node-a: ≥ 2 jobs with DNS observations/ });
  await expect(nodeA.locator('.ops-dns-node-count')).toHaveText('≥ 2');
  await expect(nodeA.locator('.ops-dns-node-meta')).toContainText('≥ 3 episodes');
  const unidentified = queue.getByRole('button', { name: /unidentified.*≥ 1 jobs with DNS observations/ });
  await expect(unidentified.locator('.ops-dns-node-count')).toHaveText('≥ 1');
  await expect(unidentified.locator('.ops-dns-node-meta')).toContainText('≥ 1 episodes');
  await expect(queue.locator('.ops-dns-bar-track')).toHaveCount(3);
});

test('legacy Queue DNS deep links migrate to CI Analytics', async ({ page }) => {
  await routeDnsFixture(page);
  await page.goto('/?ops_queue_view=dns&ops_queue_dns_window=3h#ci-queue', {
    waitUntil: 'domcontentloaded',
  });

  await expect(page.locator('#tab-ci-analytics')).toHaveClass(/\bactive\b/);
  await expect(page.locator('#tab-ci-analytics .ops-dns-node-bar').first()).toBeVisible();
  await expect.poll(() => page.evaluate(() => ({
    hash: window.location.hash,
    search: window.location.search,
  }))).toEqual({
    hash: '#ci-analytics',
    search: '?ops_analytics_view=dns&ops_analytics_dns_window=3h',
  });
});

test('delayed DNS data cannot repaint Analytics after the user switches views', async ({ page }) => {
  await routeDnsFixture(page, DNS_FIXTURE, 800);
  await page.goto('/?ops_analytics_view=dns#ci-analytics', {
    waitUntil: 'domcontentloaded',
  });

  const panel = page.locator('#tab-ci-analytics');
  await expect(panel.getByText('Loading DNS observations...')).toBeVisible();
  await panel.getByRole('button', { name: 'AMD nightlies', exact: true }).click();
  await expect(panel.getByRole('button', { name: 'AMD nightlies', exact: true }))
    .toHaveAttribute('aria-pressed', 'true');
  await expect(panel.locator('.ops-loading')).toHaveCount(0);

  await page.waitForTimeout(1_000);
  await expect(panel.locator('.ops-dns-summary')).toHaveCount(0);
  await expect(panel.locator('.ops-dns-node-bar')).toHaveCount(0);
  await expect(panel.getByText('Loading DNS observations...')).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => new URL(window.location.href).searchParams.get('ops_analytics_view')))
    .toBe('nightlies');
});

test('analytics DNS paints fast Pages data without loading the operations manifest', async ({ page }) => {
  const requested = [];
  page.on('request', request => requested.push(request.url()));
  await page.route('https://raw.githubusercontent.com/**/dns_failures.json*', () => new Promise(() => {}));
  await page.route('http://127.0.0.1:4173/data/vllm/ci/dns_failures.json*', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(DNS_FIXTURE),
  }));

  const started = Date.now();
  await page.goto('/?ops_analytics_view=dns&ops_analytics_dns_window=3h#ci-analytics', {
    waitUntil: 'domcontentloaded',
  });
  await expect(page.locator('#tab-ci-analytics .ops-dns-node-bar').first()).toBeVisible();
  expect(Date.now() - started).toBeLessThan(1_500);
  expect(requested.some(url => /operations_v2_manifest\.json/.test(url))).toBe(false);
  expect(requested.some(url => /operations_v2\/queue\.json/.test(url))).toBe(false);
  expect(requested.some(url => /operations_v2\/reliability\.json/.test(url))).toBe(false);
});

test('analytics DNS upgrades a fast older Pages paint when slower live data is newer', async ({ page }) => {
  const newerLive = JSON.parse(JSON.stringify(DNS_FIXTURE));
  newerLive.generated_at = '2026-08-16T11:00:00Z';
  newerLive.retention.start = '2026-07-17T11:00:00Z';
  newerLive.retention.end_exclusive = newerLive.generated_at;
  newerLive.coverage.discovery_start = newerLive.retention.start;
  newerLive.coverage.discovery_end_exclusive = newerLive.generated_at;
  Object.entries(newerLive.windows).forEach(([windowId, windowBlock]) => {
    const option = DNS_WINDOW_OPTIONS.find(candidate => candidate.id === windowId);
    windowBlock.start = new Date(
      Date.parse(newerLive.generated_at) - option.hours * 60 * 60 * 1000,
    ).toISOString().replace('.000Z', 'Z');
    windowBlock.end_exclusive = newerLive.generated_at;
  });
  const shiftOneHour = timestamp => new Date(Date.parse(timestamp) + 60 * 60 * 1000)
    .toISOString().replace('.000Z', 'Z');
  newerLive.evidence.items.forEach(row => {
    row.first_at = shiftOneHour(row.first_at);
    row.last_at = shiftOneHour(row.last_at);
    Object.values(row.window_metrics).forEach(metric => {
      metric.first_at = shiftOneHour(metric.first_at);
      metric.last_at = shiftOneHour(metric.last_at);
    });
  });
  await page.route('https://raw.githubusercontent.com/**/dns_failures.json*', async route => {
    await new Promise(resolve => setTimeout(resolve, 400));
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'access-control-allow-origin': '*' },
      body: JSON.stringify(newerLive),
    });
  });
  await page.route('http://127.0.0.1:4173/data/vllm/ci/dns_failures.json*', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(DNS_FIXTURE),
  }));

  await page.goto('/?ops_analytics_view=dns&ops_analytics_dns_window=3h#ci-analytics', {
    waitUntil: 'domcontentloaded',
  });
  const panel = page.locator('#tab-ci-analytics');
  await expect(panel.locator('.ops-dns-node-bar').first()).toBeVisible();
  await panel.locator('summary.ops-dns-method-summary').click();
  await expect(panel.locator('.ops-dns-method-body')).toContainText('source: live dns-health-data');
});
