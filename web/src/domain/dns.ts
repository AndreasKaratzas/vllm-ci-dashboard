// CI Analytics "DNS health": a port of the classic queueDns* helpers and the
// summary logic of renderAnalyticsDns (docs/assets/js/ops-v2.js).
import { integer, value, type Rec, type Tone } from './format'
import { isCanonicalAmdQueue } from './health'

export const QUEUE_DNS_LIVE_BASE =
  'https://raw.githubusercontent.com/AndreasKaratzas/vllm-ci-dashboard/dns-health-data/data/vllm/ci/'
export const DNS_LIVE_ASSET = QUEUE_DNS_LIVE_BASE + 'dns_failures.json'
export const DNS_FALLBACK_ASSET = 'data/vllm/ci/dns_failures.json'
export const DNS_AUTO_REFRESH_MS = 5 * 60 * 1000

export const QUEUE_DNS_WINDOW_OPTIONS = [
  { id: '1h', label: 'Last hour', hours: 1 },
  { id: '3h', label: 'Last 3 hours', hours: 3 },
  { id: '12h', label: 'Last 12 hours', hours: 12 },
  { id: '24h', label: 'Last day', hours: 24 },
  { id: '72h', label: 'Last 3 days', hours: 72 },
  { id: '168h', label: 'Last 7 days', hours: 168 },
  { id: '720h', label: 'Last 30 days', hours: 720 },
] as const
export const QUEUE_DNS_WINDOW_IDS: string[] = QUEUE_DNS_WINDOW_OPTIONS.map((option) => option.id)
export const QUEUE_DNS_STALE_MS = 12 * 60 * 60 * 1000
export const QUEUE_DNS_FETCH_TIMEOUT_MS = 8 * 1000
export const QUEUE_DNS_ARBITRATION_MS = 200
export const QUEUE_DNS_OUTCOME_CONTRACT = 'dns-job-outcomes-v1'
const QUEUE_DNS_PIPELINES = new Set(['ci'])
const QUEUE_DNS_JOB_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const QUEUE_DNS_UTC_SECOND_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/
const QUEUE_DNS_WINDOW_METRIC_KEYS = ['first_at', 'last_at', 'episodes', 'match_count', 'signature_ids', 'target_categories']

const hasOwn = (target: object, key: string) => Object.prototype.hasOwnProperty.call(target, key)
const isPlainObject = (raw: unknown): raw is Rec => !!raw && typeof raw === 'object' && !Array.isArray(raw)

export function queueTimestamp(raw: unknown): number {
  const parsed = new Date((raw as string) || '').getTime()
  return Number.isFinite(parsed) ? parsed : -Infinity
}

export function compareText(left: unknown, right: unknown): number {
  return String(left || '').localeCompare(String(right || ''), undefined, { sensitivity: 'base' })
}

export function isRetiredQueue(queue: unknown): boolean {
  return /^amd_mi355b(?:_|$)/i.test(String(queue || '').trim().toLowerCase())
}

export function queueDnsCount(raw: unknown): number {
  if (raw === null || raw === undefined || raw === '' || typeof raw === 'boolean') return 0
  const parsed = Number(raw)
  return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : 0
}

export interface DnsOutcomes {
  available: boolean
  passed: number
  softFailed: number
  hardFailed: number
}

export function queueDnsOutcomeCounts(raw: Rec | null | undefined): DnsOutcomes {
  const source = raw || {}
  const keys = ['passed_jobs', 'soft_failed_jobs', 'hard_failed_jobs']
  const available =
    keys.every((key) => hasOwn(source, key) && Number.isInteger(source[key]) && source[key] >= 0) &&
    Number.isInteger(source.affected_jobs) &&
    source.affected_jobs >= 0
  const passed = queueDnsCount(source.passed_jobs)
  const softFailed = queueDnsCount(source.soft_failed_jobs)
  const hardFailed = queueDnsCount(source.hard_failed_jobs)
  return {
    available: available && passed + softFailed + hardFailed === queueDnsCount(source.affected_jobs),
    passed,
    softFailed,
    hardFailed,
  }
}

export function queueDnsRowsComplete(payload: Rec, id: string, count: number): boolean | null {
  if (payload.publication_retention === undefined) return true
  const row = ((payload.publication_retention || {}).window_rows || {})[id]
  if (!row || typeof row !== 'object' || Array.isArray(row)) return null
  return [row.source, row.published, row.omitted].every(Number.isSafeInteger) &&
    row.published === count &&
    row.omitted >= 0 &&
    row.source === row.published + row.omitted &&
    row.complete === (row.omitted === 0)
    ? row.complete
    : null
}

