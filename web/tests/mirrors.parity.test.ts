// Differential parity for CI Health "AMD test groups" (view id 'mirrors') and
// the Overview summary: the v2 port against docs/assets/js/amd-mirror-inventory.js, both its
// internal functions and the DOM it renders with the classic UI helpers.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeAll, describe, expect, it } from 'vitest'
import type { Rec } from '@/domain/format'
import { hardwareDisplayLabel } from '@/domain/health'
import * as v2 from '@/domain/mirrors'
import { classicParity, loadClassic, type ClassicFns } from './classic'
import { pinClock, readFixture } from './support/fixtures'
import { expectGoldenFile } from './support/golden'
import { rng } from './support/rng'

const INTERNALS = [
  'amdMirrorInventoryRows',
  'amdMirrorInventoryState',
  'amdMirrorSourceUrl',
  'amdMirrorGateMode',
  'amdMirrorModes',
  'amdMirrorAreaRows',
  'amdMirrorHardwareRows',
  'amdMirrorLabel',
  'amdTestGroupKind',
  'amdTestGroupKindLabel',
  'amdTestGroupKinds',
  'amdTestGroupKindText',
  'amdTestGroupKindDisplay',
  'amdTestGroupSubtitle',
  'renderAmdMirrorSummary',
  'renderAmdMirrorInventory',
]
const UI_HELPERS = [
  'badge', 'button', 'compareText', 'externalLink', 'hardwareDisplayLabel', 'integer', 'linkButton',
  'methodDisclosure', 'n', 'openDetailDrawer', 'openTableBrowser', 'panel', 'value',
]

// amd-mirror-inventory.js is a (function (global) {...})(window) module; expose its internals.
function loadMirrorClassic(): ClassicFns {
  const source = readFileSync(resolve(process.cwd(), '../docs/assets/js/amd-mirror-inventory.js'), 'utf8')
  const anchor = source.lastIndexOf('global.AmdMirrorInventory')
  if (anchor < 0) throw new Error('mirror inventory export not found')
  const exposed = `${source.slice(0, anchor)}global.__mirrorClassic = {${INTERNALS.join(',')}};\n${source.slice(anchor)}`
  new Function(exposed)()
  return (window as unknown as Record<string, ClassicFns>).__mirrorClassic!
}

const snapshot = readFixture<Rec>('mirrors_inventory.json')
const inventory: Rec = snapshot.mirror_inventory
pinClock(snapshot.generated_at)
const parity = classicParity('mirrors')
let mirror: ClassicFns
let uiBase: ClassicFns

interface Captured {
  drawers: Rec[]
  browsers: Rec[]
}

function captureUi(): { ui: Rec; captured: Captured } {
  const captured: Captured = { drawers: [], browsers: [] }
  const ui: Rec = { ...uiBase }
  ui.openDetailDrawer = (config: Rec) => captured.drawers.push(config)
  ui.openTableBrowser = (config: Rec) => captured.browsers.push(config)
  return { ui, captured }
}

const SHA = 'a'.repeat(40)
const DEVICES = ['mi300_1', 'mi355_dpx', 'MI325_8', 'mi250', 'h100', '', undefined, 'cpu', 'mi3']
const FILES = ['.buildkite/test_areas/attention.yaml', '.buildkite/test_areas/lm_eval.yml', '/.buildkite/test_areas/Kernels_Core.YAML', 'misc.yaml', '', undefined, 'a/b c.yaml']

