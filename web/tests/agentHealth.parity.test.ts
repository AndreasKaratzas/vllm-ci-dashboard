import { beforeAll, describe, expect, it } from 'vitest'
import * as v2 from '@/domain/agentHealth'
import { integer, shortDate, type Rec } from '@/domain/format'
import { loadClassic, readFixture, type ClassicFns } from './classic'

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

let classic: ClassicFns
let fixture: Rec

function rng(seed: number) {
  let s = seed >>> 0
  return () => (s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32
}

beforeAll(() => {
  ;(globalThis as Rec).requestAnimationFrame = () => 0
  Element.prototype.scrollIntoView = () => {}
  classic = loadClassic([...PURE, 'renderAmdAgentHealth', 'state', 'closeOverlay'] as unknown as string[])
  fixture = readFixture<Rec>('agent_health_live.json')
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
  /** Header labels clicked in order (classic onSort). */
  sorts?: string[]
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

function scrape(host: HTMLElement) {
  const tableRows = [...host.querySelectorAll('.ops-agent-table tbody tr')]
  const result = {
    notes: [...host.querySelectorAll(':scope > .ops-evidence-note, :scope > div > .ops-evidence-note')].map((n) => `${n.className}|${n.textContent}`),
    criteriaHidden: (host.querySelector('.ops-agent-criteria') as HTMLElement | null)?.hidden ?? null,
    kpis: [...host.querySelectorAll('.ops-status-item')].map(
      (k) => `${text(k.querySelector('.ops-stat-label'))}|${text(k.querySelector('.ops-stat-value'))}|${text(k.querySelector('.ops-stat-meta'))}|${k.className}`,
    ),
    panelMeta: text(host.querySelector('.ops-agent-table')?.closest('.ops-panel')?.querySelector('.ops-panel-meta')),
    headers: [...host.querySelectorAll('.ops-agent-table thead th')].map((th) => th.textContent),
    rows: tableRows.map((tr) => [...tr.querySelectorAll('td')].map((td) => td.textContent)),
    emptyTable: text(host.querySelector('.ops-panel .ops-empty')),
    nodeOptions: [...host.querySelectorAll('.ops-agent-toolbar select option')].map((o) => `${(o as HTMLOptionElement).value}|${o.textContent}`),
    selectedNode: (host.querySelector('.ops-agent-toolbar select') as HTMLSelectElement).value,
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

function v2Scrape(health: Rec, sel: Selection, state: { sort: v2.AgentSort; sortExplicit: boolean; eventNodeFilter: string; selectedEventKey: string; selectedNode?: string }) {
  if (!v2.agentHealthScopeValid(health)) return { unavailable: v2.AGENT_UNAVAILABLE_TEXT }
  const model = v2.agentHealthModel(health)
  const options = v2.agentOptions(
    model,
    { window: sel.window, gpu: sel.gpu, node: sel.node, cofail: sel.cofail, exclCancel: sel.exclCancel, nightly: sel.nightly, signal: sel.signal },
    (sel.search ?? '').trim(),
  )
  const view = v2.computeAgentView(model, options)
  const notes = [
    'ops-evidence-note is-info|AMD physical CI main agent health. Terminal runs from CI main builds created in the selected UTC window on AMD MI GPU hardware are attributed to the physical node from its Buildkite agent tag. The timeline and co-failure clustering use the selected Failure signal: "infra-suspect" (the default — anomalous, node-attributable failures), all hard failures, or all failures. Toggle the signal, nightly subset, cancelled-job handling, and co-failure window; click any node or event to load its timeline.',
  ]
  if (model.completionScopeNote) notes.push('ops-evidence-note is-info ops-agent-completion-scope|' + model.completionScopeNote)
  if (model.retentionNote) notes.push('ops-evidence-note is-warning|' + model.retentionNote.title + model.retentionNote.body)
  const c = model.criteria
  notes.push(
    'ops-evidence-note is-neutral ops-agent-criteria|What counts as an anomalous (infra-suspect) failure? When the Failure signal is set to Infra-suspect (the default), a soft or hard failure is treated as node-attributable when its exact test group, on that same ' +
      c.dayLabel +
      ', both: passed at least ' +
      c.pct +
      ' of its gradable runs (needs ≥ ' +
      c.samples +
      ' graded samples), so the group demonstrably works; andpassed on at least one other physical node that day, so the failure is isolated to this box rather than a broad code break.Groups that fail broadly (real code bugs) and failures inside auto-cancelled / superseded builds are filtered out by default, isolating the signal that points at a specific machine.',
  )
  const empty = v2.agentEmptyText(model, view)
  if (empty) notes.push('ops-evidence-note is-warning|' + empty)
  const sort = v2.effectiveAgentSort(state.sort, state.sortExplicit, options.signal)
  const rows = v2.sortedAgents(view.agents, sort)
  const showSignal = options.signal !== 'all'
  const arrow = (key: string) => (sort.key === key ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : '')
  const headers = [
    'Physical node' + arrow('node'),
    'GPU' + arrow('hardware'),
    'Runs' + arrow('runs'),
    'Fail %' + arrow('incident_rate'),
    'Test group failures' + arrow('failures'),
    ...(showSignal ? [v2.signalColumnLabel(options.signal) + arrow('infra_suspect')] : []),
    'Groups' + arrow('distinct_groups'),
    'Co-fail' + arrow('cofailure_event_count'),
    'Evidence',
  ]
  const selectedNode = v2.resolveSelectedNode(view, state.selectedNode ?? options.selectedNode)
  const section = v2.eventsSection(view, options, state.eventNodeFilter)
  return {
    notes,
    criteriaHidden: options.signal !== 'infra',
    kpis: v2.agentKpis(model, view, options).map(
      (k) => `${k.label}|${k.value}|${k.meta}|ops-status-item ops-linked-metric ${k.tone}`,
    ),
    panelMeta: v2.agentTableDescription(model, view, options),
    headers: rows.length ? headers : [],
    rows: rows.map((row) => {
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
    }),
    emptyTable: rows.length ? null : 'Per-node AMD reliability in ' + options.windowId + '. No matching observations.',
    nodeOptions: v2.timelineAgents(view).map((a) => `${a.node_raw}|${v2.nodeOptionLabel(a, options.signal)}`),
    selectedNode,
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
        badges: [`ops-badge ${card.patternTone}|${card.pattern}`, ...(card.cross ? ['ops-badge is-danger|cross-pipeline'] : [])],
        detail: card.detail,
        runs: card.runs.map((r) => [rgb(r.color), r.run.group, r.meta, ...(r.time ? [r.time] : []), r.link].join('|')),
        runLinks: card.runs.filter((r) => r.run.url).map((r) => r.run.url),
        more: card.more,
      }
    }),
  }
}

async function expectSame(health: Rec, sel: Selection) {
  const host = classicDom(health, sel)
  if (sel.search) await settle()
  const gate = host.querySelector(':scope > .ops-evidence-note.is-warning')
  if (!v2.agentHealthScopeValid(health)) {
    expect(text(gate)).toBe(v2.AGENT_UNAVAILABLE_TEXT)
    expect(host.querySelector('.ops-agent-controls')).toBeNull()
    host.remove()
    return null
  }
  const v2State = { sort: { key: 'infra_suspect', dir: 'desc' as 'asc' | 'desc' }, sortExplicit: false, eventNodeFilter: '', selectedEventKey: '', selectedNode: undefined as string | undefined }
  expect(v2Scrape(health, sel, v2State)).toEqual(scrape(host))
  for (const label of sel.sorts ?? []) {
    const button = host.querySelector(`.ops-agent-table button[aria-label="Sort by ${label}"]`) as HTMLButtonElement | null
    if (!button) continue
    button.click()
    const key = { 'Physical node': 'node', GPU: 'hardware', Runs: 'runs', 'Fail %': 'incident_rate', 'Test group failures': 'failures', 'Infra-suspect failures': 'infra_suspect', 'Hard failures': 'infra_suspect', Groups: 'distinct_groups', 'Co-fail': 'cofailure_event_count' }[label]!
    const options = v2.agentOptions(v2.agentHealthModel(health), { signal: sel.signal })
    v2State.sort = v2.nextAgentSort(v2.effectiveAgentSort(v2State.sort, v2State.sortExplicit, options.signal), key)
    v2State.sortExplicit = true
    expect(v2Scrape(health, sel, v2State)).toEqual(scrape(host))
  }
  if (sel.focus !== undefined) {
    const buttons = host.querySelectorAll('.ops-agent-table tbody tr td:first-child button')
    const button = buttons[sel.focus % Math.max(1, buttons.length)] as HTMLButtonElement | undefined
    if (button) {
      const label = button.textContent
      button.click()
      const model = v2.agentHealthModel(health)
      const options = v2.agentOptions(model, { ...sel }, (sel.search ?? '').trim())
      const view = v2.computeAgentView(model, options)
      const agent = v2.sortedAgents(view.agents, v2.effectiveAgentSort(v2State.sort, v2State.sortExplicit, options.signal)).find((a) => (a.node || '-') === label)!
      v2State.eventNodeFilter = agent.node_raw
      v2State.selectedNode = agent.node_raw
      expect(v2Scrape(health, sel, v2State)).toEqual(scrape(host))
      const card = host.querySelector('.ops-cofailure-card .ops-cofailure-meta') as HTMLElement | null
      if (card) {
        card.click()
        const event = v2.eventsSection(view, options, v2State.eventNodeFilter).events[0]!
        v2State.selectedEventKey = v2.eventKey(event)
        v2State.selectedNode = event.node_raw
        expect(v2Scrape(health, sel, v2State)).toEqual(scrape(host))
      }
      // The node evidence drawer (Evidence column) lists the retained runs newest first.
      const evidenceButton = [...host.querySelectorAll('.ops-agent-table tbody tr')]
        .find((tr) => tr.querySelector('td')?.textContent === label)
        ?.querySelector('td:last-child button') as HTMLButtonElement | undefined
      if (evidenceButton) {
        evidenceButton.click()
        const dialog = document.querySelector('[role="dialog"]')!
        const drawer = v2.nodeEvidence(agent, options)
        expect(text(dialog.querySelector('.ops-overlay-title'))).toBe(drawer.title)
        expect(text(dialog.querySelector('.ops-overlay-subtitle'))).toBe(drawer.subtitle)
        const classicRows = [...dialog.querySelectorAll('tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map((td) => td.textContent).join('|'))
        expect(drawer.rows.slice(0, 30).map((p) => [p.label, shortDate(p.timestamp), p.valueSummary, p.url ? 'Open source' : 'Inspect'].join('|'))).toEqual(classicRows)
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
  { signal: 'hard', sorts: ['Physical node', 'Physical node', 'Runs'] },
  { signal: 'all', sorts: ['Fail %', 'GPU'] },
  { window: '1d', focus: 0 },
  { window: '3d', cofail: '30', focus: 1 },
  { window: '14d', nightly: '1', exclCancel: '0', focus: 2 },
  { window: '30d', gpu: 'MI355', signal: 'all', cofail: '1440', focus: 0 },
  { window: '60d', gpu: 'MI300', sorts: ['Co-fail', 'Groups', 'Test group failures'] },
  { gpu: 'MI999', node: 'crsuse2-m2m-160', cofail: '999', signal: 'bogus', window: '2d' },
  { search: 'crsuse2', focus: 0 },
  { search: 'mi300', signal: 'hard' },
  { search: 'zzz' },
]

describe('CI agent health domain matches the classic renderer', () => {
  it('renders the same notes, KPIs, table, timeline nodes, and events on the real snapshot', async () => {
    let rows = 0
    let events = 0
    for (const sel of SELECTIONS) {
      const scraped = await expectSame(fixture, sel)
      rows += scraped!.rows.length
      events += scraped!.events.length
    }
    expect(rows).toBeGreaterThan(100)
    expect(events).toBeGreaterThan(10)
  }, 60_000)

  it('agrees on the classic helpers for the real snapshot', () => {
    expect(v2.agentSourceCoverage(fixture)).toEqual(classic.agentSourceCoverage!(fixture))
    expect(v2.agentPipelineScopeLabel(fixture)).toBe(classic.agentPipelineScopeLabel!(fixture))
    for (const day of [undefined, '2026-10-01', '2026-10-12', '2026-10-13', '2026-10-19', '2026-10-20', 'bad']) {
      expect(v2.agentSourceHistoryComplete(fixture, day)).toBe(classic.agentSourceHistoryComplete!(fixture, day))
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
            expect(v2.agentAggregateRunCounts(fixture.node_accounting_totals, options)).toEqual(
              classic.agentAggregateRunCounts!(fixture.node_accounting_totals, options),
            )
            expect(v2.agentAggregateFailureCounts(fixture.failure_accounting_totals, options)).toEqual(
              classic.agentAggregateFailureCounts!(fixture.failure_accounting_totals, options),
            )
            expect([...v2.agentFailureAccountingCounts(fixture.failure_accounting, options)]).toEqual([
              ...classic.agentFailureAccountingCounts!(fixture.failure_accounting, options),
            ])
          }
        }
      }
    }
    for (const s of ['pass', 'PASSED', 'soft', 'hard', 'canceled', '', null]) expect(v2.agentStateColor(s)).toBe(classic.agentStateColor!(s))
    for (const m of [30, 60, 90, 1440, 0, 'x']) expect(v2.agentCofailLabel(m)).toBe(classic.agentCofailLabel!(m))
    for (const [p, b, j] of [
      ['ci', 1, 'abc'],
      ['ci', 0, ''],
      ['', 1, 'x'],
      ['ci', null, 'x'],
    ])
      expect(v2.buildkiteJobUrl(p, b, j)).toBe(classic.buildkiteJobUrl!(p, b, j))
    for (const t of ['short', 'x'.repeat(34), 'y'.repeat(35), '']) expect(v2.agentTruncate(t, 34)).toBe(classic.agentTruncate!(t, 34))
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
      expect(v2.clusterNodeCofailures('n (MI300)', 'n', 'MI300', runs as unknown as v2.AgentRun[], mins)).toEqual(
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
      (h) => (h.infra_suspect_min_pass_rate = 0.75),
      (h) => (h.infra_suspect_min_samples = 0),
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
        sorts: rand() < 0.5 ? [pick(['Physical node', 'Runs', 'Fail %', 'Groups'])] : [],
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
      day_basis: 'build_created_at_utc', attempt_policy: 'latest_attempt_per_step',
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
    await expectSame(rich, { sorts: ['Physical node', 'Physical node', 'GPU'] })
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
