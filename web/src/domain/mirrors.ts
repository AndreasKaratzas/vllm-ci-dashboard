// CI Health "AMD test groups" view (view id 'mirrors') and the Overview summary:
// ports of docs/assets/js/amd-mirror-inventory.js with the classic UI helpers
// inlined. The inventory lists every AMD test group on vLLM main: GPU and
// CPU-only mirror.amd declarations plus native AMD steps.
import { integer, value, type Rec, type Tone } from './format'
import { hardwareDisplayLabel } from './health'
import { compareText } from './healthHardware'

export const MIRROR_SOURCE_ASSET_URL = 'data/vllm/ci/operations_v2/test_group_parity.json'

export type MirrorMode = 'required' | 'optional' | 'soft fail'
export type TestGroupKind = 'gpu_mirror' | 'cpu_mirror' | 'native'

export const TEST_GROUP_KINDS: { key: TestGroupKind; label: string; plural: string }[] = [
  { key: 'gpu_mirror', label: 'GPU mirror', plural: 'GPU mirrors' },
  { key: 'cpu_mirror', label: 'CPU-only mirror', plural: 'CPU-only mirrors' },
  { key: 'native', label: 'Native AMD', plural: 'native AMD steps' },
]

/** Rows published before kinds existed were GPU mirrors only. */
export function amdTestGroupKind(row: Rec | null | undefined): TestGroupKind {
  const kind = (row || {}).kind
  return kind === 'cpu_mirror' || kind === 'native' ? kind : 'gpu_mirror'
}

export function amdTestGroupKindLabel(row: Rec | null | undefined): string {
  const kind = amdTestGroupKind(row)
  return TEST_GROUP_KINDS.find((item) => item.key === kind)!.label
}

export function amdMirrorInventoryRows(inventory: Rec | null | undefined): Rec[] {
  return (Array.isArray((inventory || {}).rows) ? (inventory as Rec).rows : [])
    .filter((row: unknown) => row && typeof row === 'object')
    .map((row: Rec) => {
      const sourceFile = String(row.source_file || '')
      return Object.assign({}, row, {
        yaml_file: sourceFile,
        label: row.upstream_label || row.nvidia_label || row.amd_label,
        key: row.definition_id,
        kind: amdTestGroupKind(row),
        area: row.area || sourceFile.split('/').pop()!.replace(/\.ya?ml$/i, '').replaceAll('_', ' '),
      })
    })
    .sort(
      (left: Rec, right: Rec) =>
        compareText(left.area, right.area) ||
        compareText(left.yaml_file, right.yaml_file) ||
        Number(left.yaml_index || 0) - Number(right.yaml_index || 0) ||
        compareText(left.label, right.label),
    )
}

export interface MirrorInventoryState {
  total: number | null
  rows: Rec[]
  aggregateComplete: boolean
  detailComplete: boolean
  omitted: number
  kinds: Record<TestGroupKind, number>
  kindsExact: boolean
}

export function amdMirrorInventoryState(inventory: Rec | null | undefined): MirrorInventoryState {
  const summary = (inventory || {}).summary || {}
  const source = (inventory || {}).source || {}
  const commit = source.current_definition_commit_sha || source.main_commit || ''
  const currentSource = source.pipeline === 'ci' && /^[0-9a-f]{40}$/.test(commit)
  const rawTotal = currentSource ? summary.total : null
  const parsedTotal = rawTotal === null || rawTotal === undefined || rawTotal === '' ? null : Number(rawTotal)
  const total = Number.isFinite(parsedTotal) && (parsedTotal as number) >= 0 ? Math.floor(parsedTotal as number) : null
  const rows = currentSource ? amdMirrorInventoryRows(inventory) : []
  const retention = (inventory || {}).publication_retention || {}
  const groupIndex = retention.group_index || {}
  const aggregateComplete = retention.aggregate_summaries_complete !== false
  const detailComplete =
    aggregateComplete && groupIndex.complete_relative_to_source !== false && total !== null && rows.length === total
  const published = amdTestGroupKinds(rows)
  const byKind = currentSource ? summary.by_kind : null
  const exactKinds =
    byKind && typeof byKind === 'object' && TEST_GROUP_KINDS.every((item) => Number.isInteger(byKind[item.key]) && byKind[item.key] >= 0)
      ? (Object.fromEntries(TEST_GROUP_KINDS.map((item) => [item.key, byKind[item.key]])) as Record<TestGroupKind, number>)
      : null
  const kindsExact = Boolean(exactKinds) && aggregateComplete
  return {
    total,
    rows,
    aggregateComplete,
    detailComplete,
    omitted: Math.max(0, total === null ? 0 : total - rows.length),
    kinds: exactKinds && kindsExact ? exactKinds : published,
    kindsExact: kindsExact || detailComplete,
  }
}