export function queueDnsPayloadValid(payload: unknown): boolean {
  if (!isPlainObject(payload)) return false
  if (payload.schema_version !== 1 || queueTimestamp(payload.generated_at) === -Infinity) return false
  if (!isPlainObject(payload.retention)) return false
  const outcomesMarked = hasOwn(payload, 'outcome_contract')
  if (outcomesMarked && payload.outcome_contract !== QUEUE_DNS_OUTCOME_CONTRACT) return false
  if (!Array.isArray(payload.window_options) || payload.window_options.length !== QUEUE_DNS_WINDOW_OPTIONS.length) return false
  if (
    !payload.window_options.every((option: unknown, index: number) => {
      const expected = QUEUE_DNS_WINDOW_OPTIONS[index]!
      return isPlainObject(option) && option.id === expected.id && option.label === expected.label && option.hours === expected.hours
    })
  )
    return false
  if (payload.default_window !== '24h') return false
  if (payload.count_basis === null || payload.count_basis === undefined) return false
  if (
    !isPlainObject(payload.scope) ||
    payload.scope.hardware_scope !== 'amd_mi_gpu' ||
    !Array.isArray(payload.scope.pipelines) ||
    payload.scope.pipelines.length !== 1 ||
    payload.scope.pipelines[0] !== 'ci'
  )
    return false
  if (!isPlainObject(payload.classifier)) return false
  if (!isPlainObject(payload.coverage)) return false
  if (!isPlainObject(payload.windows)) return false
  if (!isPlainObject(payload.evidence) || !Array.isArray(payload.evidence.items)) return false
  const publishedWindowIds = Object.keys(payload.windows)
  if (publishedWindowIds.length !== QUEUE_DNS_WINDOW_IDS.length || !QUEUE_DNS_WINDOW_IDS.every((id) => publishedWindowIds.includes(id)))
    return false
  const generatedAtMs = queueTimestamp(payload.generated_at)
  if (queueTimestamp(payload.retention.end_exclusive) !== generatedAtMs) return false
  const windowsValid = QUEUE_DNS_WINDOW_OPTIONS.every((option) => {
    const windowBlock = payload.windows[option.id]
    const startMs = queueTimestamp(windowBlock && windowBlock.start)
    const endMs = queueTimestamp(windowBlock && windowBlock.end_exclusive)
    const structurallyValid =
      isPlainObject(windowBlock) &&
      startMs !== -Infinity &&
      endMs === generatedAtMs &&
      endMs - startMs === option.hours * 60 * 60 * 1000 &&
      isPlainObject(windowBlock.coverage) &&
      isPlainObject(windowBlock.totals) &&
      Array.isArray(windowBlock.rows)
    if (!structurallyValid) return false
    const rowsComplete = queueDnsRowsComplete(payload, option.id, windowBlock.rows.length)
    if (rowsComplete === null || !outcomesMarked) return rowsComplete !== null
    const totals = queueDnsOutcomeCounts(windowBlock.totals)
    if (!totals.available || !windowBlock.rows.every((row: Rec) => queueDnsOutcomeCounts(row).available)) return false
    return (
      [
        ['passed_jobs', totals.passed],
        ['soft_failed_jobs', totals.softFailed],
        ['hard_failed_jobs', totals.hardFailed],
      ] as const
    ).every(([key, total]) => {
      const published = windowBlock.rows.reduce((sum: number, row: Rec) => sum + queueDnsCount(row[key]), 0)
      return rowsComplete ? total === published : published <= total
    })
  })
  return windowsValid && payload.evidence.items.every((row: Rec) => queueDnsEvidenceItemValid(row, payload.windows))
}

export interface DnsCandidate {
  payload: Rec
  source: string
  priority: number
}

/** Newer generated_at wins; equal timestamps prefer the live source (lower priority). */
export function compareQueueDnsCandidates(left: DnsCandidate, right: DnsCandidate): number {
  const leftGeneratedAt = queueTimestamp(left.payload.generated_at)
  const rightGeneratedAt = queueTimestamp(right.payload.generated_at)
  if (leftGeneratedAt !== rightGeneratedAt) return rightGeneratedAt > leftGeneratedAt ? 1 : -1
  return left.priority - right.priority
}

export function queueDnsWindow(payload: Rec | null | undefined, requestedWindow: string): { id: string; block: Rec | null } {
  const windows = (payload || {}).windows || {}
  const publishedOptions: string[] = Array.isArray((payload || {}).window_options)
    ? payload!.window_options.map((option: unknown) => String(option && typeof option === 'object' ? (option as Rec).id : option))
    : []
  const requested =
    QUEUE_DNS_WINDOW_IDS.includes(requestedWindow) && publishedOptions.includes(requestedWindow) ? requestedWindow : null
  const publishedDefault = String((payload || {}).default_window || '')
  const fallback =
    QUEUE_DNS_WINDOW_IDS.includes(publishedDefault) && publishedOptions.includes(publishedDefault)
      ? publishedDefault
      : QUEUE_DNS_WINDOW_IDS.find((id) => publishedOptions.includes(id) && windows[id])
  const id = requested && windows[requested] ? requested : fallback
  return { id: id || requestedWindow, block: (id && windows[id]) || null }
}

export interface DnsCoverage {
  complete: boolean
  status: string
  notes: string[]
  facts: string[]
}

