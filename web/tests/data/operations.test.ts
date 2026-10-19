import { createHash } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MANIFEST, manifestOf, publish, QUEUE_LIVE, sectionPath, SOURCES } from './publication'
import { installServer, settle } from './server'

type Operations = typeof import('@/data/operations')
let operations: Operations
let server: ReturnType<typeof installServer>

beforeEach(async () => {
  // Node's WebCrypto settles through setImmediate, which must stay real.
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] })
  vi.resetModules()
  server = installServer()
  operations = await import('@/data/operations')
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const G1 = '2026-10-19T10:00:00Z'
const G2 = '2026-10-19T10:30:00Z'

describe('loadOperations', () => {
  it('merges the manifest shell with the named sections', async () => {
    publish(server, { generatedAt: G1, sources: SOURCES })
    server.set(sectionPath('nightly'), { nightly: { label: 'nightly' }, home: { status: 'attention' } })
    const ops = await operations.loadOperations(['nightly'])
    expect(ops.generated_at).toBe(G1)
    expect(ops.nightly).toEqual({ label: 'nightly' })
    expect(ops.home).toEqual({ status: 'attention' })
    expect(ops.sources.analytics.timestamp).toBe(SOURCES.analytics)
  })

  it('fails clearly for a section the manifest does not list', async () => {
    publish(server, { generatedAt: G1, sources: SOURCES })
    await expect(operations.loadOperations(['ownership'])).rejects.toThrow('Operations section "ownership" is missing from the manifest')
  })

  it('prefers the newer of the live and published queue sections', async () => {
    publish(server, { generatedAt: G1, sources: SOURCES })
    server.set(QUEUE_LIVE, { queue: { snapshot: { ts: '2026-10-19T10:20:00Z' }, origin: 'live' } })
    expect((await operations.loadOperations(['queue'])).queue.origin).toBe('live')
    server.set(QUEUE_LIVE, { queue: { snapshot: { ts: '2026-10-19T09:00:00Z' }, origin: 'live' } })
    const manifest = await operations.fetchManifest(true)
    expect((await operations.fetchSection(manifest, 'queue', true)).queue.origin).toBe('pages')
  })

  it('falls back to the published queue section when the live branch is unreachable', async () => {
    publish(server, { generatedAt: G1, sources: SOURCES })
    server.set(QUEUE_LIVE, { status: 503 })
    const ops = operations.loadOperations(['queue'])
    await vi.advanceTimersByTimeAsync(1250)
    expect((await ops).queue.origin).toBe('pages')
  })
})

describe('section cache', () => {
  it('downloads the manual reliability section once per page across publications', async () => {
    publish(server, { generatedAt: G1, sources: SOURCES })
    await operations.loadOperations(['reliability'])
    publish(server, { generatedAt: G2, sources: { ...SOURCES, analytics: G2 } })
    const manifest = await operations.fetchManifest(true)
    const ops = await operations.loadOperations(['reliability'])
    expect(ops.reliability.label).toBe(`reliability@${G1}`)
    await operations.fetchSection(manifest, 'reliability')
    expect(server.count(sectionPath('reliability'))).toBe(1)
  })

  it('replaces, rather than retains, a section whose inputs changed', async () => {
    publish(server, { generatedAt: G1, sources: SOURCES })
    const first = await operations.fetchManifest()
    await operations.fetchSection(first, 'nightly')
    publish(server, { generatedAt: G2, sources: { ...SOURCES, queue_jobs: G2 } })
    const queueOnly = await operations.fetchManifest(true)
    expect((await operations.fetchSection(queueOnly, 'nightly')).nightly.label).toBe(`nightly@${G1}`)
    expect(server.count(sectionPath('nightly'))).toBe(1)
    publish(server, { generatedAt: G2, sources: { ...SOURCES, analytics: G2 } })
    const analytics = await operations.fetchManifest(true)
    expect((await operations.fetchSection(analytics, 'nightly')).nightly.label).toBe(`nightly@${G2}`)
    await operations.fetchSection(first, 'nightly')
    expect(server.count(sectionPath('nightly'))).toBe(3)
  })
})

