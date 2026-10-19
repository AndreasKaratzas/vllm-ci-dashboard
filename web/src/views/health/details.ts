import { markRaw } from 'vue'
import type { Detail } from '@/components/evidence/detail'
import { badge, dotted, drill, link, mono, num, text, type ColumnSpec, type FilterSpec } from '@/components/table/columns'
import { ROUTE_MODE_LABELS, testGroupDefinitionState, testGroupParityState } from '@/domain/ciHealth'
import { integer, toneForState, value, type Rec } from '@/domain/format'
import { hardwareDisplayLabel } from '@/domain/health'
import {
  MATRIX_BROWSER_TITLES,
  MATRIX_CLASSIFICATION_OPTIONS,
  MATRIX_STATUS_OPTIONS,
  architectureBrowser,
  architectureCellUrl,
  groupDetailState,
  matrixBrowserFilters,
  matrixClassificationPredicate,
  matrixGateKindLabel,
  matrixGroupDetail,
  matrixGroupEvidence,
  matrixGroupSearchText,
  matrixHealthStatusLabel,
  matrixHealthTone,
  matrixStatusPredicate,
  type ArchitectureHealth,
  type MatrixBrowserMode,
} from '@/domain/healthHardware'
import { amdMirrorDetail, amdMirrorGateMode, amdMirrorLabel, amdMirrorSourceUrl, mirrorAreaSearchText, mirrorDevicesLabel, type MirrorMode } from '@/domain/mirrors'
import CommandsBlock from '@/features/amd-groups/CommandsBlock.vue'
import { hardwareCell } from '@/features/amd-groups/details'
import GroupHistory from '@/features/amd-groups/GroupHistory.vue'
import { SERIES, statusVar, tint, type PaletteColor } from '@/lib/palette'
import type { Tone } from '@/lib/tone'

// --- Upstream parity ---

/** Coverage gaps are attention items (amber), never runtime failures (red). */
export function parityTone(tone: Tone): Tone {
  return tone === 'hard' ? 'soft' : tone
}

export function parityStateBadge(state: string) {
  const presentation = testGroupParityState(state)
  return { label: presentation.label.replace(/^[●■]\s*/, ''), tone: parityTone(presentation.tone) }
}

export function parityGroupDetail(row: Rec, source: Rec): Detail {
  const definition: Rec = row.definition_resolution || {}
  const commit = source.main_commit || source.commit_sha || ''
  const commitUrl = source.main_commit_url || (commit ? `https://github.com/vllm-project/vllm/commit/${commit}` : '')
  const state = parityStateBadge(row.state)
  return {
    id: `parity:${row.id}`,
    title: String(row.title || 'Upstream logical test group'),
    subtitle: 'Configured AMD routes against upstream source definitions',
    description: row.assessment || 'No source-derived assessment is available.',
    stats: [
      { label: 'Configured coverage', value: state.label, tone: state.tone },
      { label: 'AMD route mode', value: ROUTE_MODE_LABELS[row.gate_kind] || 'Unavailable' },
      { label: 'Upstream CUDA variants', value: value(row.cuda_variants) },
    ],
    fields: [
      { label: 'Inventory number', value: value(row.id) },
      { label: 'Test area', value: value(row.area) },
      { label: 'Current CI definition', value: testGroupDefinitionState(row) },
      { label: 'Source name', value: value(row.title) },
      { label: 'Current replacement names', value: (definition.successor_labels || []).join(' · ') || null },
      { label: 'Definition changes', value: definition.note || null },
    ],
    sources: [
      commitUrl ? { label: 'Current main CI source commit', url: commitUrl } : null,
      /^[0-9a-f]{40}$/.test(definition.commit_sha || '')
        ? { label: 'Current CI definitions', url: `https://github.com/vllm-project/vllm/tree/${definition.commit_sha}/.buildkite/test_areas` }
        : null,
    ].filter((s): s is { label: string; url: string } => s !== null),
    width: 'md',
  }
}

