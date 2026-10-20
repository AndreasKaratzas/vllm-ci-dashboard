// CI Analytics "CI agent health": a port of renderAmdAgentHealth and its
// helpers (docs/assets/js/ops-v2.js). Closure state in classic becomes explicit
// options here; the computations keep the classic names and order.
import { duration, integer, shortDate, value, type Rec, type Tone } from './format'
import { isAmdMiHardware, isAmdRuntimeJob } from './health'

export const AGENT_WINDOW_DAYS: Record<string, number> = { '1d': 1, '3d': 3, '7d': 7, '14d': 14, '30d': 30, '60d': 60 }
export const AGENT_WINDOW_IDS = ['1d', '3d', '7d', '14d', '30d', '60d']
export const AGENT_COFAIL_IDS = ['30', '60', '120', '180', '360', '720', '1440']
export const AGENT_SIGNALS = ['infra', 'hard', 'all'] as const
export type AgentSignal = (typeof AGENT_SIGNALS)[number]
export const COFAIL_COMPACT_RUNS = 3
export const AMD_AGENT_HEALTH_ASSET = 'data/vllm/ci/operations_v2/amd_agent_health.json'

export function agentStateColor(stateName: unknown): string {
  const s = String(stateName || '').toLowerCase()
  if (s === 'pass' || s === 'passed') return '#35bb78'
  if (s === 'soft') return '#e3a63a'
  if (s === 'hard') return '#e06464'
  if (s === 'canceled') return '#8a93a0'
  return '#66717d'
}

export function agentTruncate(text: unknown, max: number): string {
  const s = String(text || '')
  return s.length > max ? s.slice(0, max - 1) + '…' : s
}

export function agentNodeLabel(raw: string, hardware: string): string {
  if (!raw || !hardware) return raw
  return raw + ' (' + hardware + ')'
}

export function agentCofailLabel(mins: unknown): string {
  const m = Number(mins) || 0
  return m % 60 === 0 ? m / 60 + 'h' : m + 'm'
}

export function buildkiteJobUrl(pipeline: unknown, build: unknown, jobId: unknown): string {
  if (!pipeline || build === null || build === undefined) return ''
  let url = 'https://buildkite.com/vllm/' + pipeline + '/builds/' + build
  if (jobId) url += '/steps/canvas?jid=' + jobId + '&tab=output'
  return url
}

/** The collector's verdict on what caused a failing attempt (collect_agent_health._attribute_failures). */
export const AGENT_ATTRIBUTIONS = ['node', 'build', 'test', 'infra', 'unknown'] as const
export type AgentAttribution = (typeof AGENT_ATTRIBUTIONS)[number]
export const ATTRIBUTION_LABEL: Record<AgentAttribution, string> = {
  node: 'Node',
  build: 'Build-wide',
  test: 'Test or code',
  infra: 'Shared infrastructure',
  unknown: 'Unexplained',
}
export const ATTRIBUTION_REASON: Record<string, string> = {
  agent_lost: 'The agent died mid-job on this node alone.',
  agent_loss_burst: 'Agents died on three or more nodes within half an hour.',
  job_never_started: 'The job’s containers never started, on this node alone.',
  start_failure_burst: 'Jobs’ containers never started on three or more nodes within half an hour.',
  build_wide: 'The same build failed on four or more other nodes, and the step never passed.',
  build_wide_transient: 'The same build failed broadly, and the step passed on a retry: a passing outage.',
  upstream_build_wide: 'Many of the same build’s CUDA and AMD jobs failed, and the step never passed: the commit is broken on every accelerator.',
  failed_on_retry_elsewhere: 'The automatic retry failed on another node too.',
  upstream_mirror_failed: 'The CUDA job running the same test failed on the same commit, and the AMD job never passed.',
  group_fails_elsewhere: 'The test group also failed on other nodes within a day.',
  group_not_passing_elsewhere: 'The test group passed on no other node within a day.',
  node_repeats: 'Unexplained failures of two or more test groups on this node within three days.',
  isolated: 'An unexplained one-off: the group passes elsewhere and nothing else failed here.',
}

export function agentAttribution(raw: unknown): AgentAttribution {
  return (AGENT_ATTRIBUTIONS as readonly unknown[]).includes(raw) ? (raw as AgentAttribution) : 'unknown'
}

export interface AgentRun {
  node_raw: string
  hardware: string
  pipeline: string
  queue: string
  group: string
  state: string
  nightly: boolean
  /** Attributed to the node or unexplained: the failures the 'infra' signal keeps. */
  node_suspect: boolean
  attribution: AgentAttribution
  confidence: string
  reason: string
  /** Buildkite superseded this attempt with an automatic retry. */
  retried: boolean
  build_canceled: boolean
  build_number: unknown
  url: string
  started_at: string
  day: unknown
  _start: number | null
  _end: number | null
}

export interface CofailEvent {
  node: string
  node_raw: string
  hardware: string
  pattern: 'concurrent' | 'sequential'
  concurrent: boolean
  started_at: string
  _startMs: number
  _endMs: number
  span_mins: number
  run_count: number
  group_count: number
  cross_pipeline: boolean
  pipelines: string[]
  hard_failed: number
  soft_failed: number
  runs: AgentRun[]
}

export function makeCofailEvent(node: string, nodeRaw: string, hardware: string, cluster: AgentRun[]): CofailEvent {
  const starts = cluster.map((r) => r._start as number)
  const ends = cluster.map((r) => (r._end !== null ? r._end : (r._start as number)))
  const startMs = Math.min(...starts)
  const endMs = Math.max(...ends)
  const intervals = cluster
    .map((r) => [r._start as number, r._end !== null ? r._end : (r._start as number)] as const)
    .sort((a, b) => a[0] - b[0])
  let concurrent = false
  for (let i = 1; i < intervals.length; i++) {
    if (intervals[i]![0] < intervals[i - 1]![1]) {
      concurrent = true
      break
    }
  }
  const pipelines = Array.from(new Set(cluster.map((r) => r.pipeline))).sort()
  return {
    node,
    node_raw: nodeRaw,
    hardware,
    pattern: concurrent ? 'concurrent' : 'sequential',
    concurrent,
    started_at: new Date(startMs).toISOString(),
    _startMs: startMs,
    _endMs: endMs,
    span_mins: Math.round(((endMs - startMs) / 60000) * 100) / 100,
    run_count: cluster.length,
    group_count: new Set(cluster.map((r) => r.group)).size,
    cross_pipeline: pipelines.length > 1,
    pipelines,
    hard_failed: cluster.filter((r) => r.state === 'hard').length,
    soft_failed: cluster.filter((r) => r.state === 'soft').length,
    runs: cluster.slice().sort((a, b) => (a._start as number) - (b._start as number)),
  }
}

export function clusterNodeCofailures(node: string, nodeRaw: string, hardware: string, runs: AgentRun[], windowMins: number): CofailEvent[] {
  const failing = runs.filter((r) => r._start !== null).sort((a, b) => (a._start as number) - (b._start as number))
  const windowMs = windowMins * 60000
  const events: CofailEvent[] = []
  let cluster: AgentRun[] = []
  let clusterEnd: number | null = null
  function flush() {
    const byKey = new Map<string, AgentRun>()
    for (const r of cluster) {
      const key = r.pipeline + '\u001f' + r.build_number + '\u001f' + r.group
      const prev = byKey.get(key)
      if (!prev || (r._start as number) > (prev._start as number)) byKey.set(key, r)
    }
    const distinct = Array.from(byKey.values())
    if (distinct.length >= 2) events.push(makeCofailEvent(node, nodeRaw, hardware, distinct))
    cluster = []
  }
  for (const r of failing) {
    const start = r._start as number
    const end = r._end !== null ? r._end : start
    if (clusterEnd !== null && start - clusterEnd > windowMs) {
      flush()
      clusterEnd = null
    }
    cluster.push(r)
    clusterEnd = clusterEnd === null ? end : Math.max(clusterEnd, end)
  }
  flush()
  return events
}

interface AccountingOptions {
  startDay?: string
  hardware?: string
  nightlyOnly?: boolean
  excludeCancelled?: boolean
  signal?: string
}

/** Whether a compact failure-accounting row counts toward the selected failure signal ('node': attributed to the node). */
export function accountingSignal(row: Rec, signal: string | undefined): boolean {
  return (signal === 'infra' && !!row.i) || (signal === 'hard' && row.s === 'hard') || signal === 'all' || (signal === 'node' && row.a === 'node')
}

