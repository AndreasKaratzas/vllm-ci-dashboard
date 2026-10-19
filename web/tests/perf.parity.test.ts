import { beforeAll, describe, expect, it } from 'vitest'
import * as v2 from '@/domain/perf'
import { age, integer, shortDate, value, type Rec } from '@/domain/format'
import { classicParity, loadClassic, type ClassicFns } from './classic'
import { pinClock, readFixture } from './support/fixtures'
import { expectGoldenFile } from './support/golden'
import { rng } from './support/rng'

// renderPerf and openPerfHistory are DOM builders: run them on the snapshot and
// compare what they render with the port's view model.
const PORTED = ['state', 'cache', 'charts', 'renderPerf', 'perfValue', 'perfDelta', 'accuracyRows', 'openPerfHistory', 'closeOverlay', 'drawPerfSpark'] as const

const perf = readFixture<Rec>('perf_eval.json')
const NOW = pinClock(perf.generated_at)
const parity = classicParity('perf')
let classic: ClassicFns & { state: Rec; cache: Map<string, Promise<unknown>>; charts: Map<string, unknown> }
let lastChart: Rec | null = null

const text = (el: Element | null | undefined) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()
const all = (root: ParentNode, sel: string) => Array.from(root.querySelectorAll(sel))

interface Rendered {
  unavailable: boolean
  header?: string
  strip?: string[][]
  back?: boolean
  devices?: string[]
  activeDevice?: string
  models?: string[]
  empty?: string[]
  table?: { caption: string; head: string[]; rows: string[][] }
  sections?: Rec[]
}

function classicDelta(el: Element) {
  const tone = (el.className.match(/is-(success|danger|neutral)/) || [])[0]
  return { label: text(el), tone, title: el.getAttribute('title') }
}

async function renderClassic(data: Rec, state: v2.PerfState, opsGeneratedAt = '2026-09-01T00:00:00Z'): Promise<Rendered> {
  classic.cache.set(v2.PERF_SOURCE, Promise.resolve(data))
  classic.state.perfView = state.view
  classic.state.perfModel = state.model
  classic.state.perfDevice = state.device
  const host = document.createElement('div')
  await classic.renderPerf!(host, { generated_at: opsGeneratedAt })
  const warning = host.querySelector('.ops-evidence-note.is-warning')
  if (warning && /performance data is unavailable/.test(text(warning))) return { unavailable: true }
  const out: Rendered = { unavailable: false }
  out.header = text(host.querySelector('.ops-page-header .ops-panel-meta'))
  out.strip = all(host, '.ops-status-item').map((item) => [
    text(item.querySelector('.ops-stat-label')),
    text(item.querySelector('.ops-stat-value')),
    text(item.querySelector('.ops-stat-meta')),
    item.getAttribute('href') || '',
  ])
  out.back = Boolean(host.querySelector('.ops-perf-back'))
  const hw = host.querySelector('[aria-label="Performance hardware filter"]')
  out.devices = hw ? all(hw, 'button').map((b) => text(b)) : []
  out.activeDevice = hw ? text(hw.querySelector('.is-active')) : ''
  out.models = all(host, '.ops-perf-toolbar select option').map((o) => `${(o as HTMLOptionElement).value}|${text(o)}`)
  out.empty = all(host, '.ops-empty').map(text)
  const table = host.querySelector('table')
  if (table) {
    out.table = {
      caption: text(table.querySelector('caption')),
      head: all(table, 'thead th').map(text),
      rows: all(table, 'tbody tr').map((tr) =>
        all(tr, 'td').map((td) => {
          const a = td.querySelector('a')
          const badge = td.querySelector('.ops-badge')
          const tone = badge ? (badge.className.match(/is-[a-z-]+/) || [''])[0] : ''
          return [text(td), a?.getAttribute('href') || '', tone].join('|')
        }),
      ),
    }
  }
  out.sections = all(host, '.ops-perf-model-section').map((section) => ({
    title: text(section.querySelector('.ops-perf-model-title')),
    devices: all(section, '.ops-perf-model-header .ops-badge').map(text),
    provenance: all(section, '.ops-perf-provenance > *').map((el) => `${text(el)}|${el.getAttribute('href') || ''}`),
    nightlies: text(section.querySelector('.ops-perf-nightly-count')),
    empty: all(section, '.ops-empty').map(text),
    configs: all(section, '.ops-perf-config').map((config) => ({
      label: text(config.querySelector('h3')),
      meta: text(config.querySelector('.ops-panel-meta')),
      tiles: all(config, '.ops-perf-metric').map((tile) => ({
        tone: (tile.className.match(/is-(success|danger|neutral)/) || [])[0],
        label: text(tile.querySelector('.ops-perf-metric-name')),
        delta: classicDelta(tile.querySelector('.ops-perf-delta')!),
        value: text(tile.querySelector('.ops-perf-metric-value')),
        direction: text(tile.querySelector('.ops-perf-direction')),
      })),
    })),
  }))
  return out
}

