import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { effectScope, type EffectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MANIFEST, publish, QUEUE_LIVE, sectionPath, SOURCES, STATUS } from './publication'
import { elapse, installServer } from './server'

type UseDataset = typeof import('@/composables/useDataset')
type UseOperations = typeof import('@/composables/useOperations')
let datasets: UseDataset
let wrappers: UseOperations
let server: ReturnType<typeof installServer>
let visibility: DocumentVisibilityState
const scopes: EffectScope[] = []

const FIVE_MINUTES = 5 * 60 * 1000
const G1 = '2026-10-19T10:00:00Z'
const G2 = '2026-10-19T10:15:00Z'
const G3 = '2026-10-19T10:30:00Z'

function setVisibility(state: DocumentVisibilityState) {
  visibility = state
  document.dispatchEvent(new Event('visibilitychange'))
}

/** Runs a composable inside a scope, as a mounted component would. */
function mount<T>(composable: () => T): T {
  const scope = effectScope()
  scopes.push(scope)
  return scope.run(composable)!
}

const flush = () => elapse(0)

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] })
  vi.setSystemTime(new Date(G1))
  vi.resetModules()
  visibility = 'visible'
  vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility)
  server = installServer()
  server.set(STATUS, { generated_at: G1 })
  publish(server, { generatedAt: G1, sources: SOURCES })
  datasets = await import('@/composables/useDataset')
  wrappers = await import('@/composables/useOperations')
})