/** Hard, soft, and signal failure counts per node, or per any other key of the accounting row. */
export function agentFailureAccountingCounts(rows: unknown, options: AccountingOptions, keyOf: (row: Rec) => string = (row) => row.nd) {
  const totals = new Map<string, { hard: number; soft: number; signal: number }>()
  for (const row of (Array.isArray(rows) ? rows : []) as Rec[]) {
    if (String(row.d || '') < String(options.startDay || '')) continue
    if (options.nightlyOnly && !row.ng) continue
    if (options.excludeCancelled && row.bc) continue
    const count = Math.max(0, Number(row.c) || 0)
    if (!count || !row.nd || !['hard', 'soft'].includes(row.s)) continue
    const key = keyOf(row)
    let entry = totals.get(key)
    if (!entry) {
      entry = { hard: 0, soft: 0, signal: 0 }
      totals.set(key, entry)
    }
    entry[row.s as 'hard' | 'soft'] += count
    if (accountingSignal(row, options.signal)) entry.signal += count
  }
  return totals
}

export function agentAggregateRunCounts(rows: unknown, options: AccountingOptions) {
  const totals = { runs: 0, identifiedRuns: 0 }
  for (const row of (Array.isArray(rows) ? rows : []) as Rec[]) {
    if (String(row.d || '') < String(options.startDay || '')) continue
    if (options.hardware && options.hardware !== 'all' && row.h !== options.hardware) continue
    const bucket = options.nightlyOnly ? row.n : row.a
    if (!Array.isArray(bucket) || !Number(bucket[0])) continue
    totals.runs += Math.max(0, Number(bucket[0]) || 0)
    if (row.id) totals.identifiedRuns += Math.max(0, Number(bucket[0]) || 0)
  }
  return totals
}

export function agentAggregateFailureCounts(rows: unknown, options: AccountingOptions) {
  const totals = { hard: 0, soft: 0, signal: 0 }
  for (const row of (Array.isArray(rows) ? rows : []) as Rec[]) {
    if (String(row.d || '') < String(options.startDay || '')) continue
    if (options.hardware && options.hardware !== 'all' && row.h !== options.hardware) continue
    if (options.nightlyOnly && !row.ng) continue
    if (options.excludeCancelled && row.bc) continue
    const count = Math.max(0, Number(row.c) || 0)
    if (!count || !['hard', 'soft'].includes(row.s)) continue
    totals[row.s as 'hard' | 'soft'] += count
    if (accountingSignal(row, options.signal)) totals.signal += count
  }
  return totals
}

function utcClock(raw: unknown): number {
  if (typeof raw !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|\+00:00)$/.test(raw)) return NaN
  const instant = Date.parse(raw)
  return Number.isFinite(instant) && new Date(instant).toISOString().replace('.000Z', 'Z') === raw.replace('+00:00', 'Z') ? instant : NaN
}

const hasOwn = (target: object, key: string) => Object.prototype.hasOwnProperty.call(target, key)

export function agentSourceCoverage(agentHealth: Rec | null | undefined): { start: number; end: number } | null {
  if (
    !agentHealth ||
    !Array.isArray(agentHealth.pipelines) ||
    agentHealth.pipelines.length !== 1 ||
    agentHealth.pipelines[0] !== 'ci' ||
    agentHealth.max_window_days !== 60
  )
    return null
  const retention = agentHealth.retention || {}
  const scope = retention.pipeline_scope || {}
  const states = ['creating', 'scheduled', 'running', 'failing', 'blocked', 'canceling']
  const isCreatedCohort = scope.version === 2 || scope.version === 3
  const isMainCohort = scope.version === 3
  const legs = isCreatedCohort ? ['created'] : ['created', 'older_finished', 'older_active']
  const dayCounts = [retention.original_day_count, retention.retained_day_count, retention.dropped_oldest_day_count]
  if (
    retention.configured_days !== 60 ||
    typeof retention.byte_limited !== 'boolean' ||
    dayCounts.some((count) => !Number.isSafeInteger(count) || count < 0) ||
    retention.original_day_count !== retention.retained_day_count + retention.dropped_oldest_day_count
  )
    return null
  if (
    (scope.version !== 1 && !isCreatedCohort) ||
    !Number.isInteger(scope.requested_days) ||
    scope.requested_days < 1 ||
    scope.requested_days > 60 ||
    scope.basis !== (isCreatedCohort ? 'terminal_jobs_by_build_created_at' : 'terminal_jobs_by_started_at') ||
    scope.exhaustive !== true ||
    scope.attempt_policy !== 'every_executed_attempt' ||
    scope.terminal_time_policy !== 'finished_at_or_terminal_build_bound_for_canceled' ||
    typeof scope.complete_window !== 'boolean' ||
    !scope.discovery_legs ||
    typeof scope.discovery_legs !== 'object' ||
    Array.isArray(scope.discovery_legs) ||
    Object.keys(scope.discovery_legs).length !== legs.length ||
    legs.some((leg) => scope.discovery_legs[leg] !== true)
  )
    return null
  if (isCreatedCohort) {
    if (
      scope.eligible_completion !== 'current_ci_build_creation_cohort_with_provable_completion' ||
      scope.day_basis !== 'build_created_at_utc' ||
      hasOwn(scope, 'finished_job_source') ||
      hasOwn(scope, 'active_build_states')
    )
      return null
  } else if (
    !Array.isArray(scope.active_build_states) ||
    scope.active_build_states.length !== states.length ||
    new Set(scope.active_build_states).size !== states.length ||
    states.some((status) => !scope.active_build_states.includes(status)) ||
    hasOwn(scope, 'eligible_completion') ||
    hasOwn(scope, 'day_basis') ||
    hasOwn(scope, 'finished_job_source')
  )
    return null
  if (isMainCohort) {
    if (
      scope.branch !== 'main' ||
      !Array.isArray(agentHealth.branches) ||
      agentHealth.branches.length !== 1 ||
      agentHealth.branches[0] !== 'main' ||
      agentHealth.job_scope !== 'amd_gpu' ||
      agentHealth.hardware_scope !== 'amd_mi_gpu'
    )
      return null
  } else if (hasOwn(scope, 'branch') || hasOwn(agentHealth, 'branches')) return null
  const start = utcClock(scope.collected_from)
  const end = utcClock(scope.collected_to)
  const clock = utcClock(agentHealth.generated_at)
  if (!Number.isFinite(start) || !Number.isFinite(end) || end !== clock || start >= end) return null
  const expectedStart = new Date(end - scope.requested_days * 86400000)
  expectedStart.setUTCHours(0, 0, 0, 0)
  const expectedComplete = scope.requested_days === 60 && retention.dropped_oldest_day_count === 0
  if (start !== expectedStart.getTime() || scope.complete_window !== expectedComplete) return null
  return { start, end }
}

export function agentSourceHistoryComplete(agentHealth: Rec | null | undefined, startDay?: string): boolean {
  const retention = (agentHealth || {}).retention || {}
  const scope = retention.pipeline_scope || {}
  const coverage = agentSourceCoverage(agentHealth)
  const start = typeof startDay === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(startDay) ? Date.parse(startDay + 'T00:00:00Z') : NaN
  const scopeComplete =
    coverage &&
    (startDay === undefined
      ? scope.complete_window
      : Number.isFinite(start) && new Date(start).toISOString().slice(0, 10) === startDay && start >= coverage.start && start < coverage.end)
  return (
    Boolean(scopeComplete) &&
    retention.byte_limited === false &&
    retention.dropped_oldest_day_count === 0 &&
    retention.original_day_count === retention.retained_day_count
  )
}

export function agentPipelineScopeLabel(agentHealth: Rec | null | undefined): string {
  if (
    Array.isArray((agentHealth || {}).pipelines) &&
    agentHealth!.pipelines.length === 1 &&
    agentHealth!.pipelines[0] === 'ci' &&
    Array.isArray(agentHealth!.branches) &&
    agentHealth!.branches.length === 1 &&
    agentHealth!.branches[0] === 'main'
  ) {
    return 'the ci pipeline on branch main'
  }
  const pipelines = Array.from(
    new Set(
      (Array.isArray((agentHealth || {}).pipelines) ? agentHealth!.pipelines : [])
        .filter((pipeline: unknown) => typeof pipeline === 'string' && pipeline.trim())
        .map((pipeline: string) => pipeline.trim()),
    ),
  )
  return pipelines.length ? 'the ' + pipelines.join(' and ') + (pipelines.length === 1 ? ' pipeline' : ' pipelines') : 'the declared pipeline scope'
}

