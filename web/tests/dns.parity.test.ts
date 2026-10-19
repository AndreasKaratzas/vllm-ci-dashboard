import { beforeAll, describe, expect, it, vi } from 'vitest'
import * as v2 from '@/domain/dns'
import { age, integer, type Rec } from '@/domain/format'
import { classicParity, loadClassic, type ClassicFns } from './classic'
import { pinClock, readFixture } from './support/fixtures'
import { expectGoldenFile } from './support/golden'
import { rng } from './support/rng'

const PURE = [
  'queueDnsCount',
  'queueDnsOutcomeCounts',
  'queueDnsRowsComplete',
  'queueDnsPayloadValid',
  'compareQueueDnsCandidates',
  'queueDnsWindow',
  'queueDnsCoverage',
  'queueDnsFreshness',
  'queueDnsScope',
  'queueDnsMatchesPublishedScope',
  'queueDnsNodeRows',
  'queueDnsQueueRows',
  'queueDnsEvidenceUrl',
  'queueDnsEvidenceMetricValid',
  'queueDnsEvidenceItemValid',
  'queueDnsEvidenceWindowRow',
  'queueDnsEvidenceForNode',
  'queueDnsNodeOutcomes',
  'queueDnsDisplayCount',
  'queueDnsTargetLabels',
  'queueDnsSignatureLabels',
  'queueDnsOutcomePresentation',
  'isRetiredQueue',
  'compareText',
  'queueTimestamp',
] as const

const pages = readFixture<Rec>('dns_pages.json')
const live = readFixture<Rec>('dns_live.json')
const NOW = pinClock(live.generated_at)
const parity = classicParity('dns')
let classic: ClassicFns

const SCOPES = ['amd', 'canonical', 'bogus']

beforeAll(() => {
  ;(globalThis as Rec).requestAnimationFrame = () => 0
  classic = loadClassic([...PURE, 'renderAnalyticsDns', 'state', 'closeOverlay', 'loadQueueDns', 'invalidateDnsData'] as unknown as string[])
})

