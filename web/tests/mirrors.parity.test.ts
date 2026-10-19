// Differential parity for CI Health "AMD mirrors" and the Overview mirror
// summary: the v2 port against docs/assets/js/amd-mirror-inventory.js, both its
// internal functions and the DOM it renders with the classic UI helpers.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeAll, describe, expect, it } from 'vitest'
import type { Rec } from '@/domain/format'
import { hardwareDisplayLabel } from '@/domain/health'
import * as v2 from '@/domain/mirrors'
import { loadClassic, readFixture, type ClassicFns } from './classic'

const INTERNALS = [
  'amdMirrorInventoryRows',
  'amdMirrorInventoryState',
  'amdMirrorSourceUrl',
  'amdMirrorGateMode',
  'amdMirrorModes',
  'amdMirrorAreaRows',
  'amdMirrorHardwareRows',
  'amdMirrorLabel',
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

let mirror: ClassicFns
let uiBase: ClassicFns
let inventory: Rec

function rng(seed: number) {
  let s = seed >>> 0
  return () => (s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32
}

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
    summary: rand() < 0.95 ? { total } : undefined,
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
  inventory = readFixture<Rec>('mirrors_inventory.json').mirror_inventory
})

const text = (el: Element | null | undefined) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()
const children = (el: Element | null | undefined) => [...(el?.children ?? [])].map((child) => text(child))

function compareFunctions(inv: Rec) {
  const ui = uiBase
  const state = v2.amdMirrorInventoryState(inv)
  expect(state).toEqual(mirror.amdMirrorInventoryState!(inv, ui))
  expect(v2.amdMirrorInventoryRows(inv)).toEqual(mirror.amdMirrorInventoryRows!(inv, ui))
  expect(v2.amdMirrorAreaRows(inv, state.rows)).toEqual(mirror.amdMirrorAreaRows!(inv, state.rows, ui))
  expect(v2.amdMirrorHardwareRows(state.rows)).toEqual(mirror.amdMirrorHardwareRows!(state.rows, ui))
  expect(v2.amdMirrorModes(state.rows)).toEqual(mirror.amdMirrorModes!(state.rows))
  for (const row of state.rows) {
    expect(v2.amdMirrorGateMode(row)).toBe(mirror.amdMirrorGateMode!(row))
    expect(v2.amdMirrorLabel(row)).toBe(mirror.amdMirrorLabel!(row, ui))
    expect(v2.amdMirrorSourceUrl(inv, row)).toBe(mirror.amdMirrorSourceUrl!(inv, row))
  }
  return state
}

