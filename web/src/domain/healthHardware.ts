// CI Health "AMD hardware" view: ports of the classic matrix helpers in
// docs/assets/js/ops-v2.js (matrixHealthPolicy ... amdMatrixSortDescription)
// plus the decisions classic makes inline in renderHealth's 'coverage' branch.
import { isIncidentObservation, observationState, observationTimestamp } from './ciHealth'
import { duration, integer, percent, value, type Rec, type Tone } from './format'
import { currentAmdHealth, isAmdMiHardware, isAmdRuntimeJob } from './health'
import { exactPipelineEvidenceUrl } from './urls'

export function compareText(left: unknown, right: unknown): number {
  return String(left || '').localeCompare(String(right || ''), undefined, { sensitivity: 'base' })
}

export function normalizeLabel(label: unknown): string {
  return String(label || '').trim().replace(/\s+/g, ' ').toLowerCase()
}

const MATRIX_INCIDENT_STATES = new Set(['failed', 'timed_out', 'broken', 'soft_fail', 'soft_failed'])
const MATRIX_WAITING_STATES = new Set(['running', 'scheduled', 'assigned'])

export function matrixHealthPolicy(matrixSummary: Rec | null | undefined): Rec {
  const summary = matrixSummary || {}
  const policies = summary.health_policies || {}
  const policy = policies.best_hardware
  if (policy) {
    const included = Number(policy.included_groups || 0)
    const publishedCount = Number(summary.health_group_count || 0)
    if (!included || !publishedCount || publishedCount !== included) {
      return {
        passing_groups: 0,
        failing_groups: 0,
        waiting_groups: 0,
        unknown_groups: 0,
        included_groups: 0,
        pass_percentage: null,
        best_hardware_unavailable: true,
      }
    }
    return Object.assign({}, policy, {
      pass_percentage: included ? (Number(policy.passing_groups || 0) / included) * 100 : null,
    })
  }
  return {
    passing_groups: 0,
    failed_only_groups: 0,
    mixed_groups: 0,
    failing_groups: 0,
    waiting_groups: 0,
    unknown_groups: 0,
    ignored_mi355_only_groups: 0,
    inherited_mi355_groups: 0,
    resolved_groups: 0,
    included_groups: 0,
    pass_percentage: null,
    generic_groups: 0,
    mi355_sensitive_groups: 0,
    best_hardware_unavailable: true,
  }
}

export function bestHardwareMatrixContract(matrixData: Rec | null | undefined) {
  const matrix = matrixData || {}
  const policy = matrixHealthPolicy(matrix.summary || {})
  const groups: Rec[] = Array.from(matrix.health_groups || [])
  const valid =
    !policy.best_hardware_unavailable &&
    Number(policy.included_groups || 0) > 0 &&
    groups.length === Number(policy.included_groups || 0)
  return { policy, groups, valid }
}

const STATUS_LABELS: Rec = {
  passing: 'Passing',
  failed: 'Failing',
  mixed: 'Mixed',
  waiting: 'In progress',
  unknown: 'No signal',
  ignored: 'Ignored',
}

export function matrixHealthStatusLabel(status: unknown): string {
  return STATUS_LABELS[status as string] || value(status)
}

const STATUS_TONES: Rec = {
  passing: 'is-success',
  failed: 'is-danger',
  mixed: 'is-warning',
  waiting: 'is-info',
  unknown: 'is-neutral',
  ignored: 'is-neutral',
}

export function matrixHealthTone(status: unknown): Tone {
  return STATUS_TONES[status as string] || 'is-neutral'
}

export function matrixGateKindLabel(group: Rec): string {
  return group.classification === 'mi355-sensitive' ? 'MI355-sensitive' : 'Generic family'
}

