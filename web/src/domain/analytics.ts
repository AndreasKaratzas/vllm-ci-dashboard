// CI Analytics: AMD test health, AMD nightly latency, and AMD nightlies.
// Ported from docs/assets/js/ops-v2.js (renderAmdHealth, openAmdCatalog,
// renderLatencyComparison, openLatencyEvidence, and the nightlies branch of
// renderAnalytics). Classic names are kept so the two are easy to compare.
import {
  amdGroupPassRate,
  amdHealthGroups,
  amdLatestState,
  amdLogicalInventory,
  logicalTestGroupPresentation,
} from './ciHealth'
import { duration, integer, percent, shortDate, toneForState, value, type Rec, type Tone } from './format'
import { hardwareDisplayLabel, isAmdMiHardware, isAmdRuntimeJob } from './health'
import { nightlyFailureCount, nightlyFailureMovement } from './nightly'
import { exactPipelineBuildUrl } from './urls'

export const AMD_TEST_HEALTH_ASSET = 'data/vllm/ci/operations_v2/amd_test_health.json'
export const OPERATIONS_ASSET = 'data/vllm/ci/operations_v2_manifest.json'
export const LATENCY_ASSET = 'data/vllm/ci/operations_v2/comparison.json'

export function normalizeLabel(label: unknown): string {
  return String(label || '').trim().replace(/\s+/g, ' ').toLowerCase()
}

export function compareText(left: unknown, right: unknown): number {
  return String(left || '').localeCompare(String(right || ''), undefined, { sensitivity: 'base' })
}

/** Classic historyPointLabel: the label a chart observation shows in its evidence table. */
export function historyPointLabel(point: Rec): string {
  return point.label || point.name || (point.timestamp ? shortDate(point.timestamp) : 'Observation')
}

/** One stat in a classic status strip; ``onOpen`` names the drill-down the view wires up. */
export interface StripItem {
  id?: string
  label: string
  value: string
  meta: string
  tone: Tone | ''
  url?: string | null
  actionLabel?: string
  scope?: string
  observed?: string
  provenance?: string
  static?: boolean
}

// ---------------------------------------------------------------------------
// AMD test health (ops_analytics_view=groups)

export function amdHealthPublicationState(amdHealth: Rec | null | undefined) {
  const retention: Rec = (amdHealth || {}).operations_publication_retention || {}
  const complete = retention.complete_relative_to_amd_test_health !== false
  return { complete, retention, prefix: complete ? '' : '≥' }
}

export interface HardwareCluster {
  id: string
  label: string
  rows: Rec[]
  passed: number
  soft: number
  hard: number
  missing: number
}

export function amdHardwareRows(groups: Rec[], latestBuild: unknown): HardwareCluster[] {
  const byHardware = new Map<string, HardwareCluster>()
  groups.forEach((group) => {
    const id = value(group.hardware_variant || group.hardware, 'unknown')
    if (!byHardware.has(id)) byHardware.set(id, { id, label: hardwareDisplayLabel(id), rows: [], passed: 0, soft: 0, hard: 0, missing: 0 })
    const cluster = byHardware.get(id)!
    const latest = amdLatestState(group, latestBuild)
    cluster.rows.push(group)
    if (latest === 'passed') cluster.passed += 1
    else if (latest === 'soft') cluster.soft += 1
    else if (latest === 'hard') cluster.hard += 1
    else cluster.missing += 1
  })
  return Array.from(byHardware.values()).sort(
    (a, b) => b.passed + b.soft + b.hard - (a.passed + a.soft + a.hard) || a.label.localeCompare(b.label),
  )
}

export function hardwareValueSummary(row: HardwareCluster): string {
  return (
    integer(row.passed) + ' passing - ' + integer(row.soft) + ' soft - ' + integer(row.hard) + ' hard - ' + integer(row.missing) + ' not in latest'
  )
}

export type AmdCatalogFilter = 'all' | 'attention' | 'passing' | 'incident' | 'missing' | 'mixed'