/** The classic gate: only the current ci/main AMD MI GPU cohort renders. */
export function agentHealthScopeValid(agentHealth: Rec | null | undefined): boolean {
  const health = agentHealth || {}
  const scopeRows = ['node_days', 'failing_runs', 'failure_accounting', 'node_accounting_totals', 'failure_accounting_totals']
  const declaredScope = (health.retention || {}).pipeline_scope || {}
  return !(
    health.hardware_scope !== 'amd_mi_gpu' ||
    health.job_scope !== 'amd_gpu' ||
    !Array.isArray(health.pipelines) ||
    health.pipelines.length !== 1 ||
    health.pipelines[0] !== 'ci' ||
    !Array.isArray(health.branches) ||
    health.branches.length !== 1 ||
    health.branches[0] !== 'main' ||
    declaredScope.version !== 3 ||
    declaredScope.branch !== 'main' ||
    scopeRows.some((key) => (health[key] || []).some((row: Rec) => !isAmdMiHardware(row.h))) ||
    (health.failing_runs || []).some((row: Rec) => row.p !== 'ci' || !isAmdRuntimeJob({ queue: row.q, name: row.g }))
  )
}

export const AGENT_UNAVAILABLE_TEXT = 'AMD MI agent-health data is unavailable. Current ci/main MI GPU cohort observations are required.'

export interface AgentModel {
  health: Rec
  nodeDays: Rec[]
  failureAccounting: Rec[]
  nodeAccountingTotals: Rec[]
  failureAccountingTotals: Rec[]
  operationsNodeRetention: Rec
  operationsAccountingRetention: Rec
  sourceRetention: Rec
  sourcePipelineScope: Rec
  sourceCoverage: { start: number; end: number } | null
  buildCreatedCohort: boolean
  pipelineHistoryIncomplete: boolean
  aggregateAccountingComplete: boolean
  evidenceRetention: Rec
  evidenceIncomplete: boolean
  hardwareTypes: string[]
  windowOptions: unknown[]
  cofailOptions: unknown[]
  endMs: number
  failing: AgentRun[]
  /** The page notes, computed once from the source-wide history state as classic does. */
  completionScopeNote: string | null
  retentionNote: { title: string; body: string } | null
  rules: AttributionRules
}

/** The collector's published attribution thresholds (see AgentAbout). */
export interface AttributionRules {
  buildWideOtherNodes: number
  upstreamSharePct: number
  upstreamMinJobs: number
  outlierFailures: number
  recurrenceHours: number
  repeatHours: number
  repeatGroups: number
  burstNodes: number
  burstMinutes: number
}

function attributionRules(raw: unknown): AttributionRules {
  const rules = (raw && typeof raw === 'object' ? raw : {}) as Rec
  const count = (value: unknown, fallback: number) => (Number.isSafeInteger(value) && (value as number) > 0 ? (value as number) : fallback)
  const share = (value: unknown, fallback: number) =>
    typeof value === 'number' && value > 0 && value <= 1 ? Math.round(value * 100) : fallback
  return {
    buildWideOtherNodes: count(rules.build_wide_min_other_nodes, 4),
    upstreamSharePct: share(rules.upstream_build_wide_min_share, 30),
    upstreamMinJobs: count(rules.upstream_min_jobs, 20),
    outlierFailures: count(rules.outlier_min_failures, 3),
    recurrenceHours: count(rules.recurrence_window_hours, 24),
    repeatHours: count(rules.node_repeat_window_hours, 72),
    repeatGroups: count(rules.node_repeat_min_groups, 2),
    burstNodes: count(rules.burst_min_nodes, 3),
    burstMinutes: count(rules.burst_window_minutes, 30),
  }
}

export function agentHealthModel(agentHealth: Rec): AgentModel {
  const nodeDays = Array.isArray(agentHealth.node_days) ? agentHealth.node_days : []
  const failingRaw: Rec[] = Array.isArray(agentHealth.failing_runs) ? agentHealth.failing_runs : []
  const failureAccounting = Array.isArray(agentHealth.failure_accounting) ? agentHealth.failure_accounting : []
  const nodeAccountingTotals = Array.isArray(agentHealth.node_accounting_totals) ? agentHealth.node_accounting_totals : []
  const failureAccountingTotals = Array.isArray(agentHealth.failure_accounting_totals) ? agentHealth.failure_accounting_totals : []
  const operationsRetention = (agentHealth || {}).operations_publication_retention || {}
  const operationsNodeRetention = operationsRetention.node_days || {}
  const operationsAccountingRetention = operationsRetention.failure_accounting || {}
  const sourceRetention = (agentHealth || {}).retention || {}
  const sourcePipelineScope = sourceRetention.pipeline_scope || {}
  const sourceCoverage = agentSourceCoverage(agentHealth)
  const buildCreatedCohort =
    sourcePipelineScope.version === 3 &&
    sourcePipelineScope.basis === 'terminal_jobs_by_build_created_at' &&
    sourcePipelineScope.day_basis === 'build_created_at_utc'
  const pipelineHistoryIncomplete = !sourceCoverage || sourcePipelineScope.complete_window === false
  const sourceHistoryIncomplete = !agentSourceHistoryComplete(agentHealth)
  const nodeDetailIncomplete = operationsNodeRetention.complete === false || sourceHistoryIncomplete
  const failureDetailIncomplete = operationsAccountingRetention.complete === false || sourceHistoryIncomplete
  const aggregateAccountingComplete = operationsRetention.aggregate_accounting_complete === true
  const evidenceRetention = ((agentHealth || {}).retention || {}).failure_evidence || {}
  const evidenceIncomplete =
    evidenceRetention.complete_relative_to_source === false || (operationsRetention.failure_evidence || {}).complete === false
  const hardwareTypes: string[] = Array.isArray(agentHealth.hardware_types) ? agentHealth.hardware_types.filter(isAmdMiHardware) : []
  const windowOptions = Array.isArray(agentHealth.window_options) ? agentHealth.window_options : [1, 3, 7, 14, 30, 60]
  const cofailOptions = Array.isArray(agentHealth.cofailure_window_options)
    ? agentHealth.cofailure_window_options
    : [30, 60, 120, 180, 360, 720, 1440]
  const endMs = new Date(agentHealth.generated_at || Date.now()).getTime()
  const failing: AgentRun[] = failingRaw.map((r) => {
    const start = Date.parse(r.t)
    const end = Date.parse(r.e)
    return {
      node_raw: r.nd,
      hardware: r.h,
      pipeline: r.p,
      queue: r.q,
      group: r.g,
      state: r.s,
      nightly: !!r.ng,
      node_suspect: !!r.i,
      attribution: agentAttribution(r.a),
      confidence: typeof r.ac === 'string' ? r.ac : 'low',
      reason: typeof r.ar === 'string' ? r.ar : 'isolated',
      retried: !!r.rt,
      build_canceled: !!r.bc,
      build_number: r.b,
      url: buildkiteJobUrl(r.p, r.b, r.j),
      started_at: r.t,
      day: r.d,
      _start: Number.isFinite(start) ? start : null,
      _end: Number.isFinite(end) ? end : null,
    }
  })

  const scopeLabel = agentPipelineScopeLabel(agentHealth)
  const completionScopeNote =
    sourceCoverage && buildCreatedCohort
      ? 'Terminal runs from CI main builds created in the selected UTC window. Runs require a recorded or bounded completion time. Fresh build creation coverage for ' +
        scopeLabel +
        ', from ' +
        value(sourcePipelineScope.collected_from) +
        ' through ' +
        value(sourcePipelineScope.collected_to) +
        ' UTC.'
      : null
  let retentionNote: AgentModel['retentionNote'] = null
  if (evidenceIncomplete || nodeDetailIncomplete || failureDetailIncomplete) {
    retentionNote = {
      title: !sourceCoverage
        ? 'Complete agent-health history is unavailable. '
        : pipelineHistoryIncomplete
          ? 'Agent-health pipeline history is incomplete. '
          : 'Agent-health drill-down evidence is storage-bounded. ',
      body:
        integer(evidenceRetention.published) +
        ' of ' +
        integer(evidenceRetention.source) +
        ' failing-run links and ' +
        integer(operationsNodeRetention.published) +
        ' of ' +
        integer(operationsNodeRetention.source) +
        ' node-day rows are published in Operations. ' +
        (!sourceCoverage
          ? 'Retained counts describe observed runs; node-specific failure rates are unavailable. '
          : pipelineHistoryIncomplete
            ? 'Complete fresh UTC history for ' +
              scopeLabel +
              ' begins ' +
              value(sourcePipelineScope.collected_from) +
              ' and ends ' +
              value(sourcePipelineScope.collected_to) +
              '; the full ' +
              integer(agentHealth.max_window_days || 60) +
              '-day window is not yet complete. Older retained history describes observed runs. '
            : sourceHistoryIncomplete
              ? 'The source ledger dropped ' +
                integer(sourceRetention.dropped_oldest_day_count) +
                ' oldest UTC days and begins ' +
                value(sourceRetention.retained_start) +
                '; compact accounting is exact only for that retained suffix. '
              : 'Exact date/hardware ledger totals remain available through compact accounting. ') +
        (pipelineHistoryIncomplete
          ? 'For selections outside the complete fresh UTC interval or with omitted rows, '
          : 'When node detail or source history is incomplete, ') +
        'node counts and node-specific failure rates are unavailable; table counts, timelines, distinct groups, and co-failure events are retained-evidence lower bounds.',
    }
  }
  return {
    health: agentHealth,
    nodeDays,
    failureAccounting,
    nodeAccountingTotals,
    failureAccountingTotals,
    operationsNodeRetention,
    operationsAccountingRetention,
    sourceRetention,
    sourcePipelineScope,
    sourceCoverage,
    buildCreatedCohort,
    pipelineHistoryIncomplete,
    aggregateAccountingComplete,
    evidenceRetention,
    evidenceIncomplete,
    hardwareTypes,
    windowOptions,
    cofailOptions,
    endMs,
    failing,
    completionScopeNote,
    retentionNote,
    rules: attributionRules(agentHealth.attribution_rules),
  }
}