export function parityInventoryDetail(id: string, title: string, rows: Rec[], source: Rec): Detail {
  const columns: ColumnSpec<Rec>[] = [
    { id: 'id', label: '#', numeric: true, value: (row) => Number(row.id || 0), cell: (row) => ({ text: integer(row.id), mono: true }) },
    {
      id: 'title',
      label: 'Upstream logical test group',
      value: (row) => [row.title, row.reviewed_title].join(' '),
      cell: (row) => drill(row.title, () => parityGroupDetail(row, source)),
      hideable: false,
    },
    { id: 'area', label: 'Area', value: (row) => value(row.area) },
    { id: 'cuda', label: 'CUDA variants', value: (row) => value(row.cuda_variants) },
    {
      id: 'coverage',
      label: 'Configured coverage',
      value: (row) => parityStateBadge(row.state).label,
      cell: (row) => badge(parityStateBadge(row.state).label, parityStateBadge(row.state).tone),
    },
    { id: 'definition', label: 'Current CI definition', value: (row) => testGroupDefinitionState(row) },
    { id: 'assessment', label: 'ROCm counterpart or assessment', sortable: false, value: (row) => value(row.assessment), cell: (row) => text(row.assessment) },
  ]
  return {
    id: `parity-inventory:${id}`,
    title,
    subtitle: `${integer(rows.length)} current upstream logical test groups`,
    tables: [
      {
        rows,
        columns,
        rowDetail: (row: Rec) => parityGroupDetail(row, source),
        searchPlaceholder: 'Filter test group, area, CUDA variant, status, or assessment',
        pageSize: 25,
      },
    ],
    width: 'xl',
  }
}

// --- AMD hardware ---

export const MATRIX_PATH = 'data/vllm/ci/amd_test_matrix.json'

/** One configured test group's status and exact AMD route evidence. */
export function matrixGroupEvidenceDetail(group: Rec, matrix: Rec | null): Detail {
  const detail = matrixGroupDetail(group, matrix)
  const tone = matrixHealthTone(group.status)
  return {
    id: `matrix-group:${group.title}`,
    title: detail.title,
    subtitle: 'Test-group status and exact AMD route evidence',
    notes: [{ tone, title: matrixHealthStatusLabel(group.status), text: detail.note }],
    fields: detail.fields,
    sources: detail.sources,
    sections: detail.commandsText
      ? [{ title: 'Executed commands', meta: detail.commandsCaption, component: markRaw(CommandsBlock), props: { text: detail.commandsText } }]
      : [],
    tables: [
      {
        title: detail.evidenceCaption,
        rows: detail.evidence,
        pageSize: 10,
        searchPlaceholder: 'Filter definition, pool, or result',
        emptyText: 'No matching observations.',
        columns: [
          { id: 'definition', label: 'Definition', value: (row: Rec) => String(row.definition), cell: (row: Rec) => link(row.definition, row.url) },
          { id: 'hardware', label: 'Hardware', value: (row: Rec) => String(row.architecture).toUpperCase(), cell: (row: Rec) => hardwareCell(row.architecture) },
          { id: 'queue', label: 'Agent pool', value: (row: Rec) => value(row.queue), cell: (row: Rec) => mono(value(row.queue)) },
          { id: 'result', label: 'Latest result', value: (row: Rec) => String(row.result), cell: (row: Rec) => badge(String(row.result), toneForState(row.result)) },
          { id: 'identity', label: 'Command identity', value: (row: Rec) => row.commandIdentity || '', cell: (row: Rec) => mono(row.commandIdentity || null) },
          { id: 'source', label: 'Source', sortable: false, value: () => '', cell: (row: Rec) => link(row.sourceUrl ? 'Source' : null, row.sourceUrl) },
        ] satisfies ColumnSpec<Rec>[],
      },
    ],
    width: 'xl',
  }
}

/** The configured-group browser, opened on one status or classification slice. */
export function matrixBrowserDetail(mode: MatrixBrowserMode, groups: Rec[], subtitle: string, matrix: Rec | null): Detail {
  const initial = matrixBrowserFilters(mode)
  const without = (options: { value: string; label: string }[]) => options.filter((o) => o.value !== 'all')
  const filters: FilterSpec<Rec>[] = [
    {
      id: 'status',
      label: 'Filter configured test-group status',
      allLabel: MATRIX_STATUS_OPTIONS[0]!.label,
      options: without(MATRIX_STATUS_OPTIONS),
      initial: initial.status === 'all' ? undefined : initial.status,
      predicate: matrixStatusPredicate,
    },
    {
      id: 'classification',
      label: 'Filter configured test-group classification',
      allLabel: MATRIX_CLASSIFICATION_OPTIONS[0]!.label,
      options: without(MATRIX_CLASSIFICATION_OPTIONS),
      initial: initial.classification === 'all' ? undefined : initial.classification,
      predicate: matrixClassificationPredicate,
    },
  ]
  const open = (group: Rec) => matrixGroupEvidenceDetail(group, matrix)
  return {
    id: `matrix-browser:${mode}`,
    title: MATRIX_BROWSER_TITLES[mode],
    subtitle,
    tables: [
      {
        rows: groups,
        filters,
        showTotal: true,
        searchText: matrixGroupSearchText,
        searchPlaceholder: 'Search group, reason, command, hardware, or queue',
        rowDetail: open,
        pageSize: 50,
        columns: [
          { id: 'title', label: 'Test group', value: (g: Rec) => String(g.title ?? ''), cell: (g: Rec) => drill(g.title, () => open(g)), hideable: false },
          { id: 'status', label: 'Status', value: (g: Rec) => matrixHealthStatusLabel(g.status), cell: (g: Rec) => badge(matrixHealthStatusLabel(g.status), matrixHealthTone(g.status)) },
          { id: 'classification', label: 'Classification', value: (g: Rec) => matrixGateKindLabel(g) },
          { id: 'hardware', label: 'Hardware', value: (g: Rec) => g.architectures.map((a: string) => a.toUpperCase()).join(', ') || '-' },
          { id: 'reason', label: 'Classification reason', value: (g: Rec) => g.classificationReason || '-', cell: (g: Rec) => ({ text: g.classificationReason || '-' }) },
          { id: 'signal', label: 'Route signal', value: (g: Rec) => `${integer(g.passedCells)} pass - ${integer(g.incidentCells)} non-passing` },
          { id: 'evidence', label: 'Evidence', numeric: true, value: (g: Rec) => `${integer(matrixGroupEvidence(g).filter((e) => e.url).length)} jobs` },
        ] satisfies ColumnSpec<Rec>[],
      },
    ],
    width: 'xl',
  }
}