export const AMD_CATALOG_FILTERS: [AmdCatalogFilter, string][] = [
  ['all', 'All retained variants'],
  ['attention', 'Needs attention'],
  ['passing', 'Passing now'],
  ['incident', 'Failures now'],
  ['missing', 'Historical only'],
  ['mixed', 'Mixed history'],
]

export function isMixedHistory(row: Rec): boolean {
  return Number(row.passed || 0) > 0 && Number(row.soft_failed || 0) + Number(row.hard_failed || 0) > 0
}

/** The search and result filter of classic openAmdCatalog. */
export function amdCatalogRows(rows: Rec[], latestBuild: unknown, mode: string, search: string): Rec[] {
  const query = normalizeLabel(search)
  return rows.filter((row) => {
    const latest = amdLatestState(row, latestBuild)
    if (mode === 'attention' && !['soft', 'hard', 'unknown'].includes(latest)) return false
    if (mode === 'passing' && latest !== 'passed') return false
    if (mode === 'incident' && !['soft', 'hard'].includes(latest)) return false
    if (mode === 'missing' && latest !== 'missing') return false
    if (mode === 'mixed' && !isMixedHistory(row)) return false
    if (!query) return true
    return [row.display_name, row.name, row.job_name, row.hardware_variant, row.hardware, row.queue].some((part) =>
      normalizeLabel(part).includes(query),
    )
  })
}

export function passRateLabel(row: Rec): string {
  const rate = amdGroupPassRate(row)
  return rate === null ? '-' : rate.toFixed(1) + '%'
}

/** A request to open the AMD job-variant catalog sheet. */
export interface CatalogRequest {
  title: string
  subtitle: string
  rows: Rec[]
  filter: AmdCatalogFilter
}

export interface AmdCluster {
  label: string
  count: number
  renderedCount: string
  description: string
  meta: string
  tone: Tone
  catalog: CatalogRequest
}

export interface BuildHealthPoint {
  label: string
  number: unknown
  passed: number
  soft: number
  hard: number
  unknown: number
  timestamp: unknown
  url: unknown
  valueSummary: string
  details: [string, unknown][]
}

export interface AmdHealthModel {
  available: boolean
  publication: ReturnType<typeof amdHealthPublicationState>
  retentionNote: string
  latestBuild: unknown
  groups: Rec[]
  strip: StripItem[]
  logical: { rows: Rec[]; inventory: Rec; available: boolean }
  catalogs: { variants: CatalogRequest; incidents: CatalogRequest; all: CatalogRequest }
  browseAllLabel: string
  clusters: AmdCluster[]
  hardware: HardwareCluster[]
  builds: BuildHealthPoint[]
  priority: Rec[]
  priorityMeta: string
  priorityCaption: string
}

const chartNumber = (raw: unknown) => (raw === null || raw === undefined || raw === '' || !Number.isFinite(Number(raw)) ? 0 : Number(raw))