function generateInventory(rand: () => number): Rec {
  const pick = <T,>(xs: T[]) => xs[Math.floor(rand() * xs.length)]!
  const rows: Rec[] = Array.from({ length: Math.floor(rand() * 18) }, (_, i) => ({
    definition_id: rand() < 0.9 ? `def-${i % 7}` : undefined,
    kind: pick(['gpu_mirror', 'gpu_mirror', 'cpu_mirror', 'native', undefined, 'other']),
    source_file: pick(FILES),
    upstream_label: rand() < 0.5 ? pick([':nvidia: (H100) Alpha', ':nvidia: (B200) beta', 'Gamma (L4)', 'alpha']) : undefined,
    nvidia_label: rand() < 0.3 ? ':nvidia: (A100) Delta' : undefined,
    amd_label: rand() < 0.4 ? pick([':amd: (MI300) Alpha', ':amd: (MI355) beta']) : undefined,
    device: pick(DEVICES),
    queue: rand() < 0.3 ? pick(['amd_mi300_1', 'q2']) : undefined,
    num_devices: pick([1, 2, 8, null, undefined, '4']),
    optional: pick([true, false, undefined, 'true', 1]),
    soft_fail: pick([true, false, undefined, 'true', 1]),
    gate_kind: pick(['required', 'optional', 'soft_fail', undefined, 'other']),
    yaml_index: pick([0, 3, 1, undefined, '2']),
    area: rand() < 0.3 ? pick(['Kernels', 'kernels', 'Attention']) : undefined,
    timeout_in_minutes: pick([30, 0, undefined]),
    dependency_file_count: pick([3, 0, undefined]),
  }))
  if (rand() < 0.1) rows.push(null as unknown as Rec, 'x' as unknown as Rec)
  const total = pick([rows.filter((r) => r && typeof r === 'object').length, rows.length + 3, 0, 2, null, undefined, '', '7', -1, 'x', 3.7])
  return {
    source: {
      pipeline: pick(['ci', 'ci', 'ci', 'amd-ci', undefined]),
      current_definition_commit_sha: pick([SHA, SHA, 'B'.repeat(40), 'abc', undefined]),
      main_commit: pick([SHA, undefined]),
      repository: pick(['vllm-project/vllm', 'x/y', 'bad repo', undefined]),
    },
    summary:
      rand() < 0.95
        ? {
            total,
            by_kind: pick([
              undefined,
              { gpu_mirror: 5, cpu_mirror: 1, native: 2 },
              { gpu_mirror: 0, cpu_mirror: 0, native: 0 },
              { gpu_mirror: 3, native: 1 },
              { gpu_mirror: -1, cpu_mirror: 0, native: 0 },
              { gpu_mirror: '4', cpu_mirror: 0, native: 0 },
              'x',
            ]),
          }
        : undefined,
    publication_retention:
      rand() < 0.3
        ? { aggregate_summaries_complete: pick([true, false, undefined]), group_index: { complete_relative_to_source: pick([true, false, undefined]) } }
        : undefined,
    rows: rand() < 0.95 ? rows : 'nope',
  }
}

beforeAll(() => {
  uiBase = loadClassic(UI_HELPERS)
  mirror = loadMirrorClassic()
})

const text = (el: Element | null | undefined) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()
const children = (el: Element | null | undefined) => [...(el?.children ?? [])].map((child) => text(child))

function compareFunctions(inv: Rec) {
  const ui = uiBase
  const state = v2.amdMirrorInventoryState(inv)
  parity('amdMirrorInventoryState', state, mirror.amdMirrorInventoryState!(inv, ui))
  parity('amdMirrorInventoryRows', v2.amdMirrorInventoryRows(inv), mirror.amdMirrorInventoryRows!(inv, ui))
  parity('amdMirrorAreaRows', v2.amdMirrorAreaRows(inv, state.rows), mirror.amdMirrorAreaRows!(inv, state.rows, ui))
  parity('amdMirrorHardwareRows', v2.amdMirrorHardwareRows(state.rows), mirror.amdMirrorHardwareRows!(state.rows, ui))
  parity('amdMirrorModes', v2.amdMirrorModes(state.rows), mirror.amdMirrorModes!(state.rows))
  parity('amdTestGroupKinds', v2.amdTestGroupKinds(state.rows), mirror.amdTestGroupKinds!(state.rows))
  parity('amdTestGroupKindText', v2.amdTestGroupKindText(state), mirror.amdTestGroupKindText!(state, ui))
  for (const item of v2.TEST_GROUP_KINDS) {
    parity('amdTestGroupKindDisplay', v2.amdTestGroupKindDisplay(state, item.key), mirror.amdTestGroupKindDisplay!(state, item.key, ui))
  }
  for (const row of state.rows) {
    parity('amdTestGroupKind', v2.amdTestGroupKind(row), mirror.amdTestGroupKind!(row))
    parity('amdTestGroupKindLabel', v2.amdTestGroupKindLabel(row), mirror.amdTestGroupKindLabel!(row))
    parity('amdTestGroupSubtitle', v2.amdTestGroupSubtitle(row), mirror.amdTestGroupSubtitle!(row))
    parity('amdMirrorGateMode', v2.amdMirrorGateMode(row), mirror.amdMirrorGateMode!(row))
    parity('amdMirrorLabel', v2.amdMirrorLabel(row), mirror.amdMirrorLabel!(row, ui))
    parity('amdMirrorSourceUrl', v2.amdMirrorSourceUrl(inv, row), mirror.amdMirrorSourceUrl!(inv, row))
  }
  return state
}