export function amdMirrorSourceUrl(inventory: Rec | null | undefined, row: Rec | null | undefined): string {
  const source = (inventory || {}).source || {}
  const repository = String(source.repository || 'vllm-project/vllm').trim()
  const commit = String(source.current_definition_commit_sha || source.main_commit || '').trim().toLowerCase()
  const path = String((row || {}).yaml_file || '').replace(/^\/+/, '')
  if (!/^[a-z0-9_.-]+\/[a-z0-9_.-]+$/i.test(repository) || !/^[0-9a-f]{40}$/.test(commit) || !path) return ''
  return 'https://github.com/' + repository + '/blob/' + commit + '/' + path.split('/').map(encodeURIComponent).join('/')
}

export function amdMirrorGateMode(row: Rec): MirrorMode {
  if (row.gate_kind === 'soft_fail' || row.soft_fail === true) return 'soft fail'
  if (row.gate_kind === 'optional' || row.optional === true) return 'optional'
  return 'required'
}

export function amdMirrorModeTone(row: Rec): Tone {
  return amdMirrorGateMode(row) === 'required' ? 'neutral' : 'soft'
}

export function amdMirrorModes(rows: Rec[] | null | undefined): Record<MirrorMode, number> {
  return (rows || []).reduce<Record<MirrorMode, number>>(
    (counts, row) => {
      counts[amdMirrorGateMode(row)] += 1
      return counts
    },
    { required: 0, optional: 0, 'soft fail': 0 },
  )
}

export function amdTestGroupKinds(rows: Rec[] | null | undefined): Record<TestGroupKind, number> {
  return (rows || []).reduce<Record<TestGroupKind, number>>(
    (counts, row) => {
      counts[amdTestGroupKind(row)] += 1
      return counts
    },
    { gpu_mirror: 0, cpu_mirror: 0, native: 0 },
  )
}

/** "219 GPU mirrors · 2 CPU-only mirrors · 19 native AMD steps", with ≥ when only published rows are known. */
export function amdTestGroupKindText(state: MirrorInventoryState): string {
  const prefix = state.kindsExact ? '' : '≥'
  return TEST_GROUP_KINDS.map((item) => prefix + integer(state.kinds[item.key]) + ' ' + item.plural).join(' · ')
}

export function amdMirrorAreaRows(inventory: Rec | null | undefined, groups: Rec[] | null | undefined): Rec[] {
  const byFile = new Map<string, Rec>()
  ;(groups || []).forEach((group) => {
    const yamlFile = String(group.yaml_file || 'unknown')
    if (!byFile.has(yamlFile)) {
      byFile.set(yamlFile, {
        area: group.area || yamlFile.split('/').pop()!.replace(/\.ya?ml$/i, '').replaceAll('_', ' '),
        yaml_file: yamlFile,
        count: 0,
        optional: 0,
        soft_fail: 0,
        devices: new Set<string>(),
        queues: new Set<string>(),
      })
    }
    const rollup = byFile.get(yamlFile)!
    rollup.count += 1
    if (amdMirrorGateMode(group) === 'optional') rollup.optional += 1
    if (amdMirrorGateMode(group) === 'soft fail') rollup.soft_fail += 1
    if (group.device) rollup.devices.add(String(group.device))
    if (group.queue) rollup.queues.add(String(group.queue))
  })
  return Array.from(byFile.values())
    .map((row) =>
      Object.assign({}, row, {
        required: row.count - row.optional - row.soft_fail,
        devices: Array.from(row.devices as Set<string>).sort(compareText),
        queues: Array.from(row.queues as Set<string>).sort(compareText),
        source_url: amdMirrorSourceUrl(inventory, row),
      }),
    )
    .sort(
      (left, right) =>
        Number(right.count) - Number(left.count) ||
        compareText(left.area, right.area) ||
        compareText(left.yaml_file, right.yaml_file),
    )
}

