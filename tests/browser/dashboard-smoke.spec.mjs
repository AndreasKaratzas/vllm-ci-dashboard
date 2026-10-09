import { expect, test } from '@playwright/test';
import { createHash } from 'node:crypto';

// cspell:ignore NVFP pypi

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
  { name: 'home', url: '/#projects', tab: 'projects', heading: 'Command Center' },
  ...[
    ['overview', ''],
    ['parity', ''],
    ['coverage', ''],
    ['mirrors', ''],
  ].map(([view,extra]) => ({
    name: `health ${view}`,
    url: `/?ops_health_view=${view.split(' ')[0]}${extra}#ci-health`,
    tab: 'ci-health',
    heading: 'CI Health',
    watchdog: view === 'overview',
  })),
  ...['groups', 'latency', 'nightlies', 'dns', 'agent-health'].map(view => ({
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

      if (route.dnsFixture) await routeDnsFixture(page);

      await page.goto(route.url, { waitUntil: 'domcontentloaded' });

      const panel = page.locator(`#tab-${route.tab}`);
      await expect(panel).toHaveClass(/\bactive\b/);
      await expect(panel.locator('h1.ops-page-title')).toHaveText(route.heading);
      await expect(panel.locator('.ops-loading')).toHaveCount(0);
      await expect(panel.locator('.ops-error')).toHaveCount(0);

      // Deep links defer the Home payload briefly. Let that background work
      // settle so its failures are included in the runtime-error assertion.
      await page.waitForTimeout(route.watchdog ? 12_500 : 2_000);

      await expect(page.locator('#last-updated')).not.toHaveText('Dashboard startup failed');
      expect(browserErrors, browserErrors.join('\n')).toEqual([]);
    });
  }
});

test('CI health keeps configured health policy in AMD hardware', async ({ page }) => {
  await page.goto('/?ops_health_view=coverage#ci-health', { waitUntil: 'domcontentloaded' });

  const health = page.locator('#tab-ci-health .ops-unique-health');
  await expect(health.locator('.ops-unique-health-rate span')).toHaveText('Passing');

  const stats = health.locator('.ops-unique-health-stat');
  await expect(stats).toHaveCount(4);
  await expect(stats.locator('span')).toHaveText([
    'Test groups',
    'Passing',
    'Failing',
    'No signal',
  ]);

  const totalStat = stats.filter({ hasText: 'Test groups' });
  const total = Number(await totalStat.locator('strong').innerText());
  expect(total).toBeGreaterThan(0);
  await totalStat.click();

  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name: 'Configured AMD test groups' })).toBeVisible();
  await expect(dialog).toContainText(`${total} configured AMD test groups`);
});

const agentStartedScope = {
  version: 1, basis: 'terminal_jobs_by_started_at', requested_days: 1,
  collected_from: '2026-10-07T00:00:00Z', collected_to: '2026-10-08T20:00:00Z', exhaustive: true,
  discovery_legs: {created: true, older_finished: true, older_active: true},
  active_build_states: ['creating', 'scheduled', 'running', 'failing', 'blocked', 'canceling'],
  attempt_policy: 'latest_attempt_per_step', terminal_time_policy: 'finished_at_or_terminal_build_bound_for_canceled',
  complete_window: false,
};
const agentCreatedScope = {
  ...agentStartedScope, version: 2, basis: 'terminal_jobs_by_build_created_at',
  eligible_completion: 'current_ci_build_creation_cohort_with_provable_completion',
  day_basis: 'build_created_at_utc', discovery_legs: {created: true},
};
delete agentCreatedScope.active_build_states;

async function routeAgentHistoryScope(page, scope, delayedEvidence = false) {
  await page.route('**/operations_v2/amd_agent_health.json*', async route => {
    const response = await route.fetch();
    const packet = await response.json();
    const agent = packet.amd_agent_health;
    Object.assign(agent, {
      pipelines: ['ci'], generated_at: '2026-10-08T20:00:00Z', max_window_days: 60,
      node_days: [{d: '2026-10-07', nd: 'fixture-ci-node', h: 'MI300', a: [10, 0, 1, 0], n: [10, 0, 1, 0]}],
      failing_runs: [],
      failure_accounting: [{d: '2026-10-07', nd: 'fixture-ci-node', h: 'MI300', s: 'hard', i: 1, ng: 1, bc: 0, c: 1}],
      retention: {
        configured_days: 60, byte_limited: false, dropped_oldest_day_count: 0, original_day_count: 1, retained_day_count: 1,
        pipeline_scope: scope,
      },
      operations_publication_retention: {
        node_days: {source: 1, published: 1, complete: true},
        failure_accounting: {source: 1, published: 1, complete: true},
        failure_evidence: {source: 0, published: 0, complete: true},
      },
    });
    if (delayedEvidence) {
      agent.node_days.push({...agent.node_days[0], d: '2026-10-06'});
      agent.failure_accounting.push({...agent.failure_accounting[0], d: '2026-10-06'});
      agent.failing_runs = [
        {d: '2026-10-07', nd: 'fixture-ci-node', h: 'MI300', p: 'ci', q: 'amd_mi300_1',
          g: 'Created-window morning job', s: 'hard', i: 1, ng: 1, bc: 0, b: 30005,
          j: '10000000-0000-0000-0000-000000000001', t: '2026-10-07T01:00:00Z', e: '2026-10-07T01:10:00Z'},
        {d: '2026-10-06', nd: 'fixture-ci-node', h: 'MI300', p: 'ci', q: 'amd_mi300_1',
          g: 'Older-build recently started job', s: 'hard', i: 1, ng: 1, bc: 0, b: 30004,
          j: '10000000-0000-0000-0000-000000000002', t: '2026-10-08T10:00:00Z', e: '2026-10-08T10:10:00Z'},
      ];
      agent.retention.original_day_count = agent.retention.retained_day_count = 2;
      agent.operations_publication_retention.node_days = {source: 2, published: 2, complete: true};
      agent.operations_publication_retention.failure_accounting = {source: 2, published: 2, complete: true};
      agent.operations_publication_retention.failure_evidence = {source: 2, published: 2, complete: true};
    }
    await route.fulfill({json: packet});
  });
}