/** Classic summaryCard DOM against the v2 summary view-model. */
function compareSummary(inv: Rec, runtime: number | null | undefined) {
  const card = mirror.renderAmdMirrorSummary!(inv, runtime, uiBase, () => {}) as HTMLElement
  const summary = v2.amdMirrorSummary(inv, runtime)
  expect(card.getAttribute('aria-label')).toBe(summary.ariaLabel)
  expect(card.classList.contains('is-unavailable')).toBe(summary.unavailable)
  expect(children(card.querySelector('.ops-health-mirror-title'))).toEqual([summary.countLabel, 'configured AMD mirror groups'])
  expect(text(card.querySelector('.ops-health-mirror-meta'))).toBe(summary.meta)
  expect(text(card.querySelector('.ops-health-mirror-facts'))).toBe(summary.facts)
  const track = card.querySelector('.ops-health-mirror-track')
  expect(Boolean(track)).toBe(Boolean(summary.track))
  if (track && summary.track) {
    expect([...track.children].map((el) => (el as HTMLElement).style.width)).toEqual(summary.track.map((s) => `${s.width}%`))
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
  expect(ring.getAttribute('aria-label')).toBe(view.hero.ariaLabel)
  expect(children(ring)).toEqual([view.hero.totalDisplay, 'mirror groups'])
  expect(text(host.querySelector('.ops-mirror-hero-meta'))).toBe(view.hero.meta)
  expect([...host.querySelectorAll('.ops-mirror-hardware-item')].map((el) => children(el).slice(1))).toEqual(
    view.hero.legend.map((row) => [row.count.toLocaleString(), row.label]),
  )
  // Configuration shape.
  expect(children(host.querySelector('.ops-mirror-mode-headline'))).toEqual([
    view.configuration.required, 'required', view.configuration.optional, 'optional', view.configuration.softFail, 'soft fail',
  ])
  const track = host.querySelector('.ops-mirror-mode-track')
  expect(Boolean(track)).toBe(Boolean(view.configuration.track))
  if (track && view.configuration.track) {
    expect(track.getAttribute('aria-label')).toBe(view.configuration.trackLabel)
    expect([...track.children].map((el) => (el as HTMLElement).style.width)).toEqual(view.configuration.track.map((s) => `${s.width}%`))
  }
  expect([...host.querySelectorAll('.ops-mirror-fact')].map((el) => children(el))).toEqual([
    [view.configuration.files, 'source files'],
    [view.configuration.families, 'GPU families'],
  ])
  expect(Boolean(host.querySelector('.ops-mirror-lower-bound'))).toBe(view.configuration.lowerBound)
  // Partial-detail warning.
  const warning = host.querySelector(':scope > .ops-evidence-note.is-warning')
  expect(warning ? text(warning) : null).toBe(view.coverageNote)
  // Area chart.
  const areaPanel = host.querySelector('.ops-mirror-area-panel')
  expect(Boolean(areaPanel)).toBe(Boolean(view.areaChart))
  if (areaPanel && view.areaChart) {
    expect(text(areaPanel.querySelector('.ops-panel-meta'))).toBe(view.areaChart.subtitle)
    const bars = [...areaPanel.querySelectorAll('.ops-mirror-area-row')]
    expect(bars.map((el) => [children(el.querySelector('.ops-mirror-area-label')), text(el.querySelector('.ops-mirror-area-breakdown')), text(el.querySelector('.ops-mirror-area-count')), el.getAttribute('href')])).toEqual(
      view.areaChart.rows.map((row) => [
        [String(row.area).replace(/\s+/g, ' ').trim(), String(row.yaml_file.split('/').pop()).replace(/\s+/g, ' ').trim()],
        v2.mirrorAreaBreakdown(row),
        row.count.toLocaleString(),
        row.source_url || null,
      ]),
    )
    const max = Math.max(...view.areaChart.rows.map((row) => Number(row.count || 0)), 1)
    expect(bars.map((el) => (el.querySelector('.ops-mirror-area-fill') as HTMLElement).style.width)).toEqual(
      view.areaChart.rows.map((row) => `${(Number(row.count || 0) / max) * 100}%`),
    )
    const browse = areaPanel.querySelector('.ops-mirror-actions button') as HTMLElement
    expect(text(browse)).toBe(view.areaChart.browseLabel)
    browse.click()
    const config = captured.browsers.pop()!
    expect(config.title).toBe('AMD mirrors by test-area file')
    expect(config.rows).toEqual(view.areas)
    for (const row of view.areas) {
      expect(config.searchText(row)).toBe(v2.mirrorAreaSearchText(row))
      const cells = config.columns.map((column: Rec) => {
        const rendered = column.render(row)
        return typeof rendered === 'string' ? rendered : text(rendered)
      })
      expect(cells).toEqual([
        row.source_url ? `${row.yaml_file.split('/').pop()} ↗` : row.yaml_file,
        row.area === '' || row.area === undefined ? '-' : row.area,
        row.count.toLocaleString(),
        row.required.toLocaleString(),
        row.optional.toLocaleString(),
        row.soft_fail.toLocaleString(),
        v2.mirrorDevicesLabel(row),
      ].map((c) => String(c).replace(/\s+/g, ' ').trim()))
    }
  }
  // Inventory preview, drawers, and the inventory browser.
  const preview = host.querySelector('.ops-mirror-inventory-panel')!
  expect(text(preview.querySelector('.ops-panel-meta'))).toBe(view.preview.subtitle)
  const buttons = [...preview.querySelectorAll('.ops-mirror-preview-row')] as HTMLElement[]
  expect(buttons.map((el) => [text(el.querySelector('.ops-mirror-preview-copy strong')), text(el.querySelector('.ops-mirror-preview-copy small')), text(el.querySelectorAll('.ops-badge')[1] ?? el.lastElementChild?.previousElementSibling)])).toEqual(
    view.preview.rows.map((row) => [v2.amdMirrorLabel(row), v2.mirrorPreviewMeta(row), v2.amdMirrorGateMode(row)].map((c) => String(c).replace(/\s+/g, ' ').trim())),
  )
  buttons.forEach((el, index) => {
    el.click()
    const drawer = captured.drawers.pop()!
    const row = view.preview.rows[index]!
    const detail = v2.amdMirrorDetail(row, inv)
    expect(drawer.title).toBe(detail.title)
    expect(drawer.subtitle).toBe(detail.subtitle)
    expect(drawer.description).toBe(detail.description)
    expect(drawer.fields.filter((f: Rec) => f.value !== null && f.value !== undefined && f.value !== '').map((f: Rec) => ({ label: f.label, value: String(f.value) }))).toEqual(detail.fields)
    expect(drawer.sources).toEqual(detail.sources)
  })
  const browseInventory = preview.querySelector('.ops-mirror-actions button') as HTMLElement | null
  expect(browseInventory ? text(browseInventory) : null).toBe(view.preview.browseLabel)
  if (!view.groups.length) expect(text(preview)).toContain('No published AMD mirror groups are available.')
  if (browseInventory) {
    browseInventory.click()
    const config = captured.browsers.pop()!
    expect(config.subtitle).toBe(view.inventoryBrowserSubtitle)
    expect(config.rows).toEqual(view.groups)
    for (const row of view.groups) {
      expect(config.searchText(row)).toBe(v2.mirrorInventorySearchText(row))
      const cells = config.columns.map((column: Rec) => {
        const rendered = column.render(row)
        return typeof rendered === 'string' ? rendered : text(rendered)
      })
      expect(cells).toEqual([
        v2.amdMirrorLabel(row),
        row.area === '' || row.area === undefined ? '-' : row.area,
        hardwareDisplayLabel(row.device),
        row.num_devices === null || row.num_devices === undefined ? '—' : Number(row.num_devices).toLocaleString(),
        v2.amdMirrorGateMode(row),
        v2.amdMirrorSourceUrl(inv, row) ? 'Pinned YAML ↗' : '-',
      ].map((c) => String(c).replace(/\s+/g, ' ').trim()))
      const clickable = config.columns[0].render(row) as HTMLElement
      clickable.click()
      expect(captured.drawers.pop()!.title).toBe(v2.amdMirrorDetail(row, inv).title)
    }
  }
  // Method disclosure commit link.
  const link = host.querySelector('.ops-method-details a')
  expect(link ? link.getAttribute('href') : '').toBe(view.commitUrl)
  if (link) expect(text(link)).toBe(`Open scanned commit ${view.commit.slice(0, 12)} ↗`)
  return view
}

describe('AMD mirror inventory matches the classic module', () => {
  it('reads the real mirror inventory identically', () => {
    const state = compareFunctions(inventory)
    expect(state.rows.length).toBeGreaterThan(100)
    expect(state.detailComplete).toBe(state.total === state.rows.length)
    const view = compareView(inventory)
    expect(view.areas.length).toBeGreaterThan(5)
    expect(view.hardwareRows.length).toBeGreaterThan(1)
    for (const runtime of [231, 0, null, undefined]) compareSummary(inventory, runtime)
    expect(compareSummary(inventory, 4).countLabel).toBe(String(inventory.summary.total))
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
    }
    expect([...seen].sort()).toEqual(['areas', 'complete', 'lower-bound', 'no-total', 'partial', 'preview', 'zero'])
  })
})