/** The classic route state for agent health (?ops_agent_* / ?agent_*), after syncRouteState validation. */
export interface AgentRouteState {
  window?: string | null
  gpu?: string | null
  node?: string | null
  cofail?: string | null
  exclCancel?: string | null
  nightly?: string | null
  signal?: string | null
}

export interface AgentOptions {
  windowId: string
  gpu: string
  search: string
  selectedNode: string
  cofailMins: number
  excludeCancelled: boolean
  nightlyOnly: boolean
  signal: AgentSignal
}

function synced(raw: string | null | undefined, allowed: string[] | null, fallback: string): string {
  return raw && (!allowed || allowed.includes(raw)) ? raw : fallback
}

export function agentOptions(model: AgentModel, route: AgentRouteState, search = ''): AgentOptions {
  const health = model.health
  const agentWindow = synced(route.window, AGENT_WINDOW_IDS, '7d')
  const agentGpu = synced(route.gpu, null, 'all')
  const agentCofail = synced(route.cofail, AGENT_COFAIL_IDS, '180')
  const agentSignal = synced(route.signal, [...AGENT_SIGNALS], 'infra')
  const m = parseInt(agentCofail, 10)
  return {
    windowId: AGENT_WINDOW_DAYS[agentWindow] ? agentWindow : (health.default_window_days || 7) + 'd',
    gpu: agentGpu === 'all' || model.hardwareTypes.includes(agentGpu) ? agentGpu : 'all',
    search,
    selectedNode: synced(route.node, null, ''),
    cofailMins: model.cofailOptions.includes(m) ? m : health.default_cofailure_window_mins || 180,
    excludeCancelled: synced(route.exclCancel, ['0', '1'], '1') !== '0',
    nightlyOnly: synced(route.nightly, ['0', '1'], '0') === '1',
    signal: (AGENT_SIGNALS as readonly string[]).includes(agentSignal) ? (agentSignal as AgentSignal) : 'infra',
  }
}

export interface AgentRow {
  node: string
  node_raw: string
  hardware: string
  identified: boolean
  runs: number
  failures: number
  soft: number
  hard: number
  canceled: number
  incident_rate: number
  /** Failures matching the selected signal. */
  signal_failures: number
  distinct_groups: number
  cofailure_event_count: number
  _runs: AgentRun[]
}

export interface AgentView {
  agents: AgentRow[]
  events: CofailEvent[]
  fRuns: AgentRun[]
  totalRuns: number
  identifiedRuns: number
  aggregateFailures: { hard: number; soft: number; signal: number }
  exactAggregateScope: boolean
  nodeDetailComplete: boolean
  failureDetailComplete: boolean
  /** Window-scoped history flags (classic recomputes its closure variables here). */
  sourceHistoryIncomplete: boolean
  nodeDetailIncomplete: boolean
  failureDetailIncomplete: boolean
}

export function matchesFilter(options: Pick<AgentOptions, 'gpu' | 'search'>, hardware: unknown, nodeRaw: unknown): boolean {
  const term = options.search.toLowerCase()
  if (options.gpu !== 'all' && hardware !== options.gpu) return false
  if (term && String(nodeRaw || '').toLowerCase().indexOf(term) === -1 && String(hardware || '').toLowerCase().indexOf(term) === -1)
    return false
  return true
}

export function signalMatch(signal: AgentSignal, r: AgentRun): boolean {
  if (signal === 'infra') return r.node_suspect
  if (signal === 'hard') return r.state === 'hard'
  return true
}

export function signalTerm(signal: AgentSignal): string {
  return signal === 'infra' ? 'node-suspect' : signal === 'hard' ? 'hard' : 'all'
}

export function signalColumnLabel(signal: AgentSignal): string {
  return signal === 'infra' ? 'Node-suspect failures' : 'Hard failures'
}

export function signalCaption(signal: AgentSignal): string {
  if (signal === 'infra')
    return 'Showing node-suspect failures — attributed to the node, or unexplained — without build-wide, test-level and shared-infrastructure failures.'
  if (signal === 'hard') return 'Showing every hard failure on the node — not just the node-suspect subset.'
  return 'Showing all failures (hard + soft) on the node.'
}

/** The first UTC day of the selected window. */
function agentWindowStartDay(model: AgentModel, windowId: string): string {
  return new Date(model.endMs - (AGENT_WINDOW_DAYS[windowId] || 7) * 86400000).toISOString().slice(0, 10)
}

/** Whether a retained failing run passes every page filter except the failure signal. */
function agentRunInScope(model: AgentModel, options: AgentOptions): (run: AgentRun) => boolean {
  const { endMs, buildCreatedCohort } = model
  const startMs = endMs - (AGENT_WINDOW_DAYS[options.windowId] || 7) * 86400000
  const startDay = agentWindowStartDay(model, options.windowId)
  function runInWindow(run: AgentRun) {
    if (run._start === null || run._start > endMs) return false
    if (!buildCreatedCohort) return run._start >= startMs
    const day = typeof run.day === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(run.day) ? Date.parse(run.day + 'T00:00:00Z') : NaN
    return Number.isFinite(day) && new Date(day).toISOString().slice(0, 10) === run.day && (run.day as string) >= startDay && day <= endMs
  }
  return (run) =>
    runInWindow(run) &&
    !(options.nightlyOnly && !run.nightly) &&
    !(options.excludeCancelled && run.build_canceled) &&
    matchesFilter(options, run.hardware, run.node_raw)
}

/** Retained failing runs under every page filter, whatever the failure signal. */
export function agentScopedFailures(model: AgentModel, options: AgentOptions): AgentRun[] {
  const inScope = agentRunInScope(model, options)
  return model.failing.filter((run) => (run.state === 'hard' || run.state === 'soft') && inScope(run))
}

/** node_days rows in the window and filters that have runs, with the bucket of the selected build scope. */
function agentScopedNodeDays(model: AgentModel, options: AgentOptions): { nd: Rec; bucket: Rec }[] {
  const startDay = agentWindowStartDay(model, options.windowId)
  const out: { nd: Rec; bucket: Rec }[] = []
  for (const nd of model.nodeDays) {
    if (String(nd.d) < startDay || !matchesFilter(options, nd.h, nd.nd)) continue
    const bucket = options.nightlyOnly ? nd.n : nd.a
    if (bucket && bucket[0]) out.push({ nd, bucket })
  }
  return out
}

