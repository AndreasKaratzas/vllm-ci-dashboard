// Differential tests: the CI Analytics ports (AMD test health, AMD nightly
// latency, AMD nightlies) against the classic renderer on a trimmed real
// snapshot plus generated variants. Inline classic logic is compared through
// the DOM the classic renderer produces (with a recording Chart stub).
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import * as v2 from '@/domain/analytics'
import { amdLatestState, amdStateLabel, movementBuilds } from '@/domain/ciHealth'
import { integer, shortDate, toneForState, value, type Rec } from '@/domain/format'
import { currentAmdHealth } from '@/domain/health'
import { latestAmd } from '@/domain/nightly'
import { classicParity, loadClassic, type ClassicFns } from './classic'
import { pinClock, readFixture } from './support/fixtures'
import { expectGoldenFile } from './support/golden'
import { rng } from './support/rng'

const NAMES = [
  'state',
  'currentAmdHealth',
  'amdHealthPublicationState',
  'amdHardwareRows',
  'latencyMetric',
  'exactLatencyJobUrl',
  'latencyComparison',
  'latencySideDuration',
  'nightlyForCohort',
  'nightlyDisplayName',
  'renderAmdHealth',
  'openAmdCatalog',
  'renderLatencyComparison',
  'openLatencyEvidence',
  'renderAnalytics',
  'closeOverlay',
]

const fixture = readFixture<Rec>('analytics_sections.json')
pinClock(fixture.generated_at)
const parity = classicParity('analytics')
let classic: ClassicFns
let charts: { key: string; config: Rec }[] = []

beforeAll(() => {
  // Draw charts synchronously into a recorder so their data can be compared.
  const w = window as unknown as Rec
  w.requestAnimationFrame = (cb: (t: number) => void) => {
    cb(0)
    return 0
  }
  w.Chart = class {
    constructor(canvas: HTMLCanvasElement, config: Rec) {
      charts.push({ key: canvas.dataset.chartKey || '', config })
    }
    destroy() {}
  }
  classic = loadClassic(NAMES)
})

afterEach(() => {
  document.body.innerHTML = ''
  charts = []
})

// --- DOM extraction ---------------------------------------------------------

const text = (el: Element | null | undefined) => (el?.textContent ?? '').trim()
const tone = (el: Element) => (el.className.match(/\bis-[a-z-]+/) || [''])[0]

function stripOf(root: ParentNode) {
  return [...root.querySelectorAll('.ops-status-item')].map((el) => ({
    label: text(el.querySelector('.ops-stat-label')),
    value: text(el.querySelector('.ops-stat-value')),
    meta: text(el.querySelector('.ops-stat-meta')),
    tone: tone(el),
    url: el.tagName === 'A' ? el.getAttribute('href') : null,
  }))
}

function v2Strip(items: v2.StripItem[]) {
  return items.map((item) => ({
    label: item.label,
    value: value(item.value),
    meta: item.meta,
    tone: item.tone,
    url: item.url || null,
  }))
}