export function queueDnsCoverage(payload: Rec | null | undefined, windowBlock: Rec | null | undefined): DnsCoverage {
  const globalCoverage = (payload || {}).coverage || {}
  const localCoverage = (windowBlock || {}).coverage || {}
  const hasLocalCoverage = Boolean(windowBlock && isPlainObject(windowBlock.coverage))
  const selectedCoverage: Rec = hasLocalCoverage ? localCoverage : globalCoverage
  const status = String(selectedCoverage.status || 'unknown')
    .trim()
    .toLowerCase()
  const explicitlyIncomplete =
    selectedCoverage.complete === false ||
    selectedCoverage.discovery_complete === false ||
    ['not_collected', 'partial', 'failed', 'error', 'incomplete', 'unavailable', 'unknown'].includes(status)
  const explicitlyComplete = selectedCoverage.complete === true || status === 'complete'
  const selectedWindowId = Object.keys((payload || {}).windows || {}).find(
    (windowId) => (payload!.windows || {})[windowId] === windowBlock,
  )
  const publicationRows = ((((payload || {}).publication_retention || {}).window_rows || {}) as Rec)[selectedWindowId as string] || {}
  const publicationIncomplete = publicationRows.complete === false
  const complete = explicitlyComplete && !explicitlyIncomplete && !publicationIncomplete
  const notes: string[] = []
  for (const key of ['reason', 'detail', 'limitation']) {
    if (selectedCoverage[key]) notes.push(String(selectedCoverage[key]))
  }
  for (const key of ['problems', 'warnings', 'limitations']) {
    if (Array.isArray(selectedCoverage[key])) selectedCoverage[key].forEach((item: unknown) => item && notes.push(String(item)))
  }
  const numericFacts = (
    [
      ['jobs scanned', selectedCoverage.scanned_jobs],
      ['eligible jobs', selectedCoverage.eligible_jobs],
      ['positive jobs', selectedCoverage.positive_jobs],
      ['jobs pending', selectedCoverage.pending_jobs],
      ['logs unavailable', selectedCoverage.unavailable_jobs],
      ['oversized logs', selectedCoverage.oversize_jobs],
      ['parse failures', selectedCoverage.parse_failures],
    ] as [string, unknown][]
  ).filter((fact) => fact[1] !== null && fact[1] !== undefined && fact[1] !== '')
  if (publicationIncomplete) {
    notes.push(
      'public storage retained ' + integer(publicationRows.published) + ' of ' + integer(publicationRows.source) + ' aggregate rows for this window',
    )
  }
  return {
    complete,
    status: status || (complete ? 'complete' : 'unknown'),
    notes: Array.from(new Set(notes)),
    facts: numericFacts.map((fact) => integer(fact[1]) + ' ' + fact[0]),
  }
}

export function queueDnsFreshness(payload: Rec | null | undefined, windowBlock: Rec | null | undefined, nowMs?: number) {
  const clock = Number.isFinite(Number(nowMs)) ? Number(nowMs) : Date.now()
  const generatedAtMs = queueTimestamp((payload || {}).generated_at)
  const windowEndMs = queueTimestamp((windowBlock || {}).end_exclusive)
  const generatedAgeMs = generatedAtMs === -Infinity ? Infinity : Math.max(0, clock - generatedAtMs)
  const windowAgeMs = windowEndMs === -Infinity ? Infinity : Math.max(0, clock - windowEndMs)
  return {
    stale: generatedAgeMs > QUEUE_DNS_STALE_MS || windowAgeMs > QUEUE_DNS_STALE_MS,
    generatedAgeMs,
    windowAgeMs,
    thresholdMs: QUEUE_DNS_STALE_MS,
  }
}

export type DnsScope = 'canonical' | 'amd'

export function queueDnsScope(requestedScope: unknown): DnsScope {
  return requestedScope === 'canonical' ? 'canonical' : 'amd'
}

export function queueDnsMatchesPublishedScope(queue: unknown, requestedScope: unknown): boolean {
  const name = String(queue || '')
    .trim()
    .toLowerCase()
  if (!/^amd_mi\d{3,4}(?:_|$)/i.test(name) || isRetiredQueue(name)) return false
  return queueDnsScope(requestedScope) !== 'canonical' || isCanonicalAmdQueue(name)
}

export interface DnsNodeRow {
  queue: string
  node: string
  nodeRaw: string
  affectedJobs: number
  episodes: number
  huggingfaceAffectedJobs: number
  evidenceTotal: number
  passedJobs: number
  softFailedJobs: number
  hardFailedJobs: number
  outcomesAvailable: boolean
}

export interface DnsQueueRow extends Omit<DnsNodeRow, 'node' | 'nodeRaw'> {
  nodes: DnsNodeRow[]
}

export function queueDnsNodeRows(windowBlock: Rec | null | undefined, requestedScope: unknown): DnsNodeRow[] {
  const grouped = new Map<string, DnsNodeRow>()
  for (const raw of (windowBlock || {}).rows || []) {
    const queue = String((raw || {}).queue || '').trim()
    if (!queue || !queueDnsMatchesPublishedScope(queue, requestedScope)) continue
    const nodeRaw = String((raw || {}).node || '').trim()
    const node = nodeRaw || '(unidentified)'
    const key = queue + '\u001f' + node
    const outcomes = queueDnsOutcomeCounts(raw)
    const current = grouped.get(key) || {
      queue,
      node,
      nodeRaw,
      affectedJobs: 0,
      episodes: 0,
      huggingfaceAffectedJobs: 0,
      evidenceTotal: 0,
      passedJobs: 0,
      softFailedJobs: 0,
      hardFailedJobs: 0,
      outcomesAvailable: true,
    }
    current.affectedJobs += queueDnsCount(raw.affected_jobs)
    current.episodes += queueDnsCount(raw.episodes)
    current.huggingfaceAffectedJobs += queueDnsCount(raw.huggingface_affected_jobs)
    current.evidenceTotal += queueDnsCount(raw.evidence_total)
    current.passedJobs += outcomes.passed
    current.softFailedJobs += outcomes.softFailed
    current.hardFailedJobs += outcomes.hardFailed
    current.outcomesAvailable = current.outcomesAvailable && outcomes.available
    grouped.set(key, current)
  }
  return Array.from(grouped.values()).sort(
    (left, right) => right.affectedJobs - left.affectedJobs || right.episodes - left.episodes || compareText(left.node, right.node),
  )
}