export function computeAgentView(model: AgentModel, options: AgentOptions): AgentView {
  const { health, failing, failureAccounting, nodeAccountingTotals, failureAccountingTotals } = model
  const { gpu, search, nightlyOnly, excludeCancelled, signal, cofailMins } = options
  const startDay = agentWindowStartDay(model, options.windowId)
  const inScope = agentRunInScope(model, options)
  const sourceHistoryIncomplete = !agentSourceHistoryComplete(health, startDay)
  const nodeDetailIncomplete = model.operationsNodeRetention.complete === false || sourceHistoryIncomplete
  const failureDetailIncomplete = model.operationsAccountingRetention.complete === false || sourceHistoryIncomplete
  const filter = (hardware: unknown, nodeRaw: unknown) => matchesFilter(options, hardware, nodeRaw)

  const byNode = new Map<string, { node_raw: string; hardware: string; runs: number; soft: number; hard: number; canceled: number }>()
  let totalRuns = 0
  let identifiedRuns = 0
  for (const { nd, bucket } of agentScopedNodeDays(model, options)) {
    const split = bucket.length >= 4
    const soft = split ? bucket[1] || 0 : 0
    const hard = split ? bucket[2] || 0 : bucket[1] || 0
    const canceled = split ? bucket[3] || 0 : bucket[2] || 0
    totalRuns += bucket[0]
    if (nd.nd !== '(unidentified)') identifiedRuns += bucket[0]
    let agg = byNode.get(nd.nd)
    if (!agg) {
      agg = { node_raw: nd.nd, hardware: nd.h, runs: 0, soft: 0, hard: 0, canceled: 0 }
      byNode.set(nd.nd, agg)
    }
    agg.runs += bucket[0]
    agg.soft += soft
    agg.hard += hard
    agg.canceled += canceled
    if (nd.h) agg.hardware = nd.h
  }
  const aggregateRuns = agentAggregateRunCounts(nodeAccountingTotals, { startDay, hardware: gpu, nightlyOnly })
  const aggregateFailures = agentAggregateFailureCounts(failureAccountingTotals, {
    startDay,
    hardware: gpu,
    nightlyOnly,
    excludeCancelled,
    signal,
  })
  const exactAggregateScope =
    model.aggregateAccountingComplete &&
    !sourceHistoryIncomplete &&
    nodeAccountingTotals.length > 0 &&
    failureAccountingTotals.length > 0 &&
    !search
  if (nodeDetailIncomplete && exactAggregateScope) {
    totalRuns = aggregateRuns.runs
    identifiedRuns = aggregateRuns.identifiedRuns
  }

  const fRuns = failing.filter((r) => signalMatch(signal, r) && inScope(r))
  const runsByNode = new Map<string, AgentRun[]>()
  for (const r of fRuns) {
    if (!runsByNode.has(r.node_raw)) runsByNode.set(r.node_raw, [])
    runsByNode.get(r.node_raw)!.push(r)
  }
  const accountingRows = failureAccounting.filter((row) => filter(row.h, row.nd))
  const accountedByNode = agentFailureAccountingCounts(accountingRows, { startDay, nightlyOnly, excludeCancelled, signal })
  const failByNode = new Map<string, { hard: number; soft: number }>()
  const signalByNode: Record<string, number> = {}
  if (failureAccounting.length) {
    accountedByNode.forEach((entry, nodeRaw) => {
      failByNode.set(nodeRaw, { hard: entry.hard, soft: entry.soft })
      signalByNode[nodeRaw] = entry.signal
    })
  } else {
    for (const r of failing) {
      if (!inScope(r)) continue
      let f = failByNode.get(r.node_raw)
      if (!f) {
        f = { hard: 0, soft: 0 }
        failByNode.set(r.node_raw, f)
      }
      if (r.state === 'hard') f.hard += 1
      else if (r.state === 'soft') f.soft += 1
    }
  }
  const events: CofailEvent[] = []
  const groupsByNode: Record<string, number> = {}
  runsByNode.forEach((runs, nodeRaw) => {
    const hardware = (byNode.get(nodeRaw) || ({} as Rec)).hardware || (runs[0] && runs[0].hardware) || ''
    if (!failureAccounting.length) signalByNode[nodeRaw] = runs.length
    groupsByNode[nodeRaw] = new Set(runs.map((x) => x.group)).size
    for (const e of clusterNodeCofailures(agentNodeLabel(nodeRaw, hardware), nodeRaw, hardware, runs, cofailMins)) events.push(e)
  })
  const eventsByNode: Record<string, number> = {}
  for (const e of events) eventsByNode[e.node_raw] = (eventsByNode[e.node_raw] || 0) + 1

  const agents: AgentRow[] = []
  byNode.forEach((agg, nodeRaw) => {
    const identified = nodeRaw !== '(unidentified)'
    const f = failByNode.get(nodeRaw) || { hard: 0, soft: 0 }
    const failures = f.hard + f.soft
    const denom = excludeCancelled ? Math.max(0, agg.runs - agg.canceled) : agg.runs
    agents.push({
      node: identified ? agentNodeLabel(nodeRaw, agg.hardware) : nodeRaw,
      node_raw: nodeRaw,
      hardware: agg.hardware,
      identified,
      runs: denom,
      failures,
      soft: f.soft,
      hard: f.hard,
      canceled: agg.canceled,
      incident_rate: denom ? failures / denom : 0,
      signal_failures: signalByNode[nodeRaw] || 0,
      distinct_groups: groupsByNode[nodeRaw] || 0,
      cofailure_event_count: eventsByNode[nodeRaw] || 0,
      _runs: runsByNode.get(nodeRaw) || [],
    })
  })

  events.sort(
    (a, b) =>
      b.hard_failed - a.hard_failed ||
      Number(b.cross_pipeline) - Number(a.cross_pipeline) ||
      b.group_count - a.group_count ||
      String(b.started_at).localeCompare(String(a.started_at)),
  )
  return {
    agents,
    events,
    fRuns,
    totalRuns,
    identifiedRuns,
    aggregateFailures,
    exactAggregateScope,
    nodeDetailComplete: !nodeDetailIncomplete,
    failureDetailComplete: !failureDetailIncomplete,
    sourceHistoryIncomplete,
    nodeDetailIncomplete,
    failureDetailIncomplete,
  }
}

export interface AgentKpi {
  id: string
  label: string
  value: string
  meta: string
  tone: Tone
}

export function agentKpis(model: AgentModel, view: AgentView, options: Pick<AgentOptions, 'windowId' | 'signal'>): AgentKpi[] {
  const identifiedNodes = view.agents.filter((a) => a.identified).length
  const unreliable = view.agents.filter((a) => a.identified && a.signal_failures > 0).length
  const coveragePct = view.totalRuns ? (100 * view.identifiedRuns) / view.totalRuns : 0
  const concurrent = view.events.filter((e) => e.concurrent).length
  const cross = view.events.filter((e) => e.cross_pipeline).length
  const nodeMetricsAvailable = view.nodeDetailComplete
  const coverageAvailable = view.nodeDetailComplete || view.exactAggregateScope
  return [
    {
      id: 'agent-nodes',
      label: 'IDENTIFIED AMD NODES',
      value: nodeMetricsAvailable ? integer(identifiedNodes) : 'Unavailable',
      meta: coverageAvailable ? integer(view.totalRuns) + ' exact runs in ' + options.windowId : 'node detail is incomplete',
      tone: 'info',
    },
    {
      id: 'agent-unreliable',
      label: 'NODES WITH FAILURES',
      value: nodeMetricsAvailable && view.failureDetailComplete ? integer(unreliable) : 'Unavailable',
      meta: view.exactAggregateScope
        ? integer(view.aggregateFailures.signal) + ' exact ' + signalTerm(options.signal) + ' failures in aggregate'
        : 'node-specific accounting is incomplete',
      tone: unreliable ? 'soft' : 'pass',
    },
    {
      id: 'agent-coverage',
      label: 'NODE COVERAGE',
      value: coverageAvailable ? coveragePct.toFixed(1) + '%' : 'Unavailable',
      meta: coverageAvailable
        ? integer(view.identifiedRuns) + ' / ' + integer(view.totalRuns) + ' exact runs identified'
        : 'aggregate accounting unavailable',
      tone: coverageAvailable && coveragePct >= 50 ? 'pass' : coverageAvailable && coveragePct > 0 ? 'soft' : 'hard',
    },
    {
      id: 'agent-cofail',
      label: 'CO-FAILURE EVENTS',
      value: (model.evidenceIncomplete ? '≥' : '') + integer(view.events.length),
      meta: integer(concurrent) + ' retained concurrent · ' + integer(cross) + ' retained cross-pipeline',
      tone: view.events.length ? 'hard' : 'pass',
    },
  ]
}

export interface AgentCells {
  runs: string
  failRate: string
  failures: string
  split: string | null
  signal: string
  groups: string
  cofail: string
}

export function agentCells(model: AgentModel, view: AgentView, row: AgentRow): AgentCells {
  return {
    runs: (view.nodeDetailIncomplete ? '≥' : '') + integer(row.runs),
    failRate: view.nodeDetailIncomplete || view.failureDetailIncomplete ? 'Unavailable' : (Number(row.incident_rate) * 100).toFixed(1) + '%',
    failures: (view.failureDetailIncomplete ? '≥' : '') + integer(row.failures),
    split: row.hard || row.soft ? integer(row.hard) + ' hard · ' + integer(row.soft) + ' soft' : null,
    signal: (view.failureDetailIncomplete ? '≥' : '') + integer(row.signal_failures),
    groups: (model.evidenceIncomplete ? '≥' : '') + integer(row.distinct_groups),
    cofail: (model.evidenceIncomplete ? '≥' : '') + integer(row.cofailure_event_count),
  }
}