/** Everything renderAmdHealth shows, from an already scope-checked (currentAmdHealth) payload. */
export function amdHealthModel(amdHealth: Rec): AmdHealthModel {
  const summary: Rec = amdHealth.summary || {}
  const groups = amdHealthGroups(amdHealth)
  const builds: Rec[] = Array.isArray(amdHealth.builds) ? amdHealth.builds : []
  const publication = amdHealthPublicationState(amdHealth)
  const latestBuild = summary.latest_build_number
  const empty: AmdHealthModel = {
    available: false,
    publication,
    retentionNote: '',
    latestBuild,
    groups,
    strip: [],
    logical: { rows: [], inventory: {}, available: false },
    catalogs: {
      variants: { title: '', subtitle: '', rows: [], filter: 'all' },
      incidents: { title: '', subtitle: '', rows: [], filter: 'all' },
      all: { title: '', subtitle: '', rows: [], filter: 'all' },
    },
    browseAllLabel: '',
    clusters: [],
    hardware: [],
    builds: [],
    priority: [],
    priorityMeta: '',
    priorityCaption: '',
  }
  if (amdHealth.available !== true || !groups.length) return empty

  const latestCounts: Rec = summary.latest_job_variant_state_counts || summary.latest_state_counts || {}
  const latestVariantCount = Number(
    summary.latest_job_variant_count !== undefined ? summary.latest_job_variant_count : summary.latest_group_count || 0,
  )
  const latestTestGroups: Rec = summary.latest_test_group_counts || {}
  const logicalInventory = amdLogicalInventory(amdHealth)
  const logicalRows: Rec[] = logicalInventory.rows
  const logicalTotal = Number(latestTestGroups.total)
  const logicalPassing = Number(latestTestGroups.passing)
  const logicalBuild = Number(latestTestGroups.build_number)
  const logicalAvailable =
    latestTestGroups.available === true &&
    Number.isFinite(logicalBuild) &&
    logicalBuild === Number(latestBuild) &&
    Number.isFinite(logicalTotal) &&
    logicalTotal >= 0 &&
    Number.isFinite(logicalPassing) &&
    logicalPassing >= 0 &&
    logicalPassing <= logicalTotal
  const logicalPresentation = logicalAvailable
    ? logicalTestGroupPresentation(latestTestGroups)
    : { value: '-', meta: 'same-build logical test-group counts unavailable', tone: 'soft' as Tone }
  const passing = Number(latestCounts.passed || 0)
  const soft = Number(latestCounts.soft || latestCounts.soft_failed || 0)
  const hard = Number(latestCounts.hard || latestCounts.hard_failed || 0)
  const incidents = soft + hard
  const unknown = Number(latestCounts.unknown || 0)
  const nonPassing = incidents + unknown
  const retainedCount = Number(
    summary.retained_job_variant_count ||
      summary.retained_group_count ||
      summary.union_group_count ||
      summary.group_count ||
      groups.length,
  )
  const notLatest = Math.max(0, retainedCount - latestVariantCount)
  const mixed = groups.filter(isMixedHistory)
  const currentIncidents = groups.filter((row) => ['soft', 'hard'].includes(amdLatestState(row, latestBuild)))
  const currentPassing = groups.filter((row) => amdLatestState(row, latestBuild) === 'passed')
  const currentUnknown = groups.filter((row) => amdLatestState(row, latestBuild) === 'unknown')
  const currentVariants = currentPassing.concat(currentIncidents, currentUnknown)
  const missing = groups.filter((row) => amdLatestState(row, latestBuild) === 'missing')

  let retentionNote = ''
  if (!publication.complete) {
    const groupRetention: Rec = publication.retention.group_catalog || {}
    const buildRetention: Rec = publication.retention.builds || {}
    retentionNote =
      'AMD test-health drill-down is storage-bounded: ' +
      integer(groupRetention.published) +
      ' of ' +
      integer(groupRetention.source) +
      ' job-variant rows and ' +
      integer(buildRetention.published) +
      ' of ' +
      integer(buildRetention.source) +
      ' nightly summary rows are published. ' +
      'Top-level latest-build totals remain source-complete; catalog counts, hardware distributions, and history charts are retained-row lower bounds, and aggregate rates are unavailable.'
  }

  const severityTone: Tone = hard ? 'hard' : soft ? 'soft' : 'pass'
  const strip: StripItem[] = [
    {
      id: 'amd-health-build',
      label: 'LATEST AMD NIGHTLY',
      value: latestBuild ? '#' + latestBuild : '-',
      meta: shortDate(summary.latest_observed_at),
      tone: severityTone,
      url: summary.latest_build_url,
      actionLabel: 'Open Buildkite ↗',
    },
    {
      id: 'amd-health-test-groups',
      label: 'LATEST AMD TEST GROUPS',
      value: logicalPresentation.value,
      meta: logicalPresentation.meta,
      tone: logicalPresentation.tone,
      scope: 'Main CI AMD nightly #' + value(latestBuild),
      observed: latestTestGroups.observed_at || summary.latest_observed_at,
      provenance:
        latestTestGroups.count_basis ||
        'Unique source-aligned AMD test-group identities observed in this main CI nightly; topology-distinct routes remain separate and configured shards count once.',
      actionLabel: logicalRows.length ? 'Browse logical test groups →' : 'Inspect count definition',
    },
    {
      id: 'amd-health-observed',
      label: 'LATEST JOB VARIANTS',
      value: integer(latestVariantCount),
      meta:
        integer(passing) +
        ' passing - ' +
        integer(nonPassing) +
        ' non-passing exact jobs' +
        (notLatest ? '; ' + integer(notLatest) + ' older variants retained only for history' : ''),
      tone: hard ? 'hard' : nonPassing ? 'soft' : 'pass',
    },
    {
      id: 'amd-health-incidents',
      label: 'FAILURE OBSERVATIONS',
      value: integer(incidents),
      meta: integer(soft) + ' soft - ' + integer(hard) + ' hard' + (unknown ? ' - ' + integer(unknown) + ' unknown' : ''),
      tone: severityTone,
    },
  ]

  const incomplete = !publication.complete
  const rendered = (count: number) => (incomplete ? '≥' : '') + integer(count)
  const clusters: AmdCluster[] = [
    {
      label: 'Needs attention',
      count: currentIncidents.length + currentUnknown.length,
      renderedCount: rendered(currentIncidents.length + currentUnknown.length),
      description: 'Soft, hard, or unresolved result in the latest nightly',
      meta: integer(soft) + ' soft - ' + integer(hard) + ' hard - ' + publication.prefix + integer(currentUnknown.length) + ' retained unresolved',
      tone: hard ? 'hard' : 'soft',
      catalog: {
        title: 'AMD variants needing attention',
        subtitle: 'Current soft, hard, and unresolved outcomes only; historical-only names are excluded',
        rows: currentVariants,
        filter: 'attention',
      },
    },
    {
      label: 'Passing now',
      count: currentPassing.length,
      renderedCount: rendered(currentPassing.length),
      description: 'Latest exact AMD job result passed',
      meta: publication.complete
        ? percent(currentPassing.length, latestVariantCount) + ' of latest job variants'
        : 'Aggregate share unavailable for incomplete catalog',
      tone: 'pass',
      catalog: { title: 'AMD job variants passing now', subtitle: 'Latest exact main CI AMD gating-job outcomes', rows: currentPassing, filter: 'passing' },
    },
    {
      label: 'Mixed history',
      count: mixed.length,
      renderedCount: rendered(mixed.length),
      description: 'Both passing and non-passing nightlies retained',
      meta: integer(summary.build_count) + ' source nightlies; retained catalog only',
      tone: 'soft',
      catalog: {
        title: 'AMD job variants with mixed history',
        subtitle: 'Job variants that passed on some AMD nightlies and had raw failures on others',
        rows: mixed,
        filter: 'mixed',
      },
    },
    {
      label: 'Historical only',
      count: missing.length,
      renderedCount: rendered(missing.length),
      description: 'Older job names not present in the latest nightly',
      meta: 'Not classified as current failures',
      tone: 'neutral',
      catalog: {
        title: 'Historical AMD job variants',
        subtitle: 'Retained older names that are not part of the latest nightly observation set',
        rows: missing,
        filter: 'missing',
      },
    },
  ]

  const priority = currentIncidents
    .slice()
    .sort((a, b) => {
      const stateDelta = (amdLatestState(a, latestBuild) === 'hard' ? 0 : 1) - (amdLatestState(b, latestBuild) === 'hard' ? 0 : 1)
      return (
        stateDelta ||
        Number(amdGroupPassRate(a) || 0) - Number(amdGroupPassRate(b) || 0) ||
        String(a.display_name || a.name).localeCompare(String(b.display_name || b.name))
      )
    })
    .slice(0, 10)

  return {
    available: true,
    publication,
    retentionNote,
    latestBuild,
    groups,
    strip,
    logical: { rows: logicalRows, inventory: logicalInventory, available: logicalAvailable },
    catalogs: {
      variants: {
        title: 'Latest AMD job variants',
        subtitle: 'Exact AMD gating-job variants observed in the latest main CI nightly',
        rows: currentVariants,
        filter: 'all',
      },
      incidents: {
        title: 'Current AMD failure observations',
        subtitle: 'AMD gating-job variants with a soft or hard result in the latest main CI nightly',
        rows: currentIncidents,
        filter: 'incident',
      },
      all: {
        title: 'Retained AMD job variants',
        subtitle:
          'Search exact AMD job variants retained across the nightly history window' +
          (publication.complete ? '' : '; the published catalog is incomplete'),
        rows: groups,
        filter: 'all',
      },
    },
    browseAllLabel: 'Browse all ' + publication.prefix + integer(groups.length) + ' retained variants',
    clusters,
    hardware: amdHardwareRows(groups, latestBuild),
    builds: builds.map((build) => ({
      label: '#' + build.number,
      number: build.number,
      passed: chartNumber(build.passed),
      soft: chartNumber(build.soft_failed),
      hard: chartNumber(build.hard_failed),
      unknown: chartNumber(build.unknown),
      timestamp: build.observed_at,
      url: build.url,
      valueSummary:
        integer(build.passed) + ' passing - ' + integer(build.soft_failed) + ' soft - ' + integer(build.hard_failed) + ' hard',
      details: [
        ['observed job variants', build.observed],
        ['passing', build.passed],
        ['soft failed', build.soft_failed],
        ['hard failed', build.hard_failed],
        ['unknown', build.unknown],
        ['job variant pass rate', Number(build.pass_rate_pct || 0).toFixed(1) + '%'],
      ],
    })),
    priority,
    priorityMeta: integer(priority.length) + ' highest-priority raw results shown; every row opens exact nightly evidence',
    priorityCaption:
      publication.prefix +
      integer(currentIncidents.length) +
      ' retained current AMD failure observations; use Browse all for the ' +
      (publication.complete ? 'complete' : 'bounded') +
      ' catalog',
  }
}