export function queueDnsQueueRows(windowBlock: Rec | null | undefined, requestedScope: unknown, queueRoster?: unknown[]): DnsQueueRow[] {
  const byQueue = new Map<string, DnsQueueRow>()
  for (const node of queueDnsNodeRows(windowBlock, requestedScope)) {
    const row = byQueue.get(node.queue) || {
      queue: node.queue,
      affectedJobs: 0,
      episodes: 0,
      huggingfaceAffectedJobs: 0,
      evidenceTotal: 0,
      passedJobs: 0,
      softFailedJobs: 0,
      hardFailedJobs: 0,
      outcomesAvailable: true,
      nodes: [],
    }
    row.affectedJobs += node.affectedJobs
    row.episodes += node.episodes
    row.huggingfaceAffectedJobs += node.huggingfaceAffectedJobs
    row.evidenceTotal += node.evidenceTotal
    row.passedJobs += node.passedJobs
    row.softFailedJobs += node.softFailedJobs
    row.hardFailedJobs += node.hardFailedJobs
    row.outcomesAvailable = row.outcomesAvailable && node.outcomesAvailable
    row.nodes.push(node)
    byQueue.set(node.queue, row)
  }
  for (const queue of queueRoster || []) {
    const name = String(queue || '').trim()
    if (name && queueDnsMatchesPublishedScope(name, requestedScope) && !byQueue.has(name)) {
      byQueue.set(name, {
        queue: name,
        affectedJobs: 0,
        episodes: 0,
        huggingfaceAffectedJobs: 0,
        evidenceTotal: 0,
        passedJobs: 0,
        softFailedJobs: 0,
        hardFailedJobs: 0,
        outcomesAvailable: false,
        nodes: [],
      })
    }
  }
  return Array.from(byQueue.values()).sort((left, right) => right.affectedJobs - left.affectedJobs || compareText(left.queue, right.queue))
}

export function queueDnsEvidenceUrl(row: Rec | null | undefined): string {
  const pipeline = String((row || {}).pipeline || '')
  const build = String((row || {}).build_number || '')
  const jobId = String((row || {}).job_id || '')
  if (!QUEUE_DNS_PIPELINES.has(pipeline)) return ''
  if (!/^[1-9]\d*$/.test(build) || !Number.isSafeInteger(Number(build))) return ''
  if (!QUEUE_DNS_JOB_ID_RE.test(jobId)) return ''
  return (
    'https://buildkite.com/vllm/' +
    encodeURIComponent(pipeline) +
    '/builds/' +
    encodeURIComponent(build) +
    '/list?jid=' +
    encodeURIComponent(jobId) +
    '&tab=output'
  )
}

export function queueDnsEvidenceMetricValid(metric: unknown, windowBlock: Rec | null | undefined): boolean {
  if (!isPlainObject(metric)) return false
  const keys = Object.keys(metric)
  if (keys.length !== QUEUE_DNS_WINDOW_METRIC_KEYS.length || !QUEUE_DNS_WINDOW_METRIC_KEYS.every((key) => keys.includes(key)))
    return false
  if (!QUEUE_DNS_UTC_SECOND_RE.test(String(metric.first_at || '')) || !QUEUE_DNS_UTC_SECOND_RE.test(String(metric.last_at || '')))
    return false
  const firstAt = queueTimestamp(metric.first_at)
  const lastAt = queueTimestamp(metric.last_at)
  const windowStart = queueTimestamp((windowBlock || {}).start)
  const windowEnd = queueTimestamp((windowBlock || {}).end_exclusive)
  if (
    firstAt === -Infinity ||
    lastAt === -Infinity ||
    windowStart === -Infinity ||
    windowEnd === -Infinity ||
    firstAt > lastAt ||
    firstAt < windowStart ||
    lastAt >= windowEnd
  )
    return false
  if (!Number.isInteger(metric.episodes) || metric.episodes < 1 || !Number.isInteger(metric.match_count) || metric.match_count < metric.episodes)
    return false
  return ['signature_ids', 'target_categories'].every((key) => {
    const values = metric[key]
    return (
      Array.isArray(values) &&
      values.length > 0 &&
      values.every((item) => typeof item === 'string' && Boolean(item)) &&
      new Set(values).size === values.length
    )
  })
}