test('CI agent health discloses fresh started-job scope and keeps covered one-day rates', async ({ page }) => {
  await routeAgentHistoryScope(page, agentStartedScope);
  await page.goto('/?ops_analytics_view=agent-health#ci-analytics', {waitUntil: 'domcontentloaded'});
  const panel = page.locator('#tab-ci-analytics');
  await expect(panel.locator('.ops-loading')).toHaveCount(0);
  await expect(panel.locator('.ops-evidence-note.is-info')).toContainText('all branches and PRs across the ci pipeline');
  await expect(panel).not.toContainText('AMD nightly and upstream CI pipelines');
  const notice = panel.locator('.ops-evidence-note.is-warning');
  await expect(notice).toContainText('Agent-health pipeline history is incomplete');
  await expect(notice).toContainText(agentStartedScope.collected_from);
  await expect(notice).toContainText(agentStartedScope.collected_to);
  await expect(notice).toContainText('Complete fresh UTC history');
  await expect(notice).toContainText('the full 60-day window is not yet complete');
  await expect(notice).not.toContainText('dropped 0 oldest UTC days');
  const row = panel.locator('.ops-agent-table tbody tr').filter({hasText: 'fixture-ci-node'});
  await expect(row).toHaveCount(1);
  await expect(row).toContainText('Unavailable');
  await panel.locator('.ops-agent-controls').getByRole('button', {name: '1d', exact: true}).click();
  await expect(row).toContainText('10.0%');
  await expect(row).not.toContainText('Unavailable');
  await expect(notice).toContainText('the full 60-day window is not yet complete');
  await panel.locator('.ops-agent-controls').getByRole('button', {name: '3d', exact: true}).click();
  await expect(row).toContainText('Unavailable');
  await panel.locator('.ops-agent-controls').getByRole('button', {name: '60d', exact: true}).click();
  await expect(row).toContainText('Unavailable');
  await expect(panel.locator('.ops-error')).toHaveCount(0);
});

test('CI agent health labels the build creation cohort and keeps exact covered default-week rates', async ({ page }) => {
  const scope = {...agentCreatedScope, requested_days: 7, collected_from: '2026-10-01T00:00:00Z'};
  await routeAgentHistoryScope(page, scope);
  await page.goto('/?ops_analytics_view=agent-health#ci-analytics', {waitUntil: 'domcontentloaded'});
  const panel = page.locator('#tab-ci-analytics');
  await expect(panel.locator('.ops-loading')).toHaveCount(0);
  const completion = panel.locator('.ops-agent-completion-scope');
  await expect(completion).toContainText('Terminal runs from CI builds created in the selected UTC window');
  await expect(completion).toContainText('Runs require a recorded or bounded completion time');
  await expect(completion).toContainText('the ci pipeline');
  await expect(completion).toContainText(scope.collected_from);
  await expect(completion).toContainText(scope.collected_to);
  const notice = panel.locator('.ops-evidence-note.is-warning');
  await expect(notice).toContainText('the full 60-day window is not yet complete');
  const row = panel.locator('.ops-agent-table tbody tr').filter({hasText: 'fixture-ci-node'});
  await expect(row).toContainText('10.0%');
  await expect(panel).not.toContainText('rollup of every build');
  for (const window of ['1d', '3d', '7d']) {
    await panel.locator('.ops-agent-controls').getByRole('button', {name: window, exact: true}).click();
    await expect(row).toContainText('10.0%');
    await expect(row).not.toContainText('Unavailable');
  }
  await panel.locator('.ops-agent-controls').getByRole('button', {name: '60d', exact: true}).click();
  await expect(row).toContainText('Unavailable');
  await expect(notice).toContainText('the full 60-day window is not yet complete');
  await expect(completion).toBeVisible();
  await expect(panel.locator('.ops-error')).toHaveCount(0);
});

test('CI agent health selects evidence by build creation day while retaining actual job times', async ({ page }) => {
  const scope = {...agentCreatedScope, requested_days: 7, collected_from: '2026-10-01T00:00:00Z'};
  await routeAgentHistoryScope(page, scope, true);
  await page.goto('/?ops_analytics_view=agent-health#ci-analytics', {waitUntil: 'domcontentloaded'});
  const panel = page.locator('#tab-ci-analytics');
  await expect(panel.locator('.ops-loading')).toHaveCount(0);
  const row = panel.locator('.ops-agent-table tbody tr').filter({hasText: 'fixture-ci-node'});
  await expect(row.locator('td').nth(2)).toHaveText('20');
  await expect(row.locator('td').nth(6)).toHaveText('2');
  await panel.locator('.ops-agent-controls').getByRole('button', {name: '1d', exact: true}).click();
  await expect(row.locator('td').nth(2)).toHaveText('10');
  await expect(row.locator('td').nth(6)).toHaveText('1');
  await expect(row).toContainText('10.0%');
  await row.getByRole('button', {name: /^Open retained infra-suspect failing runs/}).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('Created-window morning job');
  await expect(dialog).not.toContainText('Older-build recently started job');
  await expect(dialog.getByRole('link', {name: /^Created-window morning job/})).toHaveAttribute('href',
    'https://buildkite.com/vllm/ci/builds/30005/steps/canvas?jid=10000000-0000-0000-0000-000000000001&tab=output');
  await expect(dialog).toContainText('Oct 7, 01:00 AM');
});