export function amdMirrorHardwareRows(groups: Rec[] | null | undefined): Rec[] {
  const families = new Map<string, Rec>()
  ;(groups || []).forEach((group) => {
    const device = String(group.device || '').trim().toLowerCase()
    const familyMatch = device.match(/^(mi\d+)/)
    const family = familyMatch ? familyMatch[1]! : device || 'unassigned'
    if (!families.has(family)) families.set(family, { id: family, count: 0, devices: new Set<string>() })
    const row = families.get(family)!
    row.count += 1
    if (device) row.devices.add(device)
  })
  return Array.from(families.values())
    .map((row) => ({
      id: row.id,
      label: row.id === 'unassigned' ? 'Unassigned' : hardwareDisplayLabel(row.id),
      count: row.count,
      devices: Array.from(row.devices as Set<string>).sort(compareText),
    }))
    .sort((left, right) => Number(right.count) - Number(left.count) || compareText(left.label, right.label))
}

export function amdMirrorLabel(row: Rec | null | undefined): string {
  if ((row || {}).amd_label) return row!.amd_label
  const sourceLabel = String((row || {}).label || (row || {}).key || 'AMD test group')
  const deviceFamily = String((row || {}).device || '').split('_')[0]
  const hardware = deviceFamily ? hardwareDisplayLabel(deviceFamily) : 'AMD'
  return sourceLabel.replace(/:nvidia:/i, ':amd:').replace(/\((?:A|B|H|L)\d[^)]*\)/i, '(' + hardware + ')')
}

export function mirrorCommit(inventory: Rec | null | undefined): string {
  const source = (inventory || {}).source || {}
  return source.current_definition_commit_sha || source.main_commit || ''
}

/** The mirror detail sheet (classic openAmdMirrorDetail). */
export function amdMirrorDetail(row: Rec, inventory: Rec | null | undefined) {
  const sourceUrl = amdMirrorSourceUrl(inventory, row)
  const commit = mirrorCommit(inventory)
  return {
    title: amdMirrorLabel(row),
    subtitle: amdTestGroupSubtitle(row),
    description:
      'This source definition counts once in the AMD test-group total. Parallel execution changes potential job fan-out, not the declaration count.',
    fields: [
      { label: 'Kind', value: amdTestGroupKindLabel(row) },
      { label: 'Source test group', value: row.label },
      { label: 'Source definition ID', value: row.key },
      { label: 'Test area', value: row.area },
      { label: 'YAML definition', value: row.yaml_file },
      { label: 'YAML step index', value: row.yaml_index === undefined ? null : integer(Number(row.yaml_index) + 1) },
      { label: 'AMD device', value: row.device },
      { label: 'GPU width', value: row.num_devices === null || row.num_devices === undefined ? null : integer(row.num_devices) },
      { label: 'Configured route mode', value: amdMirrorGateMode(row) },
      { label: 'Timeout', value: row.timeout_in_minutes ? integer(row.timeout_in_minutes) + ' minutes' : null },
      { label: 'Dependency files', value: row.dependency_file_count === undefined ? null : integer(row.dependency_file_count) },
    ]
      .filter((field) => field.value !== null && field.value !== undefined && field.value !== '')
      .map((field) => ({ label: field.label, value: String(field.value) })),
    sources: [
      sourceUrl ? { label: 'Open pinned test-area YAML', url: sourceUrl } : null,
      commit ? { label: 'Open current main CI commit', url: 'https://github.com/vllm-project/vllm/commit/' + commit } : null,
      { label: 'Open published AMD test-group scan', url: MIRROR_SOURCE_ASSET_URL },
    ].filter(Boolean) as { label: string; url: string }[],
  }
}

