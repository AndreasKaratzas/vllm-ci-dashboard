// Differential parity for CI Health "AMD hardware": the v2 port against the
// classic matrix helpers and the classic renderHealth('coverage') DOM.
import { beforeAll, describe, expect, it } from 'vitest'
import type { Rec } from '@/domain/format'
import * as v2 from '@/domain/healthHardware'
import { classicParity, loadClassic, type ClassicFns } from './classic'
import { pinClock, readFixture } from './support/fixtures'
import { expectGoldenFile } from './support/golden'
import { rng } from './support/rng'

const PORTED = [
  'matrixHealthPolicy',
  'bestHardwareMatrixContract',
  'matrixHealthStatusLabel',
  'matrixHealthTone',
  'matrixGateKindLabel',
  'matrixHealthCollection',
  'matrixGroupEvidence',
  'amdMatrixPlatformRank',
  'sortAmdMatrixRows',
  'architectureSignalStateRank',
  'sortArchitectureSignalRows',
  'amdMatrixSortDescription',
  'compareText',
  'normalizeLabel',
  'reliabilityForPipeline',
  'reliabilityCatalog',
  'groupReliability',
  'evidenceObservations',
  'isNightlyObservation',
  'observationDurationMinutes',
  'observationWaitMinutes',
  'trailingPassStats',
  'groupPublicationHistoryComplete',
] as const

const matrix = readFixture<Rec>('health_hardware_matrix.json')
const sections = readFixture<Rec>('health_hardware_sections.json')
pinClock(matrix.generated_at)
const parity = classicParity('healthHardware')
let classic: ClassicFns

const ARCHS = ['mi250', 'mi300', 'mi325', 'mi355']
const STATES = ['passed', 'failed', 'soft_fail', 'soft_failed', 'soft', 'timed_out', 'broken', 'canceled', 'cancelled', 'expired', 'running', 'scheduled', 'assigned', 'unknown', 'skipped', 'error', 'incident', 'hard', 'failing', '', 'PASSED', ' Failed ', 'Soft_Fail', undefined]
const TITLES = ['Alpha', 'alpha', 'Beta Kernels', 'beta kernels', 'Écho', 'echo', 'Zeta', 'Basic Models (Other)', '10 Models', '9 models', '', undefined]
const AREAS = ['Kernels', 'kernels', 'Attention', 'LM Eval', '', undefined, 'Zed']
const GROUP_STATUSES = ['passing', 'pass', 'PASSED', 'failing', 'fail', 'failed', 'incident', 'soft', 'soft_fail', 'soft_failed', 'hard', 'running', 'scheduled', 'assigned', 'waiting', 'unknown', '', undefined, 'mixed']
const GATE_KINDS = ['generic_best_hardware', 'mi355_sensitive', 'MI355-Sensitive', 'generic', '', undefined]