export function matrixHealthCollection(matrixData: Rec | null | undefined): Rec[] {
  const rows: Rec[] = Array.from((matrixData || {}).rows || [])
  const publishedGroups: Rec[] = Array.from((matrixData || {}).health_groups || [])
  const rowsById = new Map(rows.map((row) => [row.id, row]))
  const priority: Rec = { failed: 0, unknown: 1, waiting: 2, passing: 3 }
  return publishedGroups
    .map((published) => {
      const members: Rec[] = Array.from(published.members || [])
      const memberRows = Array.from(
        new Set((published.member_row_ids || []).concat(members.map((member) => member.row_id))),
      )
        .map((id) => rowsById.get(id))
        .filter(Boolean) as Rec[]
      const states = members.map((member) => String(member.state || '').toLowerCase())
      const rawStatus = String(published.status || '').toLowerCase()
      const status =
        published.is_passing === true || ['pass', 'passed', 'passing'].includes(rawStatus)
          ? 'passing'
          : ['fail', 'failed', 'failing', 'incident', 'soft', 'soft_fail', 'soft_failed', 'hard'].includes(rawStatus)
            ? 'failed'
            : MATRIX_WAITING_STATES.has(rawStatus)
              ? 'waiting'
              : 'unknown'
      const gateKind = String(published.gate_kind || 'generic').toLowerCase()
      return {
        id: published.id,
        title: published.title,
        status,
        gateKind,
        classification: gateKind.includes('mi355') ? 'mi355-sensitive' : 'generic',
        classificationReason: published.classification_reason || '',
        members,
        rows: memberRows,
        componentRows: memberRows,
        duplicateSize: memberRows.length,
        definitionCount: members.reduce((count, member) => {
          const variants = member.definitions && member.definitions.length ? member.definitions : member.variants || []
          return count + Math.max(1, variants.length)
        }, 0),
        architectures: Array.from(
          new Set((published.architectures || []).concat(members.map((member) => member.architecture)).filter(Boolean)),
        ).sort() as string[],
        passedCells: states.filter((result) => result === 'passed').length,
        incidentCells: states.filter((result) => MATRIX_INCIDENT_STATES.has(result)).length,
        waitingCells: states.filter((result) => MATRIX_WAITING_STATES.has(result)).length,
        unknownCells: states.filter(
          (result) => result !== 'passed' && !MATRIX_INCIDENT_STATES.has(result) && !MATRIX_WAITING_STATES.has(result),
        ).length,
        pairMatches: [] as Rec[],
      } as Rec
    })
    .sort(
      (left, right) =>
        priority[left.status] - priority[right.status] || String(left.title).localeCompare(String(right.title)),
    )
}

export function matrixGroupEvidence(group: Rec): Rec[] {
  if ((group.members || []).length) {
    const memberRows = new Map((group.rows || []).map((row: Rec) => [row.id, row]))
    const publishedEvidence: Rec[] = []
    group.members.forEach((member: Rec) => {
      const sourceRow = (memberRows.get(member.row_id) || {}) as Rec
      const nestedDefinitions = member.definitions && member.definitions.length ? member.definitions : member.variants || []
      const definitions = nestedDefinitions.length ? nestedDefinitions : [member]
      definitions.forEach((definition: Rec) => {
        publishedEvidence.push({
          definition: definition.label || member.label || member.title || group.title,
          architecture: definition.architecture || member.architecture || '',
          queue:
            definition.agent_pool ||
            definition.queue ||
            member.agent_pool ||
            member.queue ||
            (member.agent_pools || []).join(', ') ||
            '',
          result: definition.state || member.state || 'unknown',
          url: exactPipelineEvidenceUrl(
            { latest_url: definition.url || definition.latest_url || member.url || member.latest_url },
            'ci',
          ),
          buildNumber: definition.build_number || member.build_number,
          commands: definition.commands || member.commands || [],
          commandIdentity: definition.command_fingerprint || member.command_fingerprint || sourceRow.command_fingerprint || '',
          sourceUrl: definition.source_url || member.source_url || '',
        })
      })
    })
    return publishedEvidence
  }
  return []
}