export function queueDnsEvidenceItemValid(row: unknown, windows: Rec | null | undefined): boolean {
  if (!isPlainObject(row)) return false
  if (!Array.isArray(row.window_ids) || !row.window_ids.length) return false
  const windowIds: string[] = row.window_ids.map(String)
  const canonicalIds = QUEUE_DNS_WINDOW_IDS.filter((id) => windowIds.includes(id))
  if (JSON.stringify(windowIds) !== JSON.stringify(canonicalIds) || !windowIds.includes('720h')) return false
  if (!isPlainObject(row.window_metrics) || JSON.stringify(Object.keys(row.window_metrics)) !== JSON.stringify(windowIds)) return false
  if (!windowIds.every((id) => queueDnsEvidenceMetricValid(row.window_metrics[id], (windows || {})[id]))) return false
  const retained = row.window_metrics['720h']
  return QUEUE_DNS_WINDOW_METRIC_KEYS.every((key) =>
    Array.isArray(retained[key]) ? JSON.stringify(row[key]) === JSON.stringify(retained[key]) : row[key] === retained[key],
  )
}

export function queueDnsEvidenceWindowRow(row: Rec, windowId: string, windows: Rec | null | undefined): Rec | null {
  if (!queueDnsEvidenceItemValid(row, windows) || !row.window_ids.includes(windowId)) return null
  const metric = row.window_metrics[windowId]
  return {
    id: row.id,
    first_at: metric.first_at,
    last_at: metric.last_at,
    time_basis: row.time_basis,
    pipeline: row.pipeline,
    queue: row.queue,
    node: row.node,
    hardware: row.hardware,
    build_number: row.build_number,
    job_id: row.job_id,
    state: row.state,
    episodes: metric.episodes,
    match_count: metric.match_count,
    signature_ids: metric.signature_ids.slice(),
    target_categories: metric.target_categories.slice(),
    window_id: windowId,
  }
}

export function queueDnsEvidenceForNode(payload: Rec | null | undefined, windowId: string, queue: string, nodeRaw: string): Rec[] {
  const deduplicated = new Map<string, Rec>()
  for (const row of ((payload || {}).evidence || {}).items || []) {
    if (!row || row.queue !== queue) continue
    if (String(row.node || '').trim() !== String(nodeRaw || '').trim()) continue
    const selected = queueDnsEvidenceWindowRow(row, windowId, (payload || {}).windows || {})
    if (!selected) continue
    const key = String(selected.id || [selected.pipeline, selected.build_number, selected.job_id].join('/'))
    deduplicated.set(key, selected)
  }
  return Array.from(deduplicated.values()).sort(
    (left, right) => queueTimestamp(right.last_at || right.first_at) - queueTimestamp(left.last_at || left.first_at),
  )
}

export function queueDnsNodeOutcomes(payload: Rec, windowId: string, nodeRow: DnsNodeRow): DnsOutcomes {
  if (payload.outcome_contract === QUEUE_DNS_OUTCOME_CONTRACT && nodeRow.outcomesAvailable) {
    return { available: true, passed: nodeRow.passedJobs, softFailed: nodeRow.softFailedJobs, hardFailed: nodeRow.hardFailedJobs }
  }
  const retained = queueDnsEvidenceForNode(payload, windowId, nodeRow.queue, nodeRow.nodeRaw)
  if (retained.length !== nodeRow.affectedJobs || retained.length < nodeRow.evidenceTotal) {
    return { available: false, passed: 0, softFailed: 0, hardFailed: 0 }
  }
  const counts = { available: true, passed: 0, softFailed: 0, hardFailed: 0 }
  for (const row of retained) {
    const stateName = String(row.state || '').toLowerCase()
    if (stateName === 'passed') counts.passed += 1
    else if (stateName === 'soft' || stateName === 'soft_failed' || stateName === 'soft_fail') counts.softFailed += 1
    else if (stateName === 'hard' || stateName === 'failed') counts.hardFailed += 1
    else counts.available = false
  }
  if (counts.passed + counts.softFailed + counts.hardFailed !== nodeRow.affectedJobs) counts.available = false
  return counts
}

export function queueDnsDisplayCount(raw: unknown, coverage: Partial<DnsCoverage> | null | undefined): string {
  const count = queueDnsCount(raw)
  if ((coverage || {}).complete) return integer(count)
  return count > 0 ? '≥ ' + integer(count) : '-'
}

const TARGET_LABELS: Record<string, string> = {
  huggingface_hub: 'Hugging Face Hub',
  vllm_public_assets: 'vLLM public assets',
  aws_s3: 'AWS S3',
  github: 'GitHub',
  pypi: 'PyPI',
  other_public: 'Other public host',
  unknown: 'Unknown target',
}

export function queueDnsTargetLabels(row: Rec | null | undefined): string[] {
  const values: unknown[] = Array.isArray((row || {}).target_categories) ? row!.target_categories : []
  return values
    .map((category): string => (TARGET_LABELS as Rec)[String(category)] || TARGET_LABELS.unknown)
    .filter((label, index, all) => all.indexOf(label) === index)
}

export function queueDnsSignatureLabels(row: Rec | null | undefined): string[] {
  return (Array.isArray((row || {}).signature_ids) ? row!.signature_ids : [])
    .map((signature: unknown) => String(signature || '').replace(/_/g, ' '))
    .filter(Boolean)
}

export function queueDnsOutcomePresentation(state: unknown): { label: string; tone: Tone } {
  const normalized = String(state || '').toLowerCase()
  if (normalized === 'passed') return { label: 'Passed after observation', tone: 'is-success' }
  if (normalized === 'soft' || normalized === 'soft_failed' || normalized === 'soft_fail') return { label: 'Soft-failed', tone: 'is-warning' }
  if (normalized === 'hard' || normalized === 'failed') return { label: 'Hard-failed', tone: 'is-danger' }
  return { label: value(state, 'Unknown'), tone: 'is-neutral' }
}