export function agentTableDescription(model: AgentModel, view: AgentView, options: Pick<AgentOptions, 'windowId' | 'signal'>): string {
  const count = integer(view.agents.length) + ' node(s) in ' + options.windowId
  const base = view.sourceHistoryIncomplete
    ? 'Only available source history is shown. Compact accounting covers that retained interval; node counts and node-specific Fail % are unavailable, and displayed counts are lower bounds. Groups, Co-fail, timelines, and linked Evidence use retained exact rows.'
    : view.nodeDetailIncomplete || view.failureDetailIncomplete
      ? 'Operations retained only a bounded suffix of node-granular rows. Exact date/hardware run and failure totals remain in compact accounting, but node counts and node-specific Fail % are unavailable; displayed node counts are lower bounds. Groups, Co-fail, timelines, and linked Evidence use retained exact rows.'
      : (model.buildCreatedCohort
          ? 'Terminal runs from CI builds created in the selected UTC window form the run and failure-rate denominator. Actual job timestamps remain in timelines. '
          : 'Runs come from the node_days rollup of every build. ') +
        'Test group failures and Fail % use complete compact failure accounting (with the hard/soft split) and honor the cancelled-build toggle. Groups, Co-fail, timelines, and linked Evidence use the retained exact-row evidence and may be lower bounds when its storage retention note is shown.'
  const tail = ' Click a node (or Evidence) to load its timeline; sort by any column.'
  if (options.signal === 'infra') {
    return (
      count +
      ', sorted by node-suspect failures. The Node-suspect failures column is the subset of Test group failures attributed to the node or unexplained — not build-wide, not failing on other nodes, not a shared-infrastructure event. ' +
      base +
      tail
    )
  }
  if (options.signal === 'hard') {
    return count + ', sorted by hard failures. The Hard failures column is the hard-only subset of Test group failures (soft failures excluded). ' + base + tail
  }
  return (
    count +
    ', sorted by Test group failures. With every failure counted, the total already carries the signal, so no separate signal column is shown. ' +
    base +
    tail
  )
}

export function agentEmptyText(model: AgentModel, view: AgentView): string | null {
  if (!model.nodeDays.length) return 'No AMD node data has been collected yet. It populates as collect_agent_health.py captures the Buildkite agent k8s:node tag.'
  if (!view.agents.length) return 'No AMD runs match the current window and filter.'
  return null
}

export interface TimelineRow {
  run: AgentRun
  start: number
  end: number
  mins: number
  label: string
  color: string
}

export interface TimelineRange {
  min: number
  max: number
}

/** drawAgentTimeline's data: rows (filtered by an optional zoom range), x domain, and the full data bounds. */
export function agentTimeline(nodeRuns: AgentRun[] | null | undefined, range: TimelineRange | null) {
  const allRows = (nodeRuns || [])
    .slice()
    .filter((run) => run._start !== null)
    .map((run) => {
      const start = run._start as number
      const end = run._end !== null && run._end > start ? run._end : start + 60000
      return { run, start, end, mins: (end - start) / 60000 }
    })
    .sort((a, b) => a.start - b.start)
  if (!allRows.length) return { rows: [] as TimelineRow[], xMin: null, xMax: null, bounds: null }
  const dataMin = Math.min(...allRows.map((row) => row.start))
  const dataMax = Math.max(...allRows.map((row) => row.end))
  const rows = range ? allRows.filter((row) => row.end >= range.min && row.start <= range.max) : allRows
  const pad = Math.max(60000, (dataMax - dataMin) * 0.02)
  return {
    rows: rows.map((row, index) => ({
      ...row,
      label: index + 1 + '. ' + agentTruncate(row.run.group, 34),
      color: agentStateColor(row.run.state),
    })),
    xMin: range ? range.min : dataMin - pad,
    xMax: range ? range.max : dataMax + pad,
    bounds: { min: dataMin, max: dataMax } as TimelineRange,
  }
}

export function clampTimelineRange(min: number, max: number, bounds: TimelineRange | null): TimelineRange {
  if (max - min < 60000) {
    const c = (min + max) / 2
    min = c - 30000
    max = c + 30000
  }
  if (bounds) {
    const fpad = Math.max(60000, (bounds.max - bounds.min) * 0.02)
    min = Math.max(min, bounds.min - fpad)
    max = Math.min(max, bounds.max + fpad)
  }
  return { min, max }
}

export function zoomTimeline(range: TimelineRange | null, bounds: TimelineRange | null, factor: number): TimelineRange | null {
  const r = range || (bounds ? { min: bounds.min, max: bounds.max } : null)
  if (!r) return null
  const center = (r.min + r.max) / 2
  const half = ((r.max - r.min) / 2) * factor
  return clampTimelineRange(center - half, center + half, bounds)
}

export function eventTimelineRange(event: CofailEvent): TimelineRange {
  const pad = Math.max(60000, (event._endMs - event._startMs) * 0.15)
  return { min: event._startMs - pad, max: event._endMs + pad }
}

export function eventKey(e: CofailEvent): string {
  return e.node_raw + '@' + e._startMs
}

export function eventsSection(view: AgentView, options: Pick<AgentOptions, 'windowId' | 'signal' | 'cofailMins'>, eventNodeFilter: string) {
  const filtered = eventNodeFilter ? view.events.filter((e) => e.node_raw === eventNodeFilter) : view.events
  const scoped = !!eventNodeFilter
  return {
    scoped,
    title: scoped ? 'Co-failure events · one node' : 'Co-failure events',
    description: scoped
      ? integer(filtered.length) +
        ' event(s) on this node in ' +
        options.windowId +
        '. Click an event to expand it (full runs + per-run timing) and zoom the timeline to it.'
      : integer(view.events.length) +
        ' event(s) in ' +
        options.windowId +
        '. Two or more ' +
        signalTerm(options.signal) +
        ' failures on one node within ' +
        agentCofailLabel(options.cofailMins) +
        ' (retries of the same test group within a build count once): "concurrent" (overlapping) points to contention or an ephemeral fault; "sequential" (back-to-back) suggests the node was left unclean. Click an event to expand it and zoom the timeline to it.',
    empty: scoped ? 'No co-failure events on this node in the current window and filter.' : 'No co-failure events in this window and filter.',
    events: filtered.slice(0, scoped ? 200 : 80),
  }
}

export function cofailureCard(event: CofailEvent, selected: boolean) {
  const concurrent = event.pattern === 'concurrent' || event.concurrent
  const allRuns = event.runs || []
  const shown = selected ? allRuns : allRuns.slice(0, COFAIL_COMPACT_RUNS)
  return {
    node: value(event.node),
    meta: shortDate(event.started_at) + ' · ' + duration(event.span_mins) + ' span · ' + integer(event.group_count) + ' groups',
    pattern: concurrent ? 'concurrent' : 'sequential',
    patternTone: (concurrent ? 'soft' : 'info') as Tone,
    cross: event.cross_pipeline,
    detail: selected
      ? integer(event.run_count) +
        ' runs · ' +
        integer(event.hard_failed) +
        ' hard · ' +
        integer(event.soft_failed) +
        ' soft · pipelines: ' +
        (event.pipelines || []).join(', ')
      : null,
    runs: shown.map((run) => ({
      run,
      color: agentStateColor(run.state),
      meta: [run.pipeline, run.queue].filter(Boolean).join(' · '),
      time: selected
        ? shortDate(run.started_at) +
          ' · ' +
          run.state +
          ' · ' +
          duration(run._end !== null && run._start !== null ? (run._end - run._start) / 60000 : null)
        : null,
      link: '#' + value(run.build_number) + ' log ↗',
    })),
    more: !selected && allRuns.length > shown.length ? '+' + integer(allRuns.length - shown.length) + ' more · select to expand' : null,
  }
}

/** openNodeEvidence: the retained runs on one node, newest first, as classic's history evidence table. */
export function nodeEvidence(agent: AgentRow, options: Pick<AgentOptions, 'windowId' | 'signal'>) {
  const points = (agent._runs || [])
    .slice()
    .sort((a, b) => String(a.started_at).localeCompare(String(b.started_at)))
    .map((run) => ({
      label: run.group,
      url: run.url,
      timestamp: run.started_at,
      valueSummary: run.state + ' · ' + run.pipeline,
      run,
    }))
  return {
    title: signalTerm(options.signal) + ' failures on ' + agent.node,
    subtitle: signalTerm(options.signal) + ' failing runs on this node over ' + options.windowId + ', each linking to its BuildKite log',
    rows: points.slice().reverse(),
  }
}

export type AgentDay = {
  day: string
  runs: number
  identifiedRuns: number
  /** Identified nodes with at least one run that day. */
  nodes: number
  /** Identified nodes with at least one failure matching the signal that day. */
  failingNodes: number
  hard: number
  soft: number
  /** Failures matching the signal, split by state. */
  signalHard: number
  signalSoft: number
  signal: number
  cofailEvents: number
}

interface NodeDayFailures {
  day: string
  nodeRaw: string
  hard: number
  soft: number
  signalHard: number
  signalSoft: number
  /** Node-suspect failures (attributed to the node or unexplained), whatever the signal. */
  suspect: number
  /** Failures attributed to the node itself. */
  node: number
}

/**
 * Hard and soft failures per UTC day and node from the compact failure accounting, or from the
 * retained failing runs when there is none, with the parts matching the failure signal, the
 * node-suspect subset, and the failures attributed to the node.
 */