/** The evidence drawer's note, fields, and commands block (classic openMatrixGroupEvidence). */
export function matrixGroupDetail(group: Rec, matrixData: Rec | null | undefined) {
  const evidence = matrixGroupEvidence(group)
  const commands = evidence.filter((row) => (row.commands || []).length)
  const source = (matrixData || {}).source || {}
  return {
    title: group.title || 'Evidence',
    note:
      group.classification === 'mi355-sensitive'
        ? 'This hardware-sensitive obligation uses its exact MI355 route; another architecture cannot satisfy it.'
        : 'This generic family passes when at least one configured AMD route passes. Architecture disagreements remain visible below.',
    fields: [
      { label: 'Status', value: matrixHealthStatusLabel(group.status) },
      { label: 'Test-group classification', value: matrixGateKindLabel(group) },
      { label: 'Classification reason', value: group.classificationReason },
      { label: 'Source definitions', value: integer(group.definitionCount) },
      { label: 'Hardware', value: group.architectures.map((arch: string) => arch.toUpperCase()).join(', ') },
      { label: 'Signal', value: integer(group.passedCells) + ' passing - ' + integer(group.incidentCells) + ' non-passing' },
      { label: 'Matrix rows', value: group.duplicateSize > 1 ? integer(group.duplicateSize) : null },
      {
        label: 'Shared title substring',
        value: Array.from(new Set(group.pairMatches.map((match: Rec) => match.shared_substring))).join(', ') || null,
      },
    ].filter((field) => field.value !== null && field.value !== undefined && field.value !== ''),
    sources: [
      { label: 'Open AMD test definitions', url: source.yaml_url },
      { label: 'Open latest AMD build', url: source.latest_build_url },
    ].filter((s) => s.url) as { label: string; url: string }[],
    evidence,
    evidenceCaption: integer(evidence.length) + ' exact AMD definitions',
    commandsCaption: integer(commands.length) + ' architecture definitions with published commands',
    commandsText: commands
      .map((row) => '# ' + row.architecture.toUpperCase() + ' - ' + row.definition + '\n' + row.commands.join('\n'))
      .join('\n\n'),
  }
}

export type MatrixBrowserMode = 'all' | 'passing' | 'failing' | 'no-signal' | 'mi355-sensitive' | 'generic' | 'ignored'

export const MATRIX_BROWSER_TITLES: Record<MatrixBrowserMode, string> = {
  all: 'Configured AMD test groups',
  passing: 'Passing AMD test groups',
  failing: 'Failing AMD test groups',
  'no-signal': 'AMD groups without a terminal signal',
  'mi355-sensitive': 'MI355-sensitive AMD test groups',
  generic: 'Generic AMD test families',
  ignored: 'Legacy ignored MI355-only test groups',
}

export const MATRIX_STATUS_OPTIONS = [
  { value: 'all', label: 'All statuses' },
  { value: 'passing', label: 'Passing' },
  { value: 'failing', label: 'Failing' },
  { value: 'no-signal', label: 'No signal' },
]
export const MATRIX_CLASSIFICATION_OPTIONS = [
  { value: 'all', label: 'All classifications' },
  { value: 'generic', label: 'Generic families' },
  { value: 'mi355-sensitive', label: 'MI355-sensitive' },
]

/** Initial status and classification filter for a browser opened in ``mode``. */
export function matrixBrowserFilters(mode: string | undefined) {
  return {
    status: mode === 'passing' || mode === 'failing' || mode === 'no-signal' ? mode : 'all',
    classification: mode === 'mi355-sensitive' || mode === 'generic' ? mode : 'all',
  }
}

export function matrixStatusPredicate(group: Rec, selected: string): boolean {
  return selected === 'no-signal'
    ? ['waiting', 'unknown'].includes(group.status)
    : selected === 'failing'
      ? group.status === 'failed'
      : group.status === selected
}

export function matrixClassificationPredicate(group: Rec, selected: string): boolean {
  return group.classification === selected
}

export function matrixGroupSearchText(group: Rec): string {
  return [
    group.title,
    group.status,
    group.classification,
    group.classificationReason,
    group.architectures.join(' '),
    matrixGroupEvidence(group)
      .map((row) => [row.definition, row.queue, row.commandIdentity, (row.commands || []).join(' ')].join(' '))
      .join(' '),
  ].join(' ')
}

/** Classic openTableBrowser row filtering: normalized substring search, then each non-'all' filter. */
export function browseRows<T>(
  rows: T[],
  query: string,
  searchText: (row: T) => string,
  filters: { selected: string; predicate: (row: T, selected: string) => boolean }[] = [],
): T[] {
  const needle = normalizeLabel(query)
  let filtered = needle ? rows.filter((row) => normalizeLabel(searchText(row)).includes(needle)) : rows
  for (const filter of filters) {
    if (filter.selected === 'all') continue
    filtered = filtered.filter((row) => filter.predicate(row, filter.selected))
  }
  return filtered
}

/** Rows the configured-group browser lists (classic drops 'ignored', which the collection never yields). */
export function matrixBrowserGroups(matrixData: Rec | null | undefined): Rec[] {
  return matrixHealthCollection(matrixData).filter((group) => group.status !== 'ignored')
}