function tableRows(root: ParentNode | null) {
  if (!root) return []
  return [...root.querySelectorAll('table.ops-table tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map((td) => text(td)))
}

function overlay() {
  const overlays = document.querySelectorAll('.ops-overlay')
  return overlays[overlays.length - 1] as HTMLElement
}

function chart(key: string) {
  const found = charts.filter((c) => c.key === key)
  return found[found.length - 1]
}

// --- AMD test health ----------------------------------------------------------

function classicGroups(amdHealth: Rec) {
  const host = document.createElement('div')
  document.body.append(host)
  classic.renderAmdHealth!(host, amdHealth)
  return host
}

const clusterTiles = (model: ReturnType<typeof v2.amdHealthModel>) =>
  model.clusters.map((c) => ({ label: c.label, count: c.renderedCount, description: c.description, meta: c.meta, tone: c.tone }))

function compareGroups(rawHealth: Rec, label: string) {
  const health = currentAmdHealth(rawHealth)
  parity('currentAmdHealth', health, classic.currentAmdHealth!(rawHealth))
  const model = v2.amdHealthModel(health)
  const host = classicGroups(health)
  const note = text(host.querySelector('.ops-evidence-note.is-warning'))
  if (!model.available) {
    parity('unavailableNote', 'AMD job-variant history is unavailable. No upstream result has been substituted for AMD health.', note, label)
    parity('unavailableStrip', [], stripOf(host))
    return model
  }
  parity('retentionNote', model.retentionNote, note, label)
  parity('strip', v2Strip(model.strip), stripOf(host), label)
  const actions = [...host.querySelectorAll('.ops-status-item .ops-stat-action')].map(text)
  parity('stripActions', model.strip.filter((s) => s.actionLabel).map((s) => s.actionLabel), actions)

  const tiles = [...host.querySelectorAll('.ops-cluster-tile')].map((el) => ({
    label: text(el.querySelector('.ops-cluster-label')),
    count: text(el.querySelector('.ops-cluster-count')),
    description: text(el.querySelector('.ops-cluster-description')),
    meta: text(el.querySelector('.ops-cluster-meta')),
    tone: tone(el),
  }))
  parity('clusters', clusterTiles(model), tiles, label)
  parity('browseAllLabel', model.browseAllLabel, text(host.querySelector('.ops-cluster-section header button')))

  const priorityPanel = host.querySelector('.ops-amd-priority')!
  parity('priorityMeta', model.priorityMeta, text(priorityPanel.querySelector('.ops-panel-meta')))
  const caption = text(priorityPanel.querySelector('.ops-table-caption') || priorityPanel.querySelector('.ops-empty'))
  parity('priorityCaption', model.priorityCaption, caption.slice(0, model.priorityCaption.length))
  parity(
    'priorityRows',
    model.priority.map((row) => [
      value(row.display_name || row.name || row.job_name) + value(row.hardware_variant || row.hardware, 'unknown') + ' - ' + value(row.queue, 'queue unavailable'),
      value(row.queue),
      v2.passRateLabel(row),
      amdStateLabel(amdLatestState(row, model.latestBuild)),
      `${integer(row.passed)} / ${integer(row.soft_failed)} / ${integer(row.hard_failed)}`,
      '#' + value(row.latest_build_number),
    ]),
    tableRows(priorityPanel),
    label,
  )

  // Charts: AMD health by nightly and by hardware variant.
  const builds = chart('analytics-amd-build-health')!.config
  parity('buildChartLabels', model.builds.map((b) => b.label), builds.data.labels)
  const num = (x: unknown) => (x === null || x === undefined || x === '' || !Number.isFinite(Number(x)) ? 0 : Number(x))
  parity(
    'buildChartData',
    [
      model.builds.map((b) => b.passed),
      model.builds.map((b) => b.soft),
      model.builds.map((b) => b.hard),
      model.builds.map((b) => b.unknown),
    ],
    builds.data.datasets.map((d: Rec) => d.data.map(num)),
  )
  const hardware = chart('analytics-amd-hardware-health')!.config
  parity('hardwareChartLabels', model.hardware.map((h) => h.label), hardware.data.labels)
  parity(
    'hardwareChartData',
    [
      model.hardware.map((h) => h.passed),
      model.hardware.map((h) => h.soft),
      model.hardware.map((h) => h.hard),
      model.hardware.map((h) => h.missing),
    ],
    hardware.data.datasets.map((d: Rec) => d.data),
  )
  return model
}

function chartEvidence(host: ParentNode, key: string) {
  const stage = host.querySelector(`canvas[data-chart-key="${key}"]`)!.closest('.ops-chart-stage')!
  const inspect = stage.querySelector('.ops-chart-evidence-action') as HTMLButtonElement
  inspect.click()
  const rows = tableRows(overlay())
  classic.closeOverlay!()
  return { action: text(inspect), rows }
}

function classicCatalog(health: Rec, rows: Rec[], filter: string, search = '') {
  classic.openAmdCatalog!('Catalog', 'subtitle', rows, health, filter)
  const root = overlay()
  const select = root.querySelector('select') as HTMLSelectElement
  expect(select.value).toBe(filter)
  if (search) {
    const input = root.querySelector('input') as HTMLInputElement
    input.value = search
    input.dispatchEvent(new Event('input'))
  }
  const out = {
    caption: text(root.querySelector('.ops-table-caption') || root.querySelector('.ops-empty')),
    rows: tableRows(root),
    options: [...select.options].map((o) => [o.value, o.textContent]),
  }
  classic.closeOverlay!()
  return out
}

function v2CatalogRow(row: Rec, latestBuild: unknown) {
  const latest = amdLatestState(row, latestBuild)
  return [
    value(row.display_name || row.name || row.job_name) + value(row.hardware_variant || row.hardware, 'unknown') + ' - ' + value(row.queue, 'queue unavailable'),
    amdStateLabel(latest),
    v2.passRateLabel(row),
    integer(row.runs),
    integer(row.current_pass_streak),
    value(row.hardware_variant || row.hardware),
    value(row.queue),
    row.latest_url ? 'Latest AMD job' : 'Inspect history',
  ]
}

function compareCatalog(health: Rec, rows: Rec[], filter: string, search: string, label: string) {
  const latestBuild = (health.summary || {}).latest_build_number
  const got = classicCatalog(health, rows, filter, search)
  const mine = v2.amdCatalogRows(rows, latestBuild, filter, search)
  parity('catalogFilters', v2.AMD_CATALOG_FILTERS, got.options)
  if (!mine.length) {
    parity('catalogRows', [], got.rows, label)
    return mine
  }
  parity('catalogCaption', `1-${integer(Math.min(50, mine.length))} of ${integer(mine.length)} matching AMD job variants`, got.caption, label)
  parity('catalogRows', mine.slice(0, 50).map((row) => v2CatalogRow(row, latestBuild)), got.rows, label)
  return mine
}

const HARDWARE = ['mi250_1', 'mi300_1', 'mi300_2', 'mi325_1', 'mi355_1', 'mi355_8', 'mi355_dpx', '']
const STATES = ['passed', 'soft', 'soft_fail', 'hard', 'failed', 'timed_out', 'unknown', 'canceled', 'expired', '']

function generatedHealth(rand: () => number, i: number): Rec {
  const pick = <T,>(xs: T[]) => xs[Math.floor(rand() * xs.length)]!
  const latest = 500 + Math.floor(rand() * 2)
  const groups: Rec[] = []
  const count = Math.floor(rand() * 45) + (i % 9 === 0 ? 0 : 1)
  for (let g = 0; g < count; g += 1) {
    const hw = pick(HARDWARE)
    const device = hw ? hw.split('_')[0]! : 'mi300'
    const row: Rec = {
      id: `g${g}`,
      name: `amd_${device}_1: :amd: (${device.toUpperCase()}) Group ${String.fromCharCode(65 + Math.floor(rand() * 6))}${g % 3}`,
      hardware: device,
      queue: rand() < 0.1 ? undefined : rand() < 0.5 ? `amd_${device}_1` : `amd_${device}_q${g % 5}`,
      runs: Math.floor(rand() * 9),
      passed: Math.floor(rand() * 4),
      soft_failed: Math.floor(rand() * 2),
      hard_failed: Math.floor(rand() * 2),
      current_pass_streak: Math.floor(rand() * 4),
      latest_state: pick(STATES),
      latest_build_number: rand() < 0.75 ? latest : latest - 1 - Math.floor(rand() * 3),
    }
    if (!row.queue) row.name = `amd_${device}_1: :amd: (${device.toUpperCase()}) Group ${g}`
    if (hw) row.hardware_variant = hw
    if (rand() < 0.5) row.display_name = rand() < 0.2 ? `Group ${g % 4}` : row.name.split(') ')[1]
    if (rand() < 0.3) row.pass_rate_pct = rand() < 0.8 ? Math.round(rand() * 1000) / 10 : 'n/a'
    if (rand() < 0.7) row.latest_url = `https://buildkite.com/vllm/ci/builds/${row.latest_build_number}/steps/canvas?jid=x${g}&tab=output`
    groups.push(row)
  }
  const states = { passed: Math.floor(rand() * 30), soft: Math.floor(rand() * 3), hard: Math.floor(rand() * 3), unknown: Math.floor(rand() * 2) }
  const summary: Rec = {
    latest_build_number: rand() < 0.05 ? undefined : latest,
    latest_build_url: rand() < 0.8 ? `https://buildkite.com/vllm/ci/builds/${latest}` : undefined,
    latest_observed_at: rand() < 0.9 ? '2026-10-1' + (i % 9) + 'T06:00:00Z' : undefined,
    build_count: Math.floor(rand() * 9),
  }
  if (rand() < 0.5) summary.latest_job_variant_state_counts = states
  else if (rand() < 0.7) summary.latest_state_counts = { passed: states.passed, soft_failed: states.soft, hard_failed: states.hard, unknown: states.unknown }
  if (rand() < 0.6) summary.latest_job_variant_count = Math.floor(rand() * 40)
  else if (rand() < 0.5) summary.latest_group_count = Math.floor(rand() * 40)
  const retained = ['retained_job_variant_count', 'retained_group_count', 'union_group_count', 'group_count']
  if (rand() < 0.6) summary[pick(retained)] = Math.floor(rand() * 60)
  if (rand() < 0.8) {
    const total = Math.floor(rand() * 20)
    const counts: Rec = {
      available: rand() < 0.85,
      build_number: rand() < 0.85 ? latest : latest - 1,
      total: rand() < 0.1 ? 'x' : total,
      passing: rand() < 0.1 ? total + 1 : Math.floor(rand() * (total + 1)),
      partial: Math.floor(rand() * 3),
    }
    if (rand() < 0.5) counts.passing_all = Math.floor(rand() * 5)
    if (rand() < 0.5) counts.observed_at = '2026-10-10T00:00:00Z'
    if (rand() < 0.5) counts.count_basis = 'basis ' + i
    summary.latest_test_group_counts = counts
  }
  const payload: Rec = {
    available: rand() < 0.95,
    source_pipeline: 'ci',
    job_scope: 'amd_gpu',
    hardware_scope: 'amd_mi_gpu',
    summary,
    group_catalog: groups,
    builds: Array.from({ length: Math.floor(rand() * 6) }, (_, b) => ({
      number: latest - b,
      observed_at: '2026-10-0' + (b + 1) + 'T06:00:00Z',
      url: rand() < 0.7 ? `https://buildkite.com/vllm/ci/builds/${latest - b}` : undefined,
      observed: Math.floor(rand() * 30),
      passed: rand() < 0.9 ? Math.floor(rand() * 30) : undefined,
      soft_failed: Math.floor(rand() * 3),
      hard_failed: Math.floor(rand() * 3),
      unknown: rand() < 0.5 ? Math.floor(rand() * 3) : undefined,
      pass_rate_pct: rand() < 0.8 ? rand() * 100 : undefined,
    })),
  }
  if (rand() < 0.35) {
    payload.operations_publication_retention = {
      complete_relative_to_amd_test_health: rand() < 0.3,
      group_catalog: { source: 400, published: groups.length },
      builds: rand() < 0.5 ? { source: 9, published: 5 } : undefined,
    }
  }
  return payload
}

describe('AMD test health matches the classic renderer', () => {
  it('renders the real snapshot identically', async () => {
    const health = fixture.amd_test_health
    const model = compareGroups(health, 'fixture')
    await expectGoldenFile('analytics', 'groups', { strip: v2Strip(model.strip), clusters: clusterTiles(model) })
    expect(model.available).toBe(true)
    expect(model.priority.length).toBeGreaterThan(0)
    expect(model.hardware.length).toBeGreaterThan(1)
    expect(model.logical.rows.length).toBeGreaterThan(0)
    expect(model.clusters.map((c) => c.count).every((c) => c >= 0)).toBe(true)
    expect(model.clusters.find((c) => c.label === 'Historical only')!.count).toBeGreaterThan(0)
    expect(model.clusters.find((c) => c.label === 'Mixed history')!.count).toBeGreaterThan(0)
    parity('amdHealthPublicationState', v2.amdHealthPublicationState(health), classic.amdHealthPublicationState!(health))
    parity('amdHardwareRows', v2.amdHardwareRows(model.groups, model.latestBuild), classic.amdHardwareRows!(model.groups, model.latestBuild))

    const host = document.body.firstElementChild!
    const buildEvidence = chartEvidence(host, 'analytics-amd-build-health')
    parity('buildEvidenceAction', `Inspect ${integer(model.builds.length)} observations`, buildEvidence.action)
    parity(
      'buildEvidenceRows',
      model.builds.slice().reverse().map((b) => [b.label, shortDate(b.timestamp), b.valueSummary, b.url ? 'Open source' : 'Inspect']),
      buildEvidence.rows,
    )
    const hwEvidence = chartEvidence(host, 'analytics-amd-hardware-health')
    parity(
      'hardwareEvidenceRows',
      model.hardware.slice().reverse().map((h) => [h.label, '-', v2.hardwareValueSummary(h)]),
      hwEvidence.rows.map((r) => r.slice(0, 3)),
    )
  })

  it('filters and searches the job-variant catalog identically', () => {
    const health = currentAmdHealth(fixture.amd_test_health)
    const model = v2.amdHealthModel(health)
    const requests = [model.catalogs.variants, model.catalogs.incidents, model.catalogs.all, ...model.clusters.map((c) => c.catalog)]
    const searches = ['', 'mi355', 'AMD_MI300_1', '  basic   models ', 'zzz', 'mi250_1', 'Batch']
    let nonEmpty = 0
    for (const request of requests) {
      for (const [mode] of v2.AMD_CATALOG_FILTERS) {
        for (const search of searches) {
          nonEmpty += compareCatalog(health, request.rows, mode, search, `${request.title}/${mode}/${search}`).length ? 1 : 0
        }
      }
    }
    expect(nonEmpty).toBeGreaterThan(50)
    // Each hardware bar opens its own rows.
    for (const hw of model.hardware) compareCatalog(health, hw.rows, 'all', '', hw.label)
  })

  it('agrees on generated catalogs, summaries, and retention states', () => {
    const rand = rng(4295)
    let available = 0
    for (let i = 0; i < 220; i += 1) {
      const raw = generatedHealth(rand, i)
      const model = compareGroups(raw, `generated ${i}`)
      document.body.innerHTML = ''
      charts = []
      const health = currentAmdHealth(raw)
      parity('amdHealthPublicationState', v2.amdHealthPublicationState(raw), classic.amdHealthPublicationState!(raw))
      const latest = (health.summary || {}).latest_build_number
      parity('amdHardwareRows', v2.amdHardwareRows(health.group_catalog, latest), classic.amdHardwareRows!(health.group_catalog, latest))
      if (!model.available) continue
      available += 1
      const mode = v2.AMD_CATALOG_FILTERS[i % v2.AMD_CATALOG_FILTERS.length]![0]
      compareCatalog(health, model.groups, mode, ['', 'group a', 'mi355', 'amd_mi300', 'q3', '_q1'][i % 6]!, `generated catalog ${i}`)
    }
    expect(available).toBeGreaterThan(150)
  })

  it('reports the same observed time for the view header', () => {
    const health = currentAmdHealth(fixture.amd_test_health)
    expect(v2.analyticsObservedAt('groups', fixture, health)).toBe(health.summary.latest_observed_at)
    expect(v2.analyticsObservedAt('groups', { generated_at: 'g' }, { summary: {} })).toBe('g')
    expect(v2.analyticsObservedAt('latency', { generated_at: 'g' }, health)).toBe('g')
  })
})

// --- AMD nightly latency --------------------------------------------------------

function classicLatency(ops: Rec) {
  const comparison = classic.latencyComparison!(ops)
  const host = document.createElement('div')
  document.body.append(host)
  if (comparison.available) classic.renderLatencyComparison!(host, comparison)
  return { comparison, host }
}

function v2LatencyRow(row: Rec, builds: Rec[]) {
  return [v2.latencyLabel(row), v2.latencySideDuration(row.amd), v2.observedNightliesLabel(row, builds)]
}

function compareLatency(ops: Rec, label: string) {
  const { comparison, host } = classicLatency(ops)
  const mine = v2.latencyComparison(ops)
  parity('latencyComparison', mine, comparison, label)
  if (!mine.available) return mine
  const model = v2.latencyModel(mine)
  parity('latencyContext', model.context, text(host.querySelector('.ops-window-context')))
  parity('latencyStrip', v2Strip(model.strip), stripOf(host), label)
  const panel = [...host.querySelectorAll('.ops-panel')].find((p) => text(p.querySelector('.ops-panel-title')) === 'Recent AMD MI nightly latency')!
  parity('latencyTableMeta', model.tableMeta, text(panel.querySelector('.ops-panel-meta')))
  parity('latencyRows', model.rows.slice(0, 12).map((row) => v2LatencyRow(row, model.builds)), tableRows(panel), label)
  // Browse all opens every row in the same order.
  ;(panel.querySelector('.ops-panel-footer button') as HTMLButtonElement).click()
  parity('latencyBrowseRows', model.rows.slice(0, 50).map((row) => v2LatencyRow(row, model.builds)), tableRows(overlay()), label)
  classic.closeOverlay!()

  const timing = chart('analytics-latency-comparison')
  if (!model.timed.length) {
    expect(timing).toBeUndefined()
  } else {
    parity('latencyChartData', model.top.map((row) => row.amd.median_duration_mins), timing!.config.data.datasets[0].data)
    const evidence = chartEvidence(host, 'analytics-latency-comparison')
    parity(
      'latencyChartEvidence',
      model.top.slice().reverse().map((row) => [v2.historyPointLabel({ label: row.label }), v2.latencySideDuration(row.amd) + ' AMD MI']),
      evidence.rows.map((r) => [r[0], r[2]]),
    )
  }
  return mine
}

function compareLatencyEvidence(row: Rec, comparison: Rec, label: string) {
  classic.openLatencyEvidence!(row, comparison)
  const root = overlay()
  const mine = v2.latencyEvidence(row, comparison as v2.LatencyComparison)
  parity('evidenceTitle', mine.title, text(root.querySelector('.ops-overlay-title')))
  parity('evidenceStrip', [[mine.value, mine.meta]], stripOf(root).map((s) => [s.value, s.meta]))
  const trs = [...root.querySelectorAll('table.ops-table tbody tr')]
  parity('evidenceRowCount', mine.rows.length, trs.length, label)
  trs.forEach((tr, index) => {
    const cells = [...tr.querySelectorAll('td')]
    const ev = mine.rows[index]!
    parity('evidenceBuild', '#' + ev.number, text(cells[0]), label)
    parity('evidenceBuildUrl', ev.buildUrl, cells[0]!.querySelector('a')?.getAttribute('href') ?? '', label)
    parity('evidenceCreated', ev.created, text(cells[1]))
    parity('evidenceWall', ev.wall, text(cells[2]))
    const links = [...cells[3]!.querySelectorAll('a')].map((a) => [text(a), a.getAttribute('href')])
    parity('evidenceJobs', ev.jobs.map((j) => [j.label, j.url]), links, label)
    if (!ev.observed) expect(text(cells[3])).toBe('No observation in this nightly')
    else if (!ev.jobs.length) expect(text(cells[3])).toBe('Exact job links unavailable')
  })
  classic.closeOverlay!()
}

function compactLatency(payload: Rec): Rec {
  const copy = structuredClone(payload)
  copy.job_columns = ['job_id', 'step_id', 'url', 'queue', 'hardware', 'raw_name', 'started_at', 'finished_at', 'duration_mins']
  for (const row of copy.rows) {
    for (const sample of row.amd?.samples ?? []) {
      sample.jobs = sample.jobs.map((job: Rec) => [
        job.job_id,
        'step',
        job.url,
        job.queue,
        job.hardware,
        `:amd: (${String(job.hardware).toUpperCase()}) ${row.label}`,
        '2026-10-18T07:00:00Z',
        '2026-10-18T08:00:00Z',
        job.duration_mins,
      ])
    }
  }
  return copy
}

function mutateLatency(payload: Rec, rand: () => number): Rec {
  const copy = structuredClone(payload)
  const pick = <T,>(xs: T[]) => xs[Math.floor(rand() * xs.length)]!
  const rows: Rec[] = copy.rows
  const mutations: ((r: Rec) => void)[] = [
    (r) => (r.amd = null),
    (r) => r.amd && (r.amd.median_duration_mins = pick([-1, '', null, 'x', 0, 999.5, rows[0]!.amd?.median_duration_mins])),
    (r) => r.amd && (r.amd.sample_count = Number(r.amd.sample_count) + 1),
    (r) => r.amd && r.amd.samples.pop(),
    (r) => r.amd && r.amd.samples[0] && (r.amd.samples[0].build_number = 1),
    (r) => r.amd && r.amd.samples[0] && (r.amd.samples[0].duration_mins = -2),
    (r) => r.amd && r.amd.samples[0]?.jobs[0] && (r.amd.samples[0].jobs[0].url += '&x=1'),
    (r) => r.amd && r.amd.samples[0]?.jobs[0] && (r.amd.samples[0].jobs[0].queue = 'gpu_1'),
    (r) => r.amd && r.amd.samples[0]?.jobs[0] && (r.amd.samples[0].jobs[0].hardware = 'h100'),
    (r) => r.amd && r.amd.samples[0] && (r.amd.samples[0].jobs = []),
    (r) => r.amd && (r.amd.source_pipeline = 'amd-ci'),
    (r) => (r.label = pick(['', 'Zeta group', 'alpha', 'ALPHA', rows[0]!.label])),
    (r) => r.amd && (r.amd.median_duration_mins = rows[0]!.amd?.median_duration_mins ?? 3),
    (r) => r.amd && r.amd.samples[0]?.jobs[0] && (r.amd.samples[0].jobs[0].url = `https://buildkite.com/vllm/ci/builds/${r.amd.samples[0].build_number}#${r.amd.samples[0].jobs[0].job_id}`),
    (r) => r.amd && r.amd.samples[0]?.jobs[0] && (r.amd.samples[0].jobs[0].job_id = ' '),
    (r) => rows.push({ ...structuredClone(r), id: `${r.id}-copy`, label: String(r.label).toUpperCase() }),
    (r) => rows.splice(0, 0, { ...structuredClone(r), id: `${r.id}-accent`, label: `${r.label}é` }, { ...structuredClone(r), id: `${r.id}-plain`, label: `${r.label}e` }),
  ]
  for (let m = 0; m < 1 + Math.floor(rand() * 6); m += 1) pick(mutations)(pick(rows))
  if (rand() < 0.3) copy.rows = rows.slice(0, Math.floor(rand() * rows.length))
  if (rand() < 0.15) copy.cohort.nightlies = copy.cohort.nightlies.slice(0, 1 + Math.floor(rand() * 4))
  const top: ((p: Rec) => void)[] = [
    (p) => (p.schema_version = 1),
    (p) => (p.build_limit = 4),
    (p) => (p.available = false),
    (p) => (p.unavailable_reason = rand() < 0.5 ? 'Timing source offline' : null),
    (p) => (p.cohort.nightlies = []),
    (p) => p.cohort.nightlies.push({ ...p.cohort.nightlies[0] }),
    (p) => (p.cohort.nightlies[0].created_at = 'not a date'),
    (p) => p.cohort.nightlies[1] && (p.cohort.nightlies[1].web_url = 'https://buildkite.com/vllm/ci/builds/1'),
    (p) => p.cohort.nightlies[2] && (p.cohort.nightlies[2].web_url = undefined),
    (p) => p.cohort.nightlies.forEach((b: Rec) => ((b.url = b.web_url), (b.web_url = 'https://buildkite.com/vllm/ci/builds/1'))),
    (p) => p.cohort.nightlies.forEach((b: Rec) => ((b.url = 'https://buildkite.com/vllm/ci/builds/2'), delete b.web_url)),
    (p) => (p.job_columns = ['job_id']),
    (p) => (p.branch = 'release'),
  ]
  if (rand() < 0.35) pick(top)(copy)
  return copy
}

describe('AMD nightly latency matches the classic renderer', () => {
  it('renders the real snapshot identically, including every evidence sheet', async () => {
    const comparison = compareLatency(fixture, 'fixture')
    const model = v2.latencyModel(comparison)
    await expectGoldenFile('analytics', 'latency', {
      context: model.context,
      strip: v2Strip(model.strip),
      rows: model.rows.slice(0, 12).map((row) => v2LatencyRow(row, model.builds)),
    })
    expect(comparison.available).toBe(true)
    expect(comparison.rows.length).toBeGreaterThan(12)
    expect(comparison.rows.some((row) => row.amd && row.amd.sample_count < 5)).toBe(true)
    for (const row of comparison.rows) compareLatencyEvidence(row, comparison, row.label)
  })

  it('agrees on the compact job-column contract', () => {
    const ops = { latency: compactLatency(fixture.latency) }
    const comparison = compareLatency(ops, 'compact')
    expect(comparison.available).toBe(true)
    expect(comparison.rows.every((row) => row.amd)).toBe(true)
    const broken = structuredClone(ops)
    broken.latency.rows[0].amd.samples[0].jobs[0][6] = 'not a date'
    broken.latency.rows[1].amd.samples[0].jobs[0][8] = -1
    broken.latency.rows[2].amd.samples[0].jobs[0][5] = ' '
    broken.latency.rows[3].amd.samples[0].jobs[0] = ['short']
    const result = compareLatency(broken, 'compact broken')
    expect(result.rows.slice(0, 4).every((row) => row.amd === null)).toBe(true)
  })

  it('agrees on generated latency payloads', () => {
    const rand = rng(5528)
    let available = 0
    for (let i = 0; i < 150; i += 1) {
      const payload = mutateLatency(fixture.latency, rand)
      const comparison = compareLatency({ latency: payload }, `generated ${i}`)
      if (comparison.available) {
        available += 1
        const row = comparison.rows[i % Math.max(1, comparison.rows.length)]
        if (row) compareLatencyEvidence(row, comparison, `generated ${i}`)
      } else {
        expect(comparison.unavailable_reason).toBeTruthy()
      }
      document.body.innerHTML = ''
      charts = []
    }
    expect(available).toBeGreaterThan(80)
  })

  it('validates metrics and exact job URLs like classic', () => {
    for (const raw of [null, undefined, '', '0', 0, -1, '3.5', 'x', Infinity, 12]) {
      parity('latencyMetric', v2.latencyMetric(raw), classic.latencyMetric!(raw))
    }
    const job = { job_id: 'abc', url: 'https://buildkite.com/vllm/ci/builds/7/steps/canvas?jid=abc&tab=output' }
    const urls = [
      job.url,
      'https://buildkite.com/vllm/ci/builds/7#abc',
      'https://buildkite.com/vllm/ci/builds/7/steps/canvas?tab=output&jid=abc',
      'https://buildkite.com/vllm/ci/builds/8#abc',
      'http://buildkite.com/vllm/ci/builds/7#abc',
      'https://u:p@buildkite.com/vllm/ci/builds/7#abc',
      'https://buildkite.com/vllm/ci/builds/7/steps/canvas?jid=abc&tab=output&x=1',
      'https://buildkite.com/vllm/ci/builds/7/steps/canvas?jid=abd&tab=output',
      ' https://buildkite.com/vllm/ci/builds/7#abc',
      'not a url',
    ]
    for (const url of urls) {
      for (const build of [7, 0, 7.5, -7]) {
        for (const id of ['abc', ' ', 3]) {
          const candidate = { ...job, url, job_id: id }
          parity('exactLatencyJobUrl', v2.exactLatencyJobUrl(candidate, build), classic.exactLatencyJobUrl!(candidate, build))
        }
      }
    }
    parity('exactLatencyJobUrl', v2.exactLatencyJobUrl(null, 7), classic.exactLatencyJobUrl!(null, 7))
    for (const side of [null, { median_duration_mins: 75 }, { median_duration_mins: 3000 }]) {
      parity('latencySideDuration', v2.latencySideDuration(side), classic.latencySideDuration!(side))
    }
  })
})

// --- AMD nightlies --------------------------------------------------------------

async function classicNightlies(ops: Rec) {
  ;(classic.state as unknown as Rec).analyticsView = 'nightlies'
  const host = document.createElement('div')
  document.body.append(host)
  await classic.renderAnalytics!(host, ops)
  return host
}

function nightlyCells(row: Rec) {
  return [
    '#' + row.number,
    value(row.state, 'unknown'),
    integer(row.total_groups),
    v2.nightlyCountLabel(row, 'new'),
    v2.nightlyCountLabel(row, 'recurring'),
    v2.nightlyCountLabel(row, 'fixed'),
    shortDate(row.created_at),
  ]
}

async function compareNightlies(ops: Rec, label: string) {
  const nightly = latestAmd(ops)
  parity('nightlyForCohort', nightly, classic.nightlyForCohort!(ops, 'ci-amd'))
  parity('nightlyDisplayName', v2.nightlyDisplayName(nightly), classic.nightlyDisplayName!(nightly))
  const model = v2.nightliesModel(nightly)
  const host = await classicNightlies(ops)
  const panelTitles = [...host.querySelectorAll('.ops-panel-title')].map(text)
  expect(panelTitles).toContain(model.name + ' nightly failure movement')
  const trs = [...host.querySelectorAll('table.ops-table tbody tr')]
  if (!model.rows.length) expect(text(host.querySelector('.ops-empty'))).toContain('No matching observations')
  parity('nightlyRowCount', model.rows.length, trs.length, label)
  trs.forEach((tr, index) => {
    const row = model.rows[index]!
    const cells = [...tr.querySelectorAll('td')]
    parity('nightlyRow', nightlyCells(row), cells.map(text), label)
    const badge = cells[1]!.querySelector('.ops-badge')!
    parity('nightlyStateTone', toneForState(row.state), tone(badge))
  })
  // The movement chart draws movementBuilds oldest first.
  const chartBuilds = movementBuilds(model.rows).reverse()
  const movement = chart('analytics-trend')!.config
  parity('movementChartLabels', chartBuilds.map((b) => '#' + b.number), movement.data.labels)
  if (chartBuilds.length) {
    const evidence = chartEvidence(host, 'analytics-trend')
    parity(
      'movementChartEvidence',
      chartBuilds.slice().reverse().map((b) => ['#' + b.number, v2.nightlyMovementSummary(b)]),
      evidence.rows.map((r) => [r[0], r[2]]),
    )
  }
  return { model, chartBuilds }
}

function nightlyOps(builds: Rec[], extra: Rec = {}): Rec {
  const pipeline = { ...fixture.nightly.pipelines[0], builds, ...extra }
  return { generated_at: fixture.generated_at, nightly: { ...fixture.nightly, pipelines: [pipeline] } }
}

describe('AMD nightlies match the classic renderer', () => {
  it('renders the real snapshot identically', async () => {
    const { model, chartBuilds } = await compareNightlies(fixture, 'fixture')
    await expectGoldenFile('analytics', 'nightlies', { rows: model.rows.map(nightlyCells) })
    expect(model.rows.length).toBeGreaterThan(5)
    expect(chartBuilds.length).toBeGreaterThan(5)
    expect(model.rows.some((row) => v2.nightlyCountLabel(row, 'new') === '-')).toBe(true)
    // Drop the no-signal head so the latest build carries movement.
    const withMovement = await compareNightlies(nightlyOps(fixture.nightly.pipelines[0].builds.slice(1)), 'from second')
    expect(withMovement.chartBuilds.at(-1)).toBe(withMovement.model.latest)
  })

  it('agrees on generated nightly histories', async () => {
    const rand = rng(5650)
    const pick = <T,>(xs: T[]) => xs[Math.floor(rand() * xs.length)]!
    const failed = (n: number, b: number) =>
      Array.from({ length: n }, (_, i) => ({ name: `job ${i}`, state: pick(['failed', 'soft_fail', 'passed']), build_number: b }))
    for (let i = 0; i < 80; i += 1) {
      const builds: Rec[] = []
      const count = Math.floor(rand() * 12)
      for (let b = 0; b < count; b += 1) {
        const number = rand() < 0.05 ? undefined : 9000 - b
        const build: Rec = {
          number,
          state: pick(['passed', 'failed', 'failing', 'running', 'canceled', undefined]),
          created_at: rand() < 0.9 ? `2026-10-${String(20 - b).padStart(2, '0')}T06:00:00Z` : undefined,
          url: rand() < 0.8 ? `https://buildkite.com/vllm/ci/builds/${number}` : 'https://buildkite.com/vllm/ci/builds/1',
          total_groups: rand() < 0.9 ? Math.floor(rand() * 300) : undefined,
        }
        if (rand() < 0.15) build.has_test_results = false
        const kind = rand()
        if (kind < 0.55) {
          build.failure_movement = {
            policy_id: 'observed-failure-movement-v1',
            available: rand() < 0.85,
            new: failed(Math.floor(rand() * 3), 9000 - b),
            recurring: failed(Math.floor(rand() * 4), 9000 - b),
            fixed: failed(Math.floor(rand() * 2), 9000 - b),
          }
        } else if (kind < 0.8) {
          build.transitions = {
            policy_id: 'confirmed-incidents-v1',
            preceding_build_number: rand() < 0.8 ? 8999 - b : 0,
            new: failed(Math.floor(rand() * 3), 9000 - b).map((r) => ({ ...r, current_severity: pick(['soft', 'hard']), soft_streak: Math.floor(rand() * 3), transition_change: pick(['confirmed', 'new']) })),
            recurring: failed(Math.floor(rand() * 2), 9000 - b),
            fixed: failed(Math.floor(rand() * 2), 9000 - b),
            pending_soft: failed(Math.floor(rand() * 2), 9000 - b).map((r) => ({ ...r, transition_change: pick(['pending_started', 'pending_advanced', 'cleared']) })),
          }
          if (rand() < 0.2) build.transition_eligible = false
        }
        builds.push(build)
      }
      const extra: Rec = rand() < 0.3 ? { display_name: rand() < 0.5 ? 'AMD MI300 nightly' : '' } : {}
      await compareNightlies(nightlyOps(builds, extra), `generated ${i}`)
      document.body.innerHTML = ''
      charts = []
    }
  })

  it('falls back to the empty cohort exactly as classic does', async () => {
    const two = { nightly: { pipelines: [fixture.nightly.pipelines[0], fixture.nightly.pipelines[0]] }, generated_at: 'x' }
    const { model } = await compareNightlies(two, 'two cohorts')
    expect(model.rows).toEqual([])
    await compareNightlies({ nightly: { canonical_history: fixture.nightly.pipelines[0] } }, 'canonical history')
  })
})

describe('CI Analytics helpers on hand-written inputs', () => {
  it('validates latency metrics and formats durations', () => {
    expect([null, '', '0', -1, '3.5', 'x', Infinity, 12].map((raw) => v2.latencyMetric(raw))).toEqual([null, null, 0, null, 3.5, null, null, 12])
    expect([null, { median_duration_mins: 75 }, { median_duration_mins: 3000 }].map((side) => v2.latencySideDuration(side))).toEqual(['Unavailable', '1h 15m', '2d'])
  })

  it('keeps only exact job URLs of the same build', () => {
    const urls = ['https://buildkite.com/vllm/ci/builds/7/steps/canvas?jid=abc&tab=output', 'https://buildkite.com/vllm/ci/builds/7#abc', 'https://buildkite.com/vllm/ci/builds/8#abc', 'not a url']
    expect(urls.map((url) => v2.exactLatencyJobUrl({ job_id: 'abc', url }, 7))).toEqual([urls[0], urls[1], '', ''])
  })

  it('labels pass rates from published or counted outcomes', () => {
    expect([{ pass_rate_pct: 87.5 }, { passed: 3, soft_failed: 1, hard_failed: 0 }, { pass_rate_pct: 'n/a' }].map((row) => v2.passRateLabel(row))).toEqual(['87.5%', '75.0%', '-'])
  })
})