function renderPort(data: Rec, state: v2.PerfState, opsGeneratedAt = '2026-09-01T00:00:00Z'): Rendered {
  const page = v2.perfPage(data, state)
  if (page.kind === 'unavailable') return { unavailable: true }
  const ts = data.generated_at || opsGeneratedAt
  const k = page.kpis
  const pipeline = v2.perfPipelineUrl(data)
  const out: Rendered = {
    unavailable: false,
    header: `Observed ${shortDate(ts)} - ${age(ts, NOW)}`,
    strip: [
      ['AMD MODELS', k.models, 'nightly model families', ''],
      ['NIGHTLIES TRACKED', k.nightlies, 'across retained series', pipeline],
      ['METRIC POINTS', k.points, k.pointsMeta, ''],
      ['AMD HARDWARE', k.hardware, 'ROCm only', ''],
    ],
    back: page.showBack,
    devices: page.devices.length ? ['All', ...page.devices.map((d) => d.label)] : [],
    activeDevice: page.devices.length
      ? state.device === 'all'
        ? 'All'
        : page.devices.find((d) => d.id === state.device)?.label ?? ''
      : '',
    models: ['all|All models', ...page.modelNames.map((m) => `${m}|${m}`)],
    empty: page.kind === 'empty' ? ['No model matches the selected filters.'] : [],
    sections: [],
  }
  const link = (label: string, url: unknown) => [label, url || '', ''].join('|')
  const badge = (label: string, url: unknown, tone: string) => [label, url || '', tone].join('|')
  if (page.kind === 'summary') {
    out.table = {
      caption: 'Select a model row to open its detailed performance or accuracy histories',
      head: ['Model', 'Hardware', 'Nightlies', 'Performance metrics', 'Accuracy metrics', 'Regressions', 'Improvements', 'Latest build'],
      rows: page.rows.map((row) => [
        link(row.model.model, ''),
        link(v2.perfHardwareLabel(row.model), ''),
        link(integer(row.model.nightly_count), ''),
        link(integer(row.performanceMetrics), ''),
        link(integer(row.accuracyMetrics), ''),
        badge(integer(row.regressions), (row.model.latest || {}).build_url, row.regressions ? 'hard' : 'pass'),
        badge(integer(row.improvements), (row.model.latest || {}).build_url, row.improvements ? 'pass' : 'neutral'),
        link(v2.perfLatestBuildLabel(row.model), (row.model.latest || {}).build_url),
      ]),
    }
  } else if (page.kind === 'accuracy') {
    out.table = page.rows.length
      ? {
          caption: `${integer(page.rows.length)} AMD accuracy task metrics`,
          head: ['Model', 'Task', 'Metric', 'Latest', 'vs previous', 'Status', 'Latest build', 'History'],
          rows: page.rows.map(({ model, task }) => [
            link(model.model, ''),
            link(task.task, ''),
            link(task.metric, ''),
            link(v2.perfValue(task.latest, 'ratio'), ''),
            link(v2.perfDelta(task).label, ''),
            badge(v2.accuracyStatusLabel(task.status), (model.latest || {}).build_url, v2.perfStatusTone(task.status)),
            link(v2.perfLatestBuildLabel(model), (model.latest || {}).build_url),
            link(`${integer((task.series || []).length)} points`, ''),
          ]),
        }
      : undefined
    if (!page.rows.length) out.empty = [`${integer(0)} AMD accuracy task metrics. No matching observations.`]
  } else if (page.kind === 'performance') {
    out.sections = page.sections.map((s) => ({
      title: s.model.model,
      devices: s.devices,
      provenance: [
        s.provenance.commit ? `${s.provenance.commit.label}|${s.provenance.commit.url}` : null,
        s.provenance.build ? `${s.provenance.build.label}|${s.provenance.build.url || ''}` : null,
        s.provenance.date ? `${s.provenance.date}|` : null,
        s.provenance.image ? `${s.provenance.image}|` : null,
      ].filter(Boolean),
      nightlies: s.nightlies,
      empty: s.configs.length ? [] : ['No performance configuration matches this hardware filter.'],
      configs: s.configs.map((c) => ({
        label: c.label,
        meta: c.meta,
        tiles: c.tiles.map((t) => ({ tone: t.tone, label: t.label, delta: t.delta, value: t.value, direction: t.direction })),
      })),
    }))
    out.empty = out.sections.flatMap((s) => s.empty)
  }
  return out
}