// ---- renderAnalyticsDns summary, node bars, and the evidence drawer ----

export interface DnsBarSegment {
  kind: 'passed' | 'soft' | 'hard' | 'observed'
  widthPct: number | null
}

export interface DnsNodeBar {
  node: DnsNodeRow
  /** The node row with its resolved outcomes, as passed to the evidence drawer. */
  resolved: DnsNodeRow
  renderedCount: string
  outcomeText: string
  meta: string
  ariaLabel: string
  fillPct: number
  segments: DnsBarSegment[]
}

export interface DnsQueueCard {
  queue: DnsQueueRow
  stats: string[]
  bars: DnsNodeBar[]
}

export interface DnsSummaryItem {
  label: string
  value: string
  meta: string
  tone: Tone | ''
}

export interface DnsPresentation {
  windowId: string
  block: Rec | null
  coverage: DnsCoverage
  freshness: ReturnType<typeof queueDnsFreshness>
  scope: DnsScope
  affectedQueues: DnsQueueRow[]
  affectedNodeCount: number
  totals: Omit<DnsQueueRow, 'queue' | 'nodes' | 'evidenceTotal'>
  summary: DnsSummaryItem[]
  cards: DnsQueueCard[]
  maximum: number
  emptyText: string
}

function queueDnsNodeBar(payload: Rec, windowId: string, queueRow: DnsQueueRow, nodeRow: DnsNodeRow, coverage: DnsCoverage, maximum: number): DnsNodeBar {
  const outcomes = queueDnsNodeOutcomes(payload, windowId, nodeRow)
  const renderedCount = queueDnsDisplayCount(nodeRow.affectedJobs, coverage)
  const nonpassing = outcomes.softFailed + outcomes.hardFailed
  const outcomeText = outcomes.available
    ? queueDnsDisplayCount(outcomes.passed, coverage) + ' passed, ' + queueDnsDisplayCount(nonpassing, coverage) + ' nonpassing'
    : 'job outcomes unavailable'
  const segments: DnsBarSegment[] = []
  if (outcomes.available && nodeRow.affectedJobs) {
    for (const [kind, count] of [
      ['passed', outcomes.passed],
      ['soft', outcomes.softFailed],
      ['hard', outcomes.hardFailed],
    ] as const) {
      if (!count) continue
      segments.push({ kind, widthPct: (count / nodeRow.affectedJobs) * 100 })
    }
  } else {
    segments.push({ kind: 'observed', widthPct: null })
  }
  return {
    node: nodeRow,
    resolved: {
      ...nodeRow,
      outcomesAvailable: outcomes.available,
      passedJobs: outcomes.passed,
      softFailedJobs: outcomes.softFailed,
      hardFailedJobs: outcomes.hardFailed,
    },
    renderedCount,
    outcomeText,
    meta: outcomeText + ' - ' + queueDnsDisplayCount(nodeRow.episodes, coverage) + ' episodes',
    ariaLabel:
      queueRow.queue + ', ' + nodeRow.node + ': ' + renderedCount + ' jobs with DNS observations; ' + outcomeText + '. Open exact Buildkite evidence.',
    fillPct: Math.max(3, (nodeRow.affectedJobs / Math.max(1, maximum)) * 100),
    segments,
  }
}