/** The groups view observes the latest AMD test signal; the other views observe the snapshot. */
export function analyticsObservedAt(view: string, ops: Rec, amdHealth: Rec): unknown {
  if (view === 'agent-health') return (ops.amd_agent_health || {}).generated_at
  if (view === 'groups') return (amdHealth.summary || {}).latest_observed_at || ops.generated_at
  return ops.generated_at
}

// ---------------------------------------------------------------------------
// AMD nightly latency (ops_analytics_view=latency)

export function latencyMetric(raw: unknown): number | null {
  if (raw === null || raw === undefined || raw === '') return null
  const parsed = Number(raw)
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null
}

export function exactLatencyJobUrl(job: Rec | null | undefined, buildNumber: number): string {
  if (
    !job ||
    typeof job.job_id !== 'string' ||
    !job.job_id.trim() ||
    typeof job.url !== 'string' ||
    job.url !== job.url.trim() ||
    !Number.isSafeInteger(buildNumber) ||
    buildNumber <= 0
  )
    return ''
  try {
    const parsed = new URL(job.url)
    if (parsed.protocol !== 'https:' || parsed.host !== 'buildkite.com' || parsed.username || parsed.password) return ''
    const buildPath = '/vllm/ci/builds/' + buildNumber
    if (parsed.pathname === buildPath && !parsed.search && parsed.hash === '#' + job.job_id) return job.url
    const keys = Array.from(parsed.searchParams.keys())
    if (
      parsed.pathname === buildPath + '/steps/canvas' &&
      !parsed.hash &&
      keys.length === 2 &&
      keys.includes('jid') &&
      keys.includes('tab') &&
      parsed.searchParams.get('jid') === job.job_id &&
      parsed.searchParams.get('tab') === 'output'
    )
      return job.url
  } catch {
    // An unparsable URL is not exact evidence.
  }
  return ''
}