/** What the browser opens: the group list, or classic's "Unavailable" metric detail. */
export function matrixBrowserState(matrixData: Rec | null | undefined) {
  if (((matrixData || {}).source || {}).pipeline !== 'ci') {
    return {
      available: false as const,
      label: 'Current main CI AMD hardware evidence',
      description: 'The matrix does not declare the current main CI source.',
    }
  }
  if (!bestHardwareMatrixContract(matrixData).valid) {
    return {
      available: false as const,
      label: 'Configured AMD test groups',
      description:
        'This snapshot does not contain a complete best-hardware policy and matching test-group inventory. Refresh the dashboard after the matrix collector publishes both together.',
    }
  }
  const groups = matrixBrowserGroups(matrixData)
  return {
    available: true as const,
    groups,
    subtitle:
      integer(groups.length) +
      ' configured AMD test groups; the best-hardware policy lets generic families use any passing AMD route while hardware-sensitive obligations use their exact route',
  }
}

// Classic renderHealth reads the overview card from the runtime summary that
// amd_test_health publishes, and only for the current main CI source.
export function runtimeMatrixSummary(amdTestHealth: Rec | null | undefined): Rec {
  const health = currentAmdHealth(amdTestHealth)
  return health.runtime_matrix_source && health.runtime_matrix_source.pipeline === 'ci'
    ? health.runtime_matrix_summary || {}
    : {}
}

/** The "Configured AMD test groups" card (classic matrixHealthOverview). */
export function matrixHealthOverviewPresentation(matrixSummary: Rec, policy: Rec, latestBuildNumber: unknown) {
  const noSignal = Number(policy.waiting_groups || 0) + Number(policy.unknown_groups || 0)
  return {
    meta: (latestBuildNumber ? 'Latest observed matrix build #' + latestBuildNumber + ' - ' : '') + 'one fixed best-hardware policy',
    unavailable: Boolean(policy.best_hardware_unavailable),
    rate:
      policy.pass_percentage === null || policy.pass_percentage === undefined
        ? '-'
        : Number(policy.pass_percentage).toFixed(1) + '%',
    stats: [
      { mode: 'all' as const, label: 'Test groups', count: integer(policy.included_groups) },
      { mode: 'passing' as const, label: 'Passing', count: integer(policy.passing_groups) },
      { mode: 'failing' as const, label: 'Failing', count: integer(policy.failing_groups) },
      { mode: 'no-signal' as const, label: 'No signal', count: integer(noSignal) },
    ],
    // Bar segments are omitted at zero; widths share max(1, included).
    segments: [
      { mode: 'passing' as const, label: 'Passing', count: Number(policy.passing_groups || 0), tone: 'is-success' as Tone },
      { mode: 'failing' as const, label: 'Failing', count: Number(policy.failing_groups || 0), tone: 'is-danger' as Tone },
      { mode: 'no-signal' as const, label: 'No signal', count: noSignal, tone: 'is-neutral' as Tone },
    ].filter((item) => item.count),
    total: Math.max(1, Number(policy.included_groups || 0)),
    facts: [
      integer(policy.generic_group_count !== undefined ? policy.generic_group_count : policy.generic_groups || 0) +
        ' generic best-of-hardware families',
      integer(
        policy.mi355_sensitive_group_count !== undefined
          ? policy.mi355_sensitive_group_count
          : policy.mi355_sensitive_groups || 0,
      ) + ' explicit MI355-sensitive obligations',
      'no-signal groups remain in the denominator',
    ].join(' - '),
    browseLabel: 'Browse all ' + integer(policy.included_groups) + ' test groups',
  }
}

const AMD_MATRIX_PLATFORM_ORDER = ['mi250', 'mi300', 'mi325', 'mi355']
const AMD_ARCHITECTURE_HARD_SIGNAL_STATES = new Set([
  'hard', 'failed', 'failing', 'incident', 'error', 'timed_out', 'broken', 'canceled', 'cancelled', 'expired',
])
const AMD_ARCHITECTURE_SOFT_SIGNAL_STATES = new Set(['soft', 'soft_fail', 'soft_failed'])

export function amdMatrixPlatformRank(row: Rec | null | undefined): number {
  const cells = (row || {}).cells || {}
  const rank = AMD_MATRIX_PLATFORM_ORDER.findIndex((platform) => Boolean((cells[platform] || {}).exists))
  return rank < 0 ? AMD_MATRIX_PLATFORM_ORDER.length : rank
}

