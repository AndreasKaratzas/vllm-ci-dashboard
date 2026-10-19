import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { hang, installServer } from './server'

type Client = typeof import('@/data/client')
let client: Client
let server: ReturnType<typeof installServer>

beforeEach(async () => {
  vi.useFakeTimers()
  vi.resetModules()
  server = installServer()
  client = await import('@/data/client')
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('fetchJSON', () => {
  it('requests with no-store and a unique cache-busting query per attempt', async () => {
    server.set('data/a.json', { ok: 1 })
    await expect(client.fetchJSON('data/a.json')).resolves.toEqual({ ok: 1 })
    const [request] = server.requests
    expect(request!.url).toMatch(/^\/data\/a\.json\?_health=\d+-0$/)
    expect(request!.init?.cache).toBe('no-store')
  })

  it('appends the cache buster to an existing query and leaves absolute URLs alone', async () => {
    server.set('https://example.test/x.json', { ok: 1 })
    await client.fetchJSON('https://example.test/x.json?v=3')
    expect(server.requests[0]!.url).toMatch(/^https:\/\/example\.test\/x\.json\?v=3&_health=/)
  })

  it('retries failures with 250 ms then 1 s backoff and gives up after three attempts', async () => {
    server.set('data/a.json', { status: 503 })
    const result = client.fetchJSON('data/a.json').catch((error: Error) => error)
    await vi.advanceTimersByTimeAsync(0)
    expect(server.count('data/a.json')).toBe(1)
    await vi.advanceTimersByTimeAsync(249)
    expect(server.count('data/a.json')).toBe(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(server.count('data/a.json')).toBe(2)
    await vi.advanceTimersByTimeAsync(1000)
    expect(server.count('data/a.json')).toBe(3)
    expect(((await result) as Error).message).toBe('data/a.json returned HTTP 503')
  })

  it('recovers when a retry succeeds', async () => {
    let calls = 0
    server.set('data/a.json', () => (++calls < 2 ? { status: 500 } : { ok: calls }))
    const result = client.fetchJSON('data/a.json')
    await vi.advanceTimersByTimeAsync(250)
    await expect(result).resolves.toEqual({ ok: 2 })
  })

  it('aborts an attempt at its timeout and retries', async () => {
    let calls = 0
    server.set('data/a.json', (init: RequestInit | undefined) => (++calls === 1 ? hang(init) : { ok: true }))
    const result = client.fetchJSON('data/a.json', { timeoutMs: 1000 })
    await vi.advanceTimersByTimeAsync(1000 + 250)
    await expect(result).resolves.toEqual({ ok: true })
    expect(server.requests[0]!.init?.signal?.aborted).toBe(true)
  })

  it('reports the timeout when every attempt times out', async () => {
    server.set('data/a.json', hang)
    const result = client.fetchJSON('data/a.json', { timeoutMs: 100, attempts: 2 }).catch((error: Error) => error)
    await vi.advanceTimersByTimeAsync(100 + 250 + 100)
    expect(((await result) as Error).message).toBe('data/a.json timed out after 100 ms')
  })

  it("stops at once, without retrying, when the caller's signal aborts", async () => {
    server.set('data/a.json', hang)
    const controller = new AbortController()
    const result = client.fetchJSON('data/a.json', { signal: controller.signal }).catch((error: unknown) => error)
    await vi.advanceTimersByTimeAsync(0)
    controller.abort(new Error('left the page'))
    expect(((await result) as Error).message).toBe('left the page')
    await vi.advanceTimersByTimeAsync(5000)
    expect(server.count('data/a.json')).toBe(1)
  })

  it('stops during a backoff wait when the signal aborts', async () => {
    server.set('data/a.json', { status: 500 })
    const controller = new AbortController()
    const result = client.fetchJSON('data/a.json', { signal: controller.signal }).catch((error: unknown) => error)
    await vi.advanceTimersByTimeAsync(100)
    controller.abort(new Error('stop'))
    expect(((await result) as Error).message).toBe('stop')
    await vi.advanceTimersByTimeAsync(5000)
    expect(server.count('data/a.json')).toBe(1)
  })

  it('retries a response that the validator rejects', async () => {
    let calls = 0
    server.set('data/a.json', () => ({ generation: ++calls }))
    const validate = (payload: unknown) => {
      if ((payload as { generation: number }).generation < 3) throw new Error('old generation')
    }
    const result = client.fetchJSON('data/a.json', { validate })
    await vi.advanceTimersByTimeAsync(1250)
    await expect(result).resolves.toEqual({ generation: 3 })
  })

  it('shares one request per cache key and evicts a failed one', async () => {
    let calls = 0
    server.set('data/a.json', () => (++calls === 1 ? { status: 500 } : { calls }))
    const first = client.fetchJSON('data/a.json', { attempts: 1 }).catch(() => 'failed')
    const second = client.fetchJSON('data/a.json', { attempts: 1 }).catch(() => 'failed')
    expect(await first).toBe('failed')
    expect(await second).toBe('failed')
    await expect(client.fetchJSON('data/a.json')).resolves.toEqual({ calls: 2 })
    await expect(client.fetchJSON('data/a.json')).resolves.toEqual({ calls: 2 })
    expect(server.count('data/a.json')).toBe(2)
  })

  it('keys the cache by cacheKey, bypasses it when uncached, and clears one key or all', async () => {
    let calls = 0
    server.set('data/a.json', () => ({ calls: ++calls }))
    await expect(client.fetchJSON('data/a.json', { cacheKey: 'g1' })).resolves.toEqual({ calls: 1 })
    await expect(client.fetchJSON('data/a.json', { cacheKey: 'g2' })).resolves.toEqual({ calls: 2 })
    await expect(client.fetchJSON('data/a.json', { cacheKey: 'g1' })).resolves.toEqual({ calls: 1 })
    await expect(client.fetchJSON('data/a.json', { uncached: true })).resolves.toEqual({ calls: 3 })
    client.clearDataCache('g1')
    await expect(client.fetchJSON('data/a.json', { cacheKey: 'g1' })).resolves.toEqual({ calls: 4 })
    await expect(client.fetchJSON('data/a.json', { cacheKey: 'g2' })).resolves.toEqual({ calls: 2 })
    client.clearDataCache()
    await expect(client.fetchJSON('data/a.json', { cacheKey: 'g2' })).resolves.toEqual({ calls: 5 })
  })
})