/** Classic summaryCard DOM against the v2 summary view-model. */
function compareSummary(inv: Rec, runtime: number | null | undefined) {
  const card = mirror.renderAmdMirrorSummary!(inv, runtime, uiBase, () => {}) as HTMLElement
  const summary = v2.amdMirrorSummary(inv, runtime)
  parity('summaryAriaLabel', summary.ariaLabel, card.getAttribute('aria-label'))
  parity('summaryUnavailable', summary.unavailable, card.classList.contains('is-unavailable'))
  parity('summaryTitle', [summary.countLabel, 'configured AMD test groups'], children(card.querySelector('.ops-health-mirror-title')))
  parity('summaryMeta', summary.meta, text(card.querySelector('.ops-health-mirror-meta')))
  parity('summaryFacts', summary.facts, text(card.querySelector('.ops-health-mirror-facts')))
  const track = card.querySelector('.ops-health-mirror-track')
  parity('summaryHasTrack', Boolean(summary.track), Boolean(track))
  if (track && summary.track) {
    parity('summaryTrack', summary.track.map((s) => `${s.width}%`), [...track.children].map((el) => (el as HTMLElement).style.width))
  }
  return summary
}

/** Classic render() DOM, drawers, and browsers against the v2 view-model. */
function compareView(inv: Rec) {
  const { ui, captured } = captureUi()
  const host = document.createElement('div')
  mirror.renderAmdMirrorInventory!(host, inv, ui)
  const view = v2.amdMirrorInventoryView(inv)
  // Count hero.
  const ring = host.querySelector('.ops-mirror-count-ring') as HTMLElement
  parity('heroAriaLabel', view.hero.ariaLabel, ring.getAttribute('aria-label'))
  parity('heroTitle', [view.hero.totalDisplay, 'test groups'], children(ring))
  parity('heroMeta', view.hero.meta, text(host.querySelector('.ops-mirror-hero-meta')))
  const kindBreakdown = host.querySelector('.ops-mirror-kind-breakdown')
  parity('heroKinds', view.hero.kinds || null, kindBreakdown ? text(kindBreakdown) : null)
  parity(
    'heroLegend',
    view.hero.legend.map((row) => [row.count.toLocaleString(), row.label]),
    [...host.querySelectorAll('.ops-mirror-hardware-item')].map((el) => children(el).slice(1)),
  )
  // Configuration shape.
  parity(
    'configurationHeadline',
    [
      view.configuration.required, 'required', view.configuration.optional, 'optional', view.configuration.softFail, 'soft fail',
    ],
    children(host.querySelector('.ops-mirror-mode-headline')),
  )
  const track = host.querySelector('.ops-mirror-mode-track')
  parity('configurationHasTrack', Boolean(view.configuration.track), Boolean(track))
  if (track && view.configuration.track) {
    parity('configurationTrackLabel', view.configuration.trackLabel, track.getAttribute('aria-label'))
    parity('configurationTrack', view.configuration.track.map((s) => `${s.width}%`), [...track.children].map((el) => (el as HTMLElement).style.width))
  }
  parity(
    'configurationFacts',
    [
      [view.configuration.files, 'source files'],
      [view.configuration.families, 'GPU families'],
    ],
    [...host.querySelectorAll('.ops-mirror-fact')].map((el) => children(el)),
  )
  parity('configurationLowerBound', view.configuration.lowerBound, Boolean(host.querySelector('.ops-mirror-lower-bound')))
  // Partial-detail warning.
  const warning = host.querySelector(':scope > .ops-evidence-note.is-warning')
  parity('coverageNote', view.coverageNote, warning ? text(warning) : null)
  // Area chart.
  const areaPanel = host.querySelector('.ops-mirror-area-panel')
  parity('hasAreaChart', Boolean(view.areaChart), Boolean(areaPanel))
  if (areaPanel && view.areaChart) {
    parity('areaChartSubtitle', view.areaChart.subtitle, text(areaPanel.querySelector('.ops-panel-meta')))
    const bars = [...areaPanel.querySelectorAll('.ops-mirror-area-row')]
    parity(
      'areaChartRows',
      view.areaChart.rows.map((row) => [
        [String(row.area).replace(/\s+/g, ' ').trim(), String(row.yaml_file.split('/').pop()).replace(/\s+/g, ' ').trim()],
        v2.mirrorAreaBreakdown(row),
        row.count.toLocaleString(),
        row.source_url || null,
      ]),
      bars.map((el) => [children(el.querySelector('.ops-mirror-area-label')), text(el.querySelector('.ops-mirror-area-breakdown')), text(el.querySelector('.ops-mirror-area-count')), el.getAttribute('href')]),
    )
    const max = Math.max(...view.areaChart.rows.map((row) => Number(row.count || 0)), 1)
    parity(
      'areaChartFill',
      view.areaChart.rows.map((row) => `${(Number(row.count || 0) / max) * 100}%`),
      bars.map((el) => (el.querySelector('.ops-mirror-area-fill') as HTMLElement).style.width),
    )
    const browse = areaPanel.querySelector('.ops-mirror-actions button') as HTMLElement
    parity('areaChartBrowseLabel', view.areaChart.browseLabel, text(browse))
    browse.click()
    const config = captured.browsers.pop()!
    expect(config.title).toBe('AMD test groups by test-area file')
    parity('areaBrowserRows', view.areas, config.rows)
    for (const row of view.areas) {
      parity('areaSearchText', v2.mirrorAreaSearchText(row), config.searchText(row))
      const cells = config.columns.map((column: Rec) => {
        const rendered = column.render(row)
        return typeof rendered === 'string' ? rendered : text(rendered)
      })
      parity(
        'areaBrowserCells',
        [
          row.source_url ? `${row.yaml_file.split('/').pop()} ↗` : row.yaml_file,
          row.area === '' || row.area === undefined ? '-' : row.area,
          row.count.toLocaleString(),
          row.required.toLocaleString(),
          row.optional.toLocaleString(),
          row.soft_fail.toLocaleString(),
          v2.mirrorDevicesLabel(row),
        ].map((c) => String(c).replace(/\s+/g, ' ').trim()),
        cells,
      )
    }
  }
  // Inventory preview, drawers, and the inventory browser.
  const preview = host.querySelector('.ops-mirror-inventory-panel')!
  parity('previewSubtitle', view.preview.subtitle, text(preview.querySelector('.ops-panel-meta')))
  const buttons = [...preview.querySelectorAll('.ops-mirror-preview-row')] as HTMLElement[]
  parity(
    'previewRows',
    view.preview.rows.map((row) => [v2.amdMirrorLabel(row), v2.mirrorPreviewMeta(row), v2.amdMirrorGateMode(row)].map((c) => String(c).replace(/\s+/g, ' ').trim())),
    buttons.map((el) => [text(el.querySelector('.ops-mirror-preview-copy strong')), text(el.querySelector('.ops-mirror-preview-copy small')), text(el.querySelectorAll('.ops-badge')[1] ?? el.lastElementChild?.previousElementSibling)]),
  )
  buttons.forEach((el, index) => {
    el.click()
    const drawer = captured.drawers.pop()!
    const row = view.preview.rows[index]!
    const detail = v2.amdMirrorDetail(row, inv)
    parity('detailTitle', detail.title, drawer.title)
    parity('detailSubtitle', detail.subtitle, drawer.subtitle)
    parity('detailDescription', detail.description, drawer.description)
    parity(
      'detailFields',
      detail.fields,
      drawer.fields.filter((f: Rec) => f.value !== null && f.value !== undefined && f.value !== '').map((f: Rec) => ({ label: f.label, value: String(f.value) })),
    )
    parity('detailSources', detail.sources, drawer.sources)
  })
  const browseInventory = preview.querySelector('.ops-mirror-actions button') as HTMLElement | null
  parity('previewBrowseLabel', view.preview.browseLabel, browseInventory ? text(browseInventory) : null)
  if (!view.groups.length) expect(text(preview)).toContain('No published AMD test groups are available.')
  if (browseInventory) {
    browseInventory.click()
    const config = captured.browsers.pop()!
    parity('inventoryBrowserSubtitle', view.inventoryBrowserSubtitle, config.subtitle)
    parity('inventoryBrowserRows', view.groups, config.rows)
    for (const row of view.groups) {
      parity('inventorySearchText', v2.mirrorInventorySearchText(row), config.searchText(row))
      const cells = config.columns.map((column: Rec) => {
        const rendered = column.render(row)
        return typeof rendered === 'string' ? rendered : text(rendered)
      })
      parity(
        'inventoryBrowserCells',
        [
          v2.amdMirrorLabel(row),
          v2.amdTestGroupKindLabel(row),
          row.area === '' || row.area === undefined ? '-' : row.area,
          hardwareDisplayLabel(row.device),
          row.num_devices === null || row.num_devices === undefined ? '—' : Number(row.num_devices).toLocaleString(),
          v2.amdMirrorGateMode(row),
          v2.amdMirrorSourceUrl(inv, row) ? 'Pinned YAML ↗' : '-',
        ].map((c) => String(c).replace(/\s+/g, ' ').trim()),
        cells,
      )
      const clickable = config.columns[0].render(row) as HTMLElement
      clickable.click()
      parity('inventoryDetailTitle', v2.amdMirrorDetail(row, inv).title, captured.drawers.pop()!.title)
    }
  }
  // Method disclosure commit link.
  const link = host.querySelector('.ops-method-details a')
  parity('commitUrl', view.commitUrl, link ? link.getAttribute('href') : '')
  if (link) parity('commitLinkText', `Open scanned commit ${view.commit.slice(0, 12)} ↗`, text(link))
  return view
}