export type MatrixSortMode = 'platform' | 'name' | 'area'
export const MATRIX_SORT_MODES: { id: MatrixSortMode; label: string }[] = [
  { id: 'platform', label: 'Platform' },
  { id: 'name', label: 'Test group' },
  { id: 'area', label: 'Test area' },
]

export function sortAmdMatrixRows(rows: Rec[] | null | undefined, mode: string): Rec[] {
  return Array.from(rows || []).sort((left, right) => {
    if (mode === 'name') return compareText(left.title, right.title) || compareText(left.area, right.area)
    if (mode === 'area') return compareText(left.area, right.area) || compareText(left.title, right.title)
    return (
      amdMatrixPlatformRank(left) - amdMatrixPlatformRank(right) ||
      compareText(left.title, right.title) ||
      compareText(left.area, right.area)
    )
  })
}

export function architectureSignalStateRank(stateName: unknown): number {
  const normalized = String(stateName || 'unobserved').trim().toLowerCase()
  if (AMD_ARCHITECTURE_HARD_SIGNAL_STATES.has(normalized)) return 0
  if (AMD_ARCHITECTURE_SOFT_SIGNAL_STATES.has(normalized)) return 1
  if (normalized === 'passed') return 3
  return 2
}

export function sortArchitectureSignalRows(rows: Rec[] | null | undefined, architectureId: string): Rec[] {
  const latestState = (row: Rec) => (((row || {}).cells || {})[architectureId] || {}).latest_state || 'unobserved'
  return Array.from(rows || []).sort(
    (left, right) =>
      architectureSignalStateRank(latestState(left)) - architectureSignalStateRank(latestState(right)) ||
      compareText(left.title, right.title) ||
      compareText(left.area, right.area) ||
      compareText(left.id, right.id),
  )
}

export function amdMatrixSortDescription(mode: string): string {
  if (mode === 'name') return 'Test groups sorted alphabetically by name'
  if (mode === 'area') return 'Test areas sorted alphabetically, then by test-group name'
  return 'Grouped by first configured platform: MI250, MI300, MI325, then MI355'
}

/** Classic's route state for a link: health_sort accepts platform | name | area. */
export function matrixSortMode(raw: unknown): MatrixSortMode {
  return raw === 'name' || raw === 'area' ? raw : 'platform'
}

/** What the coverage view can show for this matrix payload (classic renderHealth gates). */
export function hardwareViewState(matrixData: Rec | null | undefined) {
  if (((matrixData || {}).source || {}).pipeline !== 'ci') return 'unavailable' as const
  if (((matrixData as Rec).publication_retention || {}).complete_relative_to_source === false) return 'bounded' as const
  return 'complete' as const
}

export interface ArchitectureHealth {
  architecture: Rec
  passed: number
  incident: number
  unknown: number
  missing: number
}

export function architectureHealth(architectures: Rec[], coverageRows: Rec[]): ArchitectureHealth[] {
  return architectures.map((architecture) => {
    const result = { architecture, passed: 0, incident: 0, unknown: 0, missing: 0 }
    coverageRows.forEach((row) => {
      const cell = (row.cells || {})[architecture.id] || {}
      if (!cell.exists) {
        result.missing += 1
        return
      }
      const stateName = observationState({ state: cell.latest_state })
      if (stateName === 'passed') result.passed += 1
      else if (isIncidentObservation({ state: stateName })) result.incident += 1
      else result.unknown += 1
    })
    return result
  })
}

export function architectureRows(coverageRows: Rec[], architectureId: string): Rec[] {
  return coverageRows.filter((row) => ((row.cells || {})[architectureId] || {}).exists)
}

export function architectureRate(health: ArchitectureHealth) {
  const configured = health.passed + health.incident + health.unknown
  const passRate = configured ? (health.passed / configured) * 100 : null
  return {
    configured,
    passRate,
    label: passRate === null ? '-' : passRate.toFixed(1) + '%',
    tone: (Number(passRate) >= 90 ? 'is-success' : Number(passRate) >= 50 ? 'is-warning' : 'is-danger') as Tone,
  }
}

/** The per-architecture route browser (classic openArchitectureHealth). */
export function architectureBrowser(health: ArchitectureHealth, coverageRows: Rec[]) {
  const architecture = health.architecture
  const rows = sortArchitectureSignalRows(architectureRows(coverageRows, architecture.id), architecture.id)
  return {
    title: architecture.label + ' test-group routes',
    subtitle:
      integer(rows.length) +
      ' configured hardware routes; non-passing latest results first, then test group A-Z; Build links open exact AMD jobs',
    rows,
    searchText: (row: Rec) => [row.title, row.area, ((row.cells || {})[architecture.id] || {}).latest_state].join(' '),
  }
}