/** Facts read from classic renderAnalyticsDns, in the shape the v2 view renders them. */
function classicDom(payload: Rec, windowId: string, scope: string) {
  const state = classic.state as unknown as Rec
  state.analyticsDnsWindow = windowId
  state.analyticsDnsScope = scope
  const host = document.createElement('div')
  document.body.append(host)
  classic.renderAnalyticsDns!(host, payload)
  const text = (el: Element | null) => (el ? el.textContent : null)
  const result = {
    error: text(host.querySelector('.ops-error')),
    window: (host.querySelector('.ops-dns-window-select') as HTMLSelectElement | null)?.value ?? null,
    stateWindow: state.analyticsDnsWindow,
    badge: text(host.querySelector('.ops-dns-toolbar > .ops-badge')),
    stale: text(host.querySelector('.ops-dns-stale-warning')),
    coverage: text(host.querySelector('.ops-dns-coverage-note')),
    summary: [...host.querySelectorAll('.ops-dns-summary-item')].map((item) => ({
      label: text(item.querySelector('.ops-dns-summary-label')),
      value: text(item.querySelector('.ops-dns-summary-value')),
      meta: text(item.querySelector('.ops-dns-summary-meta')),
      tone: item.className.replace('ops-dns-summary-item', '').trim(),
    })),
    empty: text(host.querySelector('.ops-empty')),
    cards: [...host.querySelectorAll('article.ops-dns-queue-card')].map((card) => ({
      queue: text(card.querySelector('.ops-dns-queue-card-title')),
      stats: [...card.querySelectorAll('.ops-dns-queue-card-stats span')].map((s) => s.textContent),
      bars: [...card.querySelectorAll('.ops-dns-node-bar')].map((bar) => ({
        aria: bar.getAttribute('aria-label'),
        name: text(bar.querySelector('.ops-dns-node-name')),
        meta: text(bar.querySelector('.ops-dns-node-meta')),
        count: text(bar.querySelector('.ops-dns-node-count')),
        fill: (bar.querySelector('.ops-dns-bar-fill') as HTMLElement).style.width,
        segments: [...bar.querySelectorAll('.ops-dns-bar-segment')].map(
          (s) => `${s.className.replace('ops-dns-bar-segment ', '')}:${(s as HTMLElement).style.width}`,
        ),
      })),
    })),
    method: [...host.querySelectorAll('.ops-dns-method-body p')].map((p) => p.textContent),
  }
  // Each node bar opens the exact-evidence drawer.
  const drawers = [...host.querySelectorAll('.ops-dns-node-bar')].map((bar) => {
    ;(bar as HTMLButtonElement).click()
    const dialog = document.querySelector('[role="dialog"]')!
    const drawer = {
      title: text(dialog.querySelector('.ops-overlay-title')),
      subtitle: text(dialog.querySelector('.ops-overlay-subtitle')),
      fields: [...dialog.querySelectorAll('.ops-detail-field')].map((f) => `${text(f.querySelector('dt'))}=${text(f.querySelector('dd'))}`),
      notes: [...dialog.querySelectorAll('.ops-dns-evidence > .ops-evidence-note')].slice(1).map((n) => n.textContent),
      meta: text(dialog.querySelector('.ops-dns-evidence .ops-panel-meta')),
      rows: [...dialog.querySelectorAll('.ops-dns-evidence tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map((td) => td.textContent)),
      links: [...dialog.querySelectorAll('.ops-dns-evidence tbody tr td:nth-child(2) a')].map((a) => a.getAttribute('href')),
    }
    classic.closeOverlay!()
    return drawer
  })
  host.remove()
  return { ...result, drawers }
}

/** The same facts from the v2 domain, formatted as the v2 view renders them. */
function v2Facts(payload: Rec, windowId: string, scope: string, now: number) {
  const p = v2.dnsPresentation(payload, windowId, scope, now)
  if (!p.block) return { error: 'The selected DNS observation window is not present in the published aggregate.' }
  const option = v2.QUEUE_DNS_WINDOW_OPTIONS.find((o) => o.id === p.windowId)
  const shortDate = (ts: unknown) => new Date(String(ts)).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  return {
    error: null,
    window: p.windowId,
    stateWindow: p.windowId,
    badge: (p.freshness.stale ? 'Stale - ' : 'Updated ') + age(p.block.end_exclusive, now),
    stale: p.freshness.stale
      ? 'DNS observations are stale. ' +
        (option ? option.label : p.windowId) +
        ' ended ' +
        shortDate(p.block.end_exclusive) +
        '. Treat these as historical observations, not the current window.'
      : null,
    coverage: p.coverage.complete
      ? null
      : 'Partial coverage - counts are lower bounds. ' + (p.coverage.facts.length ? p.coverage.facts.join(' - ') + '. ' : '') + 'Missing observations never mean zero.',
    summary: p.summary.map((s) => ({ ...s })),
    empty: p.cards.length ? null : p.emptyText,
    cards: p.cards.map((card) => ({
      queue: card.queue.queue,
      stats: card.stats,
      bars: card.bars.map((bar) => ({
        aria: bar.ariaLabel,
        name: bar.node.node,
        meta: bar.meta,
        count: bar.renderedCount,
        fill: `${bar.fillPct}%`,
        segments: bar.segments.map((s) => `is-${s.kind}:${s.widthPct === null ? '' : `${s.widthPct}%`}`),
      })),
    })),
    method: [
      'One job attempt counts once when its complete log contains a DNS-specific resolver signature. Repeated matching lines collapse into episodes; retries remain distinct attempts. Generic connection, TLS, timeout, and HTTP failures do not count.',
      'Selected window: ' +
        shortDate(p.block.start) +
        ' to ' +
        shortDate(p.block.end_exclusive) +
        '. Schema v' +
        (payload.schema_version ?? '-') +
        ', generated ' +
        shortDate(payload.generated_at) +
        ', source: ' +
        v2.queueDnsSourceLabel(payload.__sourceAsset) +
        '.',
      ...(p.coverage.notes.length ? [p.coverage.notes.join('; ') + '.'] : []),
    ],
    drawers: p.cards.flatMap((card) =>
      card.bars.map((bar) => {
        const d = v2.queueDnsNodeEvidence(payload, p.windowId, card.queue, bar.resolved, p.coverage)
        return {
          title: d.title,
          subtitle: d.subtitle,
          fields: d.fields.filter((f) => f.value !== null && f.value !== '').map((f) => `${f.label}=${f.value}`),
          notes: d.note ? [d.note.text] : [],
          meta: d.tableMeta,
          rows: d.rows.slice(0, 30).map((row) => {
            const outcome = v2.queueDnsOutcomePresentation(row.state)
            const url = v2.queueDnsEvidenceUrl(row)
            return [
              outcome.label,
              url ? 'Open exact log' : 'Exact link unavailable',
              shortDate(row.first_at) + (row.last_at && row.last_at !== row.first_at ? ' → ' + shortDate(row.last_at) : ''),
              String(row.time_basis || '').replace(/_/g, ' ') || '-',
              row.hardware || '-',
              (row.pipeline || '-') + ' #' + (row.build_number ?? '-'),
              integer(row.episodes),
              integer(row.match_count),
              v2.queueDnsTargetLabels(row).join(', ') || '-',
              v2.queueDnsSignatureLabels(row).join(', ') || '-',
            ]
          }),
          links: d.rows.slice(0, 30).map((row) => v2.queueDnsEvidenceUrl(row)),
        }
      }),
    ),
  }
}

function expectSameView(payload: Rec, windowId: string, scope: string, now = NOW) {
  const fromClassic = classicDom(payload, windowId, scope)
  const fromV2 = v2Facts(payload, windowId, scope, now)
  if (fromV2.error) {
    parity('error', fromV2.error, fromClassic.error)
    return fromClassic
  }
  parity('view', fromV2, fromClassic)
  return fromClassic
}

function comparePure(payload: Rec) {
  parity('queueDnsPayloadValid', v2.queueDnsPayloadValid(payload), classic.queueDnsPayloadValid!(payload))
  for (const windowId of [...v2.QUEUE_DNS_WINDOW_IDS, 'bogus', '']) {
    const fromV2 = v2.queueDnsWindow(payload, windowId)
    parity('queueDnsWindow', fromV2, classic.queueDnsWindow!(payload, windowId))
    parity('queueDnsCoverage', v2.queueDnsCoverage(payload, fromV2.block), classic.queueDnsCoverage!(payload, fromV2.block))
    const generatedMs = Date.parse(payload.generated_at || '2026-01-01T00:00:00Z')
    const endMs = Date.parse((fromV2.block || {}).end_exclusive || '') || generatedMs
    for (const now of [generatedMs + 3600_000, generatedMs + 30 * 3600_000, endMs + 30 * 3600_000, Math.max(generatedMs, endMs) + 3 * 86400_000]) {
      parity('queueDnsFreshness', v2.queueDnsFreshness(payload, fromV2.block, now), classic.queueDnsFreshness!(payload, fromV2.block, now))
    }
    for (const scope of SCOPES) {
      const nodes = v2.queueDnsNodeRows(fromV2.block, scope)
      parity('queueDnsNodeRows', nodes, classic.queueDnsNodeRows!(fromV2.block, scope))
      const roster = ['amd_mi250_1', 'amd_mi325_8', 'amd_mi355b_1', 'gpu_1', ' amd_mi300_2 ']
      parity('queueDnsQueueRows', v2.queueDnsQueueRows(fromV2.block, scope, roster), classic.queueDnsQueueRows!(fromV2.block, scope, roster))
      for (const node of nodes) {
        parity('queueDnsNodeOutcomes', v2.queueDnsNodeOutcomes(payload, fromV2.id, node), classic.queueDnsNodeOutcomes!(payload, fromV2.id, node))
        parity('queueDnsEvidenceForNode', v2.queueDnsEvidenceForNode(payload, fromV2.id, node.queue, node.nodeRaw), classic.queueDnsEvidenceForNode!(payload, fromV2.id, node.queue, node.nodeRaw))
      }
    }
  }
  for (const row of (payload.evidence || {}).items || []) {
    parity('queueDnsEvidenceItemValid', v2.queueDnsEvidenceItemValid(row, payload.windows), classic.queueDnsEvidenceItemValid!(row, payload.windows))
    parity('queueDnsEvidenceUrl', v2.queueDnsEvidenceUrl(row), classic.queueDnsEvidenceUrl!(row))
    parity('queueDnsTargetLabels', v2.queueDnsTargetLabels(row), classic.queueDnsTargetLabels!(row))
    parity('queueDnsSignatureLabels', v2.queueDnsSignatureLabels(row), classic.queueDnsSignatureLabels!(row))
    parity('queueDnsOutcomePresentation', v2.queueDnsOutcomePresentation(row.state), classic.queueDnsOutcomePresentation!(row.state))
  }
}

/** Mutations that keep the payload loadable but move counts, outcomes, scopes, coverage, and evidence. */
function variant(base: Rec, seed: number): Rec {
  const rand = rng(seed)
  const pick = <T,>(xs: T[]) => xs[Math.floor(rand() * xs.length)]!
  const payload = structuredClone(base)
  const QUEUES = ['amd_mi250_1', 'amd_mi300_1', 'amd_mi300_8', 'amd_mi325_1', 'amd_mi355_4', 'amd_mi355b_1', 'AMD_MI300_2', 'gpu_1', 'amd_mi3000_1', '']
  const NODES = ['node-a', 'node-b', ' node-a ', '', 'Node-B', 'unidentified']
  for (const id of v2.QUEUE_DNS_WINDOW_IDS) {
    const block = payload.windows[id]
    if (rand() < 0.25 && block.rows.length === 0) {
      block.rows = Array.from({ length: 1 + Math.floor(rand() * 6) }, () => ({
        queue: pick(QUEUES),
        node: pick(NODES),
        affected_jobs: Math.floor(rand() * 5),
        episodes: Math.floor(rand() * 7),
        huggingface_affected_jobs: Math.floor(rand() * 3),
        evidence_total: Math.floor(rand() * 4),
        passed_jobs: 0,
        soft_failed_jobs: 0,
        hard_failed_jobs: 0,
      }))
    }
    for (const row of block.rows) {
      if (rand() < 0.3) row.queue = pick(QUEUES)
      if (rand() < 0.2) row.node = pick(NODES)
      if (rand() < 0.3) {
        const a = Number(row.affected_jobs) || 0
        const p = Math.floor(rand() * (a + 1))
        const s = Math.floor(rand() * (a - p + 1))
        Object.assign(row, { passed_jobs: p, soft_failed_jobs: s, hard_failed_jobs: a - p - s })
      }
      if (rand() < 0.1) row.passed_jobs = pick([1.5, '2', -1, null, true])
      if (rand() < 0.1) row.affected_jobs = pick(['3', 0, 7, ''])
      if (rand() < 0.1) row.episodes = pick([0, '4', 9])
    }
    if (rand() < 0.3) block.coverage = pick([{ status: 'complete', complete: true }, { status: 'partial', scanned_jobs: 3, pending_jobs: 1 }, { complete: true, reason: 'r', warnings: ['w', 'w'] }])
    if (rand() < 0.1) delete block.coverage
  }
  if (rand() < 0.3) delete payload.outcome_contract
  if (rand() < 0.2) {
    payload.publication_retention = {
      window_rows: Object.fromEntries(
        v2.QUEUE_DNS_WINDOW_IDS.map((id) => {
          const published = payload.windows[id].rows.length
          const omitted = rand() < 0.3 ? 2 : 0
          return [id, { source: published + omitted, published, omitted, complete: omitted === 0 }]
        }),
      ),
    }
  }
  for (const item of payload.evidence.items) {
    if (rand() < 0.2) item.state = pick(['passed', 'soft', 'soft_fail', 'hard', 'failed', 'canceled', 'PASSED'])
    if (rand() < 0.05) item.job_id = 'not-a-uuid'
    if (rand() < 0.05) item.pipeline = 'amd-ci'
    if (rand() < 0.05) item.target_categories = [...item.target_categories, 'pypi', 'mystery']
  }
  if (rand() < 0.5) payload.__sourceAsset = pick([v2.DNS_LIVE_ASSET, v2.DNS_FALLBACK_ASSET])
  return payload
}

describe('DNS health domain matches the classic renderer', () => {
  it('reads the real Pages and live snapshots identically', () => {
    for (const payload of [pages, live]) {
      expect(v2.queueDnsPayloadValid(payload)).toBe(true)
      comparePure(payload)
    }
  })

  it('renders the same summary, queue cards, node bars, and evidence drawers on the real snapshots', async () => {
    let bars = 0
    let evidenceRows = 0
    let notes = 0
    for (const payload of [pages, { ...live, __sourceAsset: v2.DNS_LIVE_ASSET }]) {
      for (const windowId of [...v2.QUEUE_DNS_WINDOW_IDS, 'bogus']) {
        for (const scope of SCOPES) {
          const view = expectSameView(payload, windowId, scope)
          bars += view.drawers.length
          evidenceRows += view.drawers.reduce((sum, d) => sum + d.rows.length, 0)
          notes += view.drawers.reduce((sum, d) => sum + d.notes.length, 0)
        }
      }
    }
    expect(bars).toBeGreaterThan(50)
    expect(evidenceRows).toBeGreaterThan(50)
    expect(notes).toBeGreaterThan(5)
    await expectGoldenFile('dns', 'live-week', v2Facts({ ...live, __sourceAsset: v2.DNS_LIVE_ASSET }, '168h', 'amd', NOW))
  })

  it('flags staleness the same way when only the generation time or only the window end is old', () => {
    const seen = new Set<string>()
    for (const base of [pages, live]) {
      const windowId = v2.QUEUE_DNS_WINDOW_IDS[0]!
      const endMs = Date.parse(v2.queueDnsWindow(base, windowId).block!.end_exclusive)
      for (const [genOffsetH, nowOffsetH] of [
        [0, 1],
        [0, 20],
        [24, 25],
        [-24, 1],
        [-14, 1],
        [14, 15],
        [48, 49],
      ] as const) {
        const payload = structuredClone(base)
        payload.generated_at = new Date(endMs + genOffsetH * 3600_000).toISOString()
        const now = endMs + nowOffsetH * 3600_000
        const block = v2.queueDnsWindow(payload, windowId).block
        const fromV2 = v2.queueDnsFreshness(payload, block, now)
        parity('queueDnsFreshness', fromV2, classic.queueDnsFreshness!(payload, block, now))
        expect(v2.dnsPresentation(payload, windowId, 'amd', now).freshness).toEqual(fromV2)
        seen.add(String(fromV2.stale))
        // The classic renderer reads the wall clock.
        vi.setSystemTime(now)
        const view = expectSameView(payload, windowId, 'amd', now)
        expect(view.stale !== null).toBe(fromV2.stale)
        expect(String(view.badge).startsWith('Stale - ')).toBe(fromV2.stale)
        vi.setSystemTime(NOW)
      }
    }
    expect([...seen].sort()).toEqual(['false', 'true'])
  })

  it('agrees on generated variants of the snapshot', () => {
    let rendered = 0
    for (let seed = 1; seed <= 60; seed += 1) {
      const payload = variant(seed % 2 ? pages : live, seed)
      comparePure(payload)
      const windowId = v2.QUEUE_DNS_WINDOW_IDS[seed % v2.QUEUE_DNS_WINDOW_IDS.length]!
      rendered += expectSameView(payload, windowId, SCOPES[seed % 3]!).cards.length
    }
    expect(rendered).toBeGreaterThan(20)
  })

  it('agrees on counts, outcomes, labels, and URLs for arbitrary inputs', () => {
    const rand = rng(9091)
    const pick = <T,>(xs: T[]) => xs[Math.floor(rand() * xs.length)]!
    const RAW = [null, undefined, '', true, false, 0, 1, 2.7, -3, '4', 'x', Infinity, NaN, '1e2']
    for (let i = 0; i < 2000; i += 1) {
      const raw = pick(RAW)
      parity('queueDnsCount', v2.queueDnsCount(raw), classic.queueDnsCount!(raw))
      const row: Rec = {}
      for (const key of ['passed_jobs', 'soft_failed_jobs', 'hard_failed_jobs', 'affected_jobs']) if (rand() < 0.85) row[key] = pick([0, 1, 2, 3, '1', 1.5, -1, null])
      parity('queueDnsOutcomeCounts', v2.queueDnsOutcomeCounts(row), classic.queueDnsOutcomeCounts!(row))
      const coverage = pick([{ complete: true }, { complete: false }, null, {}])
      parity('queueDnsDisplayCount', v2.queueDnsDisplayCount(raw, coverage), classic.queueDnsDisplayCount!(raw, coverage))
      const q = pick(['amd_mi250_1', 'amd_mi355b_2', 'amd_mi325_1', 'AMD_MI300_8', 'amd_mi300b_1', 'amd_mi3001', 'gpu_1', ' amd_mi355_1 ', null])
      for (const scope of SCOPES) parity('queueDnsMatchesPublishedScope', v2.queueDnsMatchesPublishedScope(q, scope), classic.queueDnsMatchesPublishedScope!(q, scope))
      const ev = {
        pipeline: pick(['ci', 'amd-ci', '']),
        build_number: pick([1, 0, '12', '012', 2 ** 60, -4, null]),
        job_id: pick(['01a00c92-9cab-4dd2-9a75-32210e739d02', '01A00C92-9CAB-4DD2-9A75-32210E739D02', '01a00c92-9cab-6dd2-9a75-32210e739d02', 'x']),
        target_categories: pick([['github', 'github', 'pypi'], ['mystery', 'unknown'], 'github', []]),
        signature_ids: pick([['a_b', '', null], 'x', ['temporary_name_resolution']]),
        state: pick(['passed', 'SOFT', 'soft_failed', 'failed', 'hard', 'canceled', '', null]),
      }
      parity('queueDnsEvidenceUrl', v2.queueDnsEvidenceUrl(ev), classic.queueDnsEvidenceUrl!(ev))
      parity('queueDnsTargetLabels', v2.queueDnsTargetLabels(ev), classic.queueDnsTargetLabels!(ev))
      parity('queueDnsSignatureLabels', v2.queueDnsSignatureLabels(ev), classic.queueDnsSignatureLabels!(ev))
      parity('queueDnsOutcomePresentation', v2.queueDnsOutcomePresentation(ev.state), classic.queueDnsOutcomePresentation!(ev.state))
    }
  })

  it('rejects the same malformed payloads', () => {
    const mutations: ((p: Rec) => void)[] = [
      (p) => (p.schema_version = 2),
      (p) => (p.outcome_contract = 'other'),
      (p) => (p.default_window = '3h'),
      (p) => p.window_options.pop(),
      (p) => (p.window_options[1].label = 'x'),
      (p) => delete p.windows['12h'],
      (p) => (p.scope.pipelines = ['ci', 'amd-ci']),
      (p) => (p.scope.hardware_scope = 'any'),
      (p) => (p.retention.end_exclusive = '2020-01-01T00:00:00Z'),
      (p) => (p.windows['3h'].start = p.windows['1h'].start),
      (p) => (p.windows['24h'].totals.passed_jobs += 1),
      (p) => (p.evidence.items[0].window_ids = ['1h']),
      (p) => (p.evidence.items[0].window_metrics['720h'].episodes = 0),
      (p) => (p.evidence.items[0].episodes = 999),
      (p) => (p.publication_retention = { window_rows: {} }),
      (p) => delete p.count_basis,
      (p) => (p.coverage = []),
    ]
    for (const mutate of mutations) {
      const copy = structuredClone(pages)
      mutate(copy)
      parity('queueDnsPayloadValid', v2.queueDnsPayloadValid(copy), classic.queueDnsPayloadValid!(copy))
      expect(v2.queueDnsPayloadValid(copy)).toBe(false)
    }
  })

  it('arbitrates live and Pages candidates like classic loadQueueDns', async () => {
    const older = structuredClone(live)
    const newer = structuredClone(live)
    for (const [payload, shiftHours] of [
      [older, -2],
      [newer, 0],
    ] as const) {
      const shift = (ts: string) => new Date(Date.parse(ts) + shiftHours * 3600_000).toISOString().replace('.000Z', 'Z')
      payload.generated_at = shift(payload.generated_at)
      payload.retention.end_exclusive = shift(payload.retention.end_exclusive)
      for (const block of Object.values(payload.windows) as Rec[]) {
        block.start = shift(block.start)
        block.end_exclusive = shift(block.end_exclusive)
      }
      for (const item of payload.evidence.items) {
        for (const metric of Object.values(item.window_metrics) as Rec[]) {
          metric.first_at = shift(metric.first_at)
          metric.last_at = shift(metric.last_at)
        }
        item.first_at = shift(item.first_at)
        item.last_at = shift(item.last_at)
      }
    }
    expect(v2.queueDnsPayloadValid(older) && v2.queueDnsPayloadValid(newer)).toBe(true)
    const scenarios: { live: Rec | null; pages: Rec | null; liveDelay: number; pagesDelay: number }[] = [
      { live: newer, pages: older, liveDelay: 0, pagesDelay: 0 },
      { live: older, pages: newer, liveDelay: 0, pagesDelay: 0 },
      { live: older, pages: older, liveDelay: 0, pagesDelay: 0 },
      { live: null, pages: older, liveDelay: 0, pagesDelay: 0 },
      { live: newer, pages: null, liveDelay: 0, pagesDelay: 0 },
      { live: { schema_version: 9 }, pages: older, liveDelay: 0, pagesDelay: 0 },
      { live: newer, pages: older, liveDelay: 80, pagesDelay: 0 },
    ]
    for (const scenario of scenarios) {
      const respond = (url: string) => {
        const isLive = url.startsWith(v2.DNS_LIVE_ASSET)
        const body = isLive ? scenario.live : scenario.pages
        const delay = isLive ? scenario.liveDelay : scenario.pagesDelay
        return new Promise<Response>((resolve) =>
          setTimeout(() => resolve(body ? new Response(JSON.stringify(body)) : new Response('{}', { status: 404 })), delay),
        )
      }
      globalThis.fetch = ((url: string) => respond(String(url))) as typeof fetch
      classic.invalidateDnsData!()
      const fromClassic = await classic.loadQueueDns!(2000).then(
        (p: Rec) => p.__sourceAsset + '@' + p.generated_at,
        (e: Error) => 'error:' + e.message,
      )
      const loader = v2.createDnsLoader(async (source) => {
        const response = await respond(source)
        if (!response.ok) throw new Error('HTTP ' + response.status)
        return response.json()
      })
      const fromV2 = await loader.load(2000).then(
        (p) => p.__sourceAsset + '@' + p.generated_at,
        (e: Error) => 'error:' + e.message,
      )
      parity('loadQueueDns', fromV2, fromClassic)
    }
  }, 60_000)
})

describe('DNS health helpers on hand-written inputs', () => {
  it('counts only non-negative integers', () => {
    expect([null, '', true, 0, 1, 2.7, -3, '4', 'x', NaN].map((raw) => v2.queueDnsCount(raw))).toEqual([0, 0, 0, 0, 1, 2, 0, 4, 0, 0])
    expect(v2.queueDnsOutcomeCounts({ passed_jobs: 1, soft_failed_jobs: '2', hard_failed_jobs: 1.5, affected_jobs: 5 })).toEqual({
      available: false,
      passed: 1,
      softFailed: 2,
      hardFailed: 1,
    })
  })

  it('links exact logs only for canonical job ids', () => {
    expect(v2.queueDnsEvidenceUrl({ pipeline: 'ci', build_number: 12, job_id: '01a00c92-9cab-4dd2-9a75-32210e739d02' })).toBe(
      'https://buildkite.com/vllm/ci/builds/12/list?jid=01a00c92-9cab-4dd2-9a75-32210e739d02&tab=output',
    )
    expect(v2.queueDnsEvidenceUrl({ pipeline: 'ci', build_number: 12, job_id: 'x' })).toBe('')
  })

  it('labels targets, signatures, and outcomes', () => {
    expect(v2.queueDnsTargetLabels({ target_categories: ['github', 'github', 'pypi', 'mystery'] })).toEqual(['GitHub', 'PyPI', 'Unknown target'])
    expect(v2.queueDnsSignatureLabels({ signature_ids: ['temporary_name_resolution', 'a_b', ''] })).toEqual(['temporary name resolution', 'a b'])
    expect(['passed', 'soft', 'failed', ''].map((state) => v2.queueDnsOutcomePresentation(state))).toEqual([
      { label: 'Passed after observation', tone: 'pass' },
      { label: 'Soft-failed', tone: 'soft' },
      { label: 'Hard-failed', tone: 'hard' },
      { label: 'Unknown', tone: 'neutral' },
    ])
  })
})