describe('AMD mirror inventory matches the classic module', () => {
  it('reads the real mirror inventory identically', async () => {
    const state = compareFunctions(inventory)
    expect(state.rows.length).toBeGreaterThan(100)
    expect(state.detailComplete).toBe(state.total === state.rows.length)
    // Every AMD test group on main: GPU mirrors, CPU-only mirrors, and native AMD steps.
    expect(state.total).toBe(240)
    expect(state.kinds).toEqual({ gpu_mirror: 219, cpu_mirror: 2, native: 19 })
    expect(v2.amdTestGroupKinds(state.rows)).toEqual(state.kinds)
    expect(state.kindsExact).toBe(true)
    const view = compareView(inventory)
    expect(view.areas.length).toBeGreaterThan(5)
    expect(view.hardwareRows.length).toBeGreaterThan(1)
    for (const runtime of [231, 0, null, undefined]) compareSummary(inventory, runtime)
    expect(compareSummary(inventory, 4).countLabel).toBe(String(inventory.summary.total))
    await expectGoldenFile('mirrors', 'inventory', {
      summary: v2.amdMirrorSummary(inventory, 231),
      hero: view.hero,
      configuration: view.configuration,
      areas: view.areaChart?.rows,
    })
  })

  it('agrees on generated inventories, summaries, drawers, and browsers', () => {
    const rand = rng(4242)
    const seen = new Set<string>()
    for (let i = 0; i < 600; i += 1) {
      const inv = generateInventory(rand)
      const state = compareFunctions(inv)
      const view = compareView(inv)
      compareSummary(inv, i % 4 === 0 ? null : i)
      seen.add(state.total === null ? 'no-total' : state.detailComplete ? 'complete' : state.aggregateComplete ? 'partial' : 'lower-bound')
      if (view.areaChart) seen.add('areas')
      if (view.preview.rows.length) seen.add('preview')
      if (state.total === 0) seen.add('zero')
      if (state.kindsExact && !state.detailComplete) seen.add('aggregate-kinds')
      if (state.rows.length && !state.kindsPublished) seen.add('kinds-unpublished')
      if (state.rows.some((row) => row.kind === 'native')) seen.add('native')
      if (state.rows.some((row) => row.kind === 'cpu_mirror')) seen.add('cpu-only')
    }
    expect([...seen].sort()).toEqual(['aggregate-kinds', 'areas', 'complete', 'cpu-only', 'kinds-unpublished', 'lower-bound', 'native', 'no-total', 'partial', 'preview', 'zero'])
  })
})