export function matrixUnavailableDetail(label: string, description: string): Detail {
  return {
    id: `matrix-unavailable:${label}`,
    title: label,
    subtitle: 'Operational aggregate',
    description,
    empty: 'This value is unavailable in the current snapshot.',
    sources: label === 'Configured AMD test groups' ? [{ label: 'Published matrix', url: MATRIX_PATH }] : [],
    width: 'md',
  }
}

/** A matrix row's test group: retained aggregate, with the 30-day ledger on request. */
export function groupHistoryDetail(row: Rec, ops: Rec | null): Detail {
  const group = { name: row.title, area: row.area }
  const state = groupDetailState(group, ops)
  return {
    id: `group-history:${row.title}`,
    title: state.kind === 'aggregate' ? state.name : String(state.row.name || 'Group evidence'),
    subtitle: 'Test-group detail · historical outcomes, latency, and exact Buildkite evidence',
    fields: state.kind === 'aggregate' ? state.fields.map((f) => ({ label: f.label, value: f.value as string | null })) : [],
    sections: [{ component: markRaw(GroupHistory), props: { group, ops } }],
    width: 'xl',
  }
}

export function architectureDetail(health: ArchitectureHealth, rows: Rec[], ops: Rec | null): Detail {
  const browser = architectureBrowser(health, rows)
  const id = health.architecture.id
  const open = (row: Rec) => groupHistoryDetail(row, ops)
  return {
    id: `architecture:${id}`,
    title: browser.title,
    subtitle: browser.subtitle,
    stats: [
      { label: 'Passing routes', value: integer(health.passed) },
      { label: 'Non-passing routes', value: integer(health.incident), tone: health.incident ? 'hard' : undefined },
      { label: 'Unobserved routes', value: integer(health.unknown) },
    ],
    tables: [
      {
        rows: browser.rows,
        searchText: browser.searchText,
        rowDetail: open,
        pageSize: 50,
        columns: [
          { id: 'title', label: 'Test group', value: (row: Rec) => String(row.title ?? ''), cell: (row: Rec) => drill(row.title, () => open(row)) },
          { id: 'area', label: 'Area', value: (row: Rec) => value(row.area) },
          {
            id: 'latest',
            label: 'Latest result',
            value: (row: Rec) => ((row.cells || {})[id] || {}).latest_state || 'unobserved',
            cell: (row: Rec) => badge(((row.cells || {})[id] || {}).latest_state || 'unobserved', toneForState(((row.cells || {})[id] || {}).latest_state)),
          },
          {
            id: 'build',
            label: 'Build',
            value: (row: Rec) => Number(((row.cells || {})[id] || {}).latest_build_number || 0),
            cell: (row: Rec) => {
              const cell = (row.cells || {})[id] || {}
              const url = architectureCellUrl(cell)
              return url ? link(`#${value(cell.latest_build_number)}`, url, true) : text('-')
            },
          },
        ] satisfies ColumnSpec<Rec>[],
      },
    ],
    width: 'lg',
  }
}

/** Columns of the route matrix: one per configured architecture. */
export function matrixColumns(architectures: Rec[], ops: Rec | null): ColumnSpec<Rec>[] {
  const open = (row: Rec) => groupHistoryDetail(row, ops)
  return [
    { id: 'title', label: 'Group', sortable: false, hideable: false, value: (row) => String(row.title ?? ''), cell: (row) => drill(row.title, () => open(row)) },
    { id: 'area', label: 'Area', sortable: false, value: (row) => value(row.area) },
    ...architectures.map(
      (arch): ColumnSpec<Rec> => ({
        id: `arch-${arch.id}`,
        label: String(arch.label),
        sortable: false,
        value: (row) => {
          const cell = (row.cells || {})[arch.id] || {}
          return cell.exists ? cell.latest_state || 'unknown' : '-'
        },
        cell: (row) => {
          const cell = (row.cells || {})[arch.id] || {}
          return cell.exists ? badge(cell.latest_state || 'unknown', toneForState(cell.latest_state)) : text('-')
        },
      }),
    ),
  ]
}