export function architectureCellUrl(cell: Rec): string {
  return exactPipelineEvidenceUrl({ latest_url: cell.latest_url, build_number: cell.latest_build_number }, 'ci')
}

export function coverageBrowserSubtitle(rows: Rec[], architectures: Rec[], mode: string): string {
  return (
    integer(rows.length) + ' group definitions across ' + integer(architectures.length) + ' architectures - ' + amdMatrixSortDescription(mode)
  )
}

// --- Test-group detail (classic openGroupDetail with the reliability ledger) ---

export function reliabilityForPipeline(ops: Rec | null | undefined, pipeline: string): Rec {
  const reliability = (ops || {}).reliability || {}
  return pipeline === 'ci' &&
    reliability.source_pipeline === 'ci' &&
    reliability.job_scope === 'amd_gpu' &&
    reliability.hardware_scope === 'amd_mi_gpu' &&
    (reliability.group_catalog || []).every((row: Rec) => isAmdMiHardware(row.hardware) && isAmdRuntimeJob(row))
    ? reliability
    : {}
}

export function reliabilityCatalog(reliability: Rec | null | undefined): Rec[] {
  if (!reliability || typeof reliability !== 'object') return []
  if (Array.isArray(reliability.group_catalog)) return reliability.group_catalog.map((row: Rec) => Object.assign({}, row))
  const candidates: Rec[] = []
  for (const key of ['groups', 'test_groups', 'flaky_candidates']) {
    const v = reliability[key]
    if (Array.isArray(v)) candidates.push(...v)
    else if (v && typeof v === 'object') candidates.push(...(Object.values(v) as Rec[]))
  }
  const rankings = reliability.latency_rankings || {}
  for (const key of ['by_p90_duration', 'by_median_duration', 'by_failure_rate']) {
    if (Array.isArray(rankings[key])) candidates.push(...rankings[key])
  }
  const byIdentity = new Map<string, Rec>()
  candidates.forEach((row) => {
    const name = row.name || row.label || row.group || row.group_name
    if (!name) return
    const variant = [row.id || row.evidence_ref, name, row.hardware || row.hw, (row.queues || []).join('|'), row.queue, row.shard, row.step_key]
      .filter(Boolean)
      .join('::')
    const key = row.id || row.evidence_ref ? 'id:' + String(row.id || row.evidence_ref) : 'variant:' + normalizeLabel(variant)
    byIdentity.set(key, Object.assign({}, byIdentity.get(key) || {}, row, { name }))
  })
  return Array.from(byIdentity.values())
}

/** The unique reliability row for a test-group name (classic groupReliability), else null. */
export function groupReliability(reliability: Rec | null | undefined, name: unknown): Rec | null {
  const key = normalizeLabel(name)
  const matches = reliabilityCatalog(reliability).filter((row) => normalizeLabel(row.name) === key)
  return matches.length === 1 ? matches[0]! : null
}

export function evidenceObservations(candidate: Rec): Rec[] {
  const rows: Rec[] = candidate.observations || candidate.evidence || candidate.runs_evidence || []
  return rows.slice().sort((a, b) => {
    const buildDelta = Number(b.build_number || 0) - Number(a.build_number || 0)
    if (buildDelta) return buildDelta
    return String(a.queue || '').localeCompare(String(b.queue || ''))
  })
}

export function isNightlyObservation(observation: Rec): boolean {
  return (
    String(observation.build_kind || '').toLowerCase() === 'nightly' ||
    /\bnightly\b/i.test(String(observation.message || observation.build_message || ''))
  )
}

export function observationDurationMinutes(observation: Rec): number | null {
  const raw =
    observation.duration_mins !== undefined
      ? observation.duration_mins
      : observation.wall_duration_mins !== undefined
        ? observation.wall_duration_mins
        : observation.duration_min !== undefined
          ? observation.duration_min
          : observation.dur
  return Number.isFinite(Number(raw)) ? Number(raw) : null
}

export function observationWaitMinutes(observation: Rec): number | null {
  const raw = observation.wait_mins !== undefined ? observation.wait_mins : observation.wait_min
  return Number.isFinite(Number(raw)) ? Number(raw) : null
}