describe('AMD mirror helpers on hand-written inputs', () => {
  it('reads gate modes and labels', () => {
    expect([{ gate_kind: 'soft_fail' }, { optional: true }, { gate_kind: 'required' }, {}].map((row) => v2.amdMirrorGateMode(row))).toEqual(['soft fail', 'optional', 'required', 'required'])
    expect(v2.amdMirrorModes([{ gate_kind: 'soft_fail' }, { optional: true }, {}, {}])).toEqual({ required: 2, optional: 1, 'soft fail': 1 })
    expect([{ amd_label: ':amd: (MI300) Alpha' }, { label: ':nvidia: (H100) Beta', device: 'mi355_8' }, {}].map((row) => v2.amdMirrorLabel(row))).toEqual([
      ':amd: (MI300) Alpha',
      ':amd: (MI355) Beta',
      'AMD test group',
    ])
  })

  it('summarizes a small inventory against the runtime group count', () => {
    const inv = {
      source: { pipeline: 'ci', current_definition_commit_sha: 'a'.repeat(40), repository: 'vllm-project/vllm' },
      summary: { total: 2 },
      rows: [
        { definition_id: 'd1', source_file: '.buildkite/test_areas/attention.yaml', device: 'mi300_1', kind: 'gpu_mirror' },
        { definition_id: 'd2', source_file: '.buildkite/test_areas/attention.yaml', device: 'mi355_8', optional: true, kind: 'native' },
      ],
    }
    expect(v2.amdMirrorSummary(inv, 3)).toEqual({
      unavailable: false,
      countLabel: '2',
      ariaLabel: '2 configured AMD test groups. Open the AMD test-group inventory.',
      meta: 'Mirror and native AMD definitions · separate from 3 runtime groups',
      track: [
        { key: 'required', label: 'Required', count: 1, width: 50 },
        { key: 'optional', label: 'Optional', count: 1, width: 50 },
        { key: 'soft_fail', label: 'Soft fail', count: 0, width: 0 },
      ],
      facts: '1 GPU mirrors · 0 CPU-only mirrors · 1 native AMD steps · 1 required · 1 optional · 0 soft fail · 1 files',
    })
  })

  it('classifies test-group kinds and prefers the exact aggregate breakdown', () => {
    expect([{ kind: 'gpu_mirror' }, { kind: 'cpu_mirror' }, { kind: 'native' }, {}, { kind: 'bogus' }].map((row) => v2.amdTestGroupKind(row))).toEqual([
      'gpu_mirror',
      'cpu_mirror',
      'native',
      'gpu_mirror',
      'gpu_mirror',
    ])
    expect([{ kind: 'native' }, { kind: 'cpu_mirror' }, {}].map((row) => v2.amdTestGroupSubtitle(row))).toEqual([
      'Native AMD step on vLLM main',
      'CPU-only mirror.amd declaration on vLLM main',
      'Physical mirror.amd declaration on vLLM main',
    ])
    const source = { pipeline: 'ci', current_definition_commit_sha: 'a'.repeat(40) }
    const rows = [
      { definition_id: 'a', source_file: 'x.yaml', kind: 'gpu_mirror' },
      { definition_id: 'b', source_file: 'x.yaml', kind: 'native' },
    ]
    // Partial detail rows: the published aggregate breakdown stays exact.
    const partial = v2.amdMirrorInventoryState({ source, summary: { total: 5, by_kind: { gpu_mirror: 3, cpu_mirror: 1, native: 1 } }, rows })
    expect(partial.detailComplete).toBe(false)
    expect(partial.kinds).toEqual({ gpu_mirror: 3, cpu_mirror: 1, native: 1 })
    expect(v2.amdTestGroupKindText(partial)).toBe('3 GPU mirrors · 1 CPU-only mirrors · 1 native AMD steps')
    // No aggregate breakdown: published rows are a lower bound.
    const lower = v2.amdMirrorInventoryState({ source, summary: { total: 5 }, rows })
    expect(v2.amdTestGroupKindText(lower)).toBe('≥1 GPU mirrors · ≥0 CPU-only mirrors · ≥1 native AMD steps')
    const view = v2.amdMirrorInventoryView({ source, summary: { total: 2, by_kind: { gpu_mirror: 1, cpu_mirror: 0, native: 1 } }, rows })
    expect(view.kinds.map((kind) => [kind.key, kind.display])).toEqual([
      ['gpu_mirror', '1'],
      ['cpu_mirror', '0'],
      ['native', '1'],
    ])
    expect(v2.amdMirrorDetail(view.groups[0]!, {}).fields[0]).toEqual({ label: 'Kind', value: 'GPU mirror' })
  })

  it('does not claim exact zero kinds for an older mirror-only snapshot', () => {
    // The pre-kind builder: complete GPU-mirror rows, no kind field, no summary.by_kind.
    const legacy: Rec = {
      source: { pipeline: 'ci', current_definition_commit_sha: 'a'.repeat(40), repository: 'vllm-project/vllm' },
      summary: { total: 3 },
      rows: [
        { definition_id: 'a', source_file: '.buildkite/test_areas/attention.yaml', device: 'mi300_1' },
        { definition_id: 'b', source_file: '.buildkite/test_areas/attention.yaml', device: 'mi325_1', optional: true },
        { definition_id: 'c', source_file: '.buildkite/test_areas/kernels.yaml', device: 'mi355_8' },
      ],
    }
    const state = compareFunctions(legacy)
    expect(state.detailComplete).toBe(true)
    expect(state.kindsPublished).toBe(false)
    expect(state.kindsExact).toBe(false)
    const kindText = '3 GPU mirrors · CPU-only mirrors not published · native AMD steps not published'
    expect(v2.amdTestGroupKindText(state)).toBe(kindText)
    expect(v2.amdTestGroupKindText(state)).not.toMatch(/\b0 (CPU-only|native)/)
    const view = compareView(legacy)
    expect(view.kinds.map((kind) => [kind.key, kind.display])).toEqual([
      ['gpu_mirror', '3'],
      ['cpu_mirror', '—'],
      ['native', '—'],
    ])
    expect(view.hero.kinds).toBe(kindText)
    expect(view.hero.meta).toBe(`${v2.OLDER_MIRROR_SNAPSHOT_NOTE}Each group is one GPU mirror.amd declaration on vLLM main.`)
    expect(compareSummary(legacy, 2).facts).toContain(kindText)
    // Partial old-shape detail: the GPU count is a lower bound, the rest stays unknown.
    const partial = compareFunctions({ ...legacy, summary: { total: 5 } })
    expect(v2.amdTestGroupKindText(partial)).toBe('≥3 GPU mirrors · CPU-only mirrors not published · native AMD steps not published')
    compareView({ ...legacy, summary: { total: 5 } })
    // One explicit kind on every raw row, or summary.by_kind, makes the breakdown exact again.
    const typed = compareFunctions({ ...legacy, rows: legacy.rows.map((row: Rec) => ({ ...row, kind: 'gpu_mirror' })) })
    expect([typed.kindsPublished, typed.kindsExact]).toEqual([true, true])
    expect(v2.amdTestGroupKindText(typed)).toBe('3 GPU mirrors · 0 CPU-only mirrors · 0 native AMD steps')
    const aggregate = compareFunctions({ ...legacy, summary: { total: 3, by_kind: { gpu_mirror: 3, cpu_mirror: 0, native: 0 } } })
    expect([aggregate.kindsPublished, aggregate.kindsExact]).toEqual([true, true])
    // A single untyped row is enough to withhold the exact claim.
    const mixed = compareFunctions({ ...legacy, rows: legacy.rows.map((row: Rec, i: number) => (i ? { ...row, kind: 'gpu_mirror' } : row)) })
    expect([mixed.kindsPublished, mixed.kindsExact]).toEqual([false, false])
  })

  it('tones gate modes', () => {
    expect([{ gate_kind: 'soft_fail' }, { soft_fail: true }, { gate_kind: 'optional' }, {}].map((row) => v2.amdMirrorModeTone(row))).toEqual(['soft', 'soft', 'soft', 'neutral'])
  })
})