// --- AMD mirrors ---

/** Required is the full series hue, optional a lighter step of it; soft fail is a soft-fail state. */
export const MIRROR_MODE_COLOR: Record<MirrorMode, PaletteColor> = {
  required: SERIES.primary,
  optional: tint(SERIES.primary, 45),
  'soft fail': statusVar('soft'),
}

export function mirrorDetail(row: Rec, inventory: Rec | null): Detail {
  const detail = amdMirrorDetail(row, inventory)
  return {
    id: `mirror:${row.yaml_file}#${row.yaml_index}:${row.key}`,
    title: detail.title,
    subtitle: detail.subtitle,
    description: detail.description,
    fields: detail.fields,
    sources: detail.sources,
    width: 'md',
  }
}

export function mirrorInventoryColumns(inventory: Rec | null): ColumnSpec<Rec>[] {
  return [
    { id: 'label', label: 'Source test group', hideable: false, value: (row) => amdMirrorLabel(row), cell: (row) => drill(amdMirrorLabel(row), () => mirrorDetail(row, inventory)) },
    { id: 'area', label: 'Test area', value: (row) => value(row.area) },
    { id: 'device', label: 'AMD device', value: (row) => hardwareDisplayLabel(row.device), cell: (row) => hardwareCell(row.device) },
    { id: 'width', label: 'GPU width', numeric: true, value: (row) => Number(row.num_devices ?? -1), cell: (row) => num(row.num_devices ?? null) },
    { id: 'mode', label: 'Mode', value: (row) => amdMirrorGateMode(row), cell: (row) => dotted(amdMirrorGateMode(row), MIRROR_MODE_COLOR[amdMirrorGateMode(row)]) },
    { id: 'source', label: 'Source', sortable: false, value: () => '', cell: (row) => link('Pinned YAML', amdMirrorSourceUrl(inventory, row) || null) },
  ]
}

export function mirrorInventoryFilters(groups: Rec[]): FilterSpec<Rec>[] {
  const devices = [...new Set(groups.map((row) => hardwareDisplayLabel(String(row.device || '').split('_')[0])))].sort()
  return [
    {
      id: 'mode',
      label: 'Mode',
      allLabel: 'All modes',
      options: (['required', 'optional', 'soft fail'] as const).map((mode) => ({ value: mode, label: mode })),
      predicate: (row, selected) => amdMirrorGateMode(row) === selected,
    },
    {
      id: 'family',
      label: 'GPU family',
      allLabel: 'All GPU families',
      options: devices.map((device) => ({ value: device, label: device })),
      predicate: (row, selected) => hardwareDisplayLabel(String(row.device || '').split('_')[0]) === selected,
    },
  ]
}

export function mirrorAreasDetail(areas: Rec[]): Detail {
  return {
    id: 'mirror-areas',
    title: 'AMD mirrors by test-area file',
    subtitle: 'Physical mirror.amd declarations grouped by their pinned source YAML file',
    tables: [
      {
        rows: areas,
        showTotal: true,
        searchText: mirrorAreaSearchText,
        searchPlaceholder: 'Filter test area, YAML file, device, or queue',
        pageSize: 50,
        columns: [
          { id: 'file', label: 'Test-area file', value: (row: Rec) => String(row.yaml_file), cell: (row: Rec) => link(String(row.yaml_file).split('/').pop(), row.source_url) },
          { id: 'area', label: 'Test area', value: (row: Rec) => value(row.area) },
          { id: 'count', label: 'Mirror groups', numeric: true, value: (row: Rec) => Number(row.count || 0), cell: (row: Rec) => num(Number(row.count || 0)) },
          { id: 'required', label: 'Required', numeric: true, value: (row: Rec) => Number(row.required || 0), cell: (row: Rec) => num(Number(row.required || 0)) },
          { id: 'optional', label: 'Optional', numeric: true, value: (row: Rec) => Number(row.optional || 0), cell: (row: Rec) => num(Number(row.optional || 0)) },
          { id: 'soft', label: 'Soft fail', numeric: true, value: (row: Rec) => Number(row.soft_fail || 0), cell: (row: Rec) => num(Number(row.soft_fail || 0)) },
          { id: 'devices', label: 'AMD devices', value: (row: Rec) => mirrorDevicesLabel(row) },
        ] satisfies ColumnSpec<Rec>[],
      },
    ],
    width: 'xl',
  }
}