function agentNodeDayFailures(model: AgentModel, options: AgentOptions): NodeDayFailures[] {
  const out: NodeDayFailures[] = []
  if (model.failureAccounting.length) {
    const rows = model.failureAccounting.filter((row) => matchesFilter(options, row.h, row.nd))
    const startDay = agentWindowStartDay(model, options.windowId)
    const keyOf = (row: Rec) => JSON.stringify([row.d, row.nd, row.s])
    const count = (signal: string) =>
      agentFailureAccountingCounts(rows, { startDay, nightlyOnly: options.nightlyOnly, excludeCancelled: options.excludeCancelled, signal }, keyOf)
    const suspect = count('infra')
    const node = count('node')
    count(options.signal).forEach((counts, key) => {
      const [day, nodeRaw, state] = JSON.parse(key) as [string, string, string]
      const hard = state === 'hard'
      out.push({
        day,
        nodeRaw,
        hard: counts.hard,
        soft: counts.soft,
        signalHard: hard ? counts.signal : 0,
        signalSoft: hard ? 0 : counts.signal,
        suspect: suspect.get(key)?.signal ?? 0,
        node: node.get(key)?.signal ?? 0,
      })
    })
    return out
  }
  const inScope = agentRunInScope(model, options)
  for (const run of model.failing) {
    if (!inScope(run) || (run.state !== 'hard' && run.state !== 'soft')) continue
    const hard = Number(run.state === 'hard')
    const soft = Number(run.state === 'soft')
    const matched = signalMatch(options.signal, run)
    out.push({
      day: String(run.day),
      nodeRaw: run.node_raw,
      hard,
      soft,
      signalHard: matched ? hard : 0,
      signalSoft: matched ? soft : 0,
      suspect: Number(run.node_suspect),
      node: Number(run.attribution === 'node'),
    })
  }
  return out
}

export interface AttributionBreakdown {
  /** Failures in scope, and how many of them each attribution explains, in AGENT_ATTRIBUTIONS order. */
  total: number
  shares: { attribution: AgentAttribution; label: string; count: number }[]
  /** Failures explained by the build, the test, or shared infrastructure rather than a node. */
  notNode: number
}

/**
 * Failures by attribution from the first UTC day of the window (or `fromDay`, if later) under the
 * page filters, from the compact failure accounting, or the retained failing runs without it.
 */
export function agentAttributionBreakdown(model: AgentModel, options: AgentOptions, fromDay?: string): AttributionBreakdown {
  const windowStart = agentWindowStartDay(model, options.windowId)
  const startDay = fromDay && fromDay > windowStart ? fromDay : windowStart
  const counts = new Map<AgentAttribution, number>(AGENT_ATTRIBUTIONS.map((a) => [a, 0]))
  const add = (raw: unknown, count: number) => {
    const attribution = agentAttribution(raw)
    counts.set(attribution, counts.get(attribution)! + count)
  }
  if (model.failureAccounting.length) {
    const rows = model.failureAccounting.filter((row) => matchesFilter(options, row.h, row.nd))
    const accounting = { startDay, nightlyOnly: options.nightlyOnly, excludeCancelled: options.excludeCancelled, signal: 'all' }
    agentFailureAccountingCounts(rows, accounting, (row) => agentAttribution(row.a)).forEach((entry, attribution) => add(attribution, entry.hard + entry.soft))
  } else {
    const inScope = agentRunInScope(model, options)
    for (const run of model.failing) {
      if (inScope(run) && String(run.day) >= startDay && (run.state === 'hard' || run.state === 'soft')) add(run.attribution, 1)
    }
  }
  const shares = AGENT_ATTRIBUTIONS.map((attribution) => ({ attribution, label: ATTRIBUTION_LABEL[attribution], count: counts.get(attribution)! }))
  const total = shares.reduce((sum, share) => sum + share.count, 0)
  const notNode = (['build', 'test', 'infra'] as const).reduce((sum, a) => sum + counts.get(a)!, 0)
  return { total, shares, notNode }
}

/** Every UTC day of the selected window, oldest first. */
function agentWindowDays(model: AgentModel, windowId: string): string[] {
  const days: string[] = []
  const endDay = new Date(model.endMs).toISOString().slice(0, 10)
  for (let ms = Date.parse(agentWindowStartDay(model, windowId) + 'T00:00:00Z'); ms <= Date.parse(endDay + 'T00:00:00Z'); ms += 86400000) {
    days.push(new Date(ms).toISOString().slice(0, 10))
  }
  return days
}

/**
 * Per UTC day of the selected window (the creation day for the build-created cohort): the page's
 * node_days runs, its compact failure accounting (retained failing runs when there is none), and
 * its co-failure events.
 */
export function agentDailySeries(model: AgentModel, view: AgentView, options: AgentOptions): AgentDay[] {
  const days = new Map<string, AgentDay & { nodeSet: Set<string>; failingSet: Set<string> }>()
  for (const day of agentWindowDays(model, options.windowId)) {
    days.set(day, { day, runs: 0, identifiedRuns: 0, nodes: 0, failingNodes: 0, hard: 0, soft: 0, signalHard: 0, signalSoft: 0, signal: 0, cofailEvents: 0, nodeSet: new Set(), failingSet: new Set() })
  }
  for (const { nd, bucket } of agentScopedNodeDays(model, options)) {
    const entry = days.get(String(nd.d))
    if (!entry) continue
    entry.runs += bucket[0]
    if (nd.nd !== '(unidentified)') {
      entry.identifiedRuns += bucket[0]
      entry.nodeSet.add(nd.nd)
    }
  }
  for (const counts of agentNodeDayFailures(model, options)) {
    const entry = days.get(counts.day)
    if (!entry) continue
    entry.hard += counts.hard
    entry.soft += counts.soft
    entry.signalHard += counts.signalHard
    entry.signalSoft += counts.signalSoft
    entry.signal += counts.signalHard + counts.signalSoft
    if (counts.signalHard + counts.signalSoft && counts.nodeRaw !== '(unidentified)') entry.failingSet.add(counts.nodeRaw)
  }
  for (const event of view.events) {
    const entry = days.get(event.started_at.slice(0, 10))
    if (entry) entry.cofailEvents += 1
  }
  return [...days.values()].map(({ nodeSet, failingSet, ...entry }) => ({ ...entry, nodes: nodeSet.size, failingNodes: failingSet.size }))
}

/** One node on one UTC day of the window. */
export type AgentNodeDayCell = {
  day: string
  runs: number
  hard: number
  soft: number
  signalHard: number
  signalSoft: number
  /** Failures matching the signal. */
  signal: number
  /** Node-suspect failures and failures attributed to the node, whatever the signal. */
  suspect: number
  node: number
  /** Co-failure events that started on this node that day. */
  cofailEvents: number
}

/** One identified node across the window: its daily cells and the totals the page ranks it by. */
export interface AgentNodeHistory {
  node: string
  node_raw: string
  hardware: string
  cells: AgentNodeDayCell[]
  signal: number
  signalHard: number
  signalSoft: number
  /** Days with at least one failure matching the signal. */
  activeDays: number
  /** Signal failures in the last RECENT_DAYS days of the window. */
  recent: number
  /** Node-suspect failures and failures attributed to the node in the last RECENT_DAYS days. */
  recentSuspect: number
  recentNode: number
  /** Co-failure events that started in the last RECENT_DAYS days of the window. */
  recentCofail: number
  cofailEvents: number
}

export interface AgentNodeDayMatrix {
  days: string[]
  /** Identified nodes with a signal failure or a co-failure event, worst first. */
  nodes: AgentNodeHistory[]
  /** The largest single node-day signal count. */
  max: number
  /** How many of the last days count as "right now". */
  recentDays: number
}

/** "Right now" is the current UTC day and the two before it. */
export const RECENT_DAYS = 3

/**
 * Per identified node and UTC day: runs (node_days), hard/soft and signal failures (compact
 * accounting, or retained runs without it), and co-failure events. Nodes rank by signal failures,
 * then by the number of days they failed on, then by co-failure events.
 */