for (const [name, scope] of [
  ['legacy creation-only', {collected_from: '2026-08-01T00:00:00Z', complete_window: true}],
  ['stale started-job', {...agentStartedScope, collected_to: '2026-10-08T19:00:00Z'}],
  ['malformed started-job', {...agentStartedScope, discovery_legs: {...agentStartedScope.discovery_legs, older_active: false}}],
  ['incomplete build-created cohort', {...agentCreatedScope, discovery_legs: {created: false}}],
  ['missing build-created eligibility', {...agentCreatedScope, eligible_completion: undefined}],
  ['mixed build-created authority', {...agentCreatedScope, active_build_states: agentStartedScope.active_build_states}],
]) {
  test(`CI agent health keeps observed counts and hides rates for ${name} coverage`, async ({ page }) => {
    await routeAgentHistoryScope(page, scope);
    await page.goto('/?ops_analytics_view=agent-health#ci-analytics', {waitUntil: 'domcontentloaded'});
    const panel = page.locator('#tab-ci-analytics');
    await expect(panel.locator('.ops-loading')).toHaveCount(0);
    await expect(panel.locator('.ops-evidence-note.is-warning')).toContainText('Complete agent-health history is unavailable');
    await expect(panel).toContainText('Retained counts describe observed runs');
    await panel.locator('.ops-agent-controls').getByRole('button', {name: '1d', exact: true}).click();
    const row = panel.locator('.ops-agent-table tbody tr').filter({hasText: 'fixture-ci-node'});
    await expect(row).toHaveCount(1);
    await expect(row.locator('td').nth(2)).toHaveText('≥10');
    await expect(row).toContainText('Unavailable');
    await expect(row).not.toContainText('10.0%');
    await expect(panel.locator('.ops-agent-completion-scope')).toHaveCount(0);
    await expect(panel.locator('.ops-error')).toHaveCount(0);
  });
}

test('CI health upstream parity exposes the main backlog and not-targeted set', async ({ page }) => {
  await page.goto('/?ops_health_view=parity#ci-health', { waitUntil: 'domcontentloaded' });

  const health = page.locator('#tab-ci-health');
  await expect(health.getByRole('button', { name: /^\d+ potential open gaps$/ }).first()).toBeVisible();
  await expect(health).toContainText('Potential open gaps by test area');
  await expect(health.getByText(/need attention/i)).toHaveCount(0);
  await health.getByRole('button', { name: /Browse \d+ not-targeted groups/i }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name: 'Not targeted / unsupported' })).toBeVisible();
  await expect(dialog.locator('tbody tr').first()).toBeVisible();
});