function classicHistory(model: Rec, config: Rec, metricName: string, block: Rec) {
  classic.openPerfHistory!(model, config, metricName, block)
  const overlay = document.body.querySelector('.ops-overlay:not([hidden])')!
  const out = {
    title: text(overlay.querySelector('.ops-overlay-title')),
    subtitle: text(overlay.querySelector('.ops-overlay-subtitle')),
    note: text(overlay.querySelector('.ops-evidence-note')),
    summary: all(overlay, '.ops-evidence-summary > *').map((item) => `${text(item.querySelector('.ops-stat-label'))} ${text(item.querySelector('.ops-stat-value'))}|${(item.className.match(/is-(success|danger)/) || [''])[0]}`),
    caption: text(overlay.querySelector('caption')),
    rows: all(overlay, 'tbody tr').map((tr) =>
      all(tr, 'td').map((td) => `${text(td)}|${td.querySelector('a')?.getAttribute('href') || ''}`),
    ),
  }
  classic.closeOverlay!()
  return out
}

// The visible classic overlay (the newest one when drawers stack).
function visibleOverlay() {
  const open = all(document.body, '.ops-overlay:not([hidden])')
  return open[open.length - 1]!
}

function classicDrawer() {
  const overlay = visibleOverlay()
  return {
    title: text(overlay.querySelector('.ops-overlay-title')),
    subtitle: text(overlay.querySelector('.ops-overlay-subtitle')),
    description: text(overlay.querySelector('.ops-detail-description')) || undefined,
    fields: all(overlay, '.ops-detail-field').map((f) => ({ label: text(f.querySelector('dt')), value: text(f.querySelector('dd')) })),
    sources: all(overlay, '.ops-source-actions a').map((a) => ({ label: text(a), url: a.getAttribute('href') })),
  }
}

function portDrawer(d: v2.PerfDetail) {
  return {
    title: d.title,
    subtitle: d.subtitle,
    description: d.description,
    fields: d.fields.map((f) => ({ label: f.label, value: f.value.replace(/\s+/g, ' ').trim() })),
    sources: d.sources.map((src) => ({ label: src.label, url: src.url })),
  }
}

// Clicks every image button of a classic history overlay and compares each drawer.
function compareImageDrawers(model: Rec, config: Rec, metricName: string, block: Rec) {
  classic.openPerfHistory!(model, config, metricName, block)
  const history = visibleOverlay()
  const buttons = all(history, 'tbody tr td:last-child button') as HTMLButtonElement[]
  const series = v2.perfHistory(model, config, metricName, block).series
  parity('imageButtons', series.length, buttons.length)
  buttons.forEach((b, i) => {
    b.click()
    parity('imageDetail', portDrawer(v2.perfImageDetail(series[i]!)), classicDrawer())
  })
  classic.closeOverlay!()
  return buttons.length
}