const LATENCY_JOB_COLUMNS = ['job_id', 'step_id', 'url', 'queue', 'hardware', 'raw_name', 'started_at', 'finished_at', 'duration_mins']

export interface LatencyComparison {
  available: boolean
  unavailable_reason?: string
  rows: Rec[]
  cohort: Rec
  interval?: unknown
  generated_at?: unknown
}

/** The five-nightly AMD MI latency contract, with every row's samples validated. */
export function latencyComparison(ops: Rec | null | undefined): LatencyComparison {
  const payload: Rec = (ops || {}).latency || {}
  const cohort: Rec = payload.cohort || {}
  const nightlies: Rec[] = Array.isArray(cohort.nightlies) ? cohort.nightlies : []
  const unavailable = (reason: string): LatencyComparison => ({ available: false, unavailable_reason: reason, rows: [], cohort })
  if (
    payload.schema_version !== 2 ||
    payload.source_pipeline !== 'ci' ||
    payload.job_scope !== 'amd_gpu' ||
    payload.hardware_scope !== 'amd_mi_gpu' ||
    payload.branch !== 'main' ||
    payload.build_limit !== 5 ||
    payload.statistic !== 'median_of_per_nightly_group_wall_minutes'
  ) {
    return unavailable('The current main CI five-nightly timing contract is unavailable')
  }
  if (payload.available !== true || !nightlies.length || nightlies.length > 5) {
    return unavailable(payload.unavailable_reason || 'No completed main CI nightly timing is available')
  }
  const compactJobs = Object.prototype.hasOwnProperty.call(payload, 'job_columns')
  if (
    compactJobs &&
    (!Array.isArray(payload.job_columns) ||
      payload.job_columns.length !== LATENCY_JOB_COLUMNS.length ||
      payload.job_columns.some((column: unknown, index: number) => column !== LATENCY_JOB_COLUMNS[index]))
  ) {
    return unavailable('The current main CI timing evidence columns are invalid')
  }
  const numbers = new Set(nightlies.map((build) => Number(build.number)))
  if (
    numbers.size !== nightlies.length ||
    nightlies.some(
      (build) => !Number.isInteger(Number(build.number)) || Number(build.number) <= 0 || !Number.isFinite(Date.parse(build.created_at)),
    )
  )
    return unavailable('The current main CI nightly cohort is invalid')

  function normalizeSample(sample: unknown): Rec | null {
    if (!sample || typeof sample !== 'object' || Array.isArray(sample)) return null
    const record = sample as Rec
    if (!compactJobs) {
      if (
        record.jobs !== undefined &&
        (!Array.isArray(record.jobs) || record.jobs.some((job: unknown) => !job || typeof job !== 'object' || Array.isArray(job)))
      )
        return null
      return record
    }
    if (!Array.isArray(record.jobs) || !record.jobs.length) return null
    const jobs: Rec[] = []
    for (const values of record.jobs as unknown[]) {
      if (
        !Array.isArray(values) ||
        values.length !== LATENCY_JOB_COLUMNS.length ||
        values.slice(0, 8).some((raw) => typeof raw !== 'string') ||
        [0, 2, 5, 6, 7].some((index) => !String(values[index]).trim()) ||
        !Number.isFinite(Date.parse(values[6])) ||
        !Number.isFinite(Date.parse(values[7])) ||
        typeof values[8] !== 'number' ||
        !Number.isFinite(values[8]) ||
        values[8] < 0
      )
        return null
      const job: Rec = {}
      LATENCY_JOB_COLUMNS.forEach((column, index) => {
        job[column] = values[index]
      })
      jobs.push(job)
    }
    return { ...record, jobs }
  }

  function sideInCohort(side: Rec | null | undefined): Rec | null {
    if (
      !side ||
      side.source_pipeline !== 'ci' ||
      side.job_scope !== 'amd_gpu' ||
      side.hardware_scope !== 'amd_mi_gpu' ||
      !Array.isArray(side.samples)
    )
      return null
    const samples = (side.samples as unknown[]).map(normalizeSample).filter(
      (sample): sample is Rec =>
        !!sample &&
        numbers.has(Number(sample.build_number)) &&
        latencyMetric(sample.duration_mins) !== null &&
        Array.isArray(sample.jobs) &&
        sample.jobs.length > 0 &&
        sample.jobs.every(
          (job: Rec) => isAmdRuntimeJob(job) && isAmdMiHardware(job.hardware) && exactLatencyJobUrl(job, Number(sample.build_number)),
        ),
    )
    const unique = new Set(samples.map((sample) => Number(sample.build_number)))
    if (
      !samples.length ||
      samples.length !== side.samples.length ||
      unique.size !== samples.length ||
      Number(side.sample_count) !== samples.length ||
      latencyMetric(side.median_duration_mins) === null
    )
      return null
    return { ...side, samples, sample_count: samples.length }
  }

  const rows = (Array.isArray(payload.rows) ? payload.rows : []).map((row: Rec) => ({ id: row.id, label: row.label, amd: sideInCohort(row.amd) }))
  return { available: true, rows, cohort, interval: payload.interval, generated_at: payload.generated_at }
}

