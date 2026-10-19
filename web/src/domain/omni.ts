// Omni CI: a port of the classic renderOmni (docs/assets/js/ops-v2.js) and the
// OMNI_* helpers before it. renderOmni is split into omniView(), which returns
// every fact the page shows, and detail builders that return the drawer
// contents classic opens, so tests can compare both with the classic DOM.
import { duration, integer, percent, shortDate, value, type Rec, type Tone } from './format'

const QUEUE_LIVE_BASE = 'https://raw.githubusercontent.com/AndreasKaratzas/vllm-ci-dashboard/queue-data/data/vllm/ci/'
export const SOURCE_ASSETS = {
  omni: 'data/vllm/ci/operations_v2/omni.json',
  queueSection: QUEUE_LIVE_BASE + 'operations_v2/queue.json',
  queueHistory: QUEUE_LIVE_BASE + 'queue_timeseries.jsonl',
  workloadMapping: 'data/vllm/ci/workload_mapping.json',
}

export const OMNI_REPOSITORIES = { omni: 'vllm-project/vllm-omni', main: 'vllm-project/vllm' } as const
export interface MappingWindow {
  id: string
  label: string
  shortLabel: string
  hours: number
  hourlyBin: number
}
export const OMNI_MAPPING_WINDOWS: MappingWindow[] = [
  { id: '6h', label: '6 hours', shortLabel: '6h', hours: 6, hourlyBin: 1 },
  { id: '1d', label: '1 day', shortLabel: '1d', hours: 24, hourlyBin: 1 },
  { id: '3d', label: '3 days', shortLabel: '3d', hours: 72, hourlyBin: 3 },
  { id: '7d', label: '7 days', shortLabel: '7d', hours: 168, hourlyBin: 6 },
  { id: '1m', label: '1 month', shortLabel: '1m', hours: 24 * 30, hourlyBin: 24 },
  { id: '3m', label: '3 months', shortLabel: '3m', hours: 24 * 90, hourlyBin: 24 },
]
export const OMNI_RANGE_WINDOWS = [
  { id: '1h', label: '1 hour', hours: 1 },
  { id: '3h', label: '3 hours', hours: 3 },
  { id: '6h', label: '6 hours', hours: 6 },
  { id: '12h', label: '12 hours', hours: 12 },
  { id: '24h', label: '1 day', hours: 24 },
  { id: '72h', label: '3 days', hours: 72 },
]
export const OMNI_AGE_BANDS = [
  { id: 'all', label: 'All active', min: null, max: null },
  { id: 'lt1h', label: '<1h', min: 0, max: 60 },
  { id: '1to3h', label: '1-3h', min: 60, max: 180 },
  { id: '3to6h', label: '3-6h', min: 180, max: 360 },
  { id: '6to12h', label: '6-12h', min: 360, max: 720 },
  { id: '12to24h', label: '12-24h', min: 720, max: 1440 },
  { id: '1to3d', label: '1-3d', min: 1440, max: 4320 },
  { id: 'gte3d', label: '3d+', min: 4320, max: null },
] as { id: string; label: string; min: number | null; max: number | null }[]

export const OMNI_MAPPING_NUMBER_FIELDS = ['mapped_jobs', 'started_jobs', 'finished_jobs', 'mapped_gpu_slots', 'gpu_hours'] as const

// Classic route state (syncRouteState 'ci-omni' + ROUTE_DEFAULTS).
export const OMNI_ROUTE_DEFAULTS = { omni_mapping_range: '7d', omni_range: '24h', omni_age: 'all' }
export function omniRouteValue(key: keyof typeof OMNI_ROUTE_DEFAULTS, raw: unknown): string {
  const allowed =
    key === 'omni_mapping_range'
      ? OMNI_MAPPING_WINDOWS.map((w) => w.id)
      : key === 'omni_range'
        ? OMNI_RANGE_WINDOWS.map((w) => w.id)
        : OMNI_AGE_BANDS.map((b) => b.id)
  const next = typeof raw === 'string' ? raw : ''
  return next && allowed.includes(next) ? next : OMNI_ROUTE_DEFAULTS[key]
}

// ---- shared classic helpers -------------------------------------------------

export function compareText(left: unknown, right: unknown): number {
  return String(left || '').localeCompare(String(right || ''), undefined, { sensitivity: 'base' })
}

export function isRetiredQueue(queue: unknown): boolean {
  const name = String(queue || '').trim().toLowerCase()
  return /^amd_mi355b(?:_|$)/i.test(name)
}

export function buildUrl(pipeline: unknown, number: unknown): string {
  if (!pipeline || number === null || number === undefined || number === '') return ''
  return 'https://buildkite.com/vllm/' + encodeURIComponent(String(pipeline)) + '/builds/' + encodeURIComponent(String(number))
}

export function signedInteger(number: unknown): string {
  if (!Number.isFinite(Number(number))) return '-'
  const v = Number(number)
  return (v > 0 ? '+' : '') + integer(v)
}

function gpuHours(v: unknown): string {
  return Number(v || 0).toLocaleString(undefined, { maximumFractionDigits: 1 })
}

// ---- mapping aggregates -----------------------------------------------------

export type MappingStats = Rec & {
  mapped_jobs: number
  started_jobs: number
  finished_jobs: number
  mapped_gpu_slots: number
  gpu_hours: number
  by_queue: Record<string, Rec>
  by_pipeline: Record<string, Rec>
}

export function emptyOmniMappingStats(): MappingStats {
  return { mapped_jobs: 0, started_jobs: 0, finished_jobs: 0, mapped_gpu_slots: 0, gpu_hours: 0, by_queue: {}, by_pipeline: {} }
}

export function addOmniMappingBreakdown(target: Record<string, Rec>, source: Rec | null | undefined) {
  for (const [name, raw] of Object.entries(source || {})) {
    const stats = (raw || {}) as Rec
    if (!target[name]) target[name] = { mapped_jobs: 0, started_jobs: 0, finished_jobs: 0, mapped_gpu_slots: 0, gpu_hours: 0 }
    for (const field of OMNI_MAPPING_NUMBER_FIELDS) target[name]![field] += Number(stats[field] || 0)
  }
}

export function addOmniMappingStats(target: MappingStats, source: Rec | null | undefined): MappingStats {
  const stats = source || {}
  for (const field of OMNI_MAPPING_NUMBER_FIELDS) target[field] += Number(stats[field] || 0)
  addOmniMappingBreakdown(target.by_queue, stats.by_queue)
  addOmniMappingBreakdown(target.by_pipeline, stats.by_pipeline)
  return target
}

export function omniMappingTotals(rows: Rec[] | null | undefined) {
  const totals = { omni: emptyOmniMappingStats(), main: emptyOmniMappingStats() }
  for (const row of rows || []) {
    const workloads = row.workloads || {}
    addOmniMappingStats(totals.omni, workloads.omni)
    addOmniMappingStats(totals.main, workloads.main)
  }
  return totals
}

export function omniMappingRowStart(row: Rec, resolution: string): number | null {
  const raw = resolution === 'hourly' ? row.hour || row.start || row.bucket_start || row.ts : row.date ? row.date + 'T00:00:00Z' : row.start || row.ts
  const parsed = new Date(raw || '').getTime()
  return Number.isFinite(parsed) ? parsed : null
}

export function omniMappingRowEnd(row: Rec, resolution: string): number | null {
  const explicit = new Date(row.end_exclusive || row.end || '').getTime()
  if (Number.isFinite(explicit)) return explicit
  const start = omniMappingRowStart(row, resolution)
  if (start === null) return null
  return start + (resolution === 'hourly' ? 60 * 60 * 1000 : 24 * 60 * 60 * 1000)
}

export interface PopulationBoundary {
  jobCreatedRangeExhaustive: boolean | null
  parentBuildLookbackDays: number | null
  sourceWindowExact: boolean
  limitation: string
}

export function omniMappingPopulationBoundary(mapping: Rec | null | undefined, resolution: string): PopulationBoundary {
  const m = mapping || {}
  const windowScope = m.window || {}
  const attribution = (m.scope || {}).attribution || {}
  const query = m.query || {}
  const resolutionCoverage = (m.coverage || {})[resolution] || {}
  const exhaustiveValue = [
    windowScope.job_created_range_exhaustive,
    resolutionCoverage.job_created_range_exhaustive,
    attribution.job_created_range_exhaustive,
    query.job_created_range_exhaustive,
  ].find((v) => v === true || v === false) as boolean | undefined
  const lookback = Number(attribution.parent_build_lookback_days === undefined ? query.parent_build_lookback_days : attribution.parent_build_lookback_days)
  return {
    jobCreatedRangeExhaustive: exhaustiveValue === undefined ? null : exhaustiveValue,
    parentBuildLookbackDays: Number.isFinite(lookback) && lookback > 0 ? lookback : null,
    sourceWindowExact: attribution.exact_within_declared_source_window === true,
    limitation:
      attribution.limitation ||
      (exhaustiveValue === false ? 'Jobs added after the configured lookback to older parent builds can be absent from these aggregates.' : ''),
  }
}

export interface MappingWindowView extends PopulationBoundary {
  selected: MappingWindow
  available: boolean
  resolution: string
  rows: Rec[]
  buckets?: Rec[]
  expectedBuckets?: number
  declaredCoverage?: Rec
  windowStart?: number | null
  anchor?: number
  complete: boolean
  apiCollectionComplete: boolean
  retainedComplete?: boolean
  coverageStatus?: string
  lowerBound: boolean
  hasOpenBucket: boolean
  selectedContiguous?: boolean
  observedThrough?: string | null
  reason: string
}