export function amdTestGroupSubtitle(row: Rec | null | undefined): string {
  const kind = amdTestGroupKind(row)
  if (kind === 'native') return 'Native AMD step on vLLM main'
  if (kind === 'cpu_mirror') return 'CPU-only mirror.amd declaration on vLLM main'
  return 'Physical mirror.amd declaration on vLLM main'
}

export function mirrorAreaSearchText(row: Rec): string {
  return [row.area, row.yaml_file, row.devices.join(' '), row.queues.join(' ')].join(' ')
}

export function mirrorInventorySearchText(row: Rec): string {
  return [row.amd_label, row.label, row.key, row.area, row.yaml_file, row.device, amdMirrorGateMode(row), amdTestGroupKindLabel(row)].join(' ')
}

export function mirrorDevicesLabel(row: Rec): string {
  return row.devices.map(hardwareDisplayLabel).join(', ') || '-'
}

/** The Overview card (classic renderAmdMirrorSummary). */
export function amdMirrorSummary(inventory: Rec | null | undefined, runtimeGroupCount: number | null | undefined) {
  const state = amdMirrorInventoryState(inventory || {})
  const rows = state.rows
  const modes = amdMirrorModes(rows)
  const files = new Set(rows.map((row) => (row || {}).yaml_file).filter(Boolean)).size
  const countLabel = state.total === null ? '—' : (state.aggregateComplete ? '' : '≥') + integer(state.total)
  const detailPrefix = state.detailComplete ? '' : '≥'
  const facts: string[] = []
  if (rows.length || state.total === 0) {
    facts.push(
      amdTestGroupKindText(state),
      detailPrefix + integer(modes.required) + ' required',
      detailPrefix + integer(modes.optional) + ' optional',
      detailPrefix + integer(modes['soft fail']) + ' soft fail',
      detailPrefix + integer(files) + ' files',
    )
  }
  return {
    unavailable: state.total === null,
    countLabel,
    ariaLabel:
      (state.total === null ? 'AMD test-group count unavailable' : countLabel + ' configured AMD test groups') +
      '. Open the AMD test-group inventory.',
    meta:
      'Mirror and native AMD definitions · ' +
      (runtimeGroupCount === null || runtimeGroupCount === undefined
        ? 'runtime group count unavailable'
        : 'separate from ' + integer(runtimeGroupCount) + ' runtime groups'),
    // The required/optional/soft-fail track shows only with complete detail.
    track: state.detailComplete && rows.length ? mirrorModeTrack(modes, rows.length) : null,
    facts: facts.length ? facts.join(' · ') : 'Open the inventory for configuration details',
  }
}

export function mirrorModeTrack(modes: Record<MirrorMode, number>, total: number) {
  return [
    { key: 'required', label: 'Required', count: modes.required, width: (modes.required / total) * 100 },
    { key: 'optional', label: 'Optional', count: modes.optional, width: (modes.optional / total) * 100 },
    { key: 'soft_fail', label: 'Soft fail', count: modes['soft fail'], width: (modes['soft fail'] / total) * 100 },
  ]
}