export function trailingPassStats(observations: Rec[], windowSize: number) {
  const size = Math.max(1, Number(windowSize || 10))
  return observations.map((_, index) => {
    const windowRows = observations.slice(Math.max(0, index - size + 1), index + 1)
    const passed = windowRows.filter((row) => observationState(row) === 'passed').length
    return { passed, total: windowRows.length, rate: windowRows.length ? (passed / windowRows.length) * 100 : null }
  })
}

export function groupPublicationHistoryComplete(group: Rec | null | undefined): boolean {
  if (!group || typeof group !== 'object') return true
  const retained = Number(group.retained_observation_count)
  const sourceRetained = Number(group.source_retained_observation_count)
  const aggregate = Number(group.observation_count)
  const sourceCountMatches = !Number.isFinite(retained) || !Number.isFinite(sourceRetained) || retained === sourceRetained
  const aggregateCountMatches = !Number.isFinite(aggregate) || !Number.isFinite(sourceRetained) || aggregate === sourceRetained
  return (
    group.publication_history_complete !== false &&
    group.history_truncated !== true &&
    sourceCountMatches &&
    aggregateCountMatches
  )
}

/** Exact observations the evidence sheet charts, oldest first (classic openMixedOutcomeEvidence). */
export function mixedOutcomeObservations(candidate: Rec): Rec[] {
  const sourcePipeline = candidate.source_pipeline || 'ci'
  return evidenceObservations(candidate)
    .filter(
      (row) =>
        (!row.source_pipeline || row.source_pipeline === sourcePipeline) && Boolean(exactPipelineEvidenceUrl(row, sourcePipeline)),
    )
    .sort((a, b) => new Date(observationTimestamp(a) || 0).getTime() - new Date(observationTimestamp(b) || 0).getTime())
}

const SOFT = ['soft', 'soft_fail', 'soft_failed']

export function mixedOutcomeSummary(observations: Rec[], complete: boolean, mode: 'main' | 'nightly') {
  const passed = observations.filter((row) => observationState(row) === 'passed').length
  const soft = observations.filter((row) => SOFT.includes(observationState(row))).length
  const incidents = observations.filter(isIncidentObservation)
  const hard = Math.max(0, incidents.length - soft)
  const prefix = complete ? '' : '≥'
  return [
    { label: mode === 'main' ? 'Main observations' : 'Nightly observations', value: prefix + integer(observations.length), tone: 'is-neutral' as Tone },
    { label: 'Passed', value: prefix + integer(passed), tone: 'is-success' as Tone },
    { label: 'Hard incidents', value: prefix + integer(hard), tone: (hard ? 'is-danger' : 'is-neutral') as Tone },
    { label: 'Soft incidents', value: prefix + integer(soft), tone: (soft ? 'is-warning' : 'is-neutral') as Tone },
    { label: 'Incident rate', value: complete ? percent(incidents.length, observations.length) : 'Unavailable', tone: (incidents.length ? 'is-warning' : 'is-success') as Tone },
  ]
}

export function outcomeSeries(observation: Rec): 'passed' | 'soft' | 'hard' | null {
  const stateName = observationState(observation)
  if (stateName === 'passed') return 'passed'
  if (SOFT.includes(stateName)) return 'soft'
  return isIncidentObservation(observation) ? 'hard' : null
}

export function evidenceResultLabel(observation: Rec): string {
  const stateName = observationState(observation)
  return stateName === 'soft' ? 'soft fail' : stateName === 'hard' ? 'hard fail' : stateName
}

/** Classic evidence table filter: newest first, result filter, then build/queue/name/result search. */
export function filterEvidenceRows(observations: Rec[], query: string, mode: string): Rec[] {
  const needle = query.trim().toLowerCase()
  return observations
    .slice()
    .reverse()
    .filter((row) => {
      const incident = isIncidentObservation(row)
      if (mode === 'passing' && observationState(row) !== 'passed') return false
      if (mode === 'incident' && !incident) return false
      if (!needle) return true
      return [row.build_number, row.queue, row.raw_name, row.name, observationState(row)].some((part) =>
        String(part || '').toLowerCase().includes(needle),
      )
    })
}