export function omniMappingWindow(mapping: Rec | null | undefined, rangeId: string): MappingWindowView {
  const selected = OMNI_MAPPING_WINDOWS.find((item) => item.id === rangeId) || OMNI_MAPPING_WINDOWS[3]!
  const hourly: Rec[] = Array.isArray((mapping || {}).hourly) ? mapping!.hourly : []
  const daily: Rec[] = Array.isArray((mapping || {}).daily) ? mapping!.daily : []
  let resolution = selected.hours <= 168 && hourly.length ? 'hourly' : 'daily'
  if (selected.hours < 72 && !hourly.length) {
    return {
      selected,
      available: false,
      resolution: 'unavailable',
      rows: [],
      buckets: [],
      complete: false,
      apiCollectionComplete: false,
      lowerBound: false,
      hasOpenBucket: false,
      reason: 'Hourly mapping history is not available yet. Daily totals cannot answer a trailing ' + selected.label + ' question.',
      ...omniMappingPopulationBoundary(mapping, 'hourly'),
    }
  }
  if (!daily.length && !hourly.length) {
    return {
      selected,
      available: false,
      resolution: 'unavailable',
      rows: [],
      buckets: [],
      complete: false,
      apiCollectionComplete: false,
      lowerBound: true,
      hasOpenBucket: false,
      reason: 'No unique-job mapping history has been collected yet.',
      ...omniMappingPopulationBoundary(mapping, resolution),
    }
  }
  if (resolution === 'daily' && !daily.length) resolution = 'hourly'
  const declaredCoverage = ((mapping || {}).coverage || {})[resolution] || {}
  const source = (resolution === 'hourly' ? hourly : daily)
    .map((row) => ({ row, start: omniMappingRowStart(row, resolution), end: omniMappingRowEnd(row, resolution) }))
    .filter((item): item is { row: Rec; start: number; end: number } => item.start !== null && item.end !== null)
    .sort((left, right) => left.start - right.start)
  if (!source.length) {
    return {
      selected,
      available: false,
      resolution,
      rows: [],
      buckets: [],
      complete: false,
      apiCollectionComplete: false,
      lowerBound: true,
      hasOpenBucket: false,
      reason: 'The retained mapping buckets do not contain valid UTC timestamps.',
      ...omniMappingPopulationBoundary(mapping, resolution),
    }
  }
  const generated = new Date((mapping || {}).generated_at || '').getTime()
  const latestEnd = source[source.length - 1]!.end
  const anchor = Number.isFinite(generated) ? Math.min(generated, latestEnd) : latestEnd
  let selectedRows: typeof source
  let expectedBuckets: number
  let bucketMs: number
  if (resolution === 'hourly') {
    expectedBuckets = selected.hours
    bucketMs = 60 * 60 * 1000
    const eligible = source.filter((item) => item.start <= anchor)
    const latestStart = eligible.length ? eligible[eligible.length - 1]!.start : null
    const earliestStart = latestStart === null ? null : latestStart - (expectedBuckets - 1) * bucketMs
    selectedRows = eligible.filter((item) => earliestStart !== null && item.start >= earliestStart)
  } else {
    expectedBuckets = Math.ceil(selected.hours / 24)
    bucketMs = 24 * 60 * 60 * 1000
    const anchorDay = new Date(anchor).toISOString().slice(0, 10)
    const eligible = source.filter((item) => item.row.date <= anchorDay)
    const latestStart = eligible.length ? eligible[eligible.length - 1]!.start : null
    const earliestStart = latestStart === null ? null : latestStart - (expectedBuckets - 1) * bucketMs
    selectedRows = eligible.filter((item) => earliestStart !== null && item.start >= earliestStart)
  }
  const rows = selectedRows.map((item) => item.row)
  const lowerBound = rows.some((row) => row.lower_bound === true || row.collection_complete === false)
  const hasOpenBucket = rows.some((row) => row.state === 'open' || row.open === true)
  const selectedContiguous = selectedRows.every((item, index) => !index || item.start - selectedRows[index - 1]!.start === bucketMs)
  const coverageComplete = rows.length === expectedBuckets && selectedContiguous
  const retainedComplete = Boolean(rows.length && coverageComplete && !lowerBound)
  const complete = retainedComplete && !hasOpenBucket
  const coverageStatus = lowerBound ? 'lower_bound' : !coverageComplete ? 'partial' : hasOpenBucket ? 'open' : 'complete'
  const lastRow = rows[rows.length - 1] || {}
  const observedThrough = lastRow.observed_through || (mapping || {}).generated_at || null
  const reasonParts: string[] = []
  if (resolution === 'daily' && selected.hours <= 168 && !hourly.length) {
    reasonParts.push('Hourly history is not retained yet, so this uses UTC-day buckets.')
  }
  if (!coverageComplete) {
    reasonParts.push(
      rows.length !== expectedBuckets
        ? 'Only ' + integer(rows.length) + ' of ' + integer(expectedBuckets) + ' selected ' + (resolution === 'hourly' ? 'UTC-hour' : 'UTC-day') + ' buckets are retained.'
        : 'The selected ' + (resolution === 'hourly' ? 'UTC-hour' : 'UTC-day') + ' buckets contain a gap.',
    )
  }
  if (lowerBound) reasonParts.push('At least one source bucket is a collection lower bound.')
  if (hasOpenBucket) {
    reasonParts.push(
      'The current UTC ' + (resolution === 'hourly' ? 'hour' : 'day') + ' is open through ' + (observedThrough ? shortDate(observedThrough) : 'the latest collection') + '.',
    )
  }
  return {
    selected,
    available: Boolean(rows.length),
    resolution,
    rows,
    expectedBuckets,
    declaredCoverage,
    windowStart: selectedRows.length ? selectedRows[0]!.start : null,
    anchor,
    complete,
    apiCollectionComplete: retainedComplete,
    retainedComplete,
    coverageStatus,
    lowerBound,
    hasOpenBucket,
    selectedContiguous,
    observedThrough,
    reason: reasonParts.join(' '),
    ...omniMappingPopulationBoundary(mapping, resolution),
  }
}

export interface MappingBucket {
  id: string
  start: number
  end: number
  rows: Rec[]
  complete: boolean
  lowerBound: boolean
  hasOpenBucket: boolean
  workloads: { omni: MappingStats; main: MappingStats }
  expectedSourceRows?: number
  sourceRows?: number
  contiguous?: boolean
}

export function omniMappingBuckets(windowInfo: MappingWindowView | null | undefined): MappingBucket[] {
  if (!windowInfo || !windowInfo.available) return []
  const resolution = windowInfo.resolution
  const binHours = resolution === 'hourly' ? windowInfo.selected.hourlyBin : 24
  const binMs = binHours * 60 * 60 * 1000
  const declaredWindowStart = Number(windowInfo.windowStart)
  const hourlyAnchor = Number.isFinite(declaredWindowStart) ? declaredWindowStart : 0
  const buckets = new Map<string, MappingBucket>()
  for (const row of windowInfo.rows) {
    const start = omniMappingRowStart(row, resolution)
    if (start === null) continue
    const bucketStart =
      resolution === 'hourly' ? hourlyAnchor + Math.floor((start - hourlyAnchor) / binMs) * binMs : Date.parse(String(row.date) + 'T00:00:00Z')
    const key = new Date(bucketStart).toISOString()
    if (!buckets.has(key)) {
      buckets.set(key, {
        id: key,
        start: bucketStart,
        end: bucketStart + binMs,
        rows: [],
        complete: true,
        lowerBound: false,
        hasOpenBucket: false,
        workloads: { omni: emptyOmniMappingStats(), main: emptyOmniMappingStats() },
      })
    }
    const bucket = buckets.get(key)!
    bucket.rows.push(row)
    bucket.complete = bucket.complete && row.complete !== false && row.collection_complete !== false
    bucket.lowerBound = bucket.lowerBound || row.lower_bound === true || row.collection_complete === false
    bucket.hasOpenBucket = bucket.hasOpenBucket || row.state === 'open' || row.open === true
    addOmniMappingStats(bucket.workloads.omni, (row.workloads || {}).omni || {})
    addOmniMappingStats(bucket.workloads.main, (row.workloads || {}).main || {})
  }
  const expectedSourceRows = resolution === 'hourly' ? binHours : 1
  const sourceStepMs = resolution === 'hourly' ? 60 * 60 * 1000 : 24 * 60 * 60 * 1000
  return Array.from(buckets.values())
    .map((bucket) => {
      const starts = bucket.rows
        .map((row) => omniMappingRowStart(row, resolution))
        .filter((start): start is number => start !== null)
        .sort((left, right) => left - right)
      const contiguous = starts.every(
        (start, index) => (!index || start - starts[index - 1]! === sourceStepMs) && start === bucket.start + index * sourceStepMs,
      )
      bucket.expectedSourceRows = expectedSourceRows
      bucket.sourceRows = starts.length
      bucket.contiguous = contiguous
      bucket.complete = bucket.complete && !bucket.lowerBound && !bucket.hasOpenBucket && starts.length === expectedSourceRows && contiguous
      return bucket
    })
    .sort((left, right) => left.start - right.start)
}

export function omniMappingBucketLabel(bucket: { start: number }, resolution: string): string {
  const date = new Date(bucket.start)
  if (resolution === 'daily') return date.toISOString().slice(0, 10)
  return date.toISOString().slice(5, 13).replace('T', ' ') + ':00'
}

// ---- live occupancy history ------------------------------------------------

export interface HistoryPoint {
  ts: string
  time: number
  allWaiting: number | null
  allRunning: number | null
  amdWaiting: number | null
  amdRunning: number | null
  waitingSupported: boolean
  runningSupported: boolean
  amdWaitingSupported: boolean
  amdRunningSupported: boolean
  waitingCoverage: string
  runningCoverage: string
  source: Rec
}

function supported(scope: Rec, kind: 'waiting' | 'running'): boolean {
  return scope[kind + '_supported'] === true || (scope[kind + '_supported'] === undefined && ['complete', 'partial'].includes(scope[kind + '_attribution']))
}

export function omniHistoryPoints(omni: Rec | null | undefined): HistoryPoint[] {
  const rows: Rec[] = ((omni || {}).history || {}).points || []
  return rows
    .map((point) => {
      const amd = point.amd || {}
      const allFleet = point.all_fleet || amd
      const waitingSupported = supported(allFleet, 'waiting')
      const runningSupported = supported(allFleet, 'running')
      const amdWaitingSupported = supported(amd, 'waiting')
      const amdRunningSupported = supported(amd, 'running')
      return {
        ts: point.ts,
        time: new Date(point.ts || '').getTime(),
        allWaiting: waitingSupported ? Number(allFleet.waiting_observed || 0) : null,
        allRunning: runningSupported ? Number(allFleet.running_observed || 0) : null,
        amdWaiting: amdWaitingSupported ? Number(amd.waiting_observed || 0) : null,
        amdRunning: amdRunningSupported ? Number(amd.running_observed || 0) : null,
        waitingSupported,
        runningSupported,
        amdWaitingSupported,
        amdRunningSupported,
        waitingCoverage: allFleet.waiting_attribution || 'unavailable',
        runningCoverage: allFleet.running_attribution || 'unavailable',
        source: point,
      }
    })
    .filter((point) => Number.isFinite(point.time))
    .sort((left, right) => left.time - right.time)
}

export function omniWindowPoints(points: HistoryPoint[], rangeId: string): HistoryPoint[] {
  if (!points.length) return []
  const selected = OMNI_RANGE_WINDOWS.find((item) => item.id === rangeId) || OMNI_RANGE_WINDOWS[4]!
  const latestTime = points[points.length - 1]!.time
  const cutoff = latestTime - selected.hours * 60 * 60 * 1000
  return points.filter((point) => point.time >= cutoff && point.time <= latestTime)
}