export function latencySideDuration(side: Rec | null | undefined): string {
  return side ? duration(side.median_duration_mins) : 'Unavailable'
}

/** Slowest median first; unobserved groups last; then by label. */
export function latencyRows(comparison: LatencyComparison): Rec[] {
  return comparison.rows.slice().sort((left, right) => {
    const leftValue = left.amd ? Number(left.amd.median_duration_mins) : -Infinity
    const rightValue = right.amd ? Number(right.amd.median_duration_mins) : -Infinity
    return rightValue - leftValue || compareText(left.label, right.label)
  })
}

export interface LatencyModel {
  builds: Rec[]
  context: string
  rows: Rec[]
  timed: Rec[]
  top: Rec[]
  strip: StripItem[]
  tableMeta: string
}

export function latencyModel(comparison: LatencyComparison): LatencyModel {
  const builds: Rec[] = (comparison.cohort || {}).nightlies || []
  const rows = latencyRows(comparison)
  const timed = rows.filter((row) => row.amd)
  return {
    builds,
    context: builds.map((build) => '#' + build.number).join(' · '),
    rows,
    timed,
    top: timed.slice(0, 12),
    strip: [
      {
        label: 'MAIN CI NIGHTLIES',
        value: integer(builds.length) + ' / 5',
        meta: builds.length
          ? shortDate(builds[builds.length - 1]!.created_at) + ' – ' + shortDate(builds[0]!.created_at)
          : 'No recent nightly observations',
        tone: '',
        static: true,
      },
      {
        label: 'AMD GROUPS TIMED',
        value: integer(timed.length),
        meta: integer(rows.length - timed.length) + ' not observed in this cohort',
        tone: '',
        static: true,
      },
    ],
    tableMeta: integer(rows.length) + ' current main CI groups · fixed latest-five-nightly cohort',
  }
}