afterEach(() => {
  for (const scope of scopes.splice(0)) scope.stop()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('useDataset', () => {
  it('shares one load and one state per key', async () => {
    const first = mount(() => datasets.useDataset({ sections: ['nightly', 'amd_test_health'] }))
    const second = mount(() => datasets.useDataset({ sections: ['amd_test_health', 'nightly'] }))
    expect(second.data).toBe(first.data)
    expect(first.loading.value).toBe(true)
    await flush()
    expect(first.loading.value).toBe(false)
    expect(first.data.value?.nightly.label).toBe(`nightly@${G1}`)
    expect(first.updatedAt.value).toBe(G1)
    expect(server.count(MANIFEST)).toBe(1)
    expect(server.count(sectionPath('nightly'))).toBe(1)
  })

  it('polls the publication every five minutes while visible and not while hidden', async () => {
    mount(() => datasets.useDataset({ sections: ['nightly'] }))
    mount(() => datasets.useDataset('publication_status'))
    await flush()
    expect(server.count(STATUS)).toBe(1)
    await vi.advanceTimersByTimeAsync(FIVE_MINUTES)
    await flush()
    expect(server.count(MANIFEST)).toBe(2)
    expect(server.count(STATUS)).toBe(2)
    setVisibility('hidden')
    await vi.advanceTimersByTimeAsync(2 * FIVE_MINUTES)
    await flush()
    expect(server.count(MANIFEST)).toBe(2)
    expect(server.count(STATUS)).toBe(2)
  })

  it('polls when the page becomes visible after the interval, not before', async () => {
    mount(() => datasets.useDataset({ sections: [] }))
    await flush()
    setVisibility('hidden')
    await vi.advanceTimersByTimeAsync(60_000)
    setVisibility('visible')
    await flush()
    expect(server.count(MANIFEST)).toBe(1)
    setVisibility('hidden')
    await vi.advanceTimersByTimeAsync(FIVE_MINUTES)
    setVisibility('visible')
    await flush()
    expect(server.count(MANIFEST)).toBe(2)
  })

  it('refetches a section only when its source timestamps change', async () => {
    const health = mount(() => datasets.useDataset({ sections: ['nightly'] }))
    await flush()
    const before = health.data.value

    // A queue-only publication: new shell, nightly inputs unchanged.
    publish(server, { generatedAt: G2, sources: { ...SOURCES, queue_jobs: G2 } })
    await vi.advanceTimersByTimeAsync(FIVE_MINUTES)
    await flush()
    expect(server.count(sectionPath('nightly'))).toBe(1)
    expect(health.updatedAt.value).toBe(G2)
    expect(health.data.value?.sources.queue_jobs.timestamp).toBe(G2)
    expect(health.data.value?.nightly.label).toBe(`nightly@${G1}`)
    expect(health.data.value).not.toBe(before)

    // An unchanged manifest changes nothing.
    const unchanged = health.data.value
    await vi.advanceTimersByTimeAsync(FIVE_MINUTES)
    await flush()
    expect(health.data.value).toBe(unchanged)

    // A full collection: analytics advanced, so nightly is refetched.
    publish(server, { generatedAt: G3, sources: { ...SOURCES, analytics: G3 } })
    await vi.advanceTimersByTimeAsync(FIVE_MINUTES)
    await flush()
    expect(server.count(sectionPath('nightly'))).toBe(2)
    expect(health.data.value?.nightly.label).toBe(`nightly@${G3}`)
    expect(health.refreshing.value).toBe(false)
  })

  it('never refreshes the reliability section automatically, only on request', async () => {
    const reliability = mount(() => datasets.useDataset({ sections: ['reliability'] }))
    await flush()
    publish(server, { generatedAt: G2, sources: { ...SOURCES, analytics: G2 } })
    await vi.advanceTimersByTimeAsync(FIVE_MINUTES)
    await flush()
    expect(server.count(MANIFEST)).toBe(2)
    expect(server.count(sectionPath('reliability'))).toBe(1)
    expect(reliability.updatedAt.value).toBe(G1)
    await reliability.refresh()
    expect(server.count(sectionPath('reliability'))).toBe(2)
    expect(reliability.data.value?.reliability.label).toBe(`reliability@${G2}`)
  })

  it('retries until a refetched section matches the manifest generation, never showing a mix', async () => {
    publish(server, { generatedAt: G1, sources: SOURCES, descriptors: { nightly: { generation: 'g1' } } })
    const health = mount(() => datasets.useDataset({ sections: ['nightly'] }))
    await flush()
    publish(server, { generatedAt: G2, sources: { ...SOURCES, analytics: G2 }, descriptors: { nightly: { generation: 'g2' } } })
    let calls = 0
    // The CDN keeps serving the previous section for four requests.
    server.set(sectionPath('nightly'), () => (++calls <= 4 ? { generation: 'g1', nightly: { label: 'old' } } : { generation: 'g2', nightly: { label: 'new' } }))
    const seen: string[] = []
    const record = () => seen.push(`${health.updatedAt.value}:${health.data.value?.generation}`)
    record()
    await vi.advanceTimersByTimeAsync(FIVE_MINUTES)
    for (let step = 0; step < 20; step += 1) {
      await elapse(250, 1)
      record()
    }
    expect(health.data.value?.nightly.label).toBe('new')
    expect(health.error.value).toBeNull()
    expect(new Set(seen)).toEqual(new Set([`${G1}:g1`, `${G2}:g2`]))
  })

  it('keeps the last good data when a refresh fails, and the view wrappers hide that error', async () => {
    const sections = mount(() => wrappers.useOperationSections(['nightly']))
    const raw = mount(() => datasets.useDataset({ sections: ['nightly'] }))
    await flush()
    server.set(MANIFEST, { status: 500 })
    await vi.advanceTimersByTimeAsync(FIVE_MINUTES)
    await elapse(2000, 8)
    const manifest = mount(() => datasets.useDataset('operations_manifest'))
    expect(manifest.error.value).toBe(`${MANIFEST} returned HTTP 500`)
    expect(raw.data.value?.nightly.label).toBe(`nightly@${G1}`)
    expect(sections.error.value).toBeNull()
  })

  it('reports a first-load failure through the wrappers and recovers on retry', async () => {
    server.set(sectionPath('nightly'), { status: 404 })
    const sections = mount(() => wrappers.useOperationSections(['nightly']))
    await elapse(2000, 8)
    expect(sections.error.value).toBe(`${sectionPath('nightly')} returned HTTP 404`)
    expect(sections.data.value).toBeNull()
    publish(server, { generatedAt: G1, sources: SOURCES })
    await sections.retry()
    expect(sections.error.value).toBeNull()
    expect(sections.data.value?.nightly.label).toBe(`nightly@${G1}`)
  })

  it('re-arbitrates the live queue on every poll and keeps data when the snapshot is unchanged', async () => {
    server.set(QUEUE_LIVE, { queue: { snapshot: { ts: '2026-10-19T10:05:00Z' }, origin: 'live' } })
    const omni = mount(() => datasets.useDataset({ sections: ['queue'] }))
    await flush()
    expect(omni.data.value?.queue.origin).toBe('live')
    const shown = omni.data.value
    await vi.advanceTimersByTimeAsync(FIVE_MINUTES)
    await flush()
    expect(server.count(QUEUE_LIVE)).toBe(2)
    expect(omni.data.value).toBe(shown)
    server.set(QUEUE_LIVE, { queue: { snapshot: { ts: '2026-10-19T10:12:00Z' }, origin: 'live' } })
    await vi.advanceTimersByTimeAsync(FIVE_MINUTES)
    await flush()
    expect(omni.data.value?.queue.snapshot.ts).toBe('2026-10-19T10:12:00Z')
  })

  it('refetches a source-bound asset only when its source timestamp changes', async () => {
    let calls = 0
    server.set('data/vllm/ci/amd_test_matrix.json', () => ({ generated_at: G1, calls: ++calls }))
    mount(() => datasets.useDataset({ sections: [] }))
    await flush()
    const matrix = mount(() => datasets.useDataset('amd_test_matrix'))
    await flush()
    publish(server, { generatedAt: G2, sources: { ...SOURCES, analytics: G2 } })
    await vi.advanceTimersByTimeAsync(FIVE_MINUTES)
    await flush()
    expect(matrix.data.value?.calls).toBe(1)
    publish(server, { generatedAt: G3, sources: { ...SOURCES, amd_test_matrix: G3 } })
    await vi.advanceTimersByTimeAsync(FIVE_MINUTES)
    await flush()
    expect(matrix.data.value?.calls).toBe(2)
  })

  it('stops polling once every consumer is disposed and catches up when one returns', async () => {
    const health = mount(() => datasets.useDataset({ sections: ['nightly'] }))
    await flush()
    scopes.splice(0).forEach((scope) => scope.stop())
    publish(server, { generatedAt: G2, sources: { ...SOURCES, analytics: G2 } })
    await vi.advanceTimersByTimeAsync(3 * FIVE_MINUTES)
    await flush()
    expect(server.count(MANIFEST)).toBe(1)

    // Another view learns of the new manifest; the returning one then syncs without a poll.
    mount(() => datasets.useDataset('operations_manifest')).refresh()
    await flush()
    mount(() => datasets.useDataset({ sections: ['nightly'] }))
    await flush()
    expect(health.data.value?.nightly.label).toBe(`nightly@${G2}`)
  })
})

describe('useDnsData', () => {
  it('serves the newer DNS candidate and refreshes it on every poll', async () => {
    const fixtures = resolve(__dirname, '../fixtures')
    const live = JSON.parse(readFileSync(resolve(fixtures, 'dns_live.json'), 'utf8'))
    const pages = JSON.parse(readFileSync(resolve(fixtures, 'dns_pages.json'), 'utf8'))
    const { DATASETS } = await import('@/data/sources')
    const [livePath, pagesPath] = DATASETS.dns.paths
    server.set(livePath, live)
    server.set(pagesPath, pages)
    const { useDnsData } = await import('@/components/analytics/dns/useDnsData')
    const dns = mount(() => useDnsData())
    await elapse(400)
    expect(dns.payload.value?.generated_at).toBe(live.generated_at)
    expect(dns.payload.value?.__sourceAsset).toBe(livePath)
    await vi.advanceTimersByTimeAsync(FIVE_MINUTES)
    await elapse(400)
    expect(server.count(livePath)).toBe(2)
    expect(server.count(pagesPath)).toBe(2)
  })
})