function generateMatrix(rand: () => number, i: number): Rec {
  const pick = <T,>(xs: T[]) => xs[Math.floor(rand() * xs.length)]!
  const urls = [
    'https://buildkite.com/vllm/ci/builds/42/steps/canvas?jid=abc',
    'https://buildkite.com/vllm/ci/builds/42',
    'https://buildkite.com/vllm/amd-ci/builds/42/steps/canvas?jid=abc',
    'https://example.com/x',
    undefined,
  ]
  const archs = ARCHS.filter(() => rand() < 0.7)
  const rows: Rec[] = []
  const nRows = Math.floor(rand() * 14)
  for (let r = 0; r < nRows; r += 1) {
    const cells: Rec = {}
    for (const arch of ARCHS) {
      if (rand() < 0.2) continue
      cells[arch] = { exists: rand() < 0.6 ? true : rand() < 0.5 ? false : 1, latest_state: pick(STATES), latest_url: pick(urls), latest_build_number: pick([42, '42', 41, undefined]) }
    }
    rows.push({ id: rand() < 0.95 ? `row-${r}` : `row-${r - 1}`, title: pick(TITLES), area: pick(AREAS), command_fingerprint: rand() < 0.5 ? `fp-${r}` : undefined, cells })
  }
  const groups: Rec[] = []
  const nGroups = Math.floor(rand() * 10)
  for (let g = 0; g < nGroups; g += 1) {
    const members: Rec[] = []
    const nMembers = Math.floor(rand() * 4)
    for (let m = 0; m < nMembers; m += 1) {
      const member: Rec = {
        row_id: rand() < 0.8 ? `row-${Math.floor(rand() * (nRows + 2))}` : undefined,
        title: pick(TITLES),
        label: rand() < 0.6 ? `:amd: (${pick(ARCHS).toUpperCase()}) ${pick(TITLES)}` : undefined,
        architecture: pick([...ARCHS, '', undefined]),
        state: pick(STATES),
        agent_pool: rand() < 0.5 ? `${pick(ARCHS)}_1` : undefined,
        agent_pools: rand() < 0.5 ? ['p1', 'p2'] : undefined,
        queue: rand() < 0.3 ? 'q' : undefined,
        url: pick(urls),
        latest_url: pick(urls),
        build_number: pick([42, 41, undefined]),
        commands: rand() < 0.5 ? ['pytest a', 'pytest b'] : undefined,
        command_fingerprint: rand() < 0.4 ? 'member-fp' : undefined,
        source_url: rand() < 0.5 ? 'https://github.com/x/y/blob/z/a.yaml' : undefined,
      }
      const nested = pick(['definitions', 'variants', 'both', 'none', 'emptydefs'])
      const makeDefs = () =>
        Array.from({ length: Math.floor(rand() * 3) }, () => ({
          label: rand() < 0.7 ? `def ${pick(TITLES)}` : undefined,
          architecture: rand() < 0.5 ? pick(ARCHS) : undefined,
          agent_pool: rand() < 0.5 ? 'dp' : undefined,
          queue: rand() < 0.3 ? 'dq' : undefined,
          state: rand() < 0.7 ? pick(STATES) : undefined,
          url: pick(urls),
          latest_url: pick(urls),
          build_number: rand() < 0.5 ? 43 : undefined,
          commands: rand() < 0.5 ? ['cmd'] : undefined,
          command_fingerprint: rand() < 0.5 ? 'def-fp' : undefined,
          source_url: rand() < 0.5 ? 'https://src' : undefined,
        }))
      if (nested === 'definitions' || nested === 'both') member.definitions = makeDefs()
      if (nested === 'variants' || nested === 'both') member.variants = makeDefs()
      if (nested === 'emptydefs') member.definitions = []
      members.push(member)
    }
    groups.push({
      id: `g-${g}`,
      title: pick(TITLES),
      status: pick(GROUP_STATUSES),
      is_passing: pick([true, false, undefined, 'true']),
      gate_kind: pick(GATE_KINDS),
      classification_reason: rand() < 0.5 ? 'reason ' + g : undefined,
      architectures: rand() < 0.7 ? archs.filter(() => rand() < 0.5) : undefined,
      member_row_ids: rand() < 0.7 ? [`row-${Math.floor(rand() * (nRows + 1))}`, `row-${Math.floor(rand() * (nRows + 1))}`] : undefined,
      members,
    })
  }
  const included = pick([groups.length, groups.length + 1, 0, undefined])
  const policy: Rec | undefined =
    rand() < 0.85
      ? {
          included_groups: included,
          passing_groups: Math.floor(rand() * 10),
          failing_groups: Math.floor(rand() * 5),
          waiting_groups: pick([0, 1, undefined]),
          unknown_groups: pick([0, 2, undefined]),
          generic_groups: pick([3, undefined]),
          generic_group_count: pick([4, 0, undefined]),
          mi355_sensitive_groups: pick([1, undefined]),
          mi355_sensitive_group_count: pick([2, 0, undefined]),
        }
      : undefined
  return {
    source: { pipeline: pick(['ci', 'ci', 'amd-ci', undefined]), yaml_url: rand() < 0.5 ? 'https://yaml' : undefined, latest_build_url: rand() < 0.5 ? 'https://build' : undefined },
    summary: {
      health_group_count: pick([groups.length, groups.length, included, 0, undefined]),
      latest_build_number: pick([42, undefined]),
      health_policies: policy ? { best_hardware: policy } : rand() < 0.5 ? {} : undefined,
    },
    architectures: archs.map((id) => ({ id, label: id.toUpperCase() })),
    publication_retention: rand() < 0.15 ? { complete_relative_to_source: false } : {},
    health_groups: groups,
    rows,
    _i: i,
  }
}

beforeAll(() => {
  classic = loadClassic([...PORTED])
})

function compareMatrix(m: Rec) {
  parity('matrixHealthPolicy', v2.matrixHealthPolicy(m.summary), classic.matrixHealthPolicy!(m.summary))
  parity('bestHardwareMatrixContract', v2.bestHardwareMatrixContract(m), classic.bestHardwareMatrixContract!(m))
  const groups = v2.matrixHealthCollection(m)
  parity('matrixHealthCollection', groups, classic.matrixHealthCollection!(m))
  for (const group of groups) {
    parity('matrixGroupEvidence', v2.matrixGroupEvidence(group), classic.matrixGroupEvidence!(group))
    parity('matrixHealthStatusLabel', v2.matrixHealthStatusLabel(group.status), classic.matrixHealthStatusLabel!(group.status))
    parity('matrixHealthTone', v2.matrixHealthTone(group.status), classic.matrixHealthTone!(group.status))
    parity('matrixGateKindLabel', v2.matrixGateKindLabel(group), classic.matrixGateKindLabel!(group))
  }
  for (const mode of ['platform', 'name', 'area', 'other']) {
    parity(
      'sortAmdMatrixRows',
      v2.sortAmdMatrixRows(m.rows, mode).map((r) => r.id + r.title),
      classic.sortAmdMatrixRows!(m.rows, mode).map((r: Rec) => r.id + r.title),
    )
    parity('amdMatrixSortDescription', v2.amdMatrixSortDescription(mode), classic.amdMatrixSortDescription!(mode))
  }
  for (const arch of ARCHS) {
    parity('sortArchitectureSignalRows', v2.sortArchitectureSignalRows(m.rows, arch), classic.sortArchitectureSignalRows!(m.rows, arch))
  }
  for (const row of m.rows as Rec[]) {
    parity('amdMatrixPlatformRank', v2.amdMatrixPlatformRank(row), classic.amdMatrixPlatformRank!(row))
  }
  return groups
}