/** Classic renders the raw label, so a missing label stays blank. */
export function latencyLabel(row: Rec): string {
  return row.label === null || row.label === undefined ? '' : String(row.label)
}

export function observedNightliesLabel(row: Rec, builds: Rec[]): string {
  return integer(row.amd ? row.amd.sample_count : 0) + ' / ' + integer(builds.length)
}

export interface LatencyEvidenceRow {
  number: unknown
  buildUrl: string
  created: string
  wall: string
  observed: boolean
  jobs: { label: string; url: string }[]
}

/** openLatencyEvidence: one row per cohort nightly, with the exact MI jobs behind each observation. */
export function latencyEvidence(row: Rec, comparison: LatencyComparison) {
  const nightlies: Rec[] = comparison.cohort.nightlies || []
  const samples: Rec[] = row.amd ? row.amd.samples : []
  const perBuild = new Map(samples.map((sample) => [Number(sample.build_number), sample]))
  const rows: LatencyEvidenceRow[] = nightlies.map((build) => {
    const sample = perBuild.get(Number(build.number)) || null
    const url = build.web_url || build.url
    return {
      number: build.number,
      buildUrl: exactPipelineBuildUrl({ build_url: url, number: build.number }, 'ci'),
      created: shortDate(build.created_at),
      wall: sample ? duration(sample.duration_mins) : 'Not observed',
      observed: Boolean(sample),
      jobs: sample
        ? (sample.jobs as Rec[]).flatMap((job) => {
            const jobUrl = exactLatencyJobUrl(job, build.number)
            return jobUrl
              ? [{ label: value(job.queue || job.hardware || job.step_id, 'MI job') + ' · ' + duration(job.duration_mins), url: jobUrl }]
              : []
          })
        : [],
    }
  })
  return {
    title: row.label || 'AMD MI nightly latency',
    value: latencySideDuration(row.amd),
    meta: row.amd
      ? integer(row.amd.sample_count) + ' / ' + integer(nightlies.length) + ' recent nightlies observed'
      : 'No AMD MI observation in the current five-nightly cohort',
    rows,
  }
}