/** The mirrors view (classic renderAmdMirrorInventory and its cards). */
export function amdMirrorInventoryView(inventory: Rec | null | undefined) {
  const state = amdMirrorInventoryState(inventory)
  const groups = state.rows
  const areas = amdMirrorAreaRows(inventory, groups)
  const hardwareRows = amdMirrorHardwareRows(groups)
  const modes = amdMirrorModes(groups)
  const totalExact = state.total !== null && state.aggregateComplete
  const breakdownComplete = state.detailComplete
  const totalDisplay = state.total === null ? '—' : (state.aggregateComplete ? '' : '≥') + integer(state.total)
  const publishedTotal = groups.length
  const shown = Boolean(publishedTotal) || state.total === 0
  const prefix = state.detailComplete ? '' : '≥'
  const topAreas = areas.slice(0, 8)
  const mirrorCount = state.total === null ? groups.length : state.total
  const commit = mirrorCommit(inventory)
  let coverageNote: string | null = null
  if (!state.detailComplete && state.total !== null) {
    coverageNote =
      (state.aggregateComplete
        ? 'The aggregate total remains exact, but '
        : 'The published aggregate is marked incomplete, and ') +
      'the published detail index contains ' +
      integer(groups.length) +
      ' of ' +
      integer(state.total) +
      ' AMD test-group definitions. File, kind, and configured-mode breakdowns are lower bounds.'
  }
  return {
    state,
    groups,
    areas,
    hardwareRows,
    modes,
    kinds: TEST_GROUP_KINDS.map((item) => ({
      ...item,
      count: state.kinds[item.key],
      display: state.kindsExact || groups.length ? (state.kindsExact ? '' : '≥') + integer(state.kinds[item.key]) : '—',
    })),
    hero: {
      totalDisplay,
      breakdownComplete,
      kinds: state.kindsExact || groups.length ? amdTestGroupKindText(state) : '',
      ariaLabel:
        (state.total === null ? 'Configured AMD test-group total unavailable' : totalDisplay + ' configured AMD test groups') +
        (breakdownComplete && hardwareRows.length
          ? '. ' + hardwareRows.map((row) => row.label + ': ' + integer(row.count)).join(', ')
          : '. Complete hardware breakdown unavailable'),
      meta:
        state.total === null
          ? 'The configured group total is unavailable in this snapshot.'
          : !totalExact
            ? 'The published aggregate is a lower bound in this snapshot.'
            : !breakdownComplete
              ? 'The total is exact; the hardware breakdown is unavailable because published detail is incomplete.'
              : 'Each group is one AMD YAML definition on vLLM main: a GPU or CPU-only mirror.amd declaration, or a native AMD step.',
      legend: breakdownComplete && hardwareRows.length ? hardwareRows : [],
    },
    configuration: {
      required: shown ? prefix + integer(modes.required) : '—',
      optional: shown ? prefix + integer(modes.optional) : '—',
      softFail: shown ? prefix + integer(modes['soft fail']) : '—',
      // Exact detail always shows the track; it is empty when nothing is published.
      track: state.detailComplete ? (publishedTotal ? mirrorModeTrack(modes, publishedTotal) : []) : null,
      trackLabel:
        integer(modes.required) +
        ' required and ' +
        integer(modes.optional) +
        ' optional and ' +
        integer(modes['soft fail']) +
        ' soft-fail published AMD test groups',
      files: shown ? prefix + integer(areas.length) : '—',
      families: shown ? prefix + integer(amdMirrorHardwareRows(state.rows).length) : '—',
      lowerBound: !state.detailComplete && state.total !== null,
    },
    coverageNote,
    areaChart: areas.length
      ? {
          subtitle:
            (state.detailComplete ? '' : 'Published detail · ') +
            'Top ' +
            integer(topAreas.length) +
            ' of ' +
            integer(areas.length) +
            ' source files by group count',
          rows: topAreas,
          browseLabel: 'Browse all ' + integer(areas.length) + ' test-area files',
        }
      : null,
    preview: {
      subtitle:
        state.total === null
          ? integer(groups.length) + ' published groups; exact aggregate unavailable'
          : integer(groups.length) + ' of ' + integer(state.total) + ' AMD test-group definitions (mirror.amd and native)',
      rows: groups.slice(0, 6),
      browseLabel: groups.length
        ? state.detailComplete
          ? 'Browse all ' + integer(mirrorCount) + ' AMD test groups'
          : 'Browse ' + integer(groups.length) + ' published AMD test groups'
        : null,
    },
    inventoryBrowserSubtitle: integer(groups.length) + ' published AMD test groups from vLLM main',
    commit,
    commitUrl: commit ? 'https://github.com/vllm-project/vllm/commit/' + commit : '',
  }
}

export function mirrorAreaBreakdown(row: Rec): string {
  return integer(row.required) + ' R · ' + integer(row.optional) + ' O · ' + integer(row.soft_fail) + ' S'
}

export function mirrorPreviewMeta(row: Rec): string {
  return value(row.area) + ' · ' + String(row.yaml_file || '').split('/').pop()
}