describe('AMD hardware domain matches the classic renderer', () => {
  it('reads the real matrix snapshot identically', () => {
    const groups = compareMatrix(matrix)
    expect(groups.length).toBeGreaterThan(50)
    expect(v2.bestHardwareMatrixContract(matrix).valid).toBe(true)
    expect(groups.some((g) => g.status === 'failed')).toBe(true)
    expect(groups.some((g) => g.classification === 'mi355-sensitive')).toBe(true)
    expect(groups.flatMap((g) => v2.matrixGroupEvidence(g)).filter((row) => row.url).length).toBeGreaterThan(50)
    const runtime = v2.runtimeMatrixSummary(sections.amd_test_health)
    parity('matrixHealthPolicy', v2.matrixHealthPolicy(runtime), classic.matrixHealthPolicy!(runtime))
    expect(v2.matrixHealthPolicy(runtime).best_hardware_unavailable).toBeUndefined()
  })

  it('agrees on generated matrices, policies, groups, and sorts', () => {
    const rand = rng(91321)
    for (let i = 0; i < 1500; i += 1) compareMatrix(generateMatrix(rand, i))
  })

  it('agrees on status labels, tones, and signal ranks for arbitrary states', () => {
    for (const s of [...GROUP_STATUSES, ...STATES, 'constructor', 'toString', '__proto__', 'hasOwnProperty']) {
      parity('matrixHealthStatusLabel', v2.matrixHealthStatusLabel(s), classic.matrixHealthStatusLabel!(s))
      parity('matrixHealthTone', v2.matrixHealthTone(s), classic.matrixHealthTone!(s))
      parity('architectureSignalStateRank', v2.architectureSignalStateRank(s), classic.architectureSignalStateRank!(s))
    }
    for (const pair of [['a', 'A'], ['é', 'e'], ['10', '9'], ['', 'x'], [null, undefined]]) {
      parity('compareTextSign', Math.sign(v2.compareText(pair[0], pair[1])), Math.sign(classic.compareText!(pair[0], pair[1])))
      parity('normalizeLabel', v2.normalizeLabel(pair[0]), classic.normalizeLabel!(pair[0]))
    }
  })

  it('agrees on the reliability ledger used by the test-group detail', () => {
    const rand = rng(5150)
    const pick = <T,>(xs: T[]) => xs[Math.floor(rand() * xs.length)]!
    for (let i = 0; i < 400; i += 1) {
      const catalog = Array.from({ length: Math.floor(rand() * 6) }, (_, k) => ({
        id: rand() < 0.5 ? `id-${k}` : undefined,
        name: pick(['Alpha', 'alpha ', ' Alpha  Beta', 'alpha beta', 'Gamma', undefined]),
        label: rand() < 0.3 ? 'Label' : undefined,
        hardware: pick(['mi300', 'mi355', 'h100', 'MI300', undefined]),
        queue: pick(['amd_mi300_1', 'amd_mi355b_1', 'gpu', undefined]),
        raw_name: rand() < 0.2 ? ':amd: (CPU) x' : undefined,
        retained_observation_count: pick([3, 4, undefined, 'x']),
        source_retained_observation_count: pick([3, 4, undefined]),
        observation_count: pick([3, 5, undefined]),
        publication_history_complete: pick([true, false, undefined]),
        history_truncated: pick([true, false, undefined]),
        observations: Array.from({ length: Math.floor(rand() * 6) }, (_, o) => ({
          build_number: pick([10, 11, 12, undefined]),
          queue: pick(['b', 'a', undefined]),
          state: pick(STATES),
          build_kind: pick(['nightly', 'Nightly', 'main', undefined]),
          message: pick(['Full CI run - nightly', 'nightlyish', 'x', undefined]),
          duration_mins: pick([5, '7', 'x', undefined]),
          wall_duration_mins: pick([6, undefined]),
          duration_min: pick([8, undefined]),
          dur: pick([9, undefined]),
          wait_mins: pick([1, 'x', undefined]),
          wait_min: pick([2, undefined]),
          o,
        })),
      }))
      const reliability: Rec = {
        source_pipeline: pick(['ci', 'amd-ci']),
        job_scope: pick(['amd_gpu', 'amd_gpu', 'gpu']),
        hardware_scope: 'amd_mi_gpu',
        group_catalog: rand() < 0.8 ? catalog : undefined,
        groups: rand() < 0.3 ? catalog : undefined,
        flaky_candidates: rand() < 0.3 ? { a: catalog[0] || {} } : undefined,
        latency_rankings: rand() < 0.3 ? { by_p90_duration: catalog } : undefined,
      }
      const ops = { reliability }
      parity('reliabilityForPipeline', v2.reliabilityForPipeline(ops, 'ci'), classic.reliabilityForPipeline!(ops, 'ci'))
      parity('reliabilityForPipeline', v2.reliabilityForPipeline(ops, 'amd-ci'), classic.reliabilityForPipeline!(ops, 'amd-ci'))
      parity('reliabilityCatalog', v2.reliabilityCatalog(reliability), classic.reliabilityCatalog!(reliability))
      for (const name of ['Alpha', 'alpha beta', 'Gamma', 'Label', 'none']) {
        parity('groupReliability', v2.groupReliability(reliability, name), classic.groupReliability!(reliability, name))
      }
      for (const row of catalog) {
        parity('groupPublicationHistoryComplete', v2.groupPublicationHistoryComplete(row), classic.groupPublicationHistoryComplete!(row))
        parity('evidenceObservations', v2.evidenceObservations(row), classic.evidenceObservations!(row))
        parity('trailingPassStats', v2.trailingPassStats(row.observations, 3), classic.trailingPassStats!(row.observations, 3))
        parity('trailingPassStats', v2.trailingPassStats(row.observations, 10), classic.trailingPassStats!(row.observations, 10))
        for (const o of row.observations) {
          parity('isNightlyObservation', v2.isNightlyObservation(o), classic.isNightlyObservation!(o))
          parity('observationDurationMinutes', v2.observationDurationMinutes(o), classic.observationDurationMinutes!(o))
          parity('observationWaitMinutes', v2.observationWaitMinutes(o), classic.observationWaitMinutes!(o))
        }
      }
    }
  })
})