/** Everything renderAnalyticsDns derives for one window and scope; null block means the window is absent. */
export function dnsPresentation(payload: Rec, requestedWindow: string, requestedScope: unknown, nowMs?: number): DnsPresentation {
  const selected = queueDnsWindow(payload, requestedWindow)
  const coverage = queueDnsCoverage(payload, selected.block)
  const freshness = queueDnsFreshness(payload, selected.block, nowMs)
  const scope = queueDnsScope(requestedScope)
  const affectedQueues = selected.block ? queueDnsQueueRows(selected.block, scope, []).filter((row) => row.affectedJobs > 0) : []
  const affectedNodes = new Set<string>()
  for (const queue of affectedQueues) {
    queue.passedJobs = 0
    queue.softFailedJobs = 0
    queue.hardFailedJobs = 0
    queue.outcomesAvailable = true
    for (const node of queue.nodes) {
      if (node.affectedJobs > 0) affectedNodes.add(node.node)
      const outcomes = queueDnsNodeOutcomes(payload, selected.id, node)
      node.outcomesAvailable = outcomes.available
      node.passedJobs = outcomes.passed
      node.softFailedJobs = outcomes.softFailed
      node.hardFailedJobs = outcomes.hardFailed
      queue.passedJobs += outcomes.passed
      queue.softFailedJobs += outcomes.softFailed
      queue.hardFailedJobs += outcomes.hardFailed
      queue.outcomesAvailable = queue.outcomesAvailable && outcomes.available
    }
  }
  const totals = affectedQueues.reduce(
    (out, queue) => {
      out.affectedJobs += queue.affectedJobs
      out.episodes += queue.episodes
      out.huggingfaceAffectedJobs += queue.huggingfaceAffectedJobs
      out.passedJobs += queue.passedJobs
      out.softFailedJobs += queue.softFailedJobs
      out.hardFailedJobs += queue.hardFailedJobs
      out.outcomesAvailable = out.outcomesAvailable && queue.outcomesAvailable
      return out
    },
    { affectedJobs: 0, episodes: 0, huggingfaceAffectedJobs: 0, passedJobs: 0, softFailedJobs: 0, hardFailedJobs: 0, outcomesAvailable: true },
  )
  const nonpassing = totals.softFailedJobs + totals.hardFailedJobs
  const outcomeTone: Tone | '' = totals.hardFailedJobs
    ? 'is-danger'
    : totals.softFailedJobs
      ? 'is-warning'
      : totals.passedJobs
        ? 'is-success'
        : ''
  const summary: DnsSummaryItem[] = [
    {
      label: 'JOBS WITH DNS OBSERVATIONS',
      value: queueDnsDisplayCount(totals.affectedJobs, coverage),
      meta: queueDnsDisplayCount(totals.episodes, coverage) + ' episodes',
      tone: totals.affectedJobs ? 'is-warning' : 'is-success',
    },
    {
      label: 'AFFECTED QUEUES',
      value: queueDnsDisplayCount(affectedQueues.length, coverage),
      meta: scope === 'canonical' ? 'canonical AMD' : 'active AMD GPU',
      tone: '',
    },
    { label: 'PHYSICAL NODES', value: queueDnsDisplayCount(affectedNodes.size, coverage), meta: 'including unidentified', tone: '' },
    { label: 'HUGGING FACE JOBS', value: queueDnsDisplayCount(totals.huggingfaceAffectedJobs, coverage), meta: 'resolver target', tone: '' },
    {
      label: 'PASSED / NONPASSING',
      value: totals.outcomesAvailable
        ? queueDnsDisplayCount(totals.passedJobs, coverage) + ' / ' + queueDnsDisplayCount(nonpassing, coverage)
        : '-',
      meta: totals.outcomesAvailable ? 'final outcome after observation' : 'outcome aggregate unavailable',
      tone: outcomeTone,
    },
  ]
  const maximum = Math.max(...affectedQueues.flatMap((queue) => queue.nodes.map((node) => node.affectedJobs)), 1)
  const cards = affectedQueues.map((queueRow) => ({
    queue: queueRow,
    stats: [
      queueDnsDisplayCount(queueRow.affectedJobs, coverage) + ' jobs',
      queueDnsDisplayCount(queueRow.nodes.length, coverage) + ' nodes',
      queueDnsDisplayCount(queueRow.huggingfaceAffectedJobs, coverage) + ' HF',
    ],
    bars: queueRow.nodes.map((nodeRow) => queueDnsNodeBar(payload, selected.id, queueRow, nodeRow, coverage, maximum)),
  }))
  return {
    windowId: selected.id,
    block: selected.block,
    coverage,
    freshness,
    scope,
    affectedQueues,
    affectedNodeCount: affectedNodes.size,
    totals,
    summary,
    cards,
    maximum,
    emptyText: coverage.complete
      ? 'No jobs with DNS resolver observations were found in this scope and window.'
      : 'No retained DNS observations are available in this scope. Partial coverage cannot establish a zero.',
  }
}

export interface DnsEvidenceDrawer {
  title: string
  subtitle: string
  rows: Rec[]
  evidenceTotal: number
  truncated: boolean
  note: { tone: Tone; text: string } | null
  fields: { label: string; value: string | null }[]
  tableMeta: string
  browserSubtitle: string
}

/** The classic openQueueDnsNodeEvidence drawer for a node bar (nodeRow carries resolved outcomes). */
export function queueDnsNodeEvidence(
  payload: Rec,
  windowId: string,
  queueRow: { queue: string },
  nodeRow: DnsNodeRow,
  coverage: DnsCoverage,
): DnsEvidenceDrawer {
  const rows = queueDnsEvidenceForNode(payload, windowId, queueRow.queue, nodeRow.nodeRaw).filter((row) => Boolean(queueDnsEvidenceUrl(row)))
  const evidenceTotal = Math.max(nodeRow.evidenceTotal, rows.length)
  const truncated = rows.length < evidenceTotal
  const note = truncated
    ? {
        tone: 'is-warning' as Tone,
        text:
          'Exact links are retained for ' +
          integer(rows.length) +
          ' of ' +
          integer(evidenceTotal) +
          ' affected jobs on this node. The histogram continues to use the published affected-job row count, independent of bounded evidence retention.',
      }
    : !rows.length && nodeRow.affectedJobs
      ? {
          tone: 'is-info' as Tone,
          text: 'The aggregate contains affected jobs for this node, but no exact log links were retained in the bounded public evidence set.',
        }
      : null
  return {
    title: nodeRow.node,
    subtitle: queueRow.queue + ' DNS evidence - ' + windowId,
    rows,
    evidenceTotal,
    truncated,
    note,
    fields: [
      { label: 'Queue', value: queueRow.queue },
      { label: 'Physical node', value: nodeRow.node },
      { label: 'Affected jobs', value: queueDnsDisplayCount(nodeRow.affectedJobs, coverage) },
      { label: 'DNS episodes', value: queueDnsDisplayCount(nodeRow.episodes, coverage) },
      { label: 'Hugging Face affected jobs', value: queueDnsDisplayCount(nodeRow.huggingfaceAffectedJobs, coverage) },
      { label: 'Passed after observation', value: nodeRow.outcomesAvailable ? queueDnsDisplayCount(nodeRow.passedJobs, coverage) : null },
      { label: 'Soft-failed after observation', value: nodeRow.outcomesAvailable ? queueDnsDisplayCount(nodeRow.softFailedJobs, coverage) : null },
      { label: 'Hard-failed after observation', value: nodeRow.outcomesAvailable ? queueDnsDisplayCount(nodeRow.hardFailedJobs, coverage) : null },
      { label: 'Exact evidence shown', value: integer(rows.length) },
      { label: 'Exact evidence total', value: integer(evidenceTotal) },
      { label: 'Evidence retention truncated', value: truncated ? 'Yes' : 'No' },
    ],
    tableMeta: integer(rows.length) + ' shown / ' + integer(evidenceTotal) + ' total' + (truncated ? ' - truncated' : ''),
    browserSubtitle: queueRow.queue + ' on ' + nodeRow.node + ' in ' + windowId,
  }
}