describe('generation consistency', () => {
  it('retries a section that lags the manifest until it matches', async () => {
    publish(server, { generatedAt: G2, sources: SOURCES, descriptors: { nightly: { generation: 'g2' } } })
    let calls = 0
    server.set(sectionPath('nightly'), () => ({ generation: ++calls < 5 ? 'g1' : 'g2', nightly: { calls } }))
    const ops = operations.loadOperations(['nightly'])
    await vi.advanceTimersByTimeAsync(5000)
    const loaded = await ops
    expect(loaded.generation).toBe('g2')
    expect(loaded.nightly.calls).toBe(5)
    // Three attempts against the first manifest, then a fresh manifest and a fresh section.
    expect(server.count(MANIFEST)).toBe(2)
  })

  it('refetches a stale manifest when the sections are already newer', async () => {
    let manifestCalls = 0
    server.set(MANIFEST, () =>
      manifestOf({ generatedAt: ++manifestCalls === 1 ? G1 : G2, sources: SOURCES, descriptors: { nightly: { generation: manifestCalls === 1 ? 'g1' : 'g2' } } }),
    )
    server.set(sectionPath('nightly'), { generation: 'g2', nightly: {} })
    const ops = operations.loadOperations(['nightly'])
    await vi.advanceTimersByTimeAsync(5000)
    expect((await ops).generated_at).toBe(G2)
  })

  it('gives up with a mismatch error when the generations never agree', async () => {
    publish(server, { generatedAt: G2, sources: SOURCES, descriptors: { nightly: { generation: 'g2' } } })
    server.set(sectionPath('nightly'), { generation: 'g1', nightly: {} })
    const ops = operations.loadOperations(['nightly']).catch((error: unknown) => error)
    await vi.advanceTimersByTimeAsync(5000)
    const error = await ops
    expect(error).toBeInstanceOf(operations.GenerationMismatchError)
    expect((error as Error).message).toBe('Section "nightly" is generation g1, the manifest expects g2')
  })

  it('verifies the section bytes against the manifest sha256', async () => {
    const body = JSON.stringify({ nightly: { ok: true } })
    const sha256 = createHash('sha256').update(body).digest('hex')
    server.set(MANIFEST, manifestOf({ generatedAt: G1, sources: SOURCES, descriptors: { nightly: { sha256 } } }))
    server.set(sectionPath('nightly'), () => new Response(body))
    await expect(operations.loadOperations(['nightly'])).resolves.toMatchObject({ nightly: { ok: true } })
    expect(server.count(sectionPath('nightly'))).toBe(1)

    server.set(MANIFEST, manifestOf({ generatedAt: G2, sources: SOURCES, descriptors: { nightly: { sha256: '0'.repeat(64) } } }))
    const manifest = await operations.fetchManifest(true)
    const mismatch = operations.fetchSection(manifest, 'nightly', true).catch((error: unknown) => error)
    expect(await settle(mismatch)).toBeInstanceOf(operations.GenerationMismatchError)
    expect(server.count(sectionPath('nightly'))).toBe(4)
  })
})

describe('section stamps', () => {
  const manifest = (sources: Record<string, string>, descriptors = {}) =>
    manifestOf({ generatedAt: G1, sources, descriptors }) as unknown as import('@/data/operations').OperationsManifest

  it('change only when one of the section inputs changes', () => {
    const base = manifest(SOURCES)
    const queueOnly = manifest({ ...SOURCES, queue_jobs: G2 })
    const analytics = manifest({ ...SOURCES, analytics: G2 })
    expect(operations.sectionStamp(queueOnly, 'nightly')).toBe(operations.sectionStamp(base, 'nightly'))
    expect(operations.sectionStamp(analytics, 'nightly')).not.toBe(operations.sectionStamp(base, 'nightly'))
    expect(operations.sectionStamp(queueOnly, 'omni')).not.toBe(operations.sectionStamp(base, 'omni'))
    const ingest = manifest({ ...SOURCES, amd_test_signal: G2 })
    expect(operations.sectionStamp(ingest, 'amd_test_health')).not.toBe(operations.sectionStamp(base, 'amd_test_health'))
  })

  it('change when a section generation or sha256 changes', () => {
    const base = manifest(SOURCES, { nightly: { generation: 'g1' } })
    expect(operations.sectionStamp(manifest(SOURCES, { nightly: { generation: 'g2' } }), 'nightly')).not.toBe(
      operations.sectionStamp(base, 'nightly'),
    )
    expect(operations.sectionStamp(manifest(SOURCES, { nightly: { generation: 'g1', sha256: 'ab' } }), 'nightly')).not.toBe(
      operations.sectionStamp(base, 'nightly'),
    )
  })

  it('mark the live queue as always due and reliability as never automatic', () => {
    expect(operations.sectionStamp(manifest(SOURCES), 'queue')).toBeNull()
    expect(operations.sectionStamp(manifest(SOURCES), 'reliability')).toBe('')
    expect(operations.isManualSection('reliability')).toBe(true)
    expect(operations.isManualSection('nightly')).toBe(false)
  })
})
