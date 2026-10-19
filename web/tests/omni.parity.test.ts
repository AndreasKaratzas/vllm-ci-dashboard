import { beforeAll, describe, expect, it } from 'vitest'
import * as v2 from '@/domain/omni'
import { shortDate, type Rec } from '@/domain/format'
import { loadClassic, readFixture, type ClassicFns } from './classic'
import {
  classicDetailShape,
  classicTableShape,
  topOverlay,
  v2DetailShape,
  v2TableShape,
  type DetailShape,
} from './omniClassicDom'

const HELPERS = [
  'emptyOmniMappingStats',
  'addOmniMappingStats',
  'omniMappingTotals',
  'omniMappingRowStart',
  'omniMappingRowEnd',
  'omniMappingPopulationBoundary',
  'omniMappingWindow',
  'omniMappingBuckets',
  'omniMappingBucketLabel',
  'omniHistoryPoints',
  'omniWindowPoints',
  'omniAgeBand',
  'omniDailyRows',
  'isRetiredQueue',
  'signedInteger',
  'buildUrl',
  'waitValue',
  'waitSource',
  'waitSourceDetail',
  'waitSampleCount',
  'hasAgentMeasurement',
  'officialWaitValue',
  'sampleWaitValue',
  'historyPointLabel',
  'historyPointSources',
] as const

let classic: ClassicFns
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let classicState: Record<string, any>
let fixture: Rec
const charts: Record<string, Rec> = {}
let frames: (() => void)[] = []