export function queueDnsEvidenceSearchText(row: Rec): string {
  return [
    row.pipeline,
    row.build_number,
    row.job_id,
    row.state,
    row.hardware,
    row.time_basis,
    queueDnsTargetLabels(row).join(' '),
    queueDnsSignatureLabels(row).join(' '),
  ].join(' ')
}

export function queueDnsSourceLabel(sourceAsset: unknown): string {
  return sourceAsset === DNS_LIVE_ASSET ? 'live dns-health-data' : 'Pages fallback'
}

// ---- Loading: live dns-health-data branch vs the Pages fallback ----

export type DnsFetcher = (source: string) => Promise<unknown>

export function queueDnsWithTimeout<T>(promise: Promise<T>, source: string, requestedTimeoutMs?: number): Promise<T> {
  const parsed = Number(requestedTimeoutMs)
  const timeoutMs = Number.isFinite(parsed) && parsed > 0 ? parsed : QUEUE_DNS_FETCH_TIMEOUT_MS
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('DNS failure source timed out: ' + source)), timeoutMs)
    Promise.resolve(promise).then(
      (result) => {
        clearTimeout(timer)
        resolve(result)
      },
      (error) => {
        clearTimeout(timer)
        reject(error)
      },
    )
  })
}

export interface DnsLoader {
  /** Resolves with the arbitrated payload (with __sourceAsset); onUpgrade fires if a slower, newer source wins later. */
  load(requestedTimeoutMs?: number): Promise<Rec>
  invalidate(): void
  refreshDue(nowMs?: number): boolean
  lastRefreshAt(): number
}

/** The classic loadQueueDns: first valid source paints, a short arbitration prefers the newer publication. */
export function createDnsLoader(fetcher: DnsFetcher, onUpgrade?: (payload: Rec) => void, sources = [DNS_LIVE_ASSET, DNS_FALLBACK_ASSET]): DnsLoader {
  let preferred: DnsCandidate | null = null
  let generation = 0
  let lastRefreshAt = 0
  const withSource = (candidate: DnsCandidate) => ({ ...candidate.payload, __sourceAsset: candidate.source })
  return {
    async load(requestedTimeoutMs?: number) {
      if (preferred) return withSource(preferred)
      const current = generation
      const resolved: DnsCandidate[] = []
      const attempts = sources.map((source, index) =>
        queueDnsWithTimeout(Promise.resolve().then(() => fetcher(source)), source, requestedTimeoutMs).then((payload) => {
          if (!queueDnsPayloadValid(payload)) throw new Error('DNS failure source is invalid: ' + source)
          const candidate = { payload: payload as Rec, source, priority: index }
          resolved.push(candidate)
          return candidate
        }),
      )
      let first: DnsCandidate
      try {
        first = await Promise.any(attempts)
      } catch {
        throw new Error('No valid DNS failure aggregate is available')
      }
      const requested = Number(requestedTimeoutMs)
      const arbitrationMs = Number.isFinite(requested) && requested > 0 ? Math.min(QUEUE_DNS_ARBITRATION_MS, requested) : QUEUE_DNS_ARBITRATION_MS
      await Promise.race([Promise.allSettled(attempts), new Promise((resolve) => setTimeout(resolve, arbitrationMs))])
      const selected = resolved.slice().sort(compareQueueDnsCandidates)[0] || first
      if (current === generation) {
        preferred = selected
        lastRefreshAt = Date.now()
      }
      // A slower source may still carry a newer publication; upgrade only when it wins.
      void Promise.allSettled(attempts).then(() => {
        if (current !== generation || !resolved.length) return
        const newest = resolved.slice().sort(compareQueueDnsCandidates)[0]!
        if (!preferred || compareQueueDnsCandidates(newest, preferred) < 0) {
          preferred = newest
          onUpgrade?.(withSource(newest))
        }
      })
      return withSource(selected)
    },
    invalidate() {
      preferred = null
      generation += 1
    },
    refreshDue(nowMs?: number) {
      const parsed = Number(nowMs)
      const clock = Number.isFinite(parsed) ? parsed : Date.now()
      return clock - lastRefreshAt >= DNS_AUTO_REFRESH_MS
    },
    lastRefreshAt: () => lastRefreshAt,
  }
}