export function omniAgeBand(job: Rec | null | undefined): string {
  if (!job || job.wait_min === null || job.wait_min === undefined || job.wait_min === '') return ''
  const minutes = Number(job && job.wait_min)
  if (!Number.isFinite(minutes) || minutes < 0) return ''
  const band = OMNI_AGE_BANDS.slice(1).find((item) => minutes >= item.min! && (item.max === null || minutes < item.max))
  return band ? band.id : ''
}

export interface DailyRow {
  day: string
  last: HistoryPoint
  waiting: number | null
  amdWaiting: number | null
  peak: number
  samples: number
  complete: boolean
  delta: number | null
}

export function omniDailyRows(points: HistoryPoint[]): DailyRow[] {
  const byDay = new Map<string, HistoryPoint[]>()
  points
    .filter((point) => point.allWaiting !== null && point.allWaiting !== undefined && Number.isFinite(Number(point.allWaiting)))
    .forEach((point) => {
      const day = new Date(point.time).toISOString().slice(0, 10)
      if (!byDay.has(day)) byDay.set(day, [])
      byDay.get(day)!.push(point)
    })
  const rows: DailyRow[] = Array.from(byDay.entries())
    .sort((left, right) => compareText(left[0], right[0]))
    .map(([day, entries]) => {
      const samples = entries.slice().sort((left, right) => left.time - right.time)
      const last = samples[samples.length - 1]!
      return {
        day,
        last,
        waiting: last.allWaiting,
        amdWaiting: last.amdWaiting,
        peak: Math.max(...samples.map((point) => point.allWaiting as number)),
        samples: samples.length,
        complete: samples.every((point) => point.waitingCoverage === 'complete'),
        delta: null,
      }
    })
  rows.forEach((row, index) => {
    if (!index) return
    const previous = rows[index - 1]!
    const dayGap = (new Date(row.day + 'T00:00:00Z').getTime() - new Date(previous.day + 'T00:00:00Z').getTime()) / (24 * 60 * 60 * 1000)
    if (dayGap === 1) row.delta = (row.waiting as number) - (previous.waiting as number)
  })
  return rows.slice(-7).reverse()
}

// ---- queue snapshot helpers (classic openQueueDetail) ------------------------

export function officialWaitValue(row: Rec | null | undefined, metric: string): number | null {
  const measured = ((row || {}).official_wait || {})[metric]
  return measured !== null && measured !== undefined && Number.isFinite(Number(measured)) ? Number(measured) : null
}

export function sampleWaitValue(row: Rec | null | undefined, metric: string): number | null {
  const measured = ((row || {}).sample_wait || {})[metric]
  return measured !== null && measured !== undefined && Number.isFinite(Number(measured)) ? Number(measured) : null
}

export function waitValue(row: Rec | null | undefined, metric: string): unknown {
  const nativeValue = officialWaitValue(row, metric)
  if ((metric === 'p50' || metric === 'p95') && nativeValue !== null) return nativeValue
  const sampledValue = sampleWaitValue(row, metric)
  if (metric === 'p99' && sampledValue !== null) return sampledValue
  const current = (row || {}).current_wait || {}
  if (current[metric] && current[metric].value !== undefined) return current[metric].value
  if (metric === 'p99' && (row || {}).p99_wait_source !== 'sample_wait') return null
  return (row || {})[metric + '_wait']
}

export function waitSampleCount(row: Rec | null | undefined): number | null {
  const nested = (row || {}).sample_wait || {}
  const count = row && row.wait_sample_count !== undefined ? row.wait_sample_count : nested.count
  return Number.isFinite(Number(count)) ? Number(count) : null
}

export function hasAgentMeasurement(row: Rec): boolean {
  if (row.connected_agents === null || row.connected_agents === undefined || row.connected_agents === '' || !Number.isFinite(Number(row.connected_agents)))
    return false
  if (row.connected_agents_available !== undefined) return row.connected_agents_available === true
  const source = String(row.connected_agents_source || row.agent_count_source || row.metrics_source || row.count_source || '').toLowerCase()
  return !!source && !['active_jobs', 'webhook', 'job_scan', 'none', 'unknown'].includes(source)
}

export function waitSource(row: Rec | null | undefined, metric: string): unknown {
  if ((metric === 'p50' || metric === 'p95') && officialWaitValue(row, metric) !== null) return 'official_wait'
  if (metric === 'p99' && sampleWaitValue(row, metric) !== null) return 'sample_wait'
  const current = (row || {}).current_wait || {}
  const source = (current[metric] && current[metric].source) || (row || {})[metric + '_wait_source'] || (row || {}).wait_source || null
  return source && !['none', 'unavailable', 'unknown'].includes(String(source).toLowerCase()) ? source : null
}

export function waitSourceDetail(row: Rec, metric: string): string | null {
  const family = waitSource(row || {}, metric)
  if (!family) return null
  const key = String(family).toLowerCase()
  const provider = key === 'official_wait' ? row.official_wait_source : key === 'sample_wait' ? row.sample_wait_source : null
  return provider && provider !== family ? family + ' - ' + provider : String(family)
}

// ---- detail (drawer) models -------------------------------------------------

export interface Field {
  label: string
  value: unknown
}
export interface Source {
  label: string
  url: string
}
export interface Cell {
  text: string
  href?: string
  open?: () => OmniDetail
  tone?: Tone
  mono?: boolean
}
export interface Column<T = Rec> {
  label: string
  numeric?: boolean
  cell: (row: T) => Cell
}
/** Classic dataTable() note for a browser table with no rows: 'No matching evidence' + '. No matching observations.' */
export const OMNI_EMPTY_TABLE_TEXT = 'No matching evidence. No matching observations.'

export interface DetailTable<T = Rec> {
  title?: string
  meta?: string
  columns: Column<T>[]
  rows: T[]
  searchText?: (row: T) => string
  searchPlaceholder?: string
}
export interface StripItem {
  label: string
  value: string
  meta: string
}
export interface OmniDetail {
  id: string
  title: string
  subtitle: string
  description?: string
  fields: Field[]
  sources: Source[]
  strip?: StripItem[]
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  tables: DetailTable<any>[]
  empty?: string
}

/** Classic openDetailDrawer() falls back to these when a title or subtitle is empty. */
export function detailTitle(detail: OmniDetail): string {
  return detail.title || 'Evidence'
}
export function detailSubtitle(detail: OmniDetail): string {
  return detail.subtitle || 'Source evidence and retained history'
}

/** The fields classic detailFields() renders: empty values are omitted. */
export function visibleFields(fields: Field[]): { label: string; text: string }[] {
  return fields.filter((f) => f.value !== null && f.value !== undefined && f.value !== '').map((f) => ({ label: f.label, text: String(f.value) }))
}

const text = (t: unknown): Cell => ({ text: t === null || t === undefined ? '-' : String(t) })
const link = (label: unknown, url: unknown, mono = false): Cell => ({ text: String(label || '-'), href: url ? String(url) : undefined, mono })
const action = (label: unknown, open: () => OmniDetail): Cell => ({ text: String(label), open })
export function toneForLabel(label: unknown): Tone {
  const stateName = String(label || '').toLowerCase()
  if (['passed', 'green', 'healthy', 'fixed', 'success'].includes(stateName)) return 'is-success'
  if (['failed', 'failing', 'hard', 'incident', 'error', 'red', 'critical', 'surge', 'broken'].includes(stateName)) return 'is-danger'
  if (['soft', 'soft_fail', 'soft_failed', 'soft_failing', 'warning', 'attention', 'elevated', 'waiting'].includes(stateName)) return 'is-warning'
  if (['new', 'info', 'recurring'].includes(stateName)) return 'is-info'
  return 'is-neutral'
}
const badge = (label: unknown, tone?: Tone): Cell => ({ text: String(label || 'unknown'), tone: tone || toneForLabel(label) })
// Classic linkedBadge: the exact source when present, else a result drawer.
function linkedBadge(label: unknown, url: unknown, tone?: Tone): Cell {
  const cell = badge(label, tone)
  if (url) return { ...cell, href: String(url) }
  return {
    ...cell,
    open: () => metricDetail({ label: 'Result', value: label, meta: 'No exact external source is present in this snapshot.' }),
  }
}

export interface MetricItem {
  id?: string
  label: string
  value: unknown
  meta?: string
  description?: string
  scope?: string
  observed?: string
  window?: string
  provenance?: string
  sources?: Source[]
}

export function metricDetail(item: MetricItem): OmniDetail {
  return {
    id: item.id || item.label,
    title: item.label,
    subtitle: item.scope || 'Operational aggregate',
    description: item.description || item.meta || 'This value is derived from the currently loaded dashboard snapshot.',
    fields: [
      { label: 'Value', value: value(item.value) },
      { label: 'Observed', value: item.observed ? shortDate(item.observed) : null },
      { label: 'Window', value: item.window },
      { label: 'Provenance', value: item.provenance },
    ],
    sources: item.sources || [],
    tables: [],
  }
}

export interface EvidencePoint {
  id?: string
  label?: string
  name?: string
  timestamp?: string
  ts?: string
  date?: string
  valueSummary?: string
  value?: unknown
  url?: string
  scope?: string
  sourceAsset?: string
  details?: Rec
  sources?: Source[]
  onOpen?: () => OmniDetail
}

export function historyPointLabel(point: EvidencePoint): string {
  return point.label || point.name || (point.timestamp ? shortDate(point.timestamp) : 'Observation')
}

export function historyPointSources(point: EvidencePoint, fallbackAsset?: string): Source[] {
  const rows: Source[] = []
  if (point.url) rows.push({ label: 'Open exact source', url: point.url })
  for (const source of point.sources || []) if (source && source.url) rows.push(source)
  if (!rows.length) rows.push({ label: 'Open published source data', url: point.sourceAsset || fallbackAsset || 'data/vllm/ci/operations_v2_manifest.json' })
  const seen = new Set<string>()
  return rows.filter((source) => {
    if (seen.has(source.url)) return false
    seen.add(source.url)
    return true
  })
}

function detailEntries(details: Rec | undefined): Field[] {
  return Object.entries(details || {}).map(([key, v]) => ({ label: key.replace(/_/g, ' '), value: v }))
}

export function inspectHistoryPoint(point: EvidencePoint, fallbackAsset?: string): OmniDetail {
  if (typeof point.onOpen === 'function') return point.onOpen()
  return {
    id: point.id || historyPointLabel(point),
    title: historyPointLabel(point),
    subtitle: point.scope || 'Historical observation',
    fields: detailEntries(point.details),
    sources: historyPointSources(point, fallbackAsset),
    tables: [],
  }
}