function rng(seed: number) {
  let s = seed >>> 0
  return () => (s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32
}

beforeAll(() => {
  classic = loadClassic([...HELPERS, 'renderOmni', 'state', 'closeOverlay'])
  classicState = classic.state as unknown as Record<string, unknown>
  fixture = readFixture<Rec>('omni_sections.json')
  ;(window as unknown as Rec).requestAnimationFrame = (cb: () => void) => frames.push(cb)
  ;(window as unknown as Rec).Chart = class {
    constructor(canvas: HTMLCanvasElement, config: Rec) {
      charts[canvas.dataset.chartKey!] = config
    }
    destroy() {}
  }
})

// ---- generated snapshot variants ------------------------------------------

const HOUR = 3600_000
function iso(ms: number) {
  return new Date(ms).toISOString().replace('.000Z', 'Z')
}

type Mutation = (ops: Rec, rand: () => number) => void
const pickFrom = <T,>(rand: () => number, xs: T[]) => xs[Math.floor(rand() * xs.length)]!

const QUEUES = ['amd_mi300_1', 'amd_mi300_2', 'amd_mi325_1', 'amd_mi325_4', 'amd_mi355_1', 'amd_mi355b_1', 'amd_mi250_1']
function randomStats(rand: () => number, queues: string[]): Rec {
  const by_queue: Rec = {}
  for (const q of queues) {
    if (rand() < 0.4) continue
    by_queue[q] = {
      mapped_jobs: Math.floor(rand() * 40),
      started_jobs: Math.floor(rand() * 40),
      finished_jobs: Math.floor(rand() * 30),
      mapped_gpu_slots: Math.floor(rand() * 90),
      gpu_hours: Math.round(rand() * 5000) / 100,
    }
  }
  const sum = (f: string) => Object.values(by_queue).reduce((s: number, r: Rec) => s + r[f], 0)
  const stats: Rec = {
    mapped_jobs: rand() < 0.1 ? undefined : sum('mapped_jobs'),
    started_jobs: sum('started_jobs'),
    finished_jobs: sum('finished_jobs'),
    mapped_gpu_slots: sum('mapped_gpu_slots'),
    gpu_hours: sum('gpu_hours'),
    by_queue,
  }
  if (rand() < 0.7) stats.by_pipeline = { 'vllm-omni-amd-ci': { mapped_jobs: stats.mapped_jobs, started_jobs: 1, mapped_gpu_slots: 2 }, other: { mapped_jobs: Math.floor(rand() * 5) } }
  return stats
}

const MUTATIONS: Record<string, Mutation> = {
  hourlyOnly: (ops) => (ops.omni.mapping_history.daily = []),
  dailyOnly: (ops) => (ops.omni.mapping_history.hourly = []),
  noMapping: (ops) => {
    ops.omni.mapping_history.daily = []
    ops.omni.mapping_history.hourly = []
  },
  mappingWrongScope: (ops, rand) => {
    if (rand() < 0.5) ops.omni.mapping_history.hardware_scope = 'cuda'
    else ops.omni.mapping_history.scope.workload_pipelines.main = ['ci', 'fastcheck']
  },
  synthesizeHourly: (ops, rand) => {
    // A contiguous closed history with random workloads, gaps and flags.
    const end = Date.parse('2026-10-19T08:00:00Z')
    const count = Math.floor(rand() * 200) + 1
    const rows: Rec[] = []
    for (let i = count; i >= 1; i -= 1) {
      if (rand() < 0.05) continue
      const row: Rec = { hour: iso(end - i * HOUR), workloads: { omni: randomStats(rand, QUEUES), main: randomStats(rand, QUEUES) } }
      if (rand() < 0.05) row.lower_bound = true
      if (rand() < 0.05) row.collection_complete = false
      if (rand() < 0.03) row.complete = false
      if (i === 1 && rand() < 0.5) row[rand() < 0.5 ? 'state' : 'open'] = rand() < 0.5 ? 'open' : true
      if (rand() < 0.02) row.hour = 'not a date'
      if (rand() < 0.1) row.end_exclusive = iso(end - (i - 1) * HOUR)
      rows.push(row)
    }
    ops.omni.mapping_history.hourly = rows
  },
  synthesizeDaily: (ops, rand) => {
    const end = Date.parse('2026-10-19T00:00:00Z')
    const rows: Rec[] = []
    for (let i = Math.floor(rand() * 100); i >= 0; i -= 1) {
      if (rand() < 0.05) continue
      const row: Rec = { date: iso(end - i * 24 * HOUR).slice(0, 10), workloads: { omni: randomStats(rand, QUEUES), main: randomStats(rand, QUEUES) } }
      if (rand() < 0.05) row.lower_bound = true
      if (i === 0 && rand() < 0.5) row.state = 'open'
      rows.push(row)
    }
    ops.omni.mapping_history.daily = rows
  },
  generatedEarlier: (ops, rand) => {
    const m = ops.omni.mapping_history
    m.generated_at = pickFrom(rand, ['2026-10-17T03:10:00Z', '2026-10-18T23:59:00Z', 'garbage', undefined])
  },
  exhaustive: (ops, rand) => {
    const m = ops.omni.mapping_history
    const v = pickFrom(rand, [true, false, undefined, 'yes'])
    m.window.job_created_range_exhaustive = v
    for (const c of Object.values(m.coverage as Rec)) (c as Rec).job_created_range_exhaustive = pickFrom(rand, [v, undefined])
    m.query.job_created_range_exhaustive = pickFrom(rand, [true, false, undefined])
    if (rand() < 0.5) delete m.scope.attribution
  },
  attribution: (ops, rand) => {
    const a = (ops.omni.mapping_history.scope.attribution ||= {})
    a.exact_within_declared_source_window = rand() < 0.5
    a.parent_build_lookback_days = pickFrom(rand, [undefined, 0, 2, '5', -1])
    a.limitation = pickFrom(rand, [undefined, '', 'A custom limitation.'])
  },
  publication: (ops, rand) => {
    const p = ops.omni.mapping_history.retention.publication
    p.complete_relative_to_source = pickFrom(rand, [false, true, undefined])
    p.hourly = rand() < 0.3 ? undefined : { source: 200, published: 150, omitted: 50 }
    if (rand() < 0.5) delete p.daily
  },
  jobs: (ops, rand) => {
    const make = (state: string): Rec => {
      const job: Rec = {
        name: rand() < 0.1 ? '' : `job ${Math.floor(rand() * 1000)}`,
        queue: rand() < 0.1 ? undefined : pickFrom(rand, QUEUES),
        state,
        pipeline: rand() < 0.1 ? undefined : 'vllm-omni-amd-ci',
        build: Math.floor(rand() * 20000),
        branch: 'feature/x',
        workload: rand() < 0.5 ? 'omni' : undefined,
        source: rand() < 0.2 ? 'fork' : '',
        analysis_excluded: rand() < 0.2,
        exclusion_reason: rand() < 0.5 ? 'stale' : '',
        created_at: iso(Date.parse('2026-10-19T00:00:00Z') - Math.floor(rand() * 48) * HOUR),
      }
      if (rand() < 0.85) job.url = `https://buildkite.com/vllm/vllm-omni-amd-ci/builds/${job.build}#${Math.floor(rand() * 1e6)}`
      if (rand() < 0.3) job.build_url = `https://buildkite.com/vllm/vllm-omni-amd-ci/builds/${job.build}`
      if (state === 'scheduled') job.wait_min = pickFrom(rand, [undefined, 0, 30, 61.5, 200, 800, 1500, 5000, -3, 'x'])
      else job.run_min = Math.round(rand() * 3000) / 10
      return job
    }
    ops.omni.current_jobs = {
      pending: Array.from({ length: Math.floor(rand() * 15) }, () => make('scheduled')),
      running: Array.from({ length: Math.floor(rand() * 15) }, () => make('running')),
    }
    if (rand() < 0.5) ops.omni.current.ledger = {}
    if (rand() < 0.5) ops.omni.current.waiting_by_queue = { amd_mi300_1: 3, amd_mi355b_2: 4, amd_mi325_1: 0 }
  },
  history: (ops, rand) => {
    const start = Date.parse('2026-10-14T00:00:00Z')
    const points: Rec[] = []
    for (let i = 0; i < 300; i += 1) {
      const scope = (): Rec => {
        const s: Rec = { waiting_observed: Math.floor(rand() * 9), running_observed: Math.floor(rand() * 9) }
        const mode = rand()
        if (mode < 0.3) {
          s.waiting_supported = rand() < 0.6
          s.running_supported = rand() < 0.6
        } else {
          s.waiting_attribution = pickFrom(rand, ['complete', 'partial', 'unavailable', undefined])
          s.running_attribution = pickFrom(rand, ['complete', 'partial', 'unavailable'])
        }
        return s
      }
      const point: Rec = { ts: rand() < 0.03 ? 'bad' : iso(start + i * 0.9 * HOUR + Math.floor(rand() * 50) * 60_000), amd: scope() }
      if (rand() < 0.4) point.all_fleet = scope()
      points.push(point)
    }
    ops.omni.history.points = points.sort(() => rand() - 0.5)
  },
  queueSnapshots: (ops, rand) => {
    const queues = ops.queue.snapshot.queues
    for (const name of Object.keys(queues)) {
      const q = queues[name]
      if (rand() < 0.3) delete q.official_wait
      if (rand() < 0.3) q.sample_wait = { p50: rand() * 30, p95: rand() < 0.5 ? null : rand() * 90, p99: rand() < 0.5 ? undefined : 120, count: 4 }
      if (rand() < 0.3) q.connected_agents_available = rand() < 0.5
      if (rand() < 0.3) q.connected_agents_source = pickFrom(rand, ['webhook', 'cluster', ''])
      if (rand() < 0.2) delete q.queue_url
      if (rand() < 0.2) q.wait_sample_expected_count = undefined
      if (rand() < 0.2) q.p99_wait_source = 'sample_wait'
      if (rand() < 0.2) q.jobs_passed = null
    }
  },
  cleanHourly: (ops, rand) => {
    // Complete, contiguous, closed hourly coverage: the only state with rates and an info note.
    const end = Date.parse('2026-10-19T08:00:00Z')
    const m = ops.omni.mapping_history
    m.hourly = Array.from({ length: 200 }, (_, k) => ({
      hour: iso(end - (200 - k) * HOUR),
      workloads: { omni: randomStats(rand, QUEUES), main: randomStats(rand, QUEUES) },
    }))
    m.generated_at = iso(end)
    m.window.job_created_range_exhaustive = true
    if (rand() < 0.5) m.retention.publication.complete_relative_to_source = false
  },
  duplicateHours: (ops, rand) => {
    // Repeated source hours keep a bucket's row count while leaving a gap.
    const rows: Rec[] = ops.omni.mapping_history.hourly || []
    for (let i = 1; i < rows.length; i += 1) if (rand() < 0.15) rows[i] = { ...rows[i], hour: rows[i - 1]!.hour }
  },
  // Last: these remove what the mutations above edit.
  mappingMissing: (ops) => delete ops.omni.mapping_history,
  noOmniScope: (ops) => (ops.omni.hardware_scope = 'cuda'),
}

function variant(seed: number): { ops: Rec; applied: string[] } {
  const rand = rng(seed)
  const ops = structuredClone(fixture)
  const applied: string[] = []
  for (const [name, mutate] of Object.entries(MUTATIONS)) {
    const p = name === 'noOmniScope' ? 0.03 : name === 'noMapping' || name === 'mappingMissing' ? 0.06 : 0.3
    if (rand() < p) {
      mutate(ops, rand)
      applied.push(name)
    }
  }
  return { ops, applied }
}

// ---- classic rendering --------------------------------------------------------

function renderClassic(ops: Rec, mappingRange: string, range: string): HTMLElement {
  classic.closeOverlay!()
  for (const key of Object.keys(charts)) delete charts[key]
  frames = []
  classicState.omniMappingRange = mappingRange
  classicState.omniRange = range
  document.body.innerHTML = ''
  const host = document.createElement('div')
  document.body.append(host)
  classic.renderOmni!(host, ops)
  for (const frame of frames.splice(0)) frame()
  return host
}

const txt = (el: Element | null | undefined) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()
const toneOf = (el: Element) => [...el.classList].find((c) => /^is-(success|danger|warning|info|neutral)$/.test(c)) ?? ''

/** Clicks a classic control, reads the drawer it opens, then backs out. */
function openClassic(control: Element | (() => void)): DetailShape {
  const before = topOverlay()
  if (typeof control === 'function') control()
  else (control as HTMLElement).click()
  const overlay = topOverlay()
  expect(overlay, 'classic opened a drawer').not.toBe(before)
  return classicDetailShape(overlay!)
}
function back() {
  ;(topOverlay()!.querySelector('.ops-overlay-back') as HTMLElement).click()
}

let drawersCompared = 0
/** Compares a drawer and, to `depth`, the drawers its first rows open. */
function compareDrawer(control: Element | (() => void), detail: v2.OmniDetail, path: string, depth = 2) {
  const shape = openClassic(control)
  expect(v2DetailShape(detail, shape), path).toEqual(shape)
  drawersCompared += 1
  if (depth > 0) {
    const overlay = topOverlay()!
    const tables = [...overlay.querySelectorAll('.ops-overlay-body table.ops-table')]
    const v2Tables = detail.tables.filter((t) => t.rows.length)
    tables.forEach((table, ti) => {
      const rows = [...table.querySelectorAll('tbody tr')]
      for (const ri of [0, 1, rows.length - 1].filter((i, k, all) => i >= 0 && i < rows.length && all.indexOf(i) === k)) {
        const cells = [...rows[ri]!.querySelectorAll('td')]
        cells.forEach((td, ci) => {
          const button = td.querySelector('button')
          const cell = v2Tables[ti]!.columns[ci]!.cell(v2Tables[ti]!.rows[ri])
          expect(Boolean(button), `${path} row ${ri} col ${ci} is a drill-down`).toBe(Boolean(cell.open))
          if (button && cell.open) compareDrawer(button, cell.open(), `${path} > ${cell.text}`, depth - 1)
        })
      }
    })
  }
  back()
}

function comparePage(ops: Rec, mappingRange: string, range: string, label: string, drillDown = true) {
  const host = renderClassic(ops, mappingRange, range)
  const page = v2.omniView(ops, mappingRange, range)
  if (!page.available) {
    expect(txt(host)).toBe(page.message)
    return
  }
  expect(txt(host.querySelector('.ops-page-title'))).toBe(page.header.title)
  expect(txt(host.querySelector('.ops-page-description'))).toBe(page.header.description)
  const observed = txt(host.querySelector('.ops-page-header .ops-panel-meta'))
  expect(observed.split(' - ')[0]).toBe(page.header.observedAt ? `Observed ${shortDate(page.header.observedAt)}` : '')
  const headerAction = host.querySelector('.ops-page-actions a')!
  expect(`${txt(headerAction)} <${headerAction.getAttribute('href')}>`).toBe(`${page.header.actionLabel} <${page.header.actionUrl}>`)

  const segments = host.querySelectorAll('.ops-omni-mapping-toolbar .ops-segment')
  expect([...segments].map(txt)).toEqual(v2.OMNI_MAPPING_WINDOWS.map((w) => w.label))
  expect(txt(host.querySelector('.ops-omni-mapping-toolbar .ops-segment.is-active'))).toBe(v2.OMNI_MAPPING_WINDOWS.find((w) => w.id === page.mappingRange)!.label)

  // KPI strip
  const kpis = [...host.querySelectorAll(':scope > .ops-status-strip .ops-status-item')]
  expect(
    kpis.map((k) => [txt(k.querySelector('.ops-stat-label')), txt(k.querySelector('.ops-stat-value')), txt(k.querySelector('.ops-stat-meta')), toneOf(k)]),
    `${label} KPIs`,
  ).toEqual(page.kpis.map((k) => [k.label, k.value, k.meta.replace(/\s+/g, ' ').trim(), k.tone]))

  // Scope note
  const note = host.querySelector('.ops-omni-coverage-note')!
  expect(toneOf(note)).toBe(page.scopeNote.tone)
  expect(txt(note.querySelector('strong'))).toBe(page.scopeNote.heading.trim())
  expect(txt(note.querySelector('span'))).toBe(page.scopeNote.text.replace(/\s+/g, ' ').trim())
  const noteButtons = [...note.querySelectorAll('button')]
  expect(noteButtons.map(txt)).toEqual(['Inspect methodology', ...(page.scopeNote.excluded ? ['Inspect excluded stale jobs'] : [])])

  // Overview grid
  const grid = [...host.querySelectorAll('.ops-omni-overview-grid > section')]
  const panelHead = (p: Element) => `${txt(p.querySelector('.ops-panel-title'))}|${txt(p.querySelector('.ops-panel-meta'))}`
  expect(panelHead(grid[0]!)).toBe(`${page.volume.title}|${page.volume.meta}`)
  expect(panelHead(grid[1]!)).toBe(`${page.impact.title}|${page.impact.meta}`)
  if (page.volume.available) {
    const chart = charts['omni-amd-mapped-jobs']!
    expect(chart.data.labels).toEqual(page.volume.bars.map((b) => b.label))
    expect(chart.data.datasets[0].data).toEqual(page.volume.bars.map((b) => b.value))
  } else {
    expect(charts['omni-amd-mapped-jobs']).toBeUndefined()
    expect(txt(grid[0]!.querySelector('.ops-empty'))).toBe(page.volume.empty)
  }
  if (page.impact.available) {
    const chart = charts['omni-queue-impact']!
    expect(chart.data.labels).toEqual(page.impact.bars.map((b) => b.label))
    expect(chart.data.datasets[0].data).toEqual(page.impact.bars.map((b) => b.value))
    expect(chart.data.datasets[0].backgroundColor).toEqual(page.impact.bars.map((b) => (b.color === 'retiring' ? '#e3a63a' : '#22b8ad')))
    const table = grid[1]!.querySelector('table.ops-table')!
    expect(classicTableShape(grid[1]!, table)).toEqual(v2TableShape(page.impact.preview))
  } else {
    expect(charts['omni-queue-impact']).toBeUndefined()
    expect(txt(grid[1]!.querySelector('.ops-empty'))).toBe(page.impact.empty)
  }

  // Repository comparison
  const comparison = host.querySelector('.ops-omni-comparison')!
  expect(panelHead(comparison)).toBe(`${page.comparison.title}|${page.comparison.meta}`)
  expect(classicTableShape(comparison, comparison.querySelector('table.ops-table')!)).toEqual(v2TableShape(page.comparison.table))
  const comparisonActions = [...comparison.querySelectorAll('.ops-inline-actions button')]
  expect(comparisonActions.map(txt)).toEqual(['Inspect time buckets', ...(page.comparison.browseQueues ? ['Browse all queues'] : [])])

  // Live state
  const live = host.querySelector('.ops-omni-live')!
  expect(panelHead(live)).toBe(`${page.live.title}|${page.live.meta}`)
  const facts = [...live.querySelectorAll('.ops-omni-live-fact')]
  expect(facts.map((f) => [txt(f.querySelector('.ops-stat-label')), txt(f.querySelector('.ops-omni-live-value')), txt(f.querySelector('.ops-stat-meta')), toneOf(f)])).toEqual(
    page.live.facts.map((f) => [f.label, f.value, f.meta, f.tone]),
  )
  const liveSegments = live.querySelectorAll('.ops-segment')
  expect([...liveSegments].map(txt)).toEqual(v2.OMNI_RANGE_WINDOWS.map((w) => w.label))
  expect(txt(live.querySelector('.ops-segment.is-active'))).toBe(v2.OMNI_RANGE_WINDOWS.find((w) => w.id === page.omniRange)!.label)
  const liveButtons = [...live.querySelectorAll('.ops-omni-live-actions > button')]
  expect(liveButtons.map(txt)).toEqual(page.live.actions.map((a) => a.label))
  const historyLink = live.querySelector('.ops-omni-live-actions > a')!
  expect(`${txt(historyLink)} <${historyLink.getAttribute('href')}>`).toBe(`Inspect published queue history <${page.live.queueHistoryUrl}>`)

  if (!drillDown) return
  kpis.forEach((k, i) => compareDrawer(k, page.kpis[i]!.open(), `${label} KPI ${i}`))
  compareDrawer(host.querySelector('.ops-omni-mapping-toolbar > button')!, page.methodology(), `${label} scope & coverage`)
  compareDrawer(noteButtons[0]!, page.scopeNote.methodology(), `${label} methodology`)
  if (page.scopeNote.excluded) compareDrawer(noteButtons[1]!, page.scopeNote.excluded(), `${label} excluded`)
  if (page.volume.available) {
    const evidence = grid[0]!.querySelector('.ops-chart-evidence-action')!
    compareDrawer(evidence, page.volume.evidence(), `${label} volume evidence`)
    const onClick = charts['omni-amd-mapped-jobs']!.options.onClick
    const bars = page.volume.bars
    for (const i of [0, bars.length - 1]) compareDrawer(() => onClick(null, [{ index: i }]), bars[i]!.open(), `${label} volume bar ${i}`)
  }
  const impact = page.impact
  if (impact.available) {
    compareDrawer(grid[1]!.querySelector('.ops-chart-evidence-action')!, impact.evidence(), `${label} impact evidence`)
    const onClick = charts['omni-queue-impact']!.options.onClick
    const bars = impact.bars
    for (const i of [0, bars.length - 1]) compareDrawer(() => onClick(null, [{ index: i }]), bars[i]!.open(), `${label} impact bar ${i}`)
    const rows = [...grid[1]!.querySelectorAll('tbody tr')]
    rows.forEach((tr, ri) => {
      const buttons = tr.querySelectorAll('button')
      compareDrawer(buttons[0]!, impact.preview.columns[0]!.cell(impact.preview.rows[ri]!).open!(), `${label} impact row ${ri}`, 1)
      compareDrawer(buttons[1]!, impact.preview.columns[1]!.cell(impact.preview.rows[ri]!).open!(), `${label} impact mapped ${ri}`, 0)
    })
  }
  ;[...comparison.querySelectorAll('tbody tr')].forEach((tr, ri) => {
    compareDrawer(tr.querySelector('button')!, page.comparison.table.columns[0]!.cell(page.comparison.table.rows[ri]).open!(), `${label} repository ${ri}`)
  })
  compareDrawer(comparisonActions[0]!, page.comparison.browseBuckets(), `${label} buckets`)
  if (page.comparison.browseQueues) compareDrawer(comparisonActions[1]!, page.comparison.browseQueues(), `${label} queues`)
  facts.forEach((f, i) => compareDrawer(f, page.live.facts[i]!.open(), `${label} fact ${i}`))
  liveButtons.forEach((b, i) => compareDrawer(b, page.live.actions[i]!.open(), `${label} live action ${i}`))
}

describe('Omni helpers match the classic renderer', () => {
  it('reads the real mapping snapshot identically for every window', () => {
    const mapping = fixture.omni.mapping_history
    for (const range of [...v2.OMNI_MAPPING_WINDOWS.map((w) => w.id), 'bogus']) {
      const view = v2.omniMappingWindow(mapping, range)
      expect(view).toEqual(classic.omniMappingWindow!(mapping, range))
      expect(v2.omniMappingBuckets(view)).toEqual(classic.omniMappingBuckets!(classic.omniMappingWindow!(mapping, range)))
      expect(v2.omniMappingTotals(view.rows)).toEqual(classic.omniMappingTotals!(view.rows))
      for (const resolution of ['hourly', 'daily', 'unavailable']) {
        expect(v2.omniMappingPopulationBoundary(mapping, resolution)).toEqual(classic.omniMappingPopulationBoundary!(mapping, resolution))
      }
    }
    expect(v2.omniMappingWindow(mapping, '7d').rows.length).toBeGreaterThan(0)
  })

  it('reads the real occupancy history identically for every range', () => {
    const points = v2.omniHistoryPoints(fixture.omni)
    expect(points).toEqual(classic.omniHistoryPoints!(fixture.omni))
    expect(points.length).toBeGreaterThan(0)
    for (const range of [...v2.OMNI_RANGE_WINDOWS.map((w) => w.id), 'bogus']) {
      expect(v2.omniWindowPoints(points, range)).toEqual(classic.omniWindowPoints!(points, range))
    }
    const waiting = points.filter((p) => p.waitingSupported)
    expect(v2.omniDailyRows(waiting)).toEqual(classic.omniDailyRows!(waiting))
  })

  it('agrees on generated mapping windows, buckets, history and jobs', () => {
    for (let seed = 1; seed <= 400; seed += 1) {
      const { ops } = variant(seed * 7919)
      const rand = rng(seed)
      const mapping = ops.omni.mapping_history
      const range = pickFrom(rand, [...v2.OMNI_MAPPING_WINDOWS.map((w) => w.id), 'x'])
      const view = v2.omniMappingWindow(mapping, range)
      const classicView = classic.omniMappingWindow!(mapping, range)
      expect(view, `seed ${seed}`).toEqual(classicView)
      expect(v2.omniMappingBuckets(view)).toEqual(classic.omniMappingBuckets!(classicView))
      const resolution = view.resolution
      for (const bucket of v2.omniMappingBuckets(view)) {
        expect(v2.omniMappingBucketLabel(bucket, resolution)).toBe(classic.omniMappingBucketLabel!(bucket, resolution))
      }
      for (const row of [...(mapping?.hourly || []), ...(mapping?.daily || [])].slice(0, 30)) {
        for (const r of ['hourly', 'daily']) {
          expect(v2.omniMappingRowStart(row, r)).toBe(classic.omniMappingRowStart!(row, r))
          expect(v2.omniMappingRowEnd(row, r)).toBe(classic.omniMappingRowEnd!(row, r))
        }
      }
      const points = v2.omniHistoryPoints(ops.omni)
      expect(points).toEqual(classic.omniHistoryPoints!(ops.omni))
      const omniRange = pickFrom(rand, [...v2.OMNI_RANGE_WINDOWS.map((w) => w.id), 'x'])
      expect(v2.omniWindowPoints(points, omniRange)).toEqual(classic.omniWindowPoints!(points, omniRange))
      const waiting = points.filter((p) => p.waitingSupported)
      expect(v2.omniDailyRows(waiting)).toEqual(classic.omniDailyRows!(waiting))
      for (const job of [...(ops.omni.current_jobs?.pending || []), ...(ops.omni.current_jobs?.running || [])]) {
        expect(v2.omniAgeBand(job)).toBe(classic.omniAgeBand!(job))
        expect(v2.isRetiredQueue(job.queue)).toBe(classic.isRetiredQueue!(job.queue))
        expect(v2.buildUrl(job.pipeline, job.build)).toBe(classic.buildUrl!(job.pipeline, job.build))
      }
      for (const q of Object.values(ops.queue.snapshot.queues as Rec)) {
        for (const metric of ['min', 'max', 'p50', 'p95', 'p99']) {
          expect(v2.waitValue(q, metric)).toBe(classic.waitValue!(q, metric))
          expect(v2.waitSource(q, metric)).toBe(classic.waitSource!(q, metric))
          expect(v2.waitSourceDetail(q, metric)).toBe(classic.waitSourceDetail!(q, metric))
          expect(v2.officialWaitValue(q, metric)).toBe(classic.officialWaitValue!(q, metric))
          expect(v2.sampleWaitValue(q, metric)).toBe(classic.sampleWaitValue!(q, metric))
        }
        expect(v2.waitSampleCount(q)).toBe(classic.waitSampleCount!(q))
        expect(v2.hasAgentMeasurement(q)).toBe(classic.hasAgentMeasurement!(q))
      }
    }
    for (const n of [-5, 0, 3, 1234567, 'x', null]) expect(v2.signedInteger(n)).toBe(classic.signedInteger!(n))
    for (const wait of [undefined, null, '', -1, 0, 59.9, 60, 179, 180, 359, 360, 719, 720, 1439, 1440, 4319, 4320, 99999, 'abc']) {
      expect(v2.omniAgeBand({ wait_min: wait })).toBe(classic.omniAgeBand!({ wait_min: wait }))
    }
    expect(v2.omniAgeBand(null)).toBe(classic.omniAgeBand!(null))
  })

  it('labels and sources evidence points as classic does', () => {
    const rand = rng(99)
    for (let i = 0; i < 500; i += 1) {
      const point: Rec = {}
      if (rand() < 0.5) point.label = pickFrom(rand, ['', 'Label'])
      if (rand() < 0.5) point.name = pickFrom(rand, ['', 'Name'])
      if (rand() < 0.5) point.timestamp = pickFrom(rand, ['', '2026-10-19T07:00:00Z', 'garbage'])
      if (rand() < 0.5) point.url = pickFrom(rand, ['', 'https://buildkite.com/x'])
      if (rand() < 0.5) point.sourceAsset = 'data/a.json'
      if (rand() < 0.5) point.sources = [{ label: 'Open published x', url: pickFrom(rand, ['https://buildkite.com/x', 'data/b.json', '']) }, null]
      const fallback = pickFrom(rand, [undefined, 'data/c.json'])
      expect(v2.historyPointLabel(point)).toBe(classic.historyPointLabel!(point))
      expect(v2.historyPointSources(point, fallback)).toEqual(classic.historyPointSources!(point, fallback))
    }
  })

  it('validates route state as classic syncRouteState does', () => {
    expect(v2.omniRouteValue('omni_mapping_range', '1m')).toBe('1m')
    expect(v2.omniRouteValue('omni_mapping_range', 'bogus')).toBe('7d')
    expect(v2.omniRouteValue('omni_range', '72h')).toBe('72h')
    expect(v2.omniRouteValue('omni_range', undefined)).toBe('24h')
    expect(v2.omniRouteValue('omni_age', 'gte3d')).toBe('gte3d')
    expect(v2.omniRouteValue('omni_age', '')).toBe('all')
  })
})

describe('the Omni page matches classic renderOmni on the same snapshot', () => {
  it('renders the real snapshot identically for every window pair, with every drawer', () => {
    for (const mappingRange of v2.OMNI_MAPPING_WINDOWS.map((w) => w.id)) {
      for (const range of v2.OMNI_RANGE_WINDOWS.map((w) => w.id)) {
        comparePage(fixture, mappingRange, range, `${mappingRange}/${range}`, range === '24h' || mappingRange === '7d')
      }
    }
    expect(drawersCompared).toBeGreaterThan(100)
  }, 180_000)

  it('renders generated snapshot variants identically, with every drawer', () => {
    const seen = new Set<string>()
    for (let seed = 1; seed <= 80; seed += 1) {
      const { ops, applied } = variant(seed * 104729)
      applied.forEach((name) => seen.add(name))
      const rand = rng(seed * 31)
      const mappingRange = pickFrom(rand, v2.OMNI_MAPPING_WINDOWS.map((w) => w.id))
      const range = pickFrom(rand, v2.OMNI_RANGE_WINDOWS.map((w) => w.id))
      comparePage(ops, mappingRange, range, `seed ${seed} [${applied.join(',')}] ${mappingRange}/${range}`)
    }
    expect([...seen].sort()).toEqual(Object.keys(MUTATIONS).sort())
  }, 180_000)
})