// --- The classic coverage view rendered in jsdom -----------------------------------

interface ClassicRender {
  host: HTMLElement
  open(button: Element): Promise<HTMLElement>
}

async function settle() {
  for (let i = 0; i < 6; i += 1) await new Promise((resolve) => setTimeout(resolve, 0))
}

async function renderClassicCoverage(m: Rec, amdTestHealth: Rec, sort: string): Promise<ClassicRender> {
  const fns = loadClassic(['renderHealth', 'state', 'cache'])
  globalThis.fetch = (async () => new Response(JSON.stringify(m))) as typeof fetch
  const state = fns.state as unknown as Rec
  state.healthView = 'coverage'
  state.healthCoverageSort = sort
  document.body.innerHTML = ''
  const host = document.createElement('div')
  document.body.append(host)
  await fns.renderHealth!(host, { amd_test_health: amdTestHealth, generated_at: '2026-10-19T00:00:00Z' })
  return {
    host,
    async open(button) {
      ;(button as HTMLElement).click()
      await settle()
      const overlays = document.querySelectorAll('.ops-overlay')
      return overlays[overlays.length - 1] as HTMLElement
    },
  }
}

const text = (el: Element | null | undefined) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()
const toneClass = (el: Element) => [...el.classList].find((c) => /^is-(success|danger|warning|info|neutral|not-targeted)$/.test(c)) ?? ''
const leafText = (el: Element) =>
  [...el.querySelectorAll('*')].filter((child) => !child.children.length).map((child) => text(child)).filter(Boolean).join(' ')