// classic drawPerfSpark -> drawChart inspectIndex for every point.
function comparePointDrawers(series: Rec[], unit: unknown) {
  lastChart = null
  const canvas = document.createElement('canvas')
  document.body.append(canvas)
  classic.drawPerfSpark!('perf-test-' + Math.random(), canvas, series, 'good', unit)
  const rows = v2.perfSparkRows(series)
  if (rows.length < 2) {
    expect(lastChart).toBeNull()
    canvas.remove()
    return 0
  }
  const onClick = (lastChart!.options as Rec).onClick as (e: unknown, els: { index: number }[]) => void
  rows.forEach((point, index) => {
    onClick(null, [{ index }])
    parity('pointDetail', portDrawer(v2.perfPointDetail(point, unit)), classicDrawer())
    classic.closeOverlay!()
  })
  canvas.remove()
  return rows.length
}

function portHistory(model: Rec, config: Rec, metricName: string, block: Rec) {
  const h = v2.perfHistory(model, config, metricName, block)
  return {
    title: h.title,
    subtitle: h.subtitle,
    note: `${h.name}${h.directionNote}`,
    summary: [
      `LATEST ${h.latest}|`,
      `PREVIOUS ${h.previous}|`,
      `CHANGE ${h.change}|${h.changeTone === 'neutral' ? '' : h.changeTone}`,
      `POINTS ${h.points}|`,
    ],
    caption: h.series.length ? h.tableTitle : '',
    rows: h.series.map((row) => [
      `${v2.perfHistoryBuildLabel(row)}|${row.build_url || ''}`,
      `${shortDate(row.date)}|`,
      `${v2.perfValue(row.value, block.unit)}|`,
      row.vllm_commit ? `${String(row.vllm_commit).slice(0, 7)}|${v2.commitUrl(row.vllm_commit)}` : '-|',
      `${value(row.image)}|`,
    ]),
  }
}

beforeAll(() => {
  classic = loadClassic([...PORTED]) as typeof classic
  // Sparklines are drawn by Chart.js; a no-op stand-in keeps them off the network.
  ;(window as unknown as Rec).Chart = class {
    canvas: unknown
    constructor(canvas: unknown, config: Rec) {
      this.canvas = canvas
      lastChart = config
    }
    destroy() {}
    resize() {}
  }
})

const STATES = (data: Rec): v2.PerfState[] => {
  const models = ['all', ...v2.perfModels(data).map((m) => m.model), 'missing/model']
  const devices = ['all', ...v2.perfDevices(data).map((d) => d.id), 'mi250']
  return models.flatMap((model) => devices.flatMap((device) => (['performance', 'accuracy'] as const).map((view) => ({ view, model, device }))))
}

async function expectSame(data: Rec, state: v2.PerfState) {
  const a = await renderClassic(data, state)
  const b = renderPort(data, state)
  parity('page', b, a, JSON.stringify(state))
  return a
}

