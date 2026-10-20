import { beforeAll, describe, expect, it } from 'vitest'
import * as v2 from '@/domain/agentHealth'
import { integer, shortDate, type Rec } from '@/domain/format'
import { classicParity, loadClassic, type ClassicFns } from './classic'
import { pinClock, readFixture } from './support/fixtures'
import { expectGoldenFile } from './support/golden'
import { rng } from './support/rng'

const PURE = [
  'agentStateColor',
  'agentTruncate',
  'agentNodeLabel',
  'agentCofailLabel',
  'buildkiteJobUrl',
  'clusterNodeCofailures',
  'makeCofailEvent',
  'agentFailureAccountingCounts',
  'agentAggregateRunCounts',
  'agentAggregateFailureCounts',
  'agentSourceCoverage',
  'agentSourceHistoryComplete',
  'agentPipelineScopeLabel',
] as const

const fixture = readFixture<Rec>('agent_health_live.json')
pinClock(fixture.generated_at)
const parity = classicParity('agentHealth')
let classic: ClassicFns

beforeAll(() => {
  ;(globalThis as Rec).requestAnimationFrame = () => 0
  Element.prototype.scrollIntoView = () => {}
  classic = loadClassic([...PURE, 'renderAmdAgentHealth', 'state', 'closeOverlay'] as unknown as string[])
})

interface Selection {
  window?: string
  gpu?: string
  node?: string
  cofail?: string
  exclCancel?: string
  nightly?: string
  signal?: string
  search?: string
  /** Focus the n-th table node (classic focusNode) and expand the first event card. */
  focus?: number
}

const text = (el: Element | null | undefined) => (el ? (el.textContent ?? '') : null)

function classicDom(health: Rec, sel: Selection) {
  const state = classic.state as unknown as Rec
  Object.assign(state, {
    agentWindow: sel.window ?? '7d',
    agentGpu: sel.gpu ?? 'all',
    agentNode: sel.node ?? '',
    agentCofail: sel.cofail ?? '180',
    agentExclCancel: sel.exclCancel ?? '1',
    agentNightly: sel.nightly ?? '0',
    agentSignal: sel.signal ?? 'infra',
  })
  const host = document.createElement('div')
  document.body.append(host)
  classic.renderAmdAgentHealth!(host, health)
  const input = host.querySelector('.ops-agent-search') as HTMLInputElement | null
  if (sel.search && input) {
    input.value = sel.search
    input.dispatchEvent(new Event('input'))
  }
  return host
}

async function settle() {
  await new Promise((resolve) => setTimeout(resolve, 230))
}

/** Table rows in one canonical order: the page sorts its own table, so only the row contents are compared. */
const byContent = (rows: (string | null)[][]) => rows.slice().sort((a, b) => (a.join('|') < b.join('|') ? -1 : a.join('|') > b.join('|') ? 1 : 0))

/**
 * The classic renderer predates failure attribution: it calls the node signal "infra-suspect" with
 * the old rule's wording. Every count, row, and event stays comparable; only the renamed signal and
 * its rewritten captions (fixtures/agent_signal_renames.json, shared with the browser specs) differ.
 */
const CLASSIC_WORDING = readFixture<[string, string][]>('agent_signal_renames.json')
function classicWording<T>(value: T): T {
  if (typeof value === 'string') return CLASSIC_WORDING.reduce((text, [from, to]) => text.split(from).join(to), value as string) as T
  if (Array.isArray(value)) return value.map(classicWording) as T
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, classicWording(v)])) as T
  return value
}
/** Classic-only static notes: the page introduction and the retired infra-suspect criteria. */
const CLASSIC_STATIC_NOTE = /^[^|]*(\|AMD physical CI main agent health\.|ops-agent-criteria\|)/

function scrape(host: HTMLElement) {
  return classicWording(scrapeClassic(host))
}