// ---------------------------------------------------------------------------
// AMD nightlies (ops_analytics_view=nightlies)

export function nightlyDisplayName(nightly: Rec): string {
  return nightly.display_name || 'AMD MI main CI'
}

/** A strip item whose drill-down is the build detail sheet in ``scope``. */
export interface NightlyStripItem extends StripItem {
  scope?: 'new' | 'recurring' | 'fixed'
  opensBuild?: boolean
}

export interface NightliesModel {
  name: string
  latest: Rec
  latestMovement: Rec | null
  strip: NightlyStripItem[]
  chartBuilds: Rec[]
  rows: Rec[]
}

export function nightliesModel(nightly: Rec): NightliesModel {
  const builds: Rec[] = nightly.builds || []
  const name = nightlyDisplayName(nightly)
  const latest = builds[0] || {}
  const published = nightlyFailureMovement(latest)
  const latestMovement = published && published.available !== false ? published : null
  const movementBuilds = builds.filter((row) => {
    const movement = nightlyFailureMovement(row)
    return row.has_test_results !== false && Boolean(movement) && movement!.available !== false
  })
  const countTone = (count: number, active: Tone): Tone => (latestMovement && count ? active : latestMovement ? 'pass' : 'neutral')
  return {
    name,
    latest,
    latestMovement,
    strip: [
      {
        label: 'LATEST ' + name.toUpperCase() + ' NIGHTLY',
        value: latest.number ? '#' + latest.number : '-',
        meta: latest.created_at ? shortDate(latest.created_at) : 'No completed nightly',
        tone: toneForState(latest.state),
        url: latest.number ? exactPipelineBuildUrl(latest, 'ci') : null,
      },
      {
        label: 'JOB VARIANTS OBSERVED',
        value: integer(latest.total_groups),
        meta: 'exact jobs in the latest completed nightly',
        tone: '',
        opensBuild: true,
      },
      {
        label: 'NEW FAILURES',
        value: latestMovement ? integer(latestMovement.new.length) : '-',
        meta: latestMovement ? 'not failing in the preceding observed nightly' : 'unavailable in this snapshot',
        tone: countTone(latestMovement ? latestMovement.new.length : 0, 'hard'),
        opensBuild: true,
        scope: 'new',
      },
      {
        label: 'RECURRING FAILURES',
        value: latestMovement ? integer(latestMovement.recurring.length) : '-',
        meta: latestMovement ? 'failed in the preceding observed nightly too' : 'unavailable in this snapshot',
        tone: countTone(latestMovement ? latestMovement.recurring.length : 0, 'soft'),
        opensBuild: true,
        scope: 'recurring',
      },
      {
        label: 'FIXED',
        value: latestMovement ? integer(latestMovement.fixed.length) : '-',
        meta: latestMovement ? 'failed previously and passed now' : 'unavailable in this snapshot',
        tone: latestMovement && latestMovement.fixed.length ? 'pass' : 'neutral',
        opensBuild: true,
        scope: 'fixed',
      },
    ],
    chartBuilds: movementBuilds.slice().reverse(),
    rows: builds,
  }
}

export function nightlyCountLabel(build: Rec, key: 'new' | 'recurring' | 'fixed'): string {
  const count = nightlyFailureCount(build, key)
  return count === null ? '-' : integer(count)
}

export function nightlyMovementSummary(build: Rec): string {
  const movement = nightlyFailureMovement(build) || { new: [], recurring: [], fixed: [] }
  return (
    integer(movement.new.length) + ' new - ' + integer(movement.recurring.length) + ' recurring - ' + integer(movement.fixed.length) + ' fixed'
  )
}