test('CI health summaries stay scoped to the selected view', async ({ page }) => {
  await page.goto('/?ops_health_view=overview#ci-health', { waitUntil: 'domcontentloaded' });

  const health = page.locator('#tab-ci-health');
  await expect(health.getByText('LATEST AMD TEST GROUPS', { exact: true })).toBeVisible();
  await expect(health.getByText('Logical runtime health', { exact: true })).toBeVisible();
  await expect(health.getByText(/LOGICAL GROUP OUTCOMES/).first()).toBeVisible();
  await health.getByRole('button', { name: /Inspect all logical test groups/ }).click();
  const runtimeDialog = page.getByRole('dialog');
  await expect(runtimeDialog.getByRole('heading', { name: 'Latest AMD logical test groups' })).toBeVisible();
  await expect(runtimeDialog.getByText('Logical AMD test group', { exact: true })).toBeVisible();
  await expect(runtimeDialog).not.toContainText('Not in latest');
  await runtimeDialog.getByRole('button', { name: /Close/i }).click();

  await health.getByRole('button', { name: 'Upstream parity', exact: true }).click();
  await expect(health.getByRole('button', { name: 'Upstream parity', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(health.getByText('UPSTREAM PARITY ON MAIN', { exact: true })).toBeVisible();
  await expect(health.getByText('Applicable test groups covered', { exact: true })).toBeVisible();
  await expect(health.getByText(/WITH PROPOSED CHANGES/i)).toHaveCount(0);
});

test('CI health overview exposes configured AMD mirror groups and routes to the inventory', async ({ page }) => {
  const parityResponse = page.waitForResponse(response => (
    new URL(response.url()).pathname.endsWith('/data/vllm/ci/operations_v2/test_group_parity.json')
  ));
  await page.goto('/?ops_health_view=overview#ci-health', { waitUntil: 'domcontentloaded' });

  const payload = await (await parityResponse).json();
  const mirrorCount = Number(payload.test_group_parity.mirror_inventory.summary.total);
  expect(mirrorCount).toBeGreaterThan(0);

  const health = page.locator('#tab-ci-health');
  const mirrorSummary = health.locator('.ops-health-mirror-summary');
  await expect(mirrorSummary).toBeVisible();
  await expect(mirrorSummary).toHaveAttribute('type', 'button');
  await expect(mirrorSummary).toContainText(String(mirrorCount));
  await expect(mirrorSummary).toContainText(/AMD mirrors/i);
  await expect(mirrorSummary).toContainText(/configured AMD mirror groups/i);

  await mirrorSummary.click();
  await expect(page).toHaveURL(/ops_health_view=mirrors/);
  await expect(health.getByRole('button', { name: 'AMD mirrors', exact: true })).toHaveAttribute('aria-pressed', 'true');
});

test('CI health overview remains usable when the mirror enhancement cannot load', async ({ page }) => {
  await page.route(/\/assets\/js\/amd-mirror-inventory\.js(?:\?.*)?$/, route => route.abort('failed'));
  await page.goto('/?ops_health_view=overview#ci-health', { waitUntil: 'domcontentloaded' });

  const health = page.locator('#tab-ci-health');
  await expect(health.getByText('Logical runtime health', { exact: true })).toBeVisible();
  await expect(health.locator('.ops-error')).toHaveCount(0);
  await expect(health.locator('.ops-health-mirror-summary')).toHaveCount(0);
});

test('CI health overview marks the mirror count unavailable without losing core health', async ({ page }) => {
  await page.route(/\/data\/vllm\/ci\/operations_v2\/test_group_parity\.json(?:\?.*)?$/, route => route.abort('failed'));
  await page.goto('/?ops_health_view=overview#ci-health', { waitUntil: 'domcontentloaded' });

  const health = page.locator('#tab-ci-health');
  await expect(health.getByText('Logical runtime health', { exact: true })).toBeVisible();
  await expect(health.locator('.ops-error')).toHaveCount(0);
  const summary = health.locator('.ops-health-mirror-summary.is-unavailable');
  await expect(summary).toBeVisible();
  await expect(summary).toContainText('—');
});

test('CI health data freshness opens in place without changing views', async ({ page }) => {
  await page.goto('/?ops_health_view=overview#ci-health', { waitUntil: 'domcontentloaded' });
  const health = page.locator('#tab-ci-health');

  const initialUrl = page.url();
  await health.getByRole('button', { name: 'Data freshness' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText(/published|collector|source/i);
  await expect(page).toHaveURL(/ops_health_view=overview/);
  await expect(page).toHaveURL(/ops_detail=ci-health-data-freshness/);
  expect(new URL(page.url()).hash).toBe(new URL(initialUrl).hash);
  await dialog.getByRole('button', { name: /Close/i }).click();
  await expect(health.getByRole('button', { name: 'Overview', exact: true })).toHaveAttribute('aria-pressed', 'true');
});

test('CI health mobile deep links keep the active view visible', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/?ops_health_view=mirrors#ci-health', { waitUntil: 'domcontentloaded' });
  const tabs = page.locator('#tab-ci-health .ops-health-tabs');
  const active = tabs.getByRole('button', { name: 'AMD mirrors', exact: true });
  await expect(active).toHaveAttribute('aria-pressed', 'true');
  await page.waitForTimeout(50);
  const tabsBox = await tabs.boundingBox();
  const activeBox = await active.boundingBox();
  expect(activeBox.x).toBeGreaterThanOrEqual(tabsBox.x - 1);
  expect(activeBox.x + activeBox.width).toBeLessThanOrEqual(tabsBox.x + tabsBox.width + 1);
});

test('CI health tabs retain keyboard focus after route-backed rerenders', async ({ page }) => {
  await page.goto('/?ops_health_view=overview#ci-health', { waitUntil: 'domcontentloaded' });
  const health = page.locator('#tab-ci-health');

  const overview = health.getByRole('button', { name: 'Overview', exact: true });
  await overview.focus();
  await overview.press('ArrowRight');
  const parity = health.getByRole('button', { name: 'Upstream parity', exact: true });
  await expect(parity).toHaveAttribute('aria-pressed', 'true');
  await expect(parity).toBeFocused();

  await parity.press('End');
  const mirrors = health.getByRole('button', { name: 'AMD mirrors', exact: true });
  await expect(mirrors).toHaveAttribute('aria-pressed', 'true');
  await expect(mirrors).toBeFocused();
});

test('CI health AMD mirrors uses graphical summaries and retains the full inventory browser', async ({ page }) => {
  const parityResponse = page.waitForResponse(response => (
    new URL(response.url()).pathname.endsWith('/data/vllm/ci/operations_v2/test_group_parity.json')
  ));
  await page.goto('/?ops_health_view=mirrors#ci-health', { waitUntil: 'domcontentloaded' });
  const health = page.locator('#tab-ci-health');
  const payload = await (await parityResponse).json();
  const inventory = (() => {
    const mirror = payload.test_group_parity.mirror_inventory;
    const retention = mirror.publication_retention || {};
    const groupRetention = retention.group_index || {};
    const publishedRows = Array.isArray(mirror.rows) ? mirror.rows.length : 0;
    const count = Number(payload.test_group_parity.mirror_inventory.summary.total);
    const aggregateComplete = retention.aggregate_summaries_complete !== false;
    return {
      count,
      publishedRows,
      aggregateComplete,
      complete: aggregateComplete
        && groupRetention.complete_relative_to_source !== false
        && publishedRows === count,
    };
  })();

  expect(inventory.count).toBeGreaterThan(0);
  const hero = health.locator('.ops-mirror-hero');
  await expect(hero).toBeVisible();
  await expect(hero).toContainText(inventory.aggregateComplete ? String(inventory.count) : `≥${inventory.count}`);
  await expect(health.locator('.ops-mirror-area-bars')).toBeVisible();
  await expect(health.locator('.ops-mirror-preview-list')).toBeVisible();
  await health.getByText('How this live count is built', { exact: true }).click();
  await expect(
    health.getByText(/One top-level YAML step with a non-empty mirror\.amd mapping counts once/),
  ).toBeVisible();

  if (!inventory.complete) {
    const coverageLead = inventory.aggregateComplete
      ? 'The aggregate total remains exact, but'
      : 'The published aggregate is marked incomplete, and';
    await expect(health.getByText(
      `${coverageLead} the published detail index contains ${inventory.publishedRows} of ${inventory.count} AMD mirror declarations.`,
      { exact: false },
    )).toBeVisible();
  }
  const browseName = inventory.complete
    ? `Browse all ${inventory.count} AMD mirrors`
    : `Browse ${inventory.publishedRows} published AMD mirrors`;
  if (inventory.publishedRows === 0) {
    await expect(health.getByRole('button', { name: browseName, exact: true })).toHaveCount(0);
    return;
  }
  await health.getByRole('button', { name: browseName, exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name: 'AMD mirror inventory', exact: true })).toBeVisible();
  await expect(dialog.locator('.ops-browser-count')).toHaveText(
    `${inventory.publishedRows} of ${inventory.publishedRows} rows`,
  );
});

test('CI health AMD mirrors does not overstate an incomplete hardware breakdown', async ({ page }) => {
  let aggregateCount = 0;
  await page.route(/\/data\/vllm\/ci\/operations_v2\/test_group_parity\.json(?:\?.*)?$/, async route => {
    const response = await route.fetch();
    const payload = await response.json();
    aggregateCount = Number(payload.test_group_parity.mirror_inventory.summary.total);
    const mirror = payload.test_group_parity.mirror_inventory;
    mirror.rows = mirror.rows.slice(0, 5);
    mirror.publication_retention = {
      ...(mirror.publication_retention || {}),
      aggregate_summaries_complete: true,
      group_index: {
        ...((mirror.publication_retention || {}).group_index || {}),
        complete_relative_to_source: false,
      },
    };
    await route.fulfill({ response, json: payload });
  });
  await page.goto('/?ops_health_view=mirrors#ci-health', { waitUntil: 'domcontentloaded' });

  const health = page.locator('#tab-ci-health');
  const hero = health.locator('.ops-mirror-total-card');
  await expect(hero).toBeVisible();
  expect(aggregateCount).toBeGreaterThan(5);
  await expect(hero.locator('.ops-mirror-count-ring strong')).toHaveText(String(aggregateCount));
  await expect(hero).toContainText('The total is exact; the hardware breakdown is unavailable');
  await expect(hero.locator('.ops-mirror-hardware-item')).toHaveCount(0);
  await expect(health.locator('.ops-mirror-configuration-card .ops-mirror-mode-track')).toHaveCount(0);
  await expect(
    health.locator('.ops-mirror-configuration-card .ops-mirror-fact').nth(1).locator('strong'),
  ).toHaveText(/^≥\d+$/);
});

test('CI health AMD mirror graphics retain text equivalents in forced colors', async ({ page }) => {
  await page.emulateMedia({ forcedColors: 'active' });
  await page.goto('/?ops_health_view=mirrors#ci-health', { waitUntil: 'domcontentloaded' });

  const health = page.locator('#tab-ci-health');
  await expect(health.locator('.ops-mirror-count-ring')).toBeVisible();
  await expect(health.locator('.ops-mirror-area-track').first()).toBeHidden();
  const breakdown = health.locator('.ops-mirror-area-breakdown').first();
  await expect(breakdown).toBeVisible();
  await expect(breakdown).toHaveText(/^\d+ R · \d+ O · \d+ S$/);
});

test('CI health parity is main-only and opens grouped gap tables', async ({ page }) => {
  await page.goto('/?ops_health_view=parity#ci-health', { waitUntil: 'domcontentloaded' });
  const health = page.locator('#tab-ci-health');
  await expect(health).toContainText('Potential open gaps by test area');
  await expect(health.getByText(/proposed/i)).toHaveCount(0);
  await health.getByRole('button', { name: /Browse all \d+ potential open gaps/ }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('Potential open gaps');
  await expect(dialog.locator('tbody tr').first()).toBeVisible();
});

test('CI parity explains changed definitions without transferring reviewed coverage', async ({ page }) => {
  await page.route('**/operations_v2/test_group_parity.json*', async route => {
    const response = await route.fetch();
    const payload = await response.json();
    const parity = payload.test_group_parity || payload;
    parity.groups[0] = {
      ...parity.groups[0],
      title: 'Reviewed OpenAI group',
      reviewed_title: 'Original reviewed title',
      definition_resolution: {
        status: 'unresolved',
        commit_sha: 'a'.repeat(40),
        successor_labels: ['OpenAI completion integration', 'OpenAI chat integration'],
        note: 'The original step was split; replacement coverage needs review.',
      },
    };
    await route.fulfill({ response, json: payload });
  });
  await page.goto('/?ops_health_view=parity#ci-health', { waitUntil: 'domcontentloaded' });
  await page.locator('#tab-ci-health').getByRole('button', { name: /Browse complete \d+-group inventory/ }).click();
  const inventory = page.getByRole('dialog');
  await expect(inventory).toContainText('Current CI definition');
  await inventory.getByPlaceholder('Filter test group, area, CUDA variant, status, or assessment').fill('Reviewed OpenAI group');
  const row = inventory.locator('tbody tr').filter({ hasText: 'Reviewed OpenAI group' });
  await expect(row).toContainText('Removed or changed');
  await row.getByRole('button', { name: 'Reviewed OpenAI group', exact: true }).click();
  const detail = page.getByRole('dialog').last();
  await expect(detail).toContainText('Source name');
  await expect(detail).toContainText('Reviewed OpenAI group');
  await expect(detail).toContainText('OpenAI completion integration');
  await expect(detail).toContainText('replacement coverage needs review');
  await expect(detail.getByRole('link', { name: 'Open current CI definitions' })).toHaveAttribute(
    'href', 'https://github.com/vllm-project/vllm/tree/' + 'a'.repeat(40) + '/.buildkite/test_areas',
  );
});









test('CI analytics separates logical test groups from exact job variants', async ({ page }) => {
  await page.goto('/?ops_analytics_view=groups#ci-analytics', { waitUntil: 'domcontentloaded' });

  const summary = page.locator('#tab-ci-analytics .ops-status-strip').first();
  const cards = summary.locator('.ops-status-item');
  await expect(cards).toHaveCount(4);
  await expect(cards.locator('.ops-stat-label')).toHaveText([
    'LATEST AMD NIGHTLY',
    'LATEST AMD TEST GROUPS',
    'LATEST JOB VARIANTS',
    'FAILURE OBSERVATIONS',
  ]);

  const testGroups = cards.filter({ hasText: 'LATEST AMD TEST GROUPS' });
  const jobVariants = cards.filter({ hasText: 'LATEST JOB VARIANTS' });
  await expect(testGroups.locator('.ops-stat-value')).toContainText(/\d+ \/ \d+ passing/);
  await expect(testGroups.locator('.ops-stat-meta')).toContainText(/\d+ pass on every route · \d+ pass on some hardware only · \d+ non-passing everywhere/);
  await expect(jobVariants.locator('.ops-stat-meta')).toContainText(/passing - \d+ non-passing exact jobs/);

  const testGroupCount = Number((await testGroups.locator('.ops-stat-value').innerText()).match(/\/\s*(\d+)/)?.[1]);
  const jobVariantCount = Number(await jobVariants.locator('.ops-stat-value').innerText());
  expect(testGroupCount).toBeGreaterThan(0);
  expect(jobVariantCount).toBeGreaterThan(testGroupCount);
});





test('nightly failure alerts exclude fixed groups while build movement retains them', async ({ page }) => {
  const group = (id, name, state) => ({
    id, name, display_name: name, state, current_state: state,
    queue: 'amd_mi300_1',
    job_url: `https://buildkite.com/vllm/ci/builds/12674#${id}`,
  });
  const hard = group('019fffb7-f7b6-4eca-b534-a381854a3268', 'Current hard failure', 'hard');
  const soft = group('019ffee8-7bb4-442b-9498-58aecc9bbb8e', 'Current soft failure', 'soft');
  const fixed = group('01a00c61-1759-41b1-82e7-a7696a4854fc', 'Recovered test group', 'passed');
  const build = {
    number: 12674, source_pipeline: 'ci', state: 'failed',
    url: 'https://buildkite.com/vllm/ci/builds/12674',
    created_at: '2026-09-07T09:00:00Z', has_test_results: true, total_groups: 3,
    passed: 1, failed: 1, soft_failed: 1,
    failed_groups: [hard], soft_failed_groups: [soft],
    failure_movement: { policy_id: 'observed-failure-movement-v1', available: true,
      new: [hard], recurring: [soft], fixed: [fixed] },
  };
  await page.route('**/operations_v2_manifest.json*', async route => {
    const response = await route.fetch();
    const payload = await response.json();
    const attention = [
      { kind: 'nightly_hard_failures', count: 1, severity: 'critical' },
      { kind: 'nightly_soft_failures', count: 1, severity: 'warning' },
    ];
    payload.shell.attention = attention;
    payload.shell.home.attention = attention;
    const amdCohort = {pipeline: 'ci', source_pipeline: 'ci', job_scope: 'amd_gpu', cohort_id: 'ci-amd', builds: [build]};
    payload.shell.nightly.canonical_history = amdCohort;
    payload.shell.nightly.pipelines = [amdCohort,...(payload.shell.nightly.pipelines || []).filter(row=>row.cohort_id!=='ci-amd')];
    await route.fulfill({ response, json: payload });
  });
  await page.route('**/operations_v2/nightly.json*', route => route.fulfill({
    json: { nightly: {pipelines: [{pipeline: 'ci', source_pipeline: 'ci', job_scope: 'amd_gpu', cohort_id: 'ci-amd', builds: [build]}]}  },
  }));
  await page.goto('/#projects', { waitUntil: 'domcontentloaded' });
  const home = page.locator('#tab-projects');
  await home.getByRole('button', { name: 'Hard-failed groups in the latest AMD nightly', exact: true }).click();
  let dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name: 'Current hard failures', exact: true })).toBeVisible();
  await expect(dialog.locator('tbody tr')).toHaveCount(1);
  await expect(dialog).toContainText(hard.name);
  await expect(dialog).not.toContainText(soft.name);
  await expect(dialog).not.toContainText(fixed.name);
  await dialog.getByRole('button', { name: 'Close dialog' }).click();
  await home.getByRole('button', { name: 'Soft-failed groups in the latest AMD nightly', exact: true }).click();
  dialog = page.getByRole('dialog');
  await expect(dialog.locator('tbody tr')).toHaveCount(1);
  await expect(dialog).toContainText(soft.name);
  await expect(dialog).not.toContainText(hard.name);
  await expect(dialog).not.toContainText(fixed.name);
  await dialog.getByRole('button', { name: 'Close dialog' }).click();
  await page.goto('/?ops_analytics_view=nightlies#ci-analytics', { waitUntil: 'domcontentloaded' });
  await page.locator('#tab-ci-analytics .ops-status-item').filter({ hasText: 'JOB VARIANTS OBSERVED' }).click();
  dialog = page.getByRole('dialog');
  await expect(dialog.getByText('Failure movement', { exact: true })).toBeVisible();
  await expect(dialog.locator('tbody tr')).toHaveCount(3);
  await expect(dialog).toContainText(fixed.name);
});

test('retired control routes are absent from the public dashboard', async ({ page }) => {
  await page.goto('/#ci-testbuild', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#tab-ci-testbuild')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Sign in|Test Build|Ready Tickets|Admin Control/i })).toHaveCount(0);
});

test('mobile navigation contains focus and restores the dashboard', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/#ci-health', { waitUntil: 'domcontentloaded' });
  const toggle = page.getByRole('button', { name: 'Open navigation' });
  await toggle.click();
  await expect(page.locator('#sidebar')).toHaveClass(/open/);
  expect(await page.locator('#main-content').evaluate(element => element.inert)).toBe(true);
  await page.keyboard.press('Escape');
  await expect(page.locator('#sidebar')).not.toHaveClass(/open/);
  expect(await page.locator('#main-content').evaluate(element => element.inert)).toBe(false);
  await expect(page.getByRole('button', { name: 'Open navigation' })).toBeFocused();
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
  await expect(dnsScope.getByRole('button', { name: 'All active AMD GPU' }))
    .toHaveAttribute('aria-pressed', 'true');
  await expect(dnsScope).toHaveAttribute('aria-describedby', 'ops-dns-scope-help');
  await expect(panel.locator('#ops-dns-scope-help')).toContainText(
    'Canonical AMD is the 12 standard MI250, MI300, and MI355 queues',
  );
  await expect(panel.locator('#ops-dns-scope-help')).toContainText(
    'All active AMD GPU also includes other amd_mi* models and widths',
  );
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
  for (const unrelated of [
    /assets\/js\/dashboard\.js/,
    /assets\/js\/ci-(?:health|analytics|perf-eval|queue|hotness|omni)\.js/,
    /assets\/js\/ci-(?:testbuild|ready|admin)\.js/,
    /data\/vllm\/ci\/(?:ci_health|parity_report|shard_bases)\.json/,
  ]) {
    expect(requested.some(url => unrelated.test(url))).toBe(false);
  }
  const fallbackIndex = requested.findIndex(url => /data\/vllm\/ci\/dns_failures\.json/.test(url));
  const rendererIndex = requested.findIndex(url => /assets\/js\/ops-v2\.js/.test(url));
  expect(fallbackIndex).toBeGreaterThanOrEqual(0);
  expect(rendererIndex).toBeGreaterThan(fallbackIndex);
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

for (const migration of [
  {url:'/?ops_queue_view=history&ops_queue_range=24h#ci-queue',tab:'ci-omni'},
  {url:'/?ops_trajectory_view=capacity&ops_capacity_jobs=30#ci-hotness',tab:'ci-health'},
  {url:'/?ops_health_view=targets&ops_health_result=failed#ci-health',tab:'ci-health'},
  {url:'/?ops_analytics_view=flakes&ops_analytics_window=30d#ci-analytics',tab:'ci-analytics'},
  {url:'/?ops_analytics_view=retries&ops_analytics_window=30d#ci-analytics',tab:'ci-analytics'},
]) {
  test(`retired view ${migration.url} resolves to a supported view`,async ({page}) => {
    await page.goto(migration.url,{waitUntil:'domcontentloaded'});
    await expect(page.locator(`#tab-${migration.tab}`)).toHaveClass(/\bactive\b/);
    await expect(page.locator(`#tab-${migration.tab} .ops-loading`)).toHaveCount(0);
    await expect(page.locator(`#tab-${migration.tab} .ops-error`)).toHaveCount(0);
    expect(await page.evaluate(()=>location.search)).not.toMatch(/ops_(queue_|trajectory_|capacity_)|ops_health_result|ops_analytics_window/);
    await expect(page.locator('#tab-ci-queue, #tab-ci-hotness')).toHaveCount(0);
    await expect(page.getByRole('button',{name:/^Target health$|^Flake comparison$|^Retry comparison$/i})).toHaveCount(0);
  });
}

for (const evidenceFormat of ['dictionary', 'compact']) {
for (const linkFormat of ['canvas', 'fragment']) {
test(`latency comparison uses five current main CI nightlies and exact ${evidenceFormat} ${linkFormat} gating links`,async ({page})=>{
  const nightlies=[30005,30004,30003,30002,30001].map((number,index)=>({number,created_at:`2026-10-0${8-index}T00:00:00Z`,finished_at:`2026-10-0${8-index}T01:00:00Z`,web_url:`https://buildkite.com/vllm/ci/builds/${number}`}));
  const jobId='019fffb7-f7b6-4eca-b534-a381854a3268';
  const jobUrl=linkFormat==='fragment'?`${nightlies[0].web_url}#${jobId}`:`${nightlies[0].web_url}/steps/canvas?jid=${jobId}&tab=output`;
  const job={job_id:jobId,step_id:'current-test',url:jobUrl,queue:'amd_mi300_1',hardware:'mi300',raw_name:'Current gating test',started_at:nightlies[0].created_at,finished_at:'2026-10-08T00:20:00Z',duration_mins:20};
  const invalidJobs=[
    ['pipeline',jobUrl.replace('/ci/','/amd-ci/')],
    ['build',jobUrl.replace('/30005','/30004')],
    ['identity',jobUrl.replace(jobId,'different-job')],
    ['scheme',jobUrl.replace('https:','http:')],
    ['host',jobUrl.replace('buildkite.com','buildkite.com.example.org')],
    ['step-only',`${nightlies[0].web_url}/steps/canvas?sid=${jobId}&tab=output`],
  ].map(([kind,url])=>({...job,url,queue:`invalid-${kind}`}));
  const sample={build_number:30005,build_url:nightlies[0].web_url,created_at:nightlies[0].created_at,finished_at:nightlies[0].finished_at,duration_mins:20,jobs:[job,...invalidJobs]};
  const side={median_duration_mins:20,sample_count:1,samples:[sample]};
  const latency={schema_version:1,source_pipeline:'ci',branch:'main',build_limit:5,statistic:'median_of_per_nightly_group_wall_minutes',available:true,cohort:{nightlies},rows:[{id:'current-test',label:'Current gating test',match_status:'matched',amd:side,upstream:{...side,median_duration_mins:10,samples:[{...sample,duration_mins:10,jobs:[{...sample.jobs[0],queue:'gpu_h100',hardware:'h100',duration_mins:10}]}]}},{id:'missing-current-test',label:'Missing recent test',match_status:'unmatched',match_reason:'No exact CUDA counterpart',amd:null,upstream:null}]};
  if(evidenceFormat==='compact') {
    latency.job_columns=['job_id','step_id','url','queue','hardware','raw_name','started_at','finished_at','duration_mins'];
    for(const row of latency.rows) for(const source of [row.amd,row.upstream]) if(source) for(const observation of source.samples) observation.jobs=observation.jobs.map(job=>latency.job_columns.map(column=>job[column]));
  }
  await page.route('**/operations_v2/comparison.json*',route=>route.fulfill({json:{latency}}));
  await page.goto('/?ops_analytics_view=latency#ci-analytics',{waitUntil:'domcontentloaded'});
  const panel=page.locator('#tab-ci-analytics');
  await expect(panel).toContainText('Latest five completed main CI nightlies');
  await expect(panel).toContainText('#30005 · #30004 · #30003 · #30002 · #30001');
  await expect(panel).toContainText('older builds are not substituted');
  await expect(panel).toContainText('2.00×');
  await expect(panel.getByRole('button',{name:'Missing recent test',exact:true})).toBeVisible();
  await panel.getByRole('button',{name:'Current gating test',exact:true}).click();
  const evidence=page.getByRole('dialog').last();
  await expect(evidence).toContainText('longest wall completion time');
  await expect(evidence.locator('tbody tr')).toHaveCount(10);
  await expect(evidence.getByText('Not observed',{exact:true})).toHaveCount(8);
  await expect(evidence.getByRole('link',{name:/^amd_mi300_1 · 20m/})).toHaveAttribute('href',jobUrl);
  await expect(evidence.getByRole('link',{name:/^invalid-/})).toHaveCount(0);
  expect(await evidence.locator('a[href*="buildkite.com"]').evaluateAll(links=>links.every(link=>link.href.includes('/vllm/ci/builds/')))).toBe(true);
});
}
}

test('current AMD mirror inventory separates required optional and soft-fail source modes',async ({page})=>{
  const response=page.waitForResponse(row=>new URL(row.url()).pathname.endsWith('/operations_v2/test_group_parity.json'));
  const requested=[];page.on('request',request=>requested.push(request.url()));
  await page.goto('/?ops_health_view=mirrors#ci-health',{waitUntil:'domcontentloaded'});
  const payload=await (await response).json();const mirror=payload.test_group_parity.mirror_inventory;
  const modes=mirror.rows.reduce((counts,row)=>{counts[row.gate_kind==='soft_fail'||row.soft_fail===true?'soft fail':row.gate_kind==='optional'||row.optional===true?'optional':'required']++;return counts;},{required:0,optional:0,'soft fail':0});
  const health=page.locator('#tab-ci-health');
  await expect(health.locator('.ops-mirror-mode-headline strong')).toHaveText([String(modes.required),String(modes.optional),String(modes['soft fail'])]);
  expect(requested.some(url=>url.includes('capacity_monitor.json'))).toBe(false);
  await health.getByRole('button',{name:`Browse all ${mirror.summary.total} AMD mirrors`,exact:true}).click();
  const browser=page.getByRole('dialog');
  await browser.getByPlaceholder('Filter test group, area, YAML file, device, mode, or key').fill('soft fail');
  await expect(browser.locator('tbody tr')).toHaveCount(modes['soft fail']);
  await expect(browser.locator('tbody tr').first()).toContainText('soft fail');
});

test('offline fixture preserves exact current-CI source and five-nightly evidence contracts',async ({page,request})=>{
  const health=(await (await request.get('/data/vllm/ci/operations_v2/amd_test_health.json')).json()).amd_test_health;
  const parity=(await (await request.get('/data/vllm/ci/operations_v2/test_group_parity.json')).json()).test_group_parity;
  const latency=(await (await request.get('/data/vllm/ci/operations_v2/comparison.json')).json()).latency;
  const nightly=(await (await request.get('/data/vllm/ci/operations_v2/nightly.json')).json()).nightly;
  expect(health.source_pipeline).toBe('ci');expect(health.job_scope).toBe('amd_gpu');
  expect(health.latest_logical_test_groups.available).toBe(true);
  expect(health.latest_logical_test_groups.summary.total).toBe(4);
  expect(parity.source.pipeline).toBe('ci');expect(parity.source.current_definition_commit_sha).toBe('a'.repeat(40));
  expect(parity.mirror_inventory.summary).toMatchObject({total:10,required:8,optional:1,soft_fail:1});
  expect(latency.source_pipeline).toBe('ci');expect(latency.build_limit).toBe(5);
  expect(latency.cohort.nightlies.map(row=>row.number)).toEqual([30005,30004,30003,30002,30001]);
  expect(latency.cohort.nightlies.map(row=>row.created_at.slice(0,10))).toEqual(['2026-10-08','2026-10-07','2026-10-06','2026-10-05','2026-10-04']);
  const basic=latency.rows.find(row=>row.id==='basic models (other)');
  expect(basic.amd.median_duration_mins).toBe(22);expect(basic.upstream.median_duration_mins).toBe(12);
  for(const source of [basic.amd,basic.upstream]) {
    expect(source.sample_count).toBe(5);
    for(const sample of source.samples) for(const job of sample.jobs) expect(job.url).toContain(`/vllm/ci/builds/${sample.build_number}/`);
  }
  expect(nightly.pipelines.map(row=>[row.cohort_id,row.source_pipeline,row.job_scope])).toEqual([['ci-amd','ci','amd_gpu'],['ci-cuda','ci','cuda_gpu']]);
  await page.goto('/?ops_analytics_view=nightlies#ci-analytics',{waitUntil:'domcontentloaded'});
  const panel=page.locator('#tab-ci-analytics');
  await expect(panel).toContainText('#30005');
  await panel.getByRole('button',{name:'CUDA gating jobs',exact:true}).click();
  await expect(panel).toContainText('#30005');
  await expect(page).toHaveURL(/ops_analytics_pipeline=ci-cuda/);
});