/** Chart click on an evidence point (classic drawChart inspectIndex). */
export function inspectChartPoint(point: EvidencePoint, fallbackAsset?: string): OmniDetail {
  if (typeof point.onOpen === 'function') return point.onOpen()
  return {
    id: point.id || historyPointLabel(point),
    title: historyPointLabel(point),
    subtitle: point.scope || (point.url ? 'Source-backed chart observation' : 'Retained chart observation'),
    fields: detailEntries(point.details),
    sources: historyPointSources(point, fallbackAsset),
    tables: [],
  }
}

export function historyEvidence(title: string, points: EvidencePoint[], subtitle?: string, fallbackAsset?: string): OmniDetail {
  const rows = (points || []).slice().reverse()
  const publishedSources: Source[] = []
  const publishedUrls = new Set<string>()
  for (const point of rows) {
    for (const source of (point.sources || []).filter((s) => s && s.url && /published|source data|history/i.test(s.label || ''))) {
      if (!publishedUrls.has(source.url)) {
        publishedUrls.add(source.url)
        publishedSources.push(source)
      }
    }
  }
  if (rows.some((point) => !point.url)) {
    const asset = fallbackAsset || (rows.find((point) => point.sourceAsset) || {}).sourceAsset || 'data/vllm/ci/operations_v2_manifest.json'
    if (asset && !publishedUrls.has(asset)) publishedSources.push({ label: 'Open published source data', url: asset })
  }
  return {
    id: 'history-' + title,
    title,
    subtitle: subtitle || 'Select an observation to inspect its exact evidence',
    fields: [],
    sources: publishedSources,
    tables: [
      {
        title: 'Retained evidence',
        meta: integer(rows.length) + ' source-backed observations',
        rows,
        columns: [
          {
            label: 'Observation',
            cell: (point: EvidencePoint) =>
              point.url ? link(historyPointLabel(point), point.url, true) : action(historyPointLabel(point), () => inspectHistoryPoint(point, fallbackAsset)),
          },
          { label: 'Observed', cell: (point: EvidencePoint) => text(shortDate(point.timestamp || point.ts || point.date)) },
          { label: 'Value', cell: (point: EvidencePoint) => text(value(point.valueSummary || point.value)) },
          {
            label: 'Evidence',
            cell: (point: EvidencePoint) => {
              if (point.url) return link('Open source', point.url)
              const sources = historyPointSources(point, fallbackAsset)
              if (typeof point.onOpen !== 'function' && sources.length === 1) return link('Open source data', sources[0]!.url)
              return action('Inspect', () => inspectHistoryPoint(point, fallbackAsset))
            },
          },
        ],
        searchText: (point: EvidencePoint) => [historyPointLabel(point), point.timestamp, point.ts, point.date, point.valueSummary, point.value].join(' '),
        searchPlaceholder: 'Filter observation, value, date, or source',
      },
    ],
  }
}

export function queueDetail(name: string, row: Rec, jobs: Rec[]): OmniDetail {
  const related = (jobs || []).filter((job) => job.queue === name)
  const nativeObservedAt = row.metrics_ts || null
  const nativeSources = [row.official_wait_source, row.jobs_passed_source, row.jobs_failed_source]
    .filter(Boolean)
    .filter((source, index, all) => all.indexOf(source) === index)
  const p50Source = waitSourceDetail(row, 'p50')
  const p95Source = waitSourceDetail(row, 'p95')
  const sampledP50 = sampleWaitValue(row, 'p50')
  const sampledP95 = sampleWaitValue(row, 'p95')
  const p99Value = waitValue(row, 'p99')
  const sampleCount = waitSampleCount(row)
  const sampleExpected = Number.isFinite(Number(row.wait_sample_expected_count)) ? Number(row.wait_sample_expected_count) : null
  const sampleCoverage =
    sampleExpected === null
      ? 'Unavailable'
      : (sampleCount === null ? '0' : integer(sampleCount)) +
        ' / ' +
        integer(sampleExpected) +
        ' non-zombie waiting jobs - ' +
        (row.wait_sample_complete === true ? 'reconciled' : 'not reconciled')
  return {
    id: 'queue-' + name,
    title: name,
    subtitle: 'Current queue state; queue-native waits include the visible backlog, while scheduled samples exclude jobs flagged at 4+ hours',
    fields: [
      { label: 'Running', value: integer(row.running) },
      { label: 'Waiting', value: integer(row.waiting) },
      { label: 'Connected agents', value: hasAgentMeasurement(row) ? integer(row.connected_agents !== undefined ? row.connected_agents : row.agents) : 'Unavailable' },
      { label: 'Min wait - latest Buildkite metrics bucket', value: duration(officialWaitValue(row, 'min')) },
      { label: 'p50 Buildkite native', value: duration(officialWaitValue(row, 'p50')) },
      { label: 'p95 Buildkite native', value: duration(officialWaitValue(row, 'p95')) },
      { label: 'Max wait - latest Buildkite metrics bucket', value: duration(officialWaitValue(row, 'max')) },
      { label: 'Jobs passed - latest Buildkite metrics bucket', value: row.jobs_passed === null || row.jobs_passed === undefined ? '-' : integer(row.jobs_passed) },
      { label: 'Jobs failed - latest Buildkite metrics bucket', value: row.jobs_failed === null || row.jobs_failed === undefined ? '-' : integer(row.jobs_failed) },
      { label: 'Latest metrics bucket observed', value: value(nativeObservedAt) },
      { label: 'Native metrics provenance', value: nativeSources.length ? nativeSources.join(', ') : '-' },
      { label: 'p50 primary / fallback', value: duration(waitValue(row, 'p50')) + (p50Source ? ' - ' + p50Source : '') },
      { label: 'p95 primary / fallback', value: duration(waitValue(row, 'p95')) + (p95Source ? ' - ' + p95Source : '') },
      { label: 'p50 reconstructed sample', value: sampledP50 === null ? 'Not measured' : duration(sampledP50) },
      { label: 'p95 reconstructed sample', value: sampledP95 === null ? 'Not measured' : duration(sampledP95) },
      {
        label: 'p99 scheduled sample',
        value: p99Value === null || p99Value === undefined ? 'Not measured' : duration(p99Value) + (sampleCount !== null ? ' - n=' + integer(sampleCount) : ''),
      },
      { label: 'p99 source', value: value(waitSourceDetail(row, 'p99')) },
      { label: 'Scheduled sample coverage', value: sampleCoverage },
      { label: '4h+ waiting jobs excluded from sample', value: integer(row.zombie_waiting) },
      { label: 'Count source', value: row.count_source },
    ],
    sources:
      row.queue_url || row.url
        ? [
            { label: 'Open Buildkite queue', url: row.queue_url || row.url },
            { label: 'Open published queue snapshot', url: SOURCE_ASSETS.queueSection },
          ]
        : [{ label: 'Open published queue snapshot', url: SOURCE_ASSETS.queueSection }],
    tables: related.length
      ? [
          {
            meta: integer(related.length) + ' active jobs on this queue',
            rows: related,
            columns: [
              { label: 'Job', cell: (job: Rec) => link(job.name || 'Unnamed job', job.url) },
              { label: 'State', cell: (job: Rec) => linkedBadge(job.state || 'unknown', job.url) },
              { label: 'Age', numeric: true, cell: (job: Rec) => text(duration(job.wait_min !== undefined ? job.wait_min : job.run_min)) },
              { label: 'Build', cell: (job: Rec) => link((job.pipeline || '?') + ' #' + value(job.build), job.build_url || buildUrl(job.pipeline, job.build), true) },
            ],
          },
        ]
      : [],
    empty: related.length ? undefined : 'No active jobs are retained for this queue.',
  }
}

// ---- the page --------------------------------------------------------------

export interface Kpi {
  id: string
  label: string
  value: string
  meta: string
  tone: Tone | ''
  open: () => OmniDetail
}

export interface QueueMappingRow {
  name: string
  omni: Rec
  main: Rec
}

export interface LiveFact {
  label: string
  value: string
  meta: string
  tone: Tone | ''
  open: () => OmniDetail
}

export interface ChartBar {
  label: string
  value: number
  color: 'omni' | 'retiring'
  open: () => OmniDetail
}

export interface OmniView {
  available: true
  header: { title: string; description: string; observedAt: string | null; actionLabel: string; actionUrl: string }
  mappingRange: string
  omniRange: string
  mappingView: MappingWindowView
  kpis: Kpi[]
  scopeNote: { tone: Tone; heading: string; text: string; methodology: () => OmniDetail; excluded: (() => OmniDetail) | null }
  methodology: () => OmniDetail
  volume:
    | { available: true; title: string; meta: string; bars: ChartBar[]; evidence: () => OmniDetail }
    | { available: false; title: string; meta: string; empty: string }
  impact:
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    | { available: true; title: string; meta: string; bars: ChartBar[]; preview: DetailTable<any>; evidence: () => OmniDetail }
    | { available: false; title: string; meta: string; empty: string }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  comparison: { title: string; meta: string; table: DetailTable<any>; browseBuckets: () => OmniDetail; browseQueues: (() => OmniDetail) | null }
  live: {
    title: string
    meta: string
    facts: LiveFact[]
    actions: { label: string; open: () => OmniDetail }[]
    queueHistoryUrl: string
  }
}

export type OmniPage = OmniView | { available: false; message: string }

const MAPPING_SOURCE: Source = { label: 'Open published AMD mapping aggregate', url: SOURCE_ASSETS.workloadMapping }