function scrapeClassic(host: HTMLElement) {
  const tableRows = [...host.querySelectorAll('.ops-agent-table tbody tr')]
  const result = {
    notes: [...host.querySelectorAll(':scope > .ops-evidence-note, :scope > div > .ops-evidence-note')]
      .map((n) => `${n.className}|${n.textContent}`)
      .filter((note) => !CLASSIC_STATIC_NOTE.test(note)),
    criteriaHidden: (host.querySelector('.ops-agent-criteria') as HTMLElement | null)?.hidden ?? null,
    kpis: [...host.querySelectorAll('.ops-status-item')].map(
      (k) => `${text(k.querySelector('.ops-stat-label'))}|${text(k.querySelector('.ops-stat-value'))}|${text(k.querySelector('.ops-stat-meta'))}|${k.className}`,
    ),
    panelMeta: text(host.querySelector('.ops-agent-table')?.closest('.ops-panel')?.querySelector('.ops-panel-meta')),
    rows: byContent(tableRows.map((tr) => [...tr.querySelectorAll('td')].map((td) => td.textContent))),
    emptyTable: text(host.querySelector('.ops-panel .ops-empty')),
    caption: text(host.querySelector('.ops-agent-signal-caption')),
    eventsTitle: text(host.querySelector('.ops-cluster-section .ops-section-title')),
    eventsDescription: text(host.querySelector('.ops-cluster-section .ops-section-description')),
    eventsEmpty: text(host.querySelector('.ops-cluster-section .ops-evidence-note')),
    events: [...host.querySelectorAll('.ops-cofailure-card')].map((card) => ({
      selected: card.classList.contains('is-selected'),
      node: text(card.querySelector('.ops-cofailure-node')),
      meta: text(card.querySelector('.ops-cofailure-meta')),
      badges: [...card.querySelectorAll('.ops-cofailure-head .ops-badge')].map((b) => `${b.className}|${b.textContent}`),
      detail: text(card.querySelector('.ops-cofailure-detail')),
      runs: [...card.querySelectorAll('.ops-cofailure-run')].map((li) =>
        [...li.children].map((c) => (c.classList.contains('ops-state-chip') ? (c as HTMLElement).style.background : c.textContent)).join('|'),
      ),
      runLinks: [...card.querySelectorAll('.ops-cofailure-run a')].map((a) => a.getAttribute('href')),
      more: text(card.querySelector('.ops-cofailure-more')),
    })),
  }
  return result
}