const normRows = (rows: unknown[][]) => rows.map((r) => r.map((c) => String(c).replace(/\s+/g, ' ').trim()))
const normList = (rows: unknown[]) => rows.map((c) => String(c).replace(/\s+/g, ' ').trim())
const tableRows = (root: Element) =>
  [...root.querySelectorAll('tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map((td) => text(td)))

function healthSectionFor(m: Rec, rand: () => number): Rec {
  return {
    source_pipeline: 'ci',
    job_scope: 'amd_gpu',
    hardware_scope: 'amd_mi_gpu',
    summary: { latest_build_number: rand() < 0.5 ? 77 : undefined },
    runtime_matrix_source: { pipeline: rand() < 0.85 ? 'ci' : 'amd-ci' },
    runtime_matrix_summary: rand() < 0.2 ? generateMatrix(rand, 0).summary : m.summary,
  }
}

const seen: Record<string, number> = {}
const hit = (key: string) => (seen[key] = (seen[key] ?? 0) + 1)

async function compareCoverageView(m: Rec, amdTestHealth: Rec, sort: string) {
  const view = await renderClassicCoverage(m, amdTestHealth, sort)
  const host = view.host
  const state = v2.hardwareViewState(m)
  if (state === 'unavailable') {
    expect(text(host)).toContain('Current main CI AMD hardware evidence is unavailable.')
    hit('unavailable')
    return
  }
  const runtime = v2.runtimeMatrixSummary(amdTestHealth)
  const policy = v2.matrixHealthPolicy(runtime)
  const overview = v2.matrixHealthOverviewPresentation(runtime, policy, runtime.latest_build_number || (amdTestHealth.summary || {}).latest_build_number)
  const card = host.querySelector('.ops-unique-health')!
  parity('overviewMeta', overview.meta, text(card.querySelector('.ops-panel-meta')))
  if (overview.unavailable) {
    expect(text(card)).toContain('Best-hardware metric unavailable.')
    expect(card.querySelector('.ops-unique-health-rate')).toBeNull()
    hit('policy-unavailable')
  } else {
    parity('overviewRate', overview.rate, text(card.querySelector('.ops-unique-health-rate strong')))
    parity(
      'overviewStats',
      overview.stats.map((s) => `${s.label} ${s.count}`),
      [...card.querySelectorAll('.ops-unique-health-stat')].map((el) => leafText(el)),
    )
    parity(
      'overviewSegmentTitles',
      overview.segments.map((s) => `${s.label}: ${s.count.toLocaleString()}`),
      [...card.querySelectorAll('.ops-unique-health-segment')].map((el) => (el as HTMLElement).title),
    )
    parity(
      'overviewSegmentWidths',
      overview.segments.map((s) => `${(s.count / overview.total) * 100}%`),
      [...card.querySelectorAll('.ops-unique-health-segment')].map((el) => (el as HTMLElement).style.width),
    )
    parity('overviewFacts', overview.facts, text(card.querySelector('.ops-unique-health-footer span')))
    parity('overviewBrowseLabel', overview.browseLabel, text(card.querySelector('.ops-unique-health-footer button')))
    // The matrix browser re-reads the matrix payload.
    const overlay = await view.open(card.querySelector('.ops-unique-health-footer button')!)
    const browser = v2.matrixBrowserState(m)
    if (!browser.available) {
      expect(text(overlay)).toContain(browser.description)
      hit('browser-unavailable')
    } else {
      hit(browser.groups.length ? 'browser-groups' : 'browser-empty')
      parity('browserSubtitle', browser.subtitle, text(overlay.querySelector('.ops-overlay-subtitle')))
      parity(
        'browserCount',
        `${browser.groups.length.toLocaleString()} of ${browser.groups.length.toLocaleString()} rows`,
        text(overlay.querySelector('.ops-browser-count')),
      )
      const first = tableRows(overlay).map((cells) => cells[0])
      parity('browserFirstColumn', normList(browser.groups.slice(0, 50).map((g) => String(g.title ?? ''))), first)
      parity(
        'browserRows',
        browser.groups.slice(0, 50).map((g) => [
          v2.matrixHealthStatusLabel(g.status),
          v2.matrixGateKindLabel(g),
          g.architectures.map((a: string) => a.toUpperCase()).join(', ') || '-',
          g.classificationReason || '-',
          `${g.passedCells.toLocaleString()} pass - ${g.incidentCells.toLocaleString()} non-passing`,
          `${v2.matrixGroupEvidence(g).filter((r) => r.url).length.toLocaleString()} jobs`,
        ]),
        tableRows(overlay).map((cells) => cells.slice(1, 7)),
      )
      const selects = overlay.querySelectorAll('select')
      for (const status of ['passing', 'failing', 'no-signal', 'all']) {
        for (const classification of ['generic', 'mi355-sensitive', 'all']) {
          ;(selects[0] as HTMLSelectElement).value = status
          ;(selects[1] as HTMLSelectElement).value = classification
          selects[1]!.dispatchEvent(new Event('change'))
          const expected = v2.browseRows(browser.groups, '', v2.matrixGroupSearchText, [
            { selected: status, predicate: v2.matrixStatusPredicate },
            { selected: classification, predicate: v2.matrixClassificationPredicate },
          ])
          parity(
            'browserFilteredCount',
            `${expected.length.toLocaleString()} of ${browser.groups.length.toLocaleString()} rows`,
            text(overlay.querySelector('.ops-browser-count')),
          )
        }
      }
      for (const query of ['alpha', 'MI300', 'cmd', 'fp', '  Beta   kernels ', 'reason 1', 'zzz']) {
        const input = overlay.querySelector('input')!
        input.value = query
        input.dispatchEvent(new Event('input'))
        const expected = v2.browseRows(browser.groups, query, v2.matrixGroupSearchText)
        parity(
          'browserSearchCount',
          `${expected.length.toLocaleString()} of ${browser.groups.length.toLocaleString()} rows`,
          text(overlay.querySelector('.ops-browser-count')),
        )
        parity(
          'browserSearchFirstColumn',
          normList(expected.slice(0, 50).map((g) => String(g.title ?? ''))),
          tableRows(overlay).map((cells) => cells[0]),
        )
      }
      // Each group's evidence drawer.
      const reset = overlay.querySelector('input')!
      reset.value = ''
      reset.dispatchEvent(new Event('input'))
      const buttons = [...overlay.querySelectorAll('tbody tr td:first-child button')].slice(0, 4)
      for (const [index, button] of buttons.entries()) {
        const group = browser.groups[index]!
        const detail = v2.matrixGroupDetail(group, m)
        const drawer = await view.open(button)
        hit(detail.evidence.length ? 'drawer-evidence' : 'drawer-empty')
        if (detail.commandsText) hit('drawer-commands')
        parity('groupDetailTitle', detail.title, text(drawer.querySelector('.ops-overlay-title')))
        parity('groupDetailNote', `${v2.matrixHealthStatusLabel(group.status)}. ${detail.note}`, text(drawer.querySelector('.ops-evidence-note')))
        parity(
          'groupDetailFields',
          detail.fields.map((f) => [f.label, String(f.value)]),
          [...drawer.querySelectorAll('.ops-detail-field')].map((el) => [text(el.querySelector('dt')), text(el.querySelector('dd'))]),
        )
        parity(
          'groupDetailSources',
          detail.sources.map((s) => s.url),
          [...drawer.querySelectorAll('.ops-source-actions a')].map((a) => (a as HTMLAnchorElement).getAttribute('href')),
        )
        if (detail.evidence.length) {
          parity('groupDetailEvidenceCaption', detail.evidenceCaption, text(drawer.querySelector('caption')))
          parity(
            'groupDetailEvidence',
            detail.evidence.map((row) => [String(row.definition).trim(), String(row.queue || '-').trim(), String(row.result).trim(), row.commandIdentity || '-']),
            tableRows(drawer).map((cells) => [cells[0], cells[2], cells[3], cells[4]]),
          )
        }
        const pre = drawer.querySelector('pre')
        parity('groupDetailCommands', detail.commandsText, pre?.textContent ?? '')
        ;(drawer.querySelector('.ops-overlay-close') as HTMLElement).click()
      }
    }
    ;(document.querySelector('.ops-overlay-close') as HTMLElement | null)?.click()
  }
  if (state === 'bounded') {
    expect(text(host)).toContain('AMD detail is storage-bounded; architecture rates and routes are hidden.')
    expect(host.querySelector('.ops-architecture-scorecard')).toBeNull()
    hit('bounded')
    return
  }
  parity('policyNote', !policy.best_hardware_unavailable, Boolean(host.querySelector('.ops-evidence-note.is-info')))
  const architectures: Rec[] = m.architectures || []
  const rows = v2.sortAmdMatrixRows(m.rows || [], sort)
  const health = v2.architectureHealth(architectures, rows)
  const archRows = [...host.querySelectorAll('.ops-architecture-row')]
  parity(
    'architectureRows',
    health.map((h) => {
      const rate = v2.architectureRate(h)
      return `${h.architecture.label} ${rate.configured.toLocaleString()} configured routes ${h.passed.toLocaleString()} passing ${h.incident.toLocaleString()} non-passing ${h.unknown.toLocaleString()} unobserved ${rate.label} passing`
    }),
    archRows.map((el) => leafText(el)),
  )
  parity(
    'architectureRateTones',
    health.map((h) => v2.architectureRate(h).tone),
    archRows.map((el) => el.querySelector('.ops-architecture-rate')!.className.split(' ').pop()),
  )
  for (const [index, h] of health.entries()) {
    const overlay = await view.open(archRows[index]!)
    hit(h.passed && h.incident ? 'arch-mixed' : 'arch')
    const browser = v2.architectureBrowser(h, rows)
    parity('architectureBrowserTitle', browser.title, text(overlay.querySelector('.ops-overlay-title')))
    parity('architectureBrowserSubtitle', browser.subtitle, text(overlay.querySelector('.ops-overlay-subtitle')))
    parity(
      'architectureBrowserRows',
      normRows(
        browser.rows.slice(0, 50).map((row) => {
          const cell = (row.cells || {})[h.architecture.id] || {}
          const url = v2.architectureCellUrl(cell)
          return [String(row.title ?? ''), row.area === undefined || row.area === null || row.area === '' ? '-' : String(row.area), String(cell.latest_state || 'unobserved'), url ? `#${cell.latest_build_number ?? '-'}` : '-']
        })),
      tableRows(overlay),
    )
    ;(overlay.querySelector('.ops-overlay-close') as HTMLElement).click()
  }
  // The preview matrix and its sort description.
  const panel = [...host.querySelectorAll('.ops-panel')].find((el) => text(el.querySelector('.ops-panel-title')) === 'Test-group routes by architecture')!
  parity('matrixSortDescription', v2.amdMatrixSortDescription(sort), text(panel.querySelector('.ops-panel-meta')))
  if (rows.length) {
    parity(
      'matrixPreviewRows',
      normRows(
        rows.slice(0, 8).map((row) => [
          String(row.title ?? ''),
          row.area === undefined || row.area === '' ? '-' : String(row.area),
          ...architectures.map((a) => {
            const c = (row.cells || {})[a.id] || {}
            return c.exists ? String(c.latest_state || 'unknown') : '-'
          }),
        ])),
      tableRows(panel),
    )
  }
  const browse = [...panel.querySelectorAll('button')].find((b) => text(b) === 'Browse complete route matrix')
  parity('hasMatrixBrowse', rows.length > 8, Boolean(browse))
  if (browse) {
    const overlay = await view.open(browse)
    hit('matrix-browse')
    parity('matrixBrowseSubtitle', v2.coverageBrowserSubtitle(rows, architectures, sort), text(overlay.querySelector('.ops-overlay-subtitle')))
    parity('matrixBrowseFirstColumn', normList(rows.slice(0, 50).map((row) => String(row.title ?? ''))), tableRows(overlay).map((cells) => cells[0]))
    ;(overlay.querySelector('.ops-overlay-close') as HTMLElement).click()
  }
}

describe('AMD hardware view matches the classic coverage DOM', () => {
  it('on the real matrix in every sort mode', async () => {
    for (const sort of ['platform', 'name', 'area']) {
      await compareCoverageView(matrix, sections.amd_test_health, sort)
    }
    const view = await renderClassicCoverage(matrix, sections.amd_test_health, 'platform')
    expect(view.host.querySelectorAll('.ops-architecture-row').length).toBe(3)
    const runtime = v2.runtimeMatrixSummary(sections.amd_test_health)
    const rows = v2.sortAmdMatrixRows(matrix.rows, 'platform')
    await expectGoldenFile('healthHardware', 'coverage', {
      overview: v2.matrixHealthOverviewPresentation(runtime, v2.matrixHealthPolicy(runtime), runtime.latest_build_number),
      architectures: v2.architectureHealth(matrix.architectures, rows).map((h) => ({ ...h, rate: v2.architectureRate(h) })),
      preview: rows.slice(0, 8).map((row) => row.title),
    })
  })

  it('on generated matrices', async () => {
    const rand = rng(777)
    for (let i = 0; i < 60; i += 1) {
      const m = generateMatrix(rand, i)
      if (i % 3 === 0) {
        // Valid contracts exercise the browser and the evidence drawers.
        m.source.pipeline = 'ci'
        m.publication_retention = {}
        const policy = m.summary.health_policies?.best_hardware ?? (m.summary.health_policies = { best_hardware: {} }).best_hardware
        policy.included_groups = m.health_groups.length || 1
        m.summary.health_group_count = policy.included_groups
      }
      await compareCoverageView(m, healthSectionFor(m, rand), ['platform', 'name', 'area'][i % 3]!)
    }
    // Architecture rate tones at their exact 90% and 50% boundaries.
    for (const [passed, total] of [[9, 10], [1, 2], [8, 9], [0, 1]] as const) {
      const rows = Array.from({ length: total }, (_, k) => ({
        id: `b-${k}`,
        title: `Boundary ${k}`,
        area: 'Edge',
        cells: { mi300: { exists: true, latest_state: k < passed ? 'passed' : 'failed' } },
      }))
      const m = { source: { pipeline: 'ci' }, summary: {}, architectures: [{ id: 'mi300', label: 'MI300' }], rows, health_groups: [] }
      await compareCoverageView(m, healthSectionFor(m, rand), 'platform')
    }
    for (const key of ['unavailable', 'policy-unavailable', 'browser-unavailable', 'browser-groups', 'drawer-evidence', 'drawer-empty', 'drawer-commands', 'bounded', 'arch-mixed', 'matrix-browse']) {
      expect(seen[key], key).toBeGreaterThan(0)
    }
  }, 60_000)
})

// --- Test-group detail: classic openGroupDetail / openMixedOutcomeEvidence DOM -----

function generateReliability(rand: () => number): Rec {
  const pick = <T,>(xs: T[]) => xs[Math.floor(rand() * xs.length)]!
  const urlFor = (n: number) =>
    pick([`https://buildkite.com/vllm/ci/builds/${n}/steps/canvas?jid=j${n}`, `https://buildkite.com/vllm/ci/builds/${n}`, `https://buildkite.com/vllm/amd-ci/builds/${n}/steps/canvas?jid=x`])
  const catalog = Array.from({ length: 1 + Math.floor(rand() * 3) }, (_, k) => ({
    id: `g${k}`,
    name: pick(['Alpha', 'alpha', 'Beta', 'Gamma']),
    hardware: pick(['mi300', 'mi300', 'mi300', 'h100']),
    queue: 'amd_mi300_1',
    source_pipeline: pick([undefined, 'ci', 'amd-ci']),
    retained_observation_count: pick([undefined, 5, 6]),
    source_retained_observation_count: pick([undefined, 5]),
    publication_history_complete: pick([undefined, true, false]),
    observations: Array.from({ length: Math.floor(rand() * 16) }, (_, o) => {
      const n = 100 + Math.floor(rand() * 30)
      return {
        build_number: rand() < 0.9 ? n : undefined,
        url: urlFor(n),
        source_pipeline: pick([undefined, 'ci', 'amd-ci']),
        state: pick(STATES),
        queue: pick(['amd_mi300_1', 'amd_mi300_2', undefined]),
        name: rand() < 0.3 ? 'Alpha shard' : undefined,
        observed_at: `2026-10-${String(1 + Math.floor(rand() * 28)).padStart(2, '0')}T0${o % 10}:00:00Z`,
        build_kind: pick(['nightly', 'main', undefined]),
        message: pick(['nightly run', 'x', undefined]),
        duration_mins: pick([12, '7.5', undefined]),
        wait_mins: pick([1, undefined]),
        variant_hardware: rand() < 0.3 ? 'mi300' : undefined,
        retries_count: pick([0, 2, undefined]),
        retried: pick([true, undefined]),
      }
    }),
  }))
  return {
    available: pick([true, false]),
    source_pipeline: 'ci',
    job_scope: 'amd_gpu',
    hardware_scope: 'amd_mi_gpu',
    cohort: { available: pick([true, false]), pipeline: 'ci' },
    scope: pick(['all-main', 'nightly-only', '']),
    group_catalog: rand() < 0.9 ? catalog : [],
  }
}

describe('test-group detail matches the classic drawer and history overlay', () => {
  it('on generated reliability ledgers', () => {
    const rand = rng(31337)
    const classicFns = loadClassic(['openGroupDetail'])
    window.requestAnimationFrame = (() => 0) as typeof window.requestAnimationFrame
    const seenKinds = new Set<string>()
    for (let i = 0; i < 250; i += 1) {
      document.body.innerHTML = ''
      const reliability = generateReliability(rand)
      const ops = rand() < 0.15 ? {} : { reliability }
      const group = { name: ['Alpha', 'Beta', 'Gamma', 'Delta'][i % 4], area: rand() < 0.5 ? 'Kernels' : undefined }
      classicFns.openGroupDetail!(group, ops)
      const overlays = document.querySelectorAll('.ops-overlay')
      const overlay = overlays[overlays.length - 1] as HTMLElement
      const state = v2.groupDetailState(group, ops)
      seenKinds.add(state.kind)
      if (state.kind === 'aggregate') {
        parity('aggregateTitle', state.name, text(overlay.querySelector('.ops-overlay-title')))
        parity('aggregateSubtitle', 'Test-group detail', text(overlay.querySelector('.ops-overlay-subtitle')))
        parity(
          'aggregateFields',
          state.fields.filter((f) => f.value !== null && f.value !== undefined && f.value !== '').map((f) => [f.label, String(f.value)]),
          [...overlay.querySelectorAll('.ops-detail-field')].map((el) => [text(el.querySelector('dt')), text(el.querySelector('dd'))]),
        )
        parity(
          'aggregateCanLoadHistory',
          state.canLoadHistory,
          Boolean([...overlay.querySelectorAll('button')].find((b) => text(b) === 'Load 30-day run history')),
        )
        continue
      }
      const row = state.row
      const complete = v2.groupPublicationHistoryComplete(row)
      const all = v2.mixedOutcomeObservations(row)
      const notice = v2.mixedOutcomeNotice(all, complete, row.source_pipeline || 'ci')
      parity('evidenceTitle', row.name || 'Group evidence', text(overlay.querySelector('.ops-overlay-title')))
      parity('evidenceNote', text({ textContent: notice.strong + notice.text } as Element), text(overlay.querySelector('.ops-evidence-note')))
      parity('evidenceNoteTone', notice.tone, toneClass(overlay.querySelector('.ops-evidence-note')!))
      if (!all.length) {
        seenKinds.add('evidence-empty')
        parity('evidenceEmptySubtitle', `${state.scopeLabel} evidence`, text(overlay.querySelector('.ops-overlay-subtitle')))
        continue
      }
      seenKinds.add('evidence-rows')
      for (const mode of ['main', 'nightly'] as const) {
        if (mode === 'nightly') {
          const toggle = [...overlay.querySelectorAll('.ops-segment')].find((b) => text(b) === 'Nightly only') as HTMLElement
          toggle.click()
        }
        const observations = all.filter((o) => mode === 'main' || v2.isNightlyObservation(o))
        if (!observations.length) {
          expect(text(overlay)).toContain('No exact nightly observations are retained for this strict test-group variant.')
          seenKinds.add('nightly-empty')
          continue
        }
        const summary = v2.mixedOutcomeSummary(observations, complete, mode)
        parity(
          'evidenceSummary',
          summary.map((s) => [s.label.toLowerCase(), s.value]),
          [...overlay.querySelectorAll('.ops-evidence-stat')].map((el) => [text(el.querySelector('.ops-stat-label')).toLowerCase(), text(el.querySelector('.ops-stat-value'))]),
        )
        const captions = [...overlay.querySelectorAll('.ops-chart-panel .ops-panel-meta')].map((el) => text(el))
        parity('outcomeChartCaption', v2.outcomeChartCaption(observations, complete), captions[0])
        const hasDuration = observations.some((o) => v2.observationDurationMinutes(o) !== null || v2.observationWaitMinutes(o) !== null)
        parity('outcomeChartCount', hasDuration ? 2 : 1, captions.length)
        const search = overlay.querySelector('input[type=search]') as HTMLInputElement
        const select = overlay.querySelector('select') as HTMLSelectElement
        for (const [query, filter] of [['', 'all'], ['', 'passing'], ['', 'incident'], ['amd_mi300_2', 'all'], ['1', 'incident'], ['alpha', 'all'], ['FAILED', 'all']] as const) {
          search.value = query
          select.value = filter
          select.dispatchEvent(new Event('change'))
          const expected = v2.filterEvidenceRows(observations, query, filter)
          const table = overlay.querySelector('.ops-evidence-table-host')!
          expect(text(table.querySelector('caption') ?? table.querySelector('.ops-empty'))).toContain(
            `${expected.length.toLocaleString()} of ${observations.length.toLocaleString()} retained ${mode} observations`,
          )
          if (expected.length) {
            parity(
              'evidenceRows',
              normRows(expected.map((o) => [
                o.build_number ? `#${o.build_number}` : 'Build',
                v2.isNightlyObservation(o) ? 'nightly' : 'main',
                (() => {
                  const ts = o.observed_at
                  return new Date(ts).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
                })(),
                v2.evidenceResultLabel(o) || 'unknown',
              ])),
              [...table.querySelectorAll('tbody tr')].map((tr) => [...tr.querySelectorAll('td')].slice(0, 4).map((td) => text(td))),
            )
          }
        }
        search.value = ''
        select.value = 'all'
        select.dispatchEvent(new Event('change'))
      }
    }
    expect([...seenKinds].sort()).toEqual(['aggregate', 'evidence', 'evidence-empty', 'evidence-rows', 'nightly-empty'])
  })

  it('agrees on the reliability scope label', () => {
    const classicFns = loadClassic(['reliabilityScopeInfo'])
    const rand = rng(99)
    for (let i = 0; i < 300; i += 1) {
      const r = generateReliability(rand)
      for (const candidate of [r, { ...r, scope: 'branch=main' }, { ...r, cohort: { available: true }, available: true, scope: 'nightly job runs' }, null, 'x']) {
        parity('reliabilityScopeInfo', v2.reliabilityScopeInfo(candidate as Rec), classicFns.reliabilityScopeInfo!(candidate))
      }
    }
  })
})

describe('AMD hardware helpers on hand-written inputs', () => {
  it('labels and tones matrix statuses', () => {
    expect(['passing', 'failed', 'mixed', 'waiting', 'unknown'].map((s) => [v2.matrixHealthStatusLabel(s), v2.matrixHealthTone(s)])).toEqual([
      ['Passing', 'pass'],
      ['Failing', 'hard'],
      ['Mixed', 'soft'],
      ['In progress', 'info'],
      ['No signal', 'neutral'],
    ])
  })

  it('ranks architecture signals and normalizes labels', () => {
    expect(['failed', 'soft_fail', 'running', 'passed'].map((s) => v2.architectureSignalStateRank(s))).toEqual([0, 1, 2, 3])
    expect(['  Alpha   Beta ', 'Écho', null].map((s) => v2.normalizeLabel(s))).toEqual(['alpha beta', 'écho', ''])
  })

  it('rates architectures at the 90% and 50% boundaries', () => {
    const rate = (passed: number, incident: number) => v2.architectureRate({ architecture: { id: 'mi300', label: 'MI300' }, passed, incident, unknown: 0, missing: 0 })
    expect([rate(9, 1), rate(1, 1), rate(0, 0)]).toEqual([
      { configured: 10, passRate: 90, label: '90.0%', tone: 'pass' },
      { configured: 2, passRate: 50, label: '50.0%', tone: 'soft' },
      { configured: 0, passRate: null, label: '-', tone: 'hard' },
    ])
  })
})