export function omniView(ops: Rec, mappingRange: string, omniRange: string): OmniPage {
  const omni = ops.omni || {}
  if (omni.hardware_scope !== 'amd_mi_gpu') {
    return { available: false, message: 'AMD MI Omni infrastructure data is unavailable. Current MI queue observations are required.' }
  }
  const current = omni.current || {}
  const currentLedger = current.ledger || {}
  const jobs = omni.current_jobs || {}
  const mappingSource = omni.mapping_history || {}
  const mapping: Rec =
    mappingSource.hardware_scope === 'amd_mi_gpu' && (((mappingSource.scope || {}).workload_pipelines || {}).main || []).join(',') === 'ci' ? mappingSource : {}
  const mappingPublication = ((mapping || {}).retention || {}).publication || {}
  const mappingPublicationIncomplete = mappingPublication.complete_relative_to_source === false
  const mappingView = omniMappingWindow(mapping, mappingRange)
  const mappingBuckets = omniMappingBuckets(mappingView)
  const mappingTotals = mappingView.available ? omniMappingTotals(mappingView.rows) : { omni: emptyOmniMappingStats(), main: emptyOmniMappingStats() }
  const omniTotal = mappingTotals.omni
  const mainTotal = mappingTotals.main
  const mappingAvailable = mappingView.available
  const mappingRatesAvailable = mappingAvailable && Boolean(mappingView.retainedComplete)
  const mappingCountPrefix = mappingRatesAvailable ? '' : '≥'
  const mappingPublicationRows = mappingPublication[mappingView.resolution] || {}
  const selectedMappingLabel = mappingView.selected.label
  const jobRangeNonExhaustive = mappingView.jobCreatedRangeExhaustive === false
  const jobRangeUnknown = mappingView.jobCreatedRangeExhaustive === null
  const lookbackLabel = mappingView.parentBuildLookbackDays
    ? integer(mappingView.parentBuildLookbackDays) + '-day parent-build lookback'
    : 'configured parent-build lookback'
  const populationBoundaryText = jobRangeNonExhaustive
    ? (mappingView.sourceWindowExact
        ? 'UUID-deduplicated counts are exact only inside the ' + lookbackLabel + '; they are not provably exhaustive for every job created in the selected interval. '
        : 'Counts cover only the ' + lookbackLabel + ' and are not provably exhaustive for every job created in the selected interval. ') +
      (mappingView.limitation || 'Jobs attached later to older parent builds can be absent.')
    : jobRangeUnknown
      ? 'Job-created-range exhaustiveness is not published for this aggregate; treat the displayed mappings as observed counts.'
      : 'The source marks the job-created range exhaustive for this aggregate.'
  const mappingCountMeta = jobRangeNonExhaustive
    ? 'source-window count · job-created range non-exhaustive'
    : jobRangeUnknown
      ? 'observed count · population coverage unknown'
      : selectedMappingLabel
  const retiringSum = (byQueue: Record<string, Rec>) =>
    Object.entries(byQueue || {}).reduce((sum, [name, stats]) => sum + (name.startsWith('amd_mi325_') ? Number(stats.mapped_jobs || 0) : 0), 0)
  const omniRetiringMapped = retiringSum(omniTotal.by_queue)
  const mainRetiringMapped = retiringSum(mainTotal.by_queue)
  const combinedMapped = Number(omniTotal.mapped_jobs || 0) + Number(mainTotal.mapped_jobs || 0)
  const queueSnapshots: Rec = ((ops.queue || {}).snapshot || {}).queues || {}

  const waitingByQueue = current.waiting_by_queue || {}
  const runningByQueue = current.running_by_queue || {}
  const pendingLedger: Rec[] = (jobs.pending || []).filter((job: Rec) => !isRetiredQueue(job.queue))
  const runningLedger: Rec[] = (jobs.running || []).filter((job: Rec) => !isRetiredQueue(job.queue))
  const pending = pendingLedger.filter((job) => !job.analysis_excluded)
  const running = runningLedger.filter((job) => !job.analysis_excluded)
  const excludedJobs = pendingLedger.filter((job) => job.analysis_excluded).concat(runningLedger.filter((job) => job.analysis_excluded))
  const activeJobs = pending.concat(running)
  const affected = new Set(
    Object.keys(waitingByQueue)
      .concat(Object.keys(runningByQueue))
      .concat(activeJobs.map((job) => job.queue || 'unknown'))
      .filter((name) => !isRetiredQueue(name)),
  )
  const ledgerWaiting = Number.isFinite(Number(currentLedger.waiting)) ? Number(currentLedger.waiting) : pending.length
  const ledgerRunning = Number.isFinite(Number(currentLedger.running)) ? Number(currentLedger.running) : running.length

  function jobsEvidence(title: string, rows: Rec[], evidenceNote?: string): OmniDetail {
    if (!rows.length) {
      return metricDetail({ label: title, value: 0, meta: 'No source-backed jobs in this scope.', sources: [{ label: 'Open published Omni snapshot', url: SOURCE_ASSETS.omni }] })
    }
    return historyEvidence(
      title,
      rows.map((job) => ({
        id: job.job_id,
        label: job.name || 'Unnamed Omni job',
        timestamp: job.created_at || job.scheduled_at || job.started_at,
        valueSummary: value(job.state) + ' on ' + value(job.queue),
        url: job.url,
        details: { queue: job.queue, state: job.state, pipeline: job.pipeline, build: job.build, exclusion_reason: job.exclusion_reason || null },
      })),
      evidenceNote || 'Every active job links to its exact Buildkite source',
      SOURCE_ASSETS.omni,
    )
  }
  function mappingBreakdownRows(stats: Rec, key: string) {
    return Object.entries((stats || {})[key] || {})
      .map(([name, s]) => ({ name, stats: (s || {}) as Rec }))
      .sort((left, right) => Number(right.stats.mapped_jobs || 0) - Number(left.stats.mapped_jobs || 0) || compareText(left.name, right.name))
  }
  function workloadMappingDetail(workload: 'omni' | 'main', title: string): OmniDetail {
    const stats = mappingTotals[workload] || emptyOmniMappingStats()
    const queueBreakdown = mappingBreakdownRows(stats, 'by_queue')
    const pipelineBreakdown = mappingBreakdownRows(stats, 'by_pipeline')
    type B = (typeof queueBreakdown)[number]
    const tables: DetailTable<B>[] = []
    if (queueBreakdown.length) {
      tables.push({
        title: 'Queue breakdown',
        meta: integer(queueBreakdown.length) + ' queues in the selected window',
        rows: queueBreakdown,
        columns: [
          { label: 'Queue', cell: (row) => ({ text: row.name, mono: true }) },
          { label: 'Mapped', numeric: true, cell: (row) => text(integer(row.stats.mapped_jobs)) },
          { label: 'Started', numeric: true, cell: (row) => text(integer(row.stats.started_jobs)) },
          { label: 'GPU-slot requests', numeric: true, cell: (row) => text(integer(row.stats.mapped_gpu_slots)) },
          { label: 'GPU-hours', numeric: true, cell: (row) => text(gpuHours(row.stats.gpu_hours)) },
        ],
      })
    }
    if (pipelineBreakdown.length) {
      tables.push({
        title: 'Pipeline breakdown',
        meta: integer(pipelineBreakdown.length) + ' exact Buildkite pipelines',
        rows: pipelineBreakdown,
        columns: [
          { label: 'Pipeline', cell: (row) => ({ text: row.name, mono: true }) },
          { label: 'Mapped', numeric: true, cell: (row) => text(integer(row.stats.mapped_jobs)) },
          { label: 'Started', numeric: true, cell: (row) => text(integer(row.stats.started_jobs)) },
          { label: 'GPU-slot requests', numeric: true, cell: (row) => text(integer(row.stats.mapped_gpu_slots)) },
        ],
      })
    }
    return {
      id: 'omni-workload-' + workload,
      title,
      subtitle: selectedMappingLabel + ' on monitored AMD queues',
      description: (mappingView.reason ? mappingView.reason + ' ' : '') + populationBoundaryText,
      fields: [],
      sources: [MAPPING_SOURCE],
      strip: [
        { label: 'OBSERVED MAPPINGS', value: mappingAvailable ? mappingCountPrefix + integer(stats.mapped_jobs) : '-', meta: mappingCountMeta },
        {
          label: 'STARTED JOBS',
          value: mappingAvailable ? mappingCountPrefix + integer(stats.started_jobs) : '-',
          meta: mappingRatesAvailable
            ? percent(stats.started_jobs, stats.mapped_jobs) + ' of mappings'
            : mappingAvailable
              ? 'Rate unavailable: selected bucket coverage is incomplete'
              : mappingView.reason,
        },
        {
          label: 'GPU-SLOT REQUESTS',
          value: mappingAvailable ? integer(stats.mapped_gpu_slots) : '-',
          meta: 'Sum of configured GPU widths across observed mappings; not simultaneous use or GPU-hours',
        },
        { label: 'GPU-HOURS', value: mappingAvailable ? gpuHours(stats.gpu_hours) : '-', meta: 'Finished jobs with usable durations' },
      ],
      tables,
    }
  }
  function mappingMethodology(): OmniDetail {
    const pipelines = (mapping.scope || {}).workload_pipelines || {}
    const apiCollectionLabel = mappingView.apiCollectionComplete
      ? 'Complete inside the configured source window'
      : mappingView.lowerBound
        ? 'Incomplete inside the configured source window'
        : 'Partial or open selected bucket coverage'
    return {
      id: 'omni-mapping-methodology',
      title: 'Mapping scope and coverage',
      subtitle: selectedMappingLabel + ' · ' + (mappingView.resolution === 'hourly' ? 'hourly' : 'UTC-day') + ' source buckets',
      description: (mappingView.reason ? mappingView.reason + ' ' : '') + populationBoundaryText,
      fields: [
        { label: OMNI_REPOSITORIES.omni + ' pipelines', value: (pipelines.omni || []).join(', ') || 'vllm-omni-amd-ci' },
        { label: OMNI_REPOSITORIES.main + ' pipelines', value: (pipelines.main || []).join(', ') || 'ci' },
        {
          label: 'Mapped job',
          value:
            'Unique Buildkite command-job UUID observed inside the declared parent-build source window with an explicit monitored AMD MI queue mapping; retries remain distinct jobs.',
        },
        { label: 'Excluded', value: 'Perf-eval and every non-configured queue.' },
        {
          label: 'Resolution',
          value: mappingView.resolution === 'hourly' ? 'Hourly source aggregates' : mappingView.resolution === 'daily' ? 'UTC calendar-day aggregates' : 'Unavailable',
        },
        { label: 'Retained buckets', value: integer(mappingView.rows.length) },
        {
          label: 'Publication buckets',
          value:
            mappingPublicationRows.source === undefined
              ? 'Not published'
              : integer(mappingPublicationRows.published) + ' of ' + integer(mappingPublicationRows.source) + ' ' + mappingView.resolution + ' rows',
        },
        { label: 'Publication complete', value: mappingPublicationIncomplete ? 'No; oldest whole buckets were omitted to satisfy the storage cap' : 'Yes' },
        { label: 'API / UUID collection', value: apiCollectionLabel },
        {
          label: 'Job-created range exhaustive',
          value: mappingView.jobCreatedRangeExhaustive === true ? 'Yes' : mappingView.jobCreatedRangeExhaustive === false ? 'No' : 'Not published',
        },
        {
          label: 'Parent-build lookback',
          value: mappingView.parentBuildLookbackDays ? integer(mappingView.parentBuildLookbackDays) + ' days before the selected job-created range' : 'Not published',
        },
        {
          label: 'Count integrity',
          value: mappingView.sourceWindowExact ? 'UUID-exact only within the declared parent-build source window' : 'Published aggregate within the declared source window',
        },
        { label: 'Population limitation', value: mappingView.limitation || populationBoundaryText },
      ],
      sources: [MAPPING_SOURCE],
      tables: [],
    }
  }
  const jobRangeLabel = () =>
    mappingView.jobCreatedRangeExhaustive === true ? 'exhaustive' : mappingView.jobCreatedRangeExhaustive === false ? 'not exhaustive' : 'not published'
  function trafficShareDetail(): OmniDetail {
    return {
      id: 'omni-traffic-share',
      title: 'Share of vLLM traffic',
      subtitle: selectedMappingLabel + ' on the same monitored AMD queue allowlist',
      description:
        'Repository counts use the same selected source buckets, parent-build source window, and queue scope; no chart-axis normalization is involved. ' +
        populationBoundaryText,
      fields: [
        { label: OMNI_REPOSITORIES.omni + ' mapped', value: mappingAvailable ? integer(omniTotal.mapped_jobs) : '-' },
        { label: OMNI_REPOSITORIES.main + ' mapped', value: mappingAvailable ? integer(mainTotal.mapped_jobs) : '-' },
        { label: 'Combined mapped jobs', value: mappingAvailable ? integer(combinedMapped) : '-' },
        { label: 'Omni share', value: mappingRatesAvailable ? percent(omniTotal.mapped_jobs, combinedMapped) : 'Unavailable' },
        { label: 'API bucket status', value: mappingView.coverageStatus },
        { label: 'Job-created range', value: jobRangeLabel() },
      ],
      sources: [MAPPING_SOURCE],
      tables: [],
    }
  }
  function queueMappingDetail(row: QueueMappingRow, contextLabel?: string): OmniDetail {
    const queueSnapshot = queueSnapshots[row.name] || {}
    return {
      id: 'omni-impact-' + row.name,
      title: row.name,
      subtitle: (contextLabel || selectedMappingLabel) + ' impact on a monitored AMD queue',
      fields: [
        { label: OMNI_REPOSITORIES.omni + ' observed mappings', value: integer(row.omni.mapped_jobs || 0) },
        { label: OMNI_REPOSITORIES.omni + ' started', value: integer(row.omni.started_jobs || 0) },
        { label: OMNI_REPOSITORIES.omni + ' GPU-slot requests', value: integer(row.omni.mapped_gpu_slots || 0) },
        { label: OMNI_REPOSITORIES.omni + ' GPU-hours', value: gpuHours(row.omni.gpu_hours) },
        { label: OMNI_REPOSITORIES.main + ' observed mappings', value: integer(row.main.mapped_jobs || 0) },
        {
          label: 'Omni share on this queue',
          value: mappingRatesAvailable ? percent(row.omni.mapped_jobs, Number(row.omni.mapped_jobs || 0) + Number(row.main.mapped_jobs || 0)) : 'Unavailable',
        },
        { label: 'Lifecycle', value: row.name.startsWith('amd_mi325_') ? 'retiring' : 'active' },
        { label: 'Scope', value: contextLabel || selectedMappingLabel },
      ],
      sources: [MAPPING_SOURCE, ...(queueSnapshot.queue_url ? [{ label: 'Open Buildkite queue', url: queueSnapshot.queue_url }] : [])],
      tables: [],
    }
  }
  function mi325ExposureDetail(): OmniDetail {
    const rows: QueueMappingRow[] = Object.entries(omniTotal.by_queue || {})
      .filter(([name]) => name.startsWith('amd_mi325_'))
      .map(([name, stats]) => ({ name, omni: stats || {}, main: (mainTotal.by_queue || {})[name] || {} }))
      .sort((left, right) => Number(right.omni.mapped_jobs || 0) - Number(left.omni.mapped_jobs || 0))
    return {
      id: 'omni-mi325-exposure',
      title: 'MI325 retirement exposure',
      subtitle: selectedMappingLabel + ' · retiring queues only',
      description:
        mappingCountPrefix +
        integer(omniRetiringMapped) +
        ' of ' +
        mappingCountPrefix +
        integer(omniTotal.mapped_jobs) +
        ' published source-window Omni mappings targeted MI325. ' +
        (mappingRatesAvailable ? '' : 'The exposure rate is unavailable because selected bucket coverage is incomplete. ') +
        populationBoundaryText,
      fields: [
        { label: 'Omni exposure', value: mappingRatesAvailable ? percent(omniRetiringMapped, omniTotal.mapped_jobs) : 'Unavailable' },
        { label: 'Observed Omni MI325 mappings', value: mappingAvailable ? integer(omniRetiringMapped) : '-' },
        { label: 'Observed ' + OMNI_REPOSITORIES.main + ' MI325 mappings', value: mappingAvailable ? integer(mainRetiringMapped) : '-' },
        { label: 'Job-created range', value: jobRangeLabel() },
      ],
      sources: [MAPPING_SOURCE],
      tables: rows.length
        ? [
            {
              rows,
              columns: [
                { label: 'Retiring queue', cell: (row: QueueMappingRow) => action(row.name, () => queueMappingDetail(row)) },
                { label: 'Omni mapped', numeric: true, cell: (row: QueueMappingRow) => text(integer(row.omni.mapped_jobs)) },
                { label: OMNI_REPOSITORIES.main + ' mapped', numeric: true, cell: (row: QueueMappingRow) => text(integer(row.main.mapped_jobs)) },
                { label: 'Omni GPU-slot requests', numeric: true, cell: (row: QueueMappingRow) => text(integer(row.omni.mapped_gpu_slots)) },
              ],
            },
          ]
        : [],
      empty: rows.length ? undefined : 'No selected-window Omni mappings targeted MI325.',
    }
  }

  const kpis: Kpi[] = [
    {
      id: 'omni-mapped-jobs',
      label: jobRangeNonExhaustive || jobRangeUnknown ? 'OBSERVED OMNI MAPPINGS' : 'INCOMING OMNI JOBS',
      value: mappingAvailable ? mappingCountPrefix + integer(omniTotal.mapped_jobs) : '-',
      meta: mappingAvailable ? mappingCountPrefix + integer(omniTotal.started_jobs) + ' started · ' + mappingCountMeta : mappingView.reason,
      tone: jobRangeNonExhaustive || jobRangeUnknown || !mappingRatesAvailable ? 'is-warning' : 'is-info',
      open: () => workloadMappingDetail('omni', OMNI_REPOSITORIES.omni),
    },
    {
      id: 'omni-gpu-demand',
      label: 'GPU-SLOT REQUESTS',
      value: mappingAvailable ? integer(omniTotal.mapped_gpu_slots) : '-',
      meta: mappingAvailable
        ? 'summed job widths · not concurrency · ' + gpuHours(omniTotal.gpu_hours) + ' completed GPU-hours'
        : 'Hourly collection is required for this window',
      tone: '',
      open: () => workloadMappingDetail('omni', OMNI_REPOSITORIES.omni + ' GPU demand'),
    },
    {
      id: 'omni-mapped-share',
      label: 'SHARE OF OBSERVED VLLM TRAFFIC',
      value: mappingRatesAvailable ? percent(omniTotal.mapped_jobs, combinedMapped) : 'Unavailable',
      meta: mappingAvailable
        ? mappingCountPrefix + integer(omniTotal.mapped_jobs) + ' of ' + mappingCountPrefix + integer(combinedMapped) + ' published source-window mappings'
        : 'No comparable mapping window',
      tone: '',
      open: trafficShareDetail,
    },
    {
      id: 'omni-retiring-share',
      label: 'MI325 RETIREMENT EXPOSURE',
      value: mappingRatesAvailable ? percent(omniRetiringMapped, omniTotal.mapped_jobs) : 'Unavailable',
      meta: mappingAvailable
        ? mappingCountPrefix + integer(omniRetiringMapped) + ' Omni · ' + mappingCountPrefix + integer(mainRetiringMapped) + ' ' + OMNI_REPOSITORIES.main
        : 'No selected-window queue evidence',
      tone: omniRetiringMapped ? 'is-warning' : 'is-success',
      open: mi325ExposureDetail,
    },
  ]

  const apiCoverageHeading =
    mappingView.coverageStatus === 'complete'
      ? 'API/UUID collection complete for the selected closed buckets. '
      : mappingView.coverageStatus === 'open'
        ? 'API/UUID collection complete inside the source window; current bucket open. '
        : mappingView.coverageStatus === 'lower_bound'
          ? 'API/UUID collection is incomplete inside the source window. '
          : 'Selected API/UUID bucket coverage is incomplete. '
  const coverageHeading =
    apiCoverageHeading +
    (jobRangeNonExhaustive ? 'All job-created mappings are not provably exhaustive. ' : jobRangeUnknown ? 'Job-created population coverage is not published. ' : '')
  const scopeNote = {
    tone: (mappingView.retainedComplete && !mappingPublicationIncomplete && !jobRangeNonExhaustive && !jobRangeUnknown ? 'is-info' : 'is-warning') as Tone,
    heading: coverageHeading,
    text:
      (mappingPublicationIncomplete
        ? 'The repository publication omitted ' +
          integer(mappingPublicationRows.omitted) +
          ' oldest ' +
          mappingView.resolution +
          ' buckets (' +
          integer(mappingPublicationRows.published) +
          ' of ' +
          integer(mappingPublicationRows.source) +
          ' published). '
        : '') +
      (mappingView.reason
        ? mappingView.reason + ' '
        : integer(mappingView.rows.length) + ' retained ' + mappingView.resolution + ' buckets; mapped and started are separate counts. ') +
      (mappingRatesAvailable ? '' : 'Rates, shares, and deltas are unavailable for incomplete selected coverage. ') +
      populationBoundaryText,
    methodology: mappingMethodology,
    excluded: excludedJobs.length
      ? () =>
          jobsEvidence(
            'Excluded stale Omni jobs',
            excludedJobs,
            'Jobs beyond the collector age threshold; retained for exact Buildkite review but excluded from active analytics',
          )
      : null,
  }

  const omniByQueue = omniTotal.by_queue || {}
  const mainByQueue = mainTotal.by_queue || {}
  const mappingQueueRows: QueueMappingRow[] = Array.from(new Set(Object.keys(omniByQueue).concat(Object.keys(mainByQueue))))
    .map((queueName) => ({ name: queueName, omni: omniByQueue[queueName] || {}, main: mainByQueue[queueName] || {} }))
    .filter((row) => Number(row.omni.mapped_jobs || 0) > 0)
    .sort((left, right) => Number(right.omni.mapped_jobs || 0) - Number(left.omni.mapped_jobs || 0) || compareText(left.name, right.name))

  const bucketLabel = (bucket: MappingBucket) => omniMappingBucketLabel(bucket, mappingView.resolution)
  function mappingBucketDetail(bucket: MappingBucket): OmniDetail {
    const bucketOmni = (bucket.workloads || {}).omni || {}
    const bucketMain = (bucket.workloads || {}).main || {}
    const queueRows: QueueMappingRow[] = Object.entries(bucketOmni.by_queue || {})
      .map(([name, stats]) => ({ name, omni: stats || {}, main: (bucketMain.by_queue || {})[name] || {} }))
      .sort((left, right) => Number(right.omni.mapped_jobs || 0) - Number(left.omni.mapped_jobs || 0))
    return {
      id: 'omni-bucket-' + bucket.id,
      title: bucketLabel(bucket) + ' UTC',
      subtitle: 'Incoming Omni workload mapped during this chart bucket',
      fields: [
        { label: OMNI_REPOSITORIES.omni + ' mapped', value: integer(bucketOmni.mapped_jobs) },
        { label: OMNI_REPOSITORIES.omni + ' started', value: integer(bucketOmni.started_jobs) },
        { label: OMNI_REPOSITORIES.omni + ' GPU-slot requests', value: integer(bucketOmni.mapped_gpu_slots) },
        { label: OMNI_REPOSITORIES.main + ' mapped', value: integer(bucketMain.mapped_jobs) },
        {
          label: 'API / UUID coverage',
          value: bucket.lowerBound
            ? 'collection lower bound'
            : bucket.hasOpenBucket
              ? 'open current UTC ' + (mappingView.resolution === 'daily' ? 'day' : 'hour')
              : bucket.complete
                ? 'complete'
                : 'partial (' + integer(bucket.sourceRows) + '/' + integer(bucket.expectedSourceRows) + ' source buckets)',
        },
      ],
      sources: [MAPPING_SOURCE],
      tables: queueRows.length
        ? [
            {
              rows: queueRows,
              columns: [
                { label: 'Queue', cell: (row: QueueMappingRow) => action(row.name, () => queueMappingDetail(row, bucketLabel(bucket) + ' UTC chart bucket')) },
                { label: OMNI_REPOSITORIES.omni, numeric: true, cell: (row: QueueMappingRow) => text(integer(row.omni.mapped_jobs)) },
                { label: OMNI_REPOSITORIES.main, numeric: true, cell: (row: QueueMappingRow) => text(integer(row.main.mapped_jobs)) },
                { label: 'GPU-slot requests', numeric: true, cell: (row: QueueMappingRow) => text(integer(row.omni.mapped_gpu_slots)) },
              ],
            },
          ]
        : [],
    }
  }
  const coverageLabel = (bucket: MappingBucket) => (bucket.lowerBound ? 'lower bound' : bucket.hasOpenBucket ? 'open' : bucket.complete ? 'complete' : 'partial')
  function mappingEvidence(bucket: MappingBucket): EvidencePoint {
    const bucketOmni = (bucket.workloads || {}).omni || {}
    return {
      id: bucket.id,
      label: bucketLabel(bucket) + ' UTC',
      timestamp: new Date(bucket.start).toISOString(),
      valueSummary: integer(bucketOmni.mapped_jobs) + ' mapped · ' + integer(bucketOmni.started_jobs) + ' started',
      details: {
        repository: OMNI_REPOSITORIES.omni,
        mapped_jobs: bucketOmni.mapped_jobs,
        started_jobs: bucketOmni.started_jobs,
        mapped_gpu_slots: bucketOmni.mapped_gpu_slots,
        api_uuid_coverage: coverageLabel(bucket),
        job_created_range_exhaustive: mappingView.jobCreatedRangeExhaustive,
      },
      sources: [MAPPING_SOURCE],
      onOpen: () => mappingBucketDetail(bucket),
    }
  }
  function browseMappingBuckets(): OmniDetail {
    const rows = mappingBuckets.slice().reverse()
    return {
      id: 'omni-mapping-buckets',
      title: OMNI_REPOSITORIES.omni + ' mapping buckets',
      subtitle:
        selectedMappingLabel +
        ' · API/UUID coverage per bucket · ' +
        (jobRangeNonExhaustive ? 'job-created range not exhaustive' : 'population scope published separately'),
      fields: [],
      sources: [],
      tables: [
        {
          rows,
          columns: [
            { label: 'UTC bucket', cell: (bucket: MappingBucket) => action(bucketLabel(bucket), () => mappingBucketDetail(bucket)) },
            { label: 'Omni mapped', numeric: true, cell: (bucket: MappingBucket) => text(integer(bucket.workloads.omni.mapped_jobs)) },
            { label: 'Omni started', numeric: true, cell: (bucket: MappingBucket) => text(integer(bucket.workloads.omni.started_jobs)) },
            { label: 'GPU-slot requests', numeric: true, cell: (bucket: MappingBucket) => text(integer(bucket.workloads.omni.mapped_gpu_slots)) },
            {
              label: 'API coverage',
              cell: (bucket: MappingBucket) => badge(coverageLabel(bucket), bucket.lowerBound ? 'is-warning' : bucket.complete ? 'is-success' : 'is-info'),
            },
          ],
          searchText: (bucket: MappingBucket) => [bucketLabel(bucket), bucket.complete, bucket.lowerBound, bucket.hasOpenBucket].join(' '),
          searchPlaceholder: 'Filter UTC bucket or coverage',
        },
      ],
    }
  }

  const volume: OmniView['volume'] = mappingBuckets.length
    ? {
        available: true,
        title: 'Observed incoming mappings from ' + OMNI_REPOSITORIES.omni,
        meta: integer(mappingBuckets.length) + ' chart buckets · ' + selectedMappingLabel + ' · source-window counts · one shared scale',
        bars: mappingBuckets.map((bucket) => ({
          label: bucketLabel(bucket),
          value: Number(bucket.workloads.omni.mapped_jobs || 0),
          color: 'omni' as const,
          open: () => inspectChartPoint(mappingEvidence(bucket), SOURCE_ASSETS.workloadMapping),
        })),
        evidence: () =>
          historyEvidence(
            OMNI_REPOSITORIES.omni + ' mapping buckets',
            mappingBuckets.map(mappingEvidence),
            'Chart values and their retained source evidence',
            SOURCE_ASSETS.workloadMapping,
          ),
      }
    : { available: false, title: 'Incoming Omni workload unavailable', meta: selectedMappingLabel, empty: mappingView.reason }

  const share = (row: QueueMappingRow) => (mappingRatesAvailable ? percent(row.omni.mapped_jobs, omniTotal.mapped_jobs) : 'Unavailable')
  const impactEvidence = (row: QueueMappingRow): EvidencePoint => ({
    id: row.name,
    label: row.name,
    valueSummary: integer(row.omni.mapped_jobs) + ' observed source-window mappings',
    details: { repository: OMNI_REPOSITORIES.omni, mapped_jobs: row.omni.mapped_jobs, share: share(row) },
    onOpen: () => queueMappingDetail(row),
  })
  const impact: OmniView['impact'] = mappingQueueRows.length
    ? {
        available: true,
        title: 'Where Omni lands',
        meta: integer(mappingQueueRows.length) + ' AMD queues · click a bar or row for impact details',
        bars: mappingQueueRows.map((row) => ({
          label: row.name,
          value: Number(row.omni.mapped_jobs || 0),
          color: row.name.startsWith('amd_mi325_') ? ('retiring' as const) : ('omni' as const),
          open: () => queueMappingDetail(row),
        })),
        preview: {
          rows: mappingQueueRows.slice(0, 6),
          columns: [
            { label: 'Queue', cell: (row) => action(row.name, () => queueMappingDetail(row)) },
            { label: 'Mapped', numeric: true, cell: (row) => action(integer(row.omni.mapped_jobs), () => queueMappingDetail(row)) },
            { label: 'Share', numeric: true, cell: (row) => text(share(row)) },
          ],
        },
        evidence: () =>
          historyEvidence(
            'Omni queue impact in ' + selectedMappingLabel,
            mappingQueueRows.map(impactEvidence),
            'Chart values and their retained source evidence',
            SOURCE_ASSETS.workloadMapping,
          ),
      }
    : {
        available: false,
        title: 'Where Omni lands',
        meta: 'No selected-window queue mappings',
        empty: mappingView.reason || 'No Omni mappings were observed in this window.',
      }

  const comparisonRows = [
    { id: 'omni' as const, repository: OMNI_REPOSITORIES.omni, stats: omniTotal },
    { id: 'main' as const, repository: OMNI_REPOSITORIES.main, stats: mainTotal },
  ]
  type CRow = (typeof comparisonRows)[number]
  const comparison: OmniView['comparison'] = {
    title: 'Repository comparison',
    meta: selectedMappingLabel + ' · comparison stays numeric instead of sharing a misleading chart scale',
    table: {
      rows: comparisonRows,
      columns: [
        { label: 'Repository', cell: (row: CRow) => action(row.repository, () => workloadMappingDetail(row.id, row.repository)) },
        { label: 'Mapped', numeric: true, cell: (row: CRow) => text(mappingAvailable ? integer(row.stats.mapped_jobs) : '-') },
        { label: 'Started', numeric: true, cell: (row: CRow) => text(mappingAvailable ? integer(row.stats.started_jobs) : '-') },
        {
          label: 'Start rate',
          numeric: true,
          cell: (row: CRow) => text(mappingRatesAvailable ? percent(row.stats.started_jobs, row.stats.mapped_jobs) : 'Unavailable'),
        },
        { label: 'GPU-slot requests', numeric: true, cell: (row: CRow) => text(mappingAvailable ? integer(row.stats.mapped_gpu_slots) : '-') },
        { label: 'GPU-hours', numeric: true, cell: (row: CRow) => text(mappingAvailable ? gpuHours(row.stats.gpu_hours) : '-') },
      ],
    },
    browseBuckets: browseMappingBuckets,
    browseQueues: mappingQueueRows.length
      ? () => ({
          id: 'omni-queue-impact-browser',
          title: 'Omni impact by AMD queue',
          subtitle:
            selectedMappingLabel +
            ' · UUID-deduplicated source-window mapping aggregates · ' +
            (jobRangeNonExhaustive ? 'job-created range not exhaustive' : 'population scope published separately'),
          fields: [],
          sources: [],
          tables: [
            {
              rows: mappingQueueRows,
              columns: [
                { label: 'Queue', cell: (row: QueueMappingRow) => action(row.name, () => queueMappingDetail(row)) },
                { label: 'Omni mapped', numeric: true, cell: (row: QueueMappingRow) => text(integer(row.omni.mapped_jobs)) },
                { label: 'Omni started', numeric: true, cell: (row: QueueMappingRow) => text(integer(row.omni.started_jobs)) },
                { label: OMNI_REPOSITORIES.main + ' mapped', numeric: true, cell: (row: QueueMappingRow) => text(integer(row.main.mapped_jobs)) },
                { label: 'Omni GPU-hours', numeric: true, cell: (row: QueueMappingRow) => text(gpuHours(row.omni.gpu_hours)) },
              ],
              searchText: (row: QueueMappingRow) => row.name,
              searchPlaceholder: 'Filter queue',
            },
          ],
        })
      : null,
  }

  // Live queue state
  const allHistoryPoints = omniHistoryPoints(omni)
  const waitingHistoryPoints = allHistoryPoints.filter((point) => point.waitingSupported)
  const occupancyHistoryPoints = allHistoryPoints.filter((point) => point.waitingSupported || point.runningSupported)
  const points = omniWindowPoints(occupancyHistoryPoints, omniRange)
  const selectedRange = OMNI_RANGE_WINDOWS.find((item) => item.id === omniRange) || OMNI_RANGE_WINDOWS[4]!
  const waitingPoints = points.filter((point) => point.waitingSupported)
  const runningPoints = points.filter((point) => point.runningSupported)
  const latestPoint = waitingPoints.length ? waitingPoints[waitingPoints.length - 1]! : null
  const waitingPeak = waitingPoints.length ? Math.max(...waitingPoints.map((point) => point.allWaiting as number)) : null
  const windowCoverage =
    waitingPoints.length && waitingPoints.every((point) => point.waitingCoverage === 'complete') ? 'complete' : waitingPoints.length ? 'partial' : 'unavailable'
  function occupancyEvidencePoints(rows: HistoryPoint[]): EvidencePoint[] {
    return rows.map((point) => ({
      label: shortDate(point.ts),
      timestamp: point.ts,
      valueSummary:
        (point.waitingSupported ? integer(point.allWaiting) : 'unavailable') +
        ' observed waiting - ' +
        (point.runningSupported ? integer(point.allRunning) : 'unavailable') +
        ' observed running',
      details: {
        monitored_amd_waiting_observed: point.amdWaiting,
        monitored_amd_running_observed: point.amdRunning,
        waiting_supported: point.waitingSupported,
        running_supported: point.runningSupported,
        amd_waiting_supported: point.amdWaitingSupported,
        amd_running_supported: point.amdRunningSupported,
        waiting_attribution: point.waitingCoverage,
        running_attribution: point.runningCoverage,
      },
      sources: [{ label: 'Open published queue history', url: SOURCE_ASSETS.queueHistory }],
    }))
  }
  function occupancyEvidence(title: string, rows: HistoryPoint[], note: string): OmniDetail {
    if (rows.length) return historyEvidence(title, occupancyEvidencePoints(rows), note, SOURCE_ASSETS.queueHistory)
    return metricDetail({
      id: 'omni-occupancy-unavailable',
      label: title,
      value: 'unavailable',
      meta: 'No workload-attributed occupancy snapshots fall inside ' + selectedRange.label + '. Aggregate queue totals are not reclassified as Omni.',
      sources: [{ label: 'Inspect published queue history', url: SOURCE_ASSETS.queueHistory }],
    })
  }
  const queueRows = Array.from(affected)
    .sort()
    .map((name) => {
      const relatedPending = pending.filter((job) => (job.queue || 'unknown') === name).length
      const relatedRunning = running.filter((job) => (job.queue || 'unknown') === name).length
      return {
        name,
        waiting: Object.prototype.hasOwnProperty.call(waitingByQueue, name) ? Number(waitingByQueue[name] || 0) : relatedPending,
        running: Object.prototype.hasOwnProperty.call(runningByQueue, name) ? Number(runningByQueue[name] || 0) : relatedRunning,
        jobs: relatedPending + relatedRunning,
      }
    })
  const dailyRows = omniDailyRows(waitingHistoryPoints)
  const visibleJobs = activeJobs.slice().sort((left, right) => Number(right.wait_min || 0) - Number(left.wait_min || 0) || compareText(left.name, right.name))
  const openQueue = (name: string) => queueDetail(name, queueSnapshots[name] || {}, activeJobs)
  function browseActiveJobs(): OmniDetail {
    return {
      id: 'omni-job-browser',
      title: 'Current Omni CI jobs on monitored AMD queues',
      subtitle: 'Every row links to the exact Buildkite job; aggregate queue totals are never expanded into synthetic jobs',
      fields: [],
      sources: [],
      tables: [
        {
          rows: visibleJobs,
          columns: [
            { label: 'Job', cell: (r: Rec) => link(r.name || 'Unnamed job', r.url) },
            { label: 'Queue', cell: (r: Rec) => action(value(r.queue), () => openQueue(r.queue)) },
            { label: 'State', cell: (r: Rec) => linkedBadge(r.state, r.url) },
            { label: 'Age', numeric: true, cell: (r: Rec) => link(duration(r.wait_min !== undefined ? r.wait_min : r.run_min), r.url) },
            { label: 'Analysis', cell: (r: Rec) => badge(r.analysis_excluded ? 'stale excluded' : 'active', r.analysis_excluded ? 'is-warning' : 'is-success') },
            { label: 'Build', cell: (r: Rec) => link((r.pipeline || '?') + ' #' + value(r.build), r.build_url || buildUrl(r.pipeline, r.build), true) },
            { label: 'Source', cell: (r: Rec) => linkedBadge(r.source || r.workload || 'omni', r.url, 'is-info') },
          ],
          searchText: (row: Rec) => [row.name, row.queue, row.pipeline, row.branch, row.state].join(' '),
          searchPlaceholder: 'Filter job, queue, pipeline, branch, or state',
        },
      ],
    }
  }
  function browseCurrentQueues(): OmniDetail {
    type Q = (typeof queueRows)[number]
    return {
      id: 'omni-current-queues',
      title: 'Current Omni CI queue distribution',
      subtitle: 'Exact active jobs on the monitored AMD allowlist',
      fields: [],
      sources: [],
      tables: [
        {
          rows: queueRows,
          columns: [
            { label: 'Queue', cell: (row: Q) => action(row.name, () => openQueue(row.name)) },
            { label: 'Waiting', numeric: true, cell: (row: Q) => text(integer(row.waiting)) },
            { label: 'Running', numeric: true, cell: (row: Q) => text(integer(row.running)) },
            { label: 'Exact jobs', numeric: true, cell: (row: Q) => text(integer(row.jobs)) },
          ],
          searchText: (row: Q) => row.name,
          searchPlaceholder: 'Filter queue',
        },
      ],
    }
  }
  function browseDailyContext(): OmniDetail {
    return {
      id: 'omni-legacy-occupancy',
      title: 'Closing occupancy context by UTC day',
      subtitle: 'Snapshot occupancy, not unique-job volume',
      fields: [],
      sources: [],
      tables: [
        {
          rows: dailyRows,
          columns: [
            {
              label: 'UTC day',
              cell: (row: DailyRow) =>
                action(row.day, () =>
                  historyEvidence(
                    'Omni queue observations on ' + row.day,
                    occupancyEvidencePoints(allHistoryPoints.filter((point) => new Date(point.time).toISOString().slice(0, 10) === row.day)),
                    'Every workload-attributed snapshot retained for this UTC day',
                    SOURCE_ASSETS.queueHistory,
                  ),
                ),
            },
            { label: 'Closing queued', numeric: true, cell: (row: DailyRow) => text(integer(row.waiting)) },
            { label: 'Day change', numeric: true, cell: (row: DailyRow) => text(row.delta === null ? '-' : signedInteger(row.delta)) },
            { label: 'Daily peak', numeric: true, cell: (row: DailyRow) => text(integer(row.peak)) },
            { label: 'Samples', numeric: true, cell: (row: DailyRow) => text(integer(row.samples)) },
            {
              label: 'Attribution',
              cell: (row: DailyRow) => badge(row.complete ? 'complete' : 'partial lower bound', row.complete ? 'is-success' : 'is-warning'),
            },
          ],
          searchText: (row: DailyRow) => [row.day, row.complete].join(' '),
          searchPlaceholder: 'Filter UTC day or attribution',
        },
      ],
    }
  }
  const facts: LiveFact[] = [
    {
      label: 'EXACT ACTIVE JOBS',
      value: integer(activeJobs.length),
      meta: integer(ledgerWaiting) + ' waiting · ' + integer(ledgerRunning) + ' running',
      tone: pending.length ? 'is-warning' : '',
      open: browseActiveJobs,
    },
    {
      label: 'OBSERVED QUEUED',
      value: latestPoint ? integer(latestPoint.allWaiting) : '-',
      meta: latestPoint ? shortDate(latestPoint.ts) : 'No waiting-attributed snapshot in ' + selectedRange.label,
      tone: latestPoint && latestPoint.allWaiting ? 'is-warning' : '',
      open: () => occupancyEvidence('Latest observed Omni queue', latestPoint ? [latestPoint] : [], 'Explicit waiting-attributed counts only'),
    },
    {
      label: 'QUEUED PEAK',
      value: waitingPeak === null ? '-' : integer(waitingPeak),
      meta: integer(waitingPoints.length) + ' waiting-attributed snapshots · ' + selectedRange.label,
      tone: waitingPeak ? 'is-warning' : '',
      open: () => occupancyEvidence('Omni queued observations', waitingPoints, 'Snapshot occupancy is operational context, not unique-job volume'),
    },
    {
      label: 'ATTRIBUTED SNAPSHOTS',
      value: points.length ? integer(points.length) : '-',
      meta: integer(waitingPoints.length) + ' waiting · ' + integer(runningPoints.length) + ' running',
      tone: points.length && windowCoverage === 'complete' ? 'is-success' : 'is-warning',
      open: () =>
        occupancyEvidence('Omni workload attribution coverage', points, 'Waiting and running availability are retained independently; partial attribution remains a lower bound'),
    },
  ]

  return {
    available: true,
    header: {
      title: 'Omni CI',
      description: 'Incoming ' + OMNI_REPOSITORIES.omni + ' workload and its impact on the AMD queues shared with ' + OMNI_REPOSITORIES.main + '.',
      observedAt: mapping.generated_at || (omni.provenance || {}).queue_snapshot_ts || null,
      actionLabel: 'Open AMD mapping aggregate',
      actionUrl: SOURCE_ASSETS.workloadMapping,
    },
    mappingRange: mappingView.selected.id,
    omniRange: selectedRange.id,
    mappingView,
    kpis,
    scopeNote,
    methodology: mappingMethodology,
    volume,
    impact,
    comparison,
    live: {
      title: 'Live AMD queue state',
      meta: 'Exact jobs now plus ' + selectedRange.label + ' observed occupancy; distinct from unique mapping volume above',
      facts,
      actions: [
        { label: 'Active jobs (' + integer(activeJobs.length) + ')', open: browseActiveJobs },
        { label: 'Current queues (' + integer(queueRows.length) + ')', open: browseCurrentQueues },
        {
          label: 'Occupancy history',
          open: () => occupancyEvidence('Omni occupancy observations in ' + selectedRange.label, points, 'Observed workload-attributed snapshots only'),
        },
        { label: 'Daily closing context', open: browseDailyContext },
      ],
      queueHistoryUrl: SOURCE_ASSETS.queueHistory,
    },
  }
}