export function agentNodeDayMatrix(model: AgentModel, view: AgentView, options: AgentOptions): AgentNodeDayMatrix {
  const days = agentWindowDays(model, options.windowId)
  const index = new Map(days.map((day, i) => [day, i]))
  const recentFrom = Math.max(0, days.length - RECENT_DAYS)
  const agents = new Map(view.agents.map((agent) => [agent.node_raw, agent]))
  const nodes = new Map<string, AgentNodeHistory>()
  const cellOf = (nodeRaw: string, day: string, hardware = ''): AgentNodeDayCell | null => {
    const at = index.get(day)
    if (at === undefined || nodeRaw === '(unidentified)' || !nodeRaw) return null
    let node = nodes.get(nodeRaw)
    if (!node) {
      const agent = agents.get(nodeRaw)
      const hw = agent?.hardware || hardware
      node = {
        node: agent?.node ?? agentNodeLabel(nodeRaw, hw),
        node_raw: nodeRaw,
        hardware: hw,
        cells: days.map((d) => ({ day: d, runs: 0, hard: 0, soft: 0, signalHard: 0, signalSoft: 0, signal: 0, suspect: 0, node: 0, cofailEvents: 0 })),
        signal: 0,
        signalHard: 0,
        signalSoft: 0,
        activeDays: 0,
        recent: 0,
        recentSuspect: 0,
        recentNode: 0,
        recentCofail: 0,
        cofailEvents: 0,
      }
      nodes.set(nodeRaw, node)
    }
    return node.cells[at]!
  }
  for (const counts of agentNodeDayFailures(model, options)) {
    const cell = cellOf(counts.nodeRaw, counts.day)
    if (!cell) continue
    cell.hard += counts.hard
    cell.soft += counts.soft
    cell.signalHard += counts.signalHard
    cell.signalSoft += counts.signalSoft
    cell.signal += counts.signalHard + counts.signalSoft
    cell.suspect += counts.suspect
    cell.node += counts.node
  }
  for (const event of view.events) {
    const cell = cellOf(event.node_raw, event.started_at.slice(0, 10), event.hardware)
    if (cell) cell.cofailEvents += 1
  }
  for (const { nd, bucket } of agentScopedNodeDays(model, options)) {
    const node = nodes.get(nd.nd)
    const at = index.get(String(nd.d))
    if (node && at !== undefined) node.cells[at]!.runs += bucket[0]
  }
  let max = 0
  const ranked = [...nodes.values()].filter((node) => {
    node.cells.forEach((cell, i) => {
      node.signal += cell.signal
      node.signalHard += cell.signalHard
      node.signalSoft += cell.signalSoft
      node.cofailEvents += cell.cofailEvents
      if (cell.signal) node.activeDays += 1
      if (i >= recentFrom) {
        node.recent += cell.signal
        node.recentSuspect += cell.suspect
        node.recentNode += cell.node
        node.recentCofail += cell.cofailEvents
      }
      max = Math.max(max, cell.signal)
    })
    return node.signal > 0 || node.cofailEvents > 0
  })
  ranked.sort(
    (a, b) =>
      b.signal - a.signal ||
      b.activeDays - a.activeDays ||
      b.cofailEvents - a.cofailEvents ||
      (a.node_raw < b.node_raw ? -1 : a.node_raw > b.node_raw ? 1 : 0),
  )
  return { days, nodes: ranked, max, recentDays: Math.min(RECENT_DAYS, days.length) }
}

/** The plural noun of the failure signal: "node-suspect failures", "hard failures", or "failures". */
export function signalNoun(signal: AgentSignal): string {
  return signal === 'all' ? 'failures' : signalTerm(signal) + ' failures'
}

export interface AgentFleetVerdict {
  tone: 'pass' | 'soft' | 'hard'
  label: string
  headline: string
  detail: string
  /** Nodes with two or more recent failures attributed to the node itself, worst first. */
  hotNodes: AgentNodeHistory[]
  /** Nodes with recent node-suspect failures that no second failure corroborates. */
  watchNodes: AgentNodeHistory[]
  recentFailures: number
  recentNodes: number
  /** Share of the window's signal failures on the five worst nodes; null without failures. */
  topShare: number | null
}

/** A node is hurting once this many recent failures are attributed to it. */
export const HURTING_NODE_FAILURES = 2

const nodesWord = (count: number) => integer(count) + (count === 1 ? ' node' : ' nodes')
const failuresWord = (count: number, noun: string) => integer(count) + ' ' + (count === 1 ? noun.replace(/s$/, '') : noun)

/**
 * Whether a node is hurting CI right now: hurting when recent failures are attributed to a node
 * itself (a lost agent, or unexplained failures of several test groups), watch when node-suspect
 * failures are uncorroborated, healthy without any. `recent` breaks the recent failures down by
 * attribution so the verdict says how many were the build, the test, or shared infrastructure.
 */
export function agentFleetVerdict(
  matrix: AgentNodeDayMatrix,
  view: AgentView,
  options: Pick<AgentOptions, 'signal' | 'windowId'>,
  recent: AttributionBreakdown,
): AgentFleetVerdict {
  const noun = signalNoun(options.signal)
  const bound = view.failureDetailIncomplete ? 'at least ' : ''
  const recentNodes = matrix.nodes.filter((node) => node.recent > 0)
  const recentFailures = recentNodes.reduce((sum, node) => sum + node.recent, 0)
  const total = matrix.nodes.reduce((sum, node) => sum + node.signal, 0)
  const top = matrix.nodes.slice(0, 5).reduce((sum, node) => sum + node.signal, 0)
  const topShare = total ? top / total : null
  const hotNodes = matrix.nodes
    .filter((node) => node.recentNode >= HURTING_NODE_FAILURES)
    .sort((a, b) => b.recentNode - a.recentNode || b.recentSuspect - a.recentSuspect || b.signal - a.signal)
  const watchNodes = matrix.nodes
    .filter((node) => node.recentSuspect > 0 && node.recentNode < HURTING_NODE_FAILURES)
    .sort((a, b) => b.recentSuspect - a.recentSuspect || b.signal - a.signal)
  const span = matrix.recentDays === 1 ? 'today' : `in the last ${matrix.recentDays} days`
  const explained = recent.notNode
    ? ` ${capitalize(bound)}${integer(recent.notNode)} of the ${integer(recent.total)} failures ${span} were build-wide, test or shared-infrastructure failures, not node faults.`
    : ''
  const concentration =
    topShare !== null && matrix.nodes.length > 5 ? ` The 5 worst nodes carry ${Math.round(topShare * 100)}% of the ${options.windowId} ${noun}.` : ''
  const base = { hotNodes, watchNodes, recentFailures, recentNodes: recentNodes.length, topShare }
  if (hotNodes.length) {
    const attributed = hotNodes.reduce((sum, node) => sum + node.recentNode, 0)
    return {
      ...base,
      tone: 'hard',
      label: 'Hurting',
      headline: `${nodesWord(hotNodes.length)} ${hotNodes.length === 1 ? 'is' : 'are'} failing jobs themselves`,
      detail: `${capitalize(bound)}${failuresWord(attributed, 'failures')} ${span} ${attributed === 1 ? 'is' : 'are'} attributed to ${hotNodes.length === 1 ? 'one node' : nodesWord(hotNodes.length)}: lost agents, or unexplained failures of several test groups on one node.${explained}${concentration}`,
    }
  }
  if (watchNodes.length) {
    const suspect = watchNodes.reduce((sum, node) => sum + node.recentSuspect, 0)
    const attributed = watchNodes.reduce((sum, node) => sum + node.recentNode, 0)
    const corroboration = attributed
      ? `${integer(attributed)} of them ${attributed === 1 ? 'is' : 'are'} attributed to a node (a lost agent, or unexplained failures of several test groups), but no node has ${HURTING_NODE_FAILURES}.`
      : 'none corroborated by a second test group or a lost agent.'
    return {
      ...base,
      tone: 'soft',
      label: 'Watch',
      headline: 'No node is failing jobs repeatedly',
      detail: `${capitalize(bound)}${failuresWord(suspect, 'node-suspect failures')} on ${nodesWord(watchNodes.length)} ${span}, ${corroboration}${explained}${concentration}`,
    }
  }
  return {
    ...base,
    tone: 'pass',
    label: 'Healthy',
    headline: 'No node is hurting CI right now',
    detail: `No node-suspect failures ${span}.${explained}` + (total ? ` ${capitalize(bound)}${integer(total)} ${noun} earlier in ${options.windowId}.` : ''),
  }
}

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1)

export type AgentLoad = {
  key: string
  failures: number
  hard: number
  soft: number
  nodes: number
  groups: number
  runs: AgentRun[]
}

/** Retained signal-matching failing runs grouped by a key (queue, test group), most failures first. */
export function agentLoadBy(runs: AgentRun[], keyOf: (run: AgentRun) => string): AgentLoad[] {
  const groups = new Map<string, AgentRun[]>()
  for (const run of runs) {
    const key = keyOf(run) || '(unknown)'
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(run)
  }
  return [...groups.entries()]
    .map(([key, list]) => ({
      key,
      failures: list.length,
      hard: list.filter((run) => run.state === 'hard').length,
      soft: list.filter((run) => run.state === 'soft').length,
      nodes: new Set(list.map((run) => run.node_raw)).size,
      groups: new Set(list.map((run) => run.group)).size,
      runs: list.slice().sort((a, b) => (b._start ?? 0) - (a._start ?? 0)),
    }))
    .sort((a, b) => b.failures - a.failures || b.nodes - a.nodes || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0))
}