function rgb(hex: string) {
  const n = parseInt(hex.slice(1), 16)
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`
}

function v2Scrape(health: Rec, sel: Selection, state: { eventNodeFilter: string; selectedEventKey: string }) {
  if (!v2.agentHealthScopeValid(health)) return { unavailable: v2.AGENT_UNAVAILABLE_TEXT }
  const model = v2.agentHealthModel(health)
  const options = v2.agentOptions(
    model,
    { window: sel.window, gpu: sel.gpu, node: sel.node, cofail: sel.cofail, exclCancel: sel.exclCancel, nightly: sel.nightly, signal: sel.signal },
    (sel.search ?? '').trim(),
  )
  const view = v2.computeAgentView(model, options)
  const notes: string[] = []
  if (model.completionScopeNote) notes.push('ops-evidence-note info ops-agent-completion-scope|' + model.completionScopeNote)
  if (model.retentionNote) notes.push('ops-evidence-note soft|' + model.retentionNote.title + model.retentionNote.body)
  const empty = v2.agentEmptyText(model, view)
  if (empty) notes.push('ops-evidence-note soft|' + empty)
  const rows = view.agents
  const showSignal = options.signal !== 'all'
  const section = v2.eventsSection(view, options, state.eventNodeFilter)
  return {
    notes,
    criteriaHidden: options.signal !== 'infra',
    kpis: v2.agentKpis(model, view, options).map(
      (k) => `${k.label}|${k.value}|${k.meta}|ops-status-item ops-linked-metric ${k.tone}`,
    ),
    panelMeta: v2.agentTableDescription(model, view, options),
    rows: byContent(rows.map((row) => {
      const cells = v2.agentCells(model, view, row)
      return [
        row.node || '-',
        row.hardware || '-',
        cells.runs,
        cells.failRate,
        cells.failures + (cells.split ?? ''),
        ...(showSignal ? [cells.signal] : []),
        cells.groups,
        cells.cofail,
        row._runs.length ? 'runs ↗' : 'not retained',
      ]
    })),
    emptyTable: rows.length ? null : 'Per-node AMD reliability in ' + options.windowId + '. No matching observations.',
    caption: v2.signalCaption(options.signal),
    eventsTitle: section.title,
    eventsDescription: section.description,
    eventsEmpty: section.events.length ? null : section.empty,
    events: section.events.map((event) => {
      const selected = v2.eventKey(event) === state.selectedEventKey
      const card = v2.cofailureCard(event, selected)
      return {
        selected,
        node: card.node,
        meta: card.meta,
        badges: [`ops-badge ${card.patternTone}|${card.pattern}`, ...(card.cross ? ['ops-badge hard|cross-pipeline'] : [])],
        detail: card.detail,
        runs: card.runs.map((r) => [rgb(r.color), r.run.group, r.meta, ...(r.time ? [r.time] : []), r.link].join('|')),
        runLinks: card.runs.filter((r) => r.run.url).map((r) => r.run.url),
        more: card.more,
      }
    }),
  }
}

function initialState() {
  return { eventNodeFilter: '', selectedEventKey: '' }
}

async function expectSame(health: Rec, sel: Selection) {
  const host = classicDom(health, sel)
  if (sel.search) await settle()
  const gate = host.querySelector(':scope > .ops-evidence-note.is-warning')
  if (!v2.agentHealthScopeValid(health)) {
    parity('unavailable', v2.AGENT_UNAVAILABLE_TEXT, text(gate))
    expect(host.querySelector('.ops-agent-controls')).toBeNull()
    host.remove()
    return null
  }
  const v2State = initialState()
  parity('view', v2Scrape(health, sel, v2State), scrape(host))
  if (sel.focus !== undefined) {
    const buttons = host.querySelectorAll('.ops-agent-table tbody tr td:first-child button')
    const button = buttons[sel.focus % Math.max(1, buttons.length)] as HTMLButtonElement | undefined
    if (button) {
      const label = button.textContent
      button.click()
      const model = v2.agentHealthModel(health)
      const options = v2.agentOptions(model, { ...sel }, (sel.search ?? '').trim())
      const view = v2.computeAgentView(model, options)
      const agent = view.agents.find((a) => (a.node || '-') === label)!
      v2State.eventNodeFilter = agent.node_raw
      parity('focusedView', v2Scrape(health, sel, v2State), scrape(host))
      const card = host.querySelector('.ops-cofailure-card .ops-cofailure-meta') as HTMLElement | null
      if (card) {
        card.click()
        const event = v2.eventsSection(view, options, v2State.eventNodeFilter).events[0]!
        v2State.selectedEventKey = v2.eventKey(event)
        parity('selectedEventView', v2Scrape(health, sel, v2State), scrape(host))
      }
      // The node evidence drawer (Evidence column) lists the retained runs newest first.
      const evidenceButton = [...host.querySelectorAll('.ops-agent-table tbody tr')]
        .find((tr) => tr.querySelector('td')?.textContent === label)
        ?.querySelector('td:last-child button') as HTMLButtonElement | undefined
      if (evidenceButton) {
        evidenceButton.click()
        const dialog = document.querySelector('[role="dialog"]')!
        const drawer = v2.nodeEvidence(agent, options)
        parity('nodeEvidenceTitle', drawer.title, classicWording(text(dialog.querySelector('.ops-overlay-title'))))
        parity('nodeEvidenceSubtitle', drawer.subtitle, classicWording(text(dialog.querySelector('.ops-overlay-subtitle'))))
        const classicRows = [...dialog.querySelectorAll('tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map((td) => td.textContent).join('|'))
        parity(
          'nodeEvidenceRows',
          drawer.rows.slice(0, 30).map((p) => [p.label, shortDate(p.timestamp), p.valueSummary, p.url ? 'Open source' : 'Inspect'].join('|')),
          classicRows,
        )
        expect(classicRows.length).toBe(Math.min(30, agent._runs.length))
        classic.closeOverlay!()
      }
    }
  }
  const scraped = scrape(host)
  host.remove()
  return scraped
}

const SELECTIONS: Selection[] = [
  {},
  { signal: 'hard' },
  { signal: 'all' },
  { window: '1d', focus: 0 },
  { window: '3d', cofail: '30', focus: 1 },
  { window: '14d', nightly: '1', exclCancel: '0', focus: 2 },
  { window: '30d', gpu: 'MI355', signal: 'all', cofail: '1440', focus: 0 },
  { window: '60d', gpu: 'MI300' },
  { gpu: 'MI999', node: 'crsuse2-m2m-160', cofail: '999', signal: 'bogus', window: '2d' },
  { search: 'crsuse2', focus: 0 },
  { search: 'mi300', signal: 'hard' },
  { search: 'zzz' },
]

describe('CI agent health domain matches the classic renderer', () => {
  it('renders the same notes, KPIs, table rows, and events on the real snapshot', async () => {
    let rows = 0
    let events = 0
    for (const sel of SELECTIONS) {
      const scraped = await expectSame(fixture, sel)
      rows += scraped!.rows.length
      events += scraped!.events.length
    }
    expect(rows).toBeGreaterThan(100)
    expect(events).toBeGreaterThan(10)
    const view = v2Scrape(fixture, {}, initialState()) as Rec
    await expectGoldenFile('agentHealth', 'default-view', { ...view, rows: view.rows.slice(0, 8), events: view.events.slice(0, 2) })
  }, 60_000)

  it('agrees on the classic helpers for the real snapshot', () => {
    parity('agentSourceCoverage', v2.agentSourceCoverage(fixture), classic.agentSourceCoverage!(fixture))
    parity('agentPipelineScopeLabel', v2.agentPipelineScopeLabel(fixture), classic.agentPipelineScopeLabel!(fixture))
    for (const day of [undefined, '2026-10-01', '2026-10-12', '2026-10-13', '2026-10-19', '2026-10-20', 'bad']) {
      parity('agentSourceHistoryComplete', v2.agentSourceHistoryComplete(fixture, day), classic.agentSourceHistoryComplete!(fixture, day))
    }
    for (const signal of ['infra', 'hard', 'all', 'x']) {
      for (const startDay of ['', '2026-10-12', '2026-10-18']) {
        for (const hardware of ['all', 'MI300', 'MI355']) {
          for (const flags of [
            [false, false],
            [true, true],
            [true, false],
          ]) {
            const options = { startDay, hardware, nightlyOnly: flags[0], excludeCancelled: flags[1], signal }
            parity(
              'agentAggregateRunCounts',
              v2.agentAggregateRunCounts(fixture.node_accounting_totals, options),
              classic.agentAggregateRunCounts!(fixture.node_accounting_totals, options),
            )
            parity(
              'agentAggregateFailureCounts',
              v2.agentAggregateFailureCounts(fixture.failure_accounting_totals, options),
              classic.agentAggregateFailureCounts!(fixture.failure_accounting_totals, options),
            )
            parity(
              'agentFailureAccountingCounts',
              [...v2.agentFailureAccountingCounts(fixture.failure_accounting, options)],
              [...classic.agentFailureAccountingCounts!(fixture.failure_accounting, options)],
            )
          }
        }
      }
    }
    for (const s of ['pass', 'PASSED', 'soft', 'hard', 'canceled', '', null]) parity('agentStateColor', v2.agentStateColor(s), classic.agentStateColor!(s))
    for (const m of [30, 60, 90, 1440, 0, 'x']) parity('agentCofailLabel', v2.agentCofailLabel(m), classic.agentCofailLabel!(m))
    for (const [p, b, j] of [
      ['ci', 1, 'abc'],
      ['ci', 0, ''],
      ['', 1, 'x'],
      ['ci', null, 'x'],
    ])
      parity('buildkiteJobUrl', v2.buildkiteJobUrl(p, b, j), classic.buildkiteJobUrl!(p, b, j))
    for (const t of ['short', 'x'.repeat(34), 'y'.repeat(35), '']) parity('agentTruncate', v2.agentTruncate(t, 34), classic.agentTruncate!(t, 34))
  })

  it('clusters generated failing runs into the same co-failure events', () => {
    const rand = rng(4242)
    const pick = <T,>(xs: T[]) => xs[Math.floor(rand() * xs.length)]!
    for (let i = 0; i < 400; i += 1) {
      const base = Date.parse('2026-10-10T00:00:00Z')
      const runs = Array.from({ length: Math.floor(rand() * 9) }, () => {
        const start = rand() < 0.1 ? null : base + Math.floor(rand() * 600) * 60000
        const end = start === null || rand() < 0.1 ? null : start + Math.floor(rand() * 90) * 60000 - (rand() < 0.1 ? 600000 : 0)
        return {
          pipeline: pick(['ci', 'amd-ci']),
          build_number: pick([1, 2, 3]),
          group: pick(['g1', 'g2', 'g3', 'g4']),
          state: pick(['hard', 'soft']),
          _start: start,
          _end: end,
          started_at: start === null ? '' : new Date(start).toISOString(),
        }
      })
      const mins = pick([30, 60, 120, 180])
      parity(
        'clusterNodeCofailures',
        v2.clusterNodeCofailures('n (MI300)', 'n', 'MI300', runs as unknown as v2.AgentRun[], mins),
        classic.clusterNodeCofailures!('n (MI300)', 'n', 'MI300', structuredClone(runs), mins),
      )
    }
  })

  it('agrees on generated variants: retention, scope, flags, and accounting paths', async () => {
    const rand = rng(777)
    const pick = <T,>(xs: T[]) => xs[Math.floor(rand() * xs.length)]!
    const mutations: ((h: Rec) => void)[] = [
      (h) => (h.operations_publication_retention.node_days.complete = false),
      (h) => (h.operations_publication_retention.failure_accounting.complete = false),
      (h) => (h.operations_publication_retention.aggregate_accounting_complete = false),
      (h) => (h.retention.failure_evidence.complete_relative_to_source = false),
      (h) => (h.operations_publication_retention.failure_evidence.complete = false),
      (h) => (h.failure_accounting = []),
      (h) => (h.node_days = []),
      (h) => (h.retention.byte_limited = true),
      (h) => Object.assign(h.retention, { dropped_oldest_day_count: 2, original_day_count: h.retention.retained_day_count + 2 }),
      (h) => (h.max_window_days = 30),
      (h) => (h.retention.pipeline_scope.collected_from = '2026-10-11T00:00:00Z'),
      (h) => (h.retention.pipeline_scope.basis = 'terminal_jobs_by_started_at'),
      (h) => (h.retention.pipeline_scope.branch = 'feature'),
      (h) => (h.branches = ['main', 'x']),
      (h) => (h.node_days[0].h = 'H100'),
      (h) => (h.failing_runs[0].q = 'gpu_1'),
      (h) => (h.hardware_types = ['MI300', 'H100']),
      (h) => (h.attribution_rules = { build_wide_min_failing_nodes: 7, node_repeat_min_groups: 0 }),
      (h) => h.failing_runs.forEach((row: Rec, i: number) => (row.a = [...v2.AGENT_ATTRIBUTIONS, 'bogus'][i % 6])),
      (h) => (h.cofailure_window_options = [60, 240]),
      (h) => (h.window_options = [1, 7]),
      (h) => h.node_days.forEach((row: Rec) => (row.a = row.a.slice(0, 3))),
      (h) =>
        h.failing_runs.forEach((row: Rec) => {
          row.i = rand() < 0.5 ? 1 : 0
          row.ng = rand() < 0.5
          row.bc = rand() < 0.2 ? 1 : 0
          row.s = pick(['hard', 'soft'])
        }),
      (h) => h.failing_runs.forEach((row: Rec) => (row.d = pick([row.d, '2026-10-19', '2026-10-12', 'bad', '2026-02-30']))),
      (h) => h.failing_runs.forEach((row: Rec) => (row.e = rand() < 0.3 ? 'bad' : row.e)),
      (h) => h.failure_accounting.forEach((row: Rec) => (row.c = pick([0, 1, 2, -1, '3']))),
      // Unidentified runs move node coverage across its 50% tone threshold.
      (h) => {
        const days = [...new Set(h.node_days.map((row: Rec) => row.d))] as string[]
        const identified = h.node_days.filter((row: Rec) => row.nd !== '(unidentified)')
        const share = 0.3 + rand() * 0.6
        for (const d of days) {
          const total = identified.filter((row: Rec) => row.d === d).reduce((sum: number, row: Rec) => sum + row.a[0], 0)
          const runs = Math.round((total * share) / (1 - share))
          h.node_days.push({ d, nd: '(unidentified)', h: 'MI300', a: [runs, 0, 1, 0], n: [Math.round(runs / 10), 0, 0, 0] })
        }
      },
    ]
    let compared = 0
    for (let i = 0; i < 70; i += 1) {
      const health = structuredClone(fixture)
      const applied = new Set<number>()
      for (let k = 0; k < 1 + Math.floor(rand() * 3); k += 1) applied.add(Math.floor(rand() * mutations.length))
      for (const index of applied) mutations[index]!(health)
      const sel: Selection = {
        window: pick(['1d', '3d', '7d', '14d', '30d', '60d']),
        signal: pick(['infra', 'hard', 'all']),
        nightly: pick(['0', '1']),
        exclCancel: pick(['0', '1']),
        cofail: pick(['30', '60', '180', '1440']),
        gpu: pick(['all', 'MI300', 'MI355', 'MI250']),
        search: rand() < 0.3 ? pick(['crsuse2', 'mi300', 'MI355', 'gpu']) : undefined,
        focus: rand() < 0.5 ? Math.floor(rand() * 4) : undefined,
      }
      if (await expectSame(health, sel)) compared += 1
    }
    expect(compared).toBeGreaterThan(35)
  }, 120_000)

  it('matches the classic smoke-test cohort facts', async () => {
    const base = structuredClone(fixture)
    const scope = {
      version: 3, branch: 'main', basis: 'terminal_jobs_by_build_created_at', requested_days: 7,
      collected_from: '2026-10-01T00:00:00Z', collected_to: '2026-10-08T20:00:00Z', exhaustive: true,
      discovery_legs: { created: true }, eligible_completion: 'current_ci_build_creation_cohort_with_provable_completion',
      day_basis: 'build_created_at_utc', attempt_policy: 'every_executed_attempt',
      terminal_time_policy: 'finished_at_or_terminal_build_bound_for_canceled', complete_window: false,
    }
    Object.assign(base, {
      pipelines: ['ci'], branches: ['main'], hardware_scope: 'amd_mi_gpu', generated_at: '2026-10-08T20:00:00Z', max_window_days: 60,
      node_days: [
        { d: '2026-10-07', nd: 'fixture-ci-node', h: 'MI300', a: [10, 0, 1, 0], n: [4, 0, 1, 0] },
        { d: '2026-10-06', nd: 'fixture-ci-node', h: 'MI300', a: [10, 0, 1, 0], n: [4, 0, 1, 0] },
      ],
      failure_accounting: [
        { d: '2026-10-07', nd: 'fixture-ci-node', h: 'MI300', s: 'hard', i: 1, ng: 1, bc: 0, c: 1 },
        { d: '2026-10-06', nd: 'fixture-ci-node', h: 'MI300', s: 'hard', i: 1, ng: 1, bc: 0, c: 1 },
      ],
      failing_runs: [
        { d: '2026-10-07', nd: 'fixture-ci-node', h: 'MI300', p: 'ci', q: 'amd_mi300_1', g: 'Created-window morning job', s: 'hard', i: 1, ng: 1, bc: 0, b: 30005, j: '10000000-0000-0000-0000-000000000001', t: '2026-10-07T01:00:00Z', e: '2026-10-07T01:10:00Z' },
        { d: '2026-10-06', nd: 'fixture-ci-node', h: 'MI300', p: 'ci', q: 'amd_mi300_1', g: 'Older-build recently started job', s: 'hard', i: 1, ng: 1, bc: 0, b: 30004, j: '10000000-0000-0000-0000-000000000002', t: '2026-10-08T10:00:00Z', e: '2026-10-08T10:10:00Z' },
      ],
      retention: { configured_days: 60, byte_limited: false, dropped_oldest_day_count: 0, original_day_count: 2, retained_day_count: 2, pipeline_scope: scope },
      operations_publication_retention: {
        node_days: { source: 2, published: 2, complete: true },
        failure_accounting: { source: 2, published: 2, complete: true },
        failure_evidence: { source: 2, published: 2, complete: true },
      },
    })
    const week = await expectSame(base, {})
    const row = week!.rows.find((r) => r[0] === 'fixture-ci-node (MI300)')!
    expect(row[2]).toBe('20')
    expect(row[3]).toBe('10.0%')
    expect(row[6]).toBe('2')
    const day = await expectSame(base, { window: '1d' })
    expect(day!.rows[0]![2]).toBe('10')
    expect(day!.rows[0]![6]).toBe('1')
    const nightly = await expectSame(base, { nightly: '1' })
    expect(nightly!.rows[0]![2]).toBe('8')
    expect(nightly!.rows[0]![3]).toBe('25.0%')
    const sixty = await expectSame(base, { window: '60d' })
    expect(sixty!.rows[0]![3]).toBe('Unavailable')
    await expectSame({ ...base, branches: undefined }, {})

    // Cancelled runs, unidentified coverage near the 50% tone threshold, and exact aggregate totals.
    const rich = structuredClone(base)
    rich.node_days = [
      { d: '2026-10-07', nd: 'fixture-ci-node', h: 'MI300', a: [10, 1, 1, 3], n: [4, 0, 1, 1] },
      { d: '2026-10-07', nd: 'other-node', h: 'MI355', a: [12, 0, 2, 0], n: [2, 0, 0, 0] },
      { d: '2026-10-07', nd: '(unidentified)', h: 'MI300', a: [26, 0, 0, 0], n: [3, 0, 0, 0] },
      { d: '2026-10-07', nd: 'Zeta-Node', h: 'MI300', a: [0, 0, 0, 0], n: [0, 0, 0, 0] },
      { d: '2026-10-06', nd: 'Zeta-Node', h: 'MI300', a: [0, 0, 0, 0], n: [0, 0, 0, 0] },
    ]
    rich.node_accounting_totals = [
      { d: '2026-10-07', h: 'MI300', id: true, a: [10, 1, 1, 3], n: [4, 0, 1, 1] },
      { d: '2026-10-07', h: 'MI355', id: true, a: [12, 0, 2, 0], n: [2, 0, 0, 0] },
      { d: '2026-10-07', h: 'MI300', id: false, a: [26, 0, 0, 0], n: [3, 0, 0, 0] },
    ]
    rich.failure_accounting_totals = [{ d: '2026-10-07', h: 'MI300', s: 'hard', i: 1, ng: 1, bc: 0, c: 1 }]
    rich.operations_publication_retention.aggregate_accounting_complete = true
    rich.node_days[3].a = [2, 0, 0, 0]
    await expectSame(rich, {})
    const cancelled = await expectSame(rich, { exclCancel: '0' })
    const excluded = await expectSame(rich, {})
    expect(cancelled!.rows).not.toEqual(excluded!.rows)
    expect(excluded!.kpis[2]).toContain('48.0%')
    rich.operations_publication_retention.node_days.complete = false
    const exact = await expectSame(rich, {})
    const searched = await expectSame(rich, { search: 'node' })
    expect(exact!.kpis).not.toEqual(searched!.kpis)
    expect(integer(1)).toBe('1')
  }, 60_000)
})

describe('CI agent health helpers on hand-written inputs', () => {
  it('labels co-failure windows, truncates, and links exact jobs', () => {
    expect([30, 60, 90, 1440, 'x'].map((m) => v2.agentCofailLabel(m))).toEqual(['30m', '1h', '90m', '24h', '0h'])
    expect(v2.agentTruncate('x'.repeat(35), 34)).toBe(`${'x'.repeat(33)}…`)
    expect([v2.buildkiteJobUrl('ci', 1, 'abc'), v2.buildkiteJobUrl('ci', 0, ''), v2.buildkiteJobUrl('', 1, 'x')]).toEqual([
      'https://buildkite.com/vllm/ci/builds/1/steps/canvas?jid=abc&tab=output',
      'https://buildkite.com/vllm/ci/builds/0',
      '',
    ])
  })

  it('clusters overlapping failures into one concurrent event', () => {
    const runs = [
      { pipeline: 'ci', build_number: 1, group: 'g1', state: 'hard', _start: 0, _end: 600_000, started_at: '1970-01-01T00:00:00.000Z' },
      { pipeline: 'ci', build_number: 1, group: 'g2', state: 'soft', _start: 300_000, _end: 900_000, started_at: '1970-01-01T00:05:00.000Z' },
      { pipeline: 'ci', build_number: 2, group: 'g3', state: 'hard', _start: 36_000_000, _end: 36_600_000, started_at: '1970-01-01T10:00:00.000Z' },
    ]
    const events = v2.clusterNodeCofailures('n (MI300)', 'n', 'MI300', runs as unknown as v2.AgentRun[], 30)
    expect(events).toHaveLength(1)
    expect(events[0]).toMatchObject({ pattern: 'concurrent', span_mins: 15, run_count: 2, group_count: 2, hard_failed: 1, soft_failed: 1, cross_pipeline: false })
  })

  it('colors states and labels nodes', () => {
    expect(['passed', 'soft', 'hard', 'canceled', ''].map((s) => v2.agentStateColor(s))).toEqual(['#35bb78', '#e3a63a', '#e06464', '#8a93a0', '#66717d'])
    expect([v2.agentNodeLabel('n1', 'MI300'), v2.agentNodeLabel('n1', '')]).toEqual(['n1 (MI300)', 'n1'])
  })
})