export function reliabilitySourcePipeline(reliability: Rec): string {
  const cohort = reliability.cohort || {}
  const provenanceCohort = (cohort.provenance || {}).cohort || {}
  return reliability.source_pipeline || cohort.pipeline || provenanceCohort.pipeline || ''
}

export function reliabilityScopeInfo(input: Rec | null | undefined) {
  const reliability: Rec = input && typeof input === 'object' ? input : {}
  const explicit = reliability.scope || reliability.observation_scope || reliability.denominator_scope || ''
  const text = JSON.stringify([explicit, reliability.cohort, reliability.denominator, reliability.source_pipeline, reliability.evidence_definitions]).toLowerCase()
  const available =
    reliability.available === true &&
    reliability.source_pipeline === 'ci' &&
    reliability.job_scope === 'amd_gpu' &&
    reliability.hardware_scope === 'amd_mi_gpu' &&
    (reliability.cohort || {}).available === true
  const allMain = available && /all[-_ ]main|origin\/main|branch.?main/.test(text) && !/nightly job runs|night(?:ly|lies)[-_ ]only/.test(text)
  const pipeline = reliabilitySourcePipeline(reliability)
  const source = 'AMD MI main CI'
  return {
    allMain,
    available,
    pipeline,
    label: allMain ? 'All-main reliability - ' + source : available ? 'Retained nightly reliability - ' + source : 'AMD MI reliability unavailable',
    detail: allMain
      ? 'All completed ' + source + ' branch=main builds in the retained window'
      : available
        ? 'Current payload contains nightly-only reliability; the all-main ledger has not landed yet'
        : 'The AMD MI main cohort is absent; nightly data was not substituted',
  }
}

export type GroupDetailState =
  | { kind: 'evidence'; row: Rec; scopeLabel: string }
  | { kind: 'aggregate'; name: string; fields: { label: string; value: unknown }[]; canLoadHistory: boolean }

/** What classic openGroupDetail shows for a matrix row ({name, area}) given the loaded ops. */
export function groupDetailState(group: Rec, ops: Rec | null | undefined): GroupDetailState {
  const reliability = reliabilityForPipeline(ops, 'ci')
  const name = group.name || group.label || group.group
  const row = groupReliability(reliability, name)
  if (row && evidenceObservations(row).length) {
    return { kind: 'evidence', row, scopeLabel: reliabilityScopeInfo(reliability).label.toLowerCase() }
  }
  const display = name || 'Test group'
  return {
    kind: 'aggregate',
    name: display,
    fields: [
      { label: 'Area', value: group.area },
      { label: 'Runs', value: group.runs !== undefined ? integer(group.runs) : group.count !== undefined ? integer(group.count) : null },
      {
        label: 'Median completion',
        value: group.median_dur !== undefined ? duration(group.median_dur) : group.p50_min !== undefined ? duration(group.p50_min) : null,
      },
      {
        label: 'p90 completion',
        value: group.p90_dur !== undefined ? duration(group.p90_dur) : group.p90_min !== undefined ? duration(group.p90_min) : null,
      },
      { label: 'Queues', value: (group.queues || []).join(', ') || group.queue },
    ],
    canLoadHistory: Boolean(display) && !reliabilityCatalog(reliability).length,
  }
}

export function mixedOutcomeNotice(observations: Rec[], complete: boolean, sourcePipeline: string) {
  const hasPassing = observations.some((row) => observationState(row) === 'passed')
  const hasIncidents = observations.some(isIncidentObservation)
  return {
    tone: (complete ? 'is-info' : 'is-warning') as Tone,
    strong: hasPassing && hasIncidents ? 'Classification: mixed-outcome candidate. ' : 'Historical group evidence. ',
    text:
      'Outcome, completion, and queue-wait history below use exact ' +
      sourcePipeline +
      ' observations. ' +
      (complete
        ? 'Any incident rate shown is not a test-case flake probability.'
        : 'This group history was byte-bounded; exact rows remain inspectable, but derived rates, percentiles, and deltas are unavailable.'),
  }
}

export function outcomeChartCaption(observations: Rec[], complete: boolean): string {
  const rolling = trailingPassStats(observations, 10)
  const current = rolling[rolling.length - 1] || ({} as Rec)
  return complete
    ? 'Current trailing 10: ' + Number(current.rate || 0).toFixed(1) + '% (' + integer(current.passed) + '/' + integer(current.total) + '); bar color is the exact result'
    : 'Rate trend unavailable because the published group history is incomplete; use the exact rows below.'
}