describe('Perf Eval matches the classic renderer', () => {
  it('renders every model, view, and hardware filter of the real snapshot identically', async () => {
    let tiles = 0
    let rows = 0
    for (const state of STATES(perf)) {
      const r = await expectSame(perf, state)
      tiles += (r.sections || []).flatMap((s) => s.configs.flatMap((c: Rec) => c.tiles)).length
      rows += r.table?.rows.length ?? 0
    }
    expect(tiles).toBeGreaterThan(100)
    expect(rows).toBeGreaterThan(20)
    await expectGoldenFile('perf', 'summary', renderPort(perf, { view: 'performance', model: 'all', device: 'all' }))
  })

  it('formats values and deltas as classic does', () => {
    const rand = rng(5711)
    const UNITS = ['ratio', 's', 'tok/s', '', undefined, 'ms']
    for (let i = 0; i < 4000; i += 1) {
      const magnitude = 10 ** Math.floor(rand() * 9 - 4)
      const raw = [rand() * magnitude * (rand() < 0.3 ? -1 : 1), String(rand() * 100), null, undefined, 'n/a', 0, 1, 999.96, 1000, 0.9995][i % 10]
      const unit = UNITS[Math.floor(rand() * UNITS.length)]
      parity('perfValue', v2.perfValue(raw, unit), classic.perfValue!(raw, unit))
      const block: Rec = {
        previous: [null, undefined, 1, 0][Math.floor(rand() * 4)],
        delta_pct: [rand() * 20 - 10, 'x', null, 0, -0.04, 0.05][Math.floor(rand() * 6)],
        status: ['good', 'bad', 'neutral', undefined][Math.floor(rand() * 4)],
      }
      const el = classic.perfDelta!(block)
      parity('perfDelta', v2.perfDelta(block), {
        label: el.textContent,
        tone: (el.className.match(/is-(success|danger|neutral)/) || [])[0],
        title: el.getAttribute('title'),
      })
    }
  })

  it('agrees on generated snapshots, guards, and filters', async () => {
    const rand = rng(1824)
    const pick = <T,>(xs: T[]) => xs[Math.floor(rand() * xs.length)]!
    const DEVICES = ['mi300x', 'mi355x', 'MI325X', 'mi300a', 'mi300b', 'mi2500', 'mi250', 'h100', '']
    const STATUSES = ['good', 'bad', 'neutral', undefined]
    const METRICS = [...v2.PREFERRED_METRICS, 'median_ttft', 'p99_tpot', 'a_metric', 'zz']
    let unavailable = 0
    let available = 0
    for (let i = 0; i < 160; i += 1) {
      const data = structuredClone(perf)
      for (const model of data.models as Rec[]) {
        if (rand() < 0.15 && Array.isArray(model.devices)) model.devices = model.devices.map((d: string) => d.toUpperCase())
        if (rand() < 0.05) model.devices = pick([[], undefined, ['h100'], ['mi300x', 'mi250']])
        if (rand() < 0.2) model.latest = pick([{}, { build_number: 0, build_url: 'u' }, { vllm_commit: 'abc' }, { date: 'bad-date' }])
        if (rand() < 0.1) delete model.nightly_count
        for (const config of model.perf_configs as Rec[]) {
          if (rand() < 0.15) config.device = String(config.device).toUpperCase()
          if (rand() < 0.03) config.device = pick(DEVICES)
          if (rand() < 0.02) config.no_gpu = true
          if (rand() < 0.1) delete config.label
          if (rand() < 0.1) config.tp = pick([null, '', 4])
          if (rand() < 0.3) {
            const names = Object.keys(config.metrics)
            const moved = config.metrics[pick(names)]
            config.metrics[pick(METRICS)] = { ...moved, status: pick(STATUSES), label: rand() < 0.3 ? undefined : moved.label }
          }
          for (const block of Object.values(config.metrics) as Rec[]) {
            if (rand() < 0.1) block.status = pick(STATUSES)
            if (rand() < 0.05) block.previous = null
            if (rand() < 0.05) block.delta_pct = null
            if (rand() < 0.05) block.unit = pick(['s', 'tok/s', 'ratio', undefined])
          }
        }
        for (const task of model.accuracy_tasks as Rec[]) {
          if (rand() < 0.3) task.status = pick(STATUSES)
          if (rand() < 0.2) delete task.direction
        }
        if (rand() < 0.2) model.accuracy_tasks = rand() < 0.5 ? [] : undefined
      }
      if (rand() < 0.1) data.models.reverse()
      if (rand() < 0.1) data.models = data.models.slice(0, 2)
      if (rand() < 0.05) data.summary.amd_devices = pick([['mi300x'], [], undefined, ['mi300x', 'h100']])
      if (rand() < 0.1) delete data.summary.models
      if (rand() < 0.05) data.summary = undefined
      if (rand() < 0.05) delete data.pipeline
      if (rand() < 0.05) delete data.generated_at
      const state: v2.PerfState = {
        view: pick(['performance', 'accuracy'] as const),
        model: pick(['all', 'all', ...(data.models as Rec[]).map((m) => m.model), 'nope']),
        device: pick(['all', 'all', 'mi300x', 'mi355x', 'mi325x', 'zzz']),
      }
      const r = await expectSame(data, state)
      if (r.unavailable) unavailable += 1
      else available += 1
    }
    expect(unavailable).toBeGreaterThan(5)
    expect(available).toBeGreaterThan(50)
  })

  it('agrees on provenance and device edge cases', async () => {
    const LATEST = [{}, { build_number: 0, build_url: 'u' }, { build_number: null }, { build_number: 7 }, { vllm_commit: 'abc' }, { date: 'bad-date', image: '' }]
    for (const latest of LATEST) {
      const data = structuredClone(perf)
      data.models[0].latest = latest
      await expectSame(data, { view: 'performance', model: data.models[0].model, device: 'all' })
      await expectSame(data, { view: 'accuracy', model: data.models[0].model, device: 'all' })
      await expectSame(data, { view: 'performance', model: 'all', device: 'all' })
    }
    for (const device of ['mi300b', 'MI300X', 'mi3000x', 'mi30x', 'mi355xa', 'h100', ' mi300x']) {
      for (const where of ['model', 'config', 'summary'] as const) {
        const data = structuredClone(perf)
        if (where === 'model') data.models[1].devices = [device]
        else if (where === 'config') data.models[1].perf_configs[0].device = device
        else data.summary.amd_devices = ['mi300x', device]
        const r = await expectSame(data, { view: 'performance', model: data.models[1].model, device: 'all' })
        expect(v2.miDevice(device)).toBe(!r.unavailable)
      }
    }
  })

  it('opens the same metric and accuracy histories', () => {
    const rand = rng(1863)
    let points = 0
    for (const model of perf.models as Rec[]) {
      for (const config of model.perf_configs as Rec[]) {
        for (const name of v2.sortedMetricNames(config.metrics)) {
          const block = config.metrics[name]
          parity('history', portHistory(model, config, name, block), classicHistory(model, config, name, block))
          points += block.series.length
        }
      }
      for (const { task } of v2.accuracyRows([model])) {
        const block = v2.accuracyBlock(task)
        parity(
          'accuracyHistory',
          portHistory(model, { label: 'Accuracy' }, task.metric, block),
          classicHistory(model, { label: 'Accuracy' }, task.metric, block),
        )
      }
    }
    expect(points).toBeGreaterThan(200)
    // Shuffled, undated, and partial series.
    for (let i = 0; i < 200; i += 1) {
      const series = Array.from({ length: Math.floor(rand() * 6) }, (_, j) => ({
        date: rand() < 0.15 ? undefined : `2026-0${1 + Math.floor(rand() * 9)}-1${j}T00:00:00Z`,
        value: rand() < 0.1 ? 'x' : rand() * 3,
        build_number: rand() < 0.2 ? 0 : 100 + j,
        build_url: rand() < 0.2 ? undefined : `https://buildkite.com/vllm/perf-eval/builds/${100 + j}`,
        vllm_commit: rand() < 0.2 ? '' : `c0ffee${j}abcdef`,
        image: rand() < 0.2 ? undefined : `img:${j}`,
      }))
      const block: Rec = {
        series,
        unit: ['s', 'ratio', 'tok/s', undefined][i % 4],
        latest: rand(),
        previous: rand() < 0.2 ? null : rand(),
        delta_pct: rand() * 10 - 5,
        status: ['good', 'bad', 'neutral'][i % 3],
        direction: ['lower', 'higher', undefined][i % 3],
        label: rand() < 0.5 ? undefined : 'Lbl',
      }
      const config = rand() < 0.3 ? {} : { label: 'Cfg' }
      parity('generatedHistory', portHistory({ model: 'm' }, config, 'metric_x', block), classicHistory({ model: 'm' }, config, 'metric_x', block))
    }
  })

  it('builds the same accuracy rows and history blocks', () => {
    parity('accuracyRows', v2.accuracyRows(perf.models), classic.accuracyRows!(perf.models))
    expect(v2.accuracyRows(perf.models).length).toBeGreaterThan(0)
  })

  it('opens the same accuracy history from every classic accuracy table cell', async () => {
    // Classic builds the accuracy block inline in each cell's click handler; click them all,
    // including tasks without direction, label, or status.
    const rand = rng(5822)
    const variants = [perf, ...Array.from({ length: 6 }, () => {
      const data = structuredClone(perf)
      for (const model of data.models as Rec[])
        for (const task of (model.accuracy_tasks || []) as Rec[]) {
          if (rand() < 0.5) delete task.direction
          else if (rand() < 0.3) task.direction = 'lower'
          if (rand() < 0.2) delete task.label
          if (rand() < 0.2) task.status = ['good', 'bad', undefined][Math.floor(rand() * 3)]
          if (rand() < 0.1) task.unit = 's'
        }
      return data
    })]
    let clicks = 0
    let undirected = 0
    for (const data of variants) {
      classic.cache.set(v2.PERF_SOURCE, Promise.resolve(data))
      classic.state.perfView = 'accuracy'
      classic.state.perfDevice = 'all'
      for (const model of data.models as Rec[]) {
        classic.state.perfModel = model.model
        const host = document.createElement('div')
        await classic.renderPerf!(host, { generated_at: '2026-09-01T00:00:00Z' })
        const page = v2.perfPage(data, { view: 'accuracy', model: model.model, device: 'all' })
        const rows = page.kind === 'accuracy' ? page.rows : []
        const trs = all(host, 'tbody tr')
        parity('accuracyRowCount', rows.length, trs.length)
        trs.forEach((tr, i) => {
          const row = rows[i]!
          if (!row.task.direction) undirected += 1
          const expected = portHistory(row.model, { label: 'Accuracy' }, row.task.metric, v2.accuracyBlock(row.task))
          for (const button of all(tr, 'button') as HTMLButtonElement[]) {
            button.click()
            const overlay = visibleOverlay()
            const got = {
              title: text(overlay.querySelector('.ops-overlay-title')),
              subtitle: text(overlay.querySelector('.ops-overlay-subtitle')),
              note: text(overlay.querySelector('.ops-evidence-note')),
              summary: all(overlay, '.ops-evidence-summary > *').map((item) => `${text(item.querySelector('.ops-stat-label'))} ${text(item.querySelector('.ops-stat-value'))}|${(item.className.match(/is-(success|danger)/) || [''])[0]}`),
              caption: text(overlay.querySelector('caption')),
              rows: all(overlay, 'tbody tr').map((r) => all(r, 'td').map((td) => `${text(td)}|${td.querySelector('a')?.getAttribute('href') || ''}`)),
            }
            parity('accuracyCellHistory', expected, got)
            classic.closeOverlay!()
            clicks += 1
          }
        })
      }
    }
    expect(clicks).toBeGreaterThan(50)
    expect(undirected).toBeGreaterThan(5)
  })

  it('opens the same model, image, and chart-point drawers as classic', async () => {
    const LATEST = [undefined, {}, { build_number: 0, build_url: 'u' }, { vllm_commit: 'abcdef0123456789' }, { image: '' }, { build_url: 'https://b/1', image: 'img:1' }]
    let models = 0
    for (const latest of LATEST) {
      const data = structuredClone(perf)
      for (const model of data.models as Rec[]) {
        if (latest !== undefined) model.latest = latest
        if (latest && 'image' in latest) delete model.nightly_count
        classic.cache.set(v2.PERF_SOURCE, Promise.resolve(data))
        classic.state.perfView = 'performance'
        classic.state.perfDevice = 'all'
        classic.state.perfModel = model.model
        const host = document.createElement('div')
        await classic.renderPerf!(host, { generated_at: '2026-09-01T00:00:00Z' })
        ;(host.querySelector('.ops-perf-model-title button') as HTMLButtonElement).click()
        parity('modelDetail', portDrawer(v2.perfModelDetail(model)), classicDrawer())
        classic.closeOverlay!()
        models += 1
      }
    }
    expect(models).toBeGreaterThan(10)

    let images = 0
    let points = 0
    for (const model of perf.models as Rec[]) {
      for (const config of model.perf_configs as Rec[]) {
        for (const name of v2.sortedMetricNames(config.metrics)) {
          images += compareImageDrawers(model, config, name, config.metrics[name])
          points += comparePointDrawers(config.metrics[name].series, config.metrics[name].unit)
        }
      }
    }
    const rand = rng(1406)
    for (let i = 0; i < 120; i += 1) {
      const series = Array.from({ length: Math.floor(rand() * 5) }, (_, j) => ({
        date: rand() < 0.2 ? undefined : rand() < 0.1 ? 'bad-date' : `2026-0${1 + Math.floor(rand() * 9)}-1${j}T00:00:00Z`,
        value: rand() < 0.15 ? 'x' : rand() * 3,
        build_number: [0, undefined, null, 100 + j, 7][Math.floor(rand() * 5)],
        build_url: rand() < 0.3 ? undefined : rand() < 0.1 ? '' : `https://buildkite.com/vllm/perf-eval/builds/${100 + j}`,
        vllm_commit: rand() < 0.3 ? ['', undefined, null][j % 3] : `c0ffee${j}abcdef`,
        image: rand() < 0.3 ? ['', undefined, null][j % 3] : `img:${j}`,
      }))
      const unit = ['s', 'ratio', 'tok/s', undefined][i % 4]
      images += compareImageDrawers({ model: 'm' }, { label: 'Cfg' }, 'x', { series, unit })
      points += comparePointDrawers(series, unit)
    }
    expect(images).toBeGreaterThan(200)
    expect(points).toBeGreaterThan(200)
  })

  it('reads route state like classic syncRouteState', () => {
    expect(v2.perfState({})).toEqual({ view: 'performance', model: 'all', device: 'all' })
    expect(v2.perfState({ view: 'accuracy', model: 'a/b', device: 'mi355x' })).toEqual({ view: 'accuracy', model: 'a/b', device: 'mi355x' })
    expect(v2.perfState({ view: 'bogus', model: '', device: '' })).toEqual({ view: 'performance', model: 'all', device: 'all' })
    expect(v2.perfQuery({ view: 'performance', model: 'all', device: 'all' })).toEqual({})
    expect(v2.perfQuery({ view: 'accuracy', model: 'a/b', device: 'mi300x' })).toEqual({ view: 'accuracy', model: 'a/b', device: 'mi300x' })
  })

  it('builds the drill-down drawers as classic does', () => {
    const model = perf.models[0]
    expect(v2.perfModelDetail(model)).toEqual({
      title: model.model,
      subtitle: 'Performance and evaluation model history',
      fields: [
        { label: 'Nightlies', value: integer(model.nightly_count) },
        { label: 'Hardware', value: model.devices.join(', ') },
        { label: 'Latest commit', value: model.latest.vllm_commit.slice(0, 12) },
        { label: 'Latest image', value: model.latest.image },
      ],
      sources: [
        { label: 'Open latest perf build', url: model.latest.build_url },
        { label: 'Open vLLM commit', url: `https://github.com/vllm-project/vllm/commit/${model.latest.vllm_commit}` },
      ],
    })
    expect(v2.perfModelFamilies(perf).map((m) => m.label)).toEqual((perf.models as Rec[]).map((m) => m.model).reverse())
    expect(v2.perfImageDetail({}).fields).toEqual([{ label: 'Image', value: '-' }])
  })
})
