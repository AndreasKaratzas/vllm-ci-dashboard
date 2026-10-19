import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installServer } from './server'

type Sources = typeof import('@/data/sources')
let sources: Sources
let server: ReturnType<typeof installServer>

beforeEach(async () => {
  vi.useFakeTimers()
  vi.resetModules()
  server = installServer()
  sources = await import('@/data/sources')
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

const at = (generated_at?: string) => ({ generated_at })

describe('newest-wins arbitration', () => {
  it('picks the newest publication and keeps candidate order on ties', () => {
    const live = at('2026-10-19T10:00:00Z')
    const pages = at('2026-10-19T11:00:00Z')
    const publishedAt = (payload: { generated_at?: string }) => payload.generated_at
    expect(sources.newest([live, pages], publishedAt)).toBe(pages)
    const tie = at('2026-10-19T10:00:00Z')
    expect(sources.newest([live, tie], publishedAt)).toBe(live)
    expect(sources.newest([at(undefined), at('not a date')], publishedAt)).toEqual(at(undefined))
    expect(sources.newest([at(undefined), live], publishedAt)).toBe(live)
    expect(sources.newest([], publishedAt)).toBeUndefined()
  })

  it('loads every candidate and falls back to whichever one is available', async () => {
    server.set('https://live.test/a.json', at('2026-10-19T12:00:00Z'))
    server.set('data/a.json', at('2026-10-19T11:00:00Z'))
    const publishedAt = (payload: { generated_at?: string }) => payload.generated_at
    await expect(sources.loadNewest(['https://live.test/a.json', 'data/a.json'], publishedAt)).resolves.toEqual(at('2026-10-19T12:00:00Z'))
    server.set('https://live.test/b.json', { status: 404 })
    server.set('data/b.json', at('2026-10-19T11:00:00Z'))
    const fallback = sources.loadNewest(['https://live.test/b.json', 'data/b.json'], publishedAt)
    await vi.advanceTimersByTimeAsync(1250)
    await expect(fallback).resolves.toEqual(at('2026-10-19T11:00:00Z'))
  })

  it("reports the published fallback's error when no candidate loads", async () => {
    server.set('data/y.json', { status: 503 })
    const failed = sources.loadNewest(['data/x.json', 'data/y.json'], undefined, { attempts: 1 }).catch((error: Error) => error.message)
    await expect(failed).resolves.toBe('data/y.json returned HTTP 503')
  })

  it('keeps the original error of a single-path dataset', async () => {
    const failed = sources.loadNewest(['data/x.json'], undefined, { attempts: 1 }).catch((error: Error) => error.message)
    await expect(failed).resolves.toBe('data/x.json returned HTTP 404')
  })
})

describe('registry', () => {
  it('rejects an incomplete manifest so the fetch is retried', async () => {
    let calls = 0
    server.set(sources.OPERATIONS_MANIFEST_PATH, () => (++calls === 1 ? { shell: {} } : { shell: {}, sections: {} }))
    const manifest = sources.datasetLoader('operations_manifest')(true, () => {})
    await vi.advanceTimersByTimeAsync(250)
    await expect(manifest).resolves.toEqual({ shell: {}, sections: {} })
    expect(calls).toBe(2)
  })

  it('arbitrates DNS between the live branch and Pages, preferring the newer publication', async () => {
    const fixtures = resolve(__dirname, '../fixtures')
    const live = JSON.parse(readFileSync(join(fixtures, 'dns_live.json'), 'utf8'))
    const pages = JSON.parse(readFileSync(join(fixtures, 'dns_pages.json'), 'utf8'))
    const [livePath, pagesPath] = sources.DATASETS.dns.paths
    server.set(livePath, pages)
    server.set(pagesPath, live)
    const load = sources.datasetLoader('dns')
    const result = load(false, () => {})
    await vi.advanceTimersByTimeAsync(200)
    const payload = await result
    expect(payload.generated_at).toBe(live.generated_at)
    expect(payload.__sourceAsset).toBe(pagesPath)
  })

  it('registers every data path hard-coded in web/src', () => {
    const root = resolve(__dirname, '../../src')
    const files: string[] = []
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const path = join(dir, name)
        if (statSync(path).isDirectory()) {
          if (name !== 'ui') walk(path)
        } else if (/\.(ts|vue)$/.test(name) && !path.endsWith(join('data', 'sources.ts'))) files.push(path)
      }
    }
    walk(root)
    const literal = /'((?:https:\/\/raw\.githubusercontent\.com\/[^']+)|(?:data\/vllm\/[^']+))'/g
    const hardCoded = new Set<string>()
    for (const file of files) for (const match of readFileSync(file, 'utf8').matchAll(literal)) hardCoded.add(match[1]!)
    const registered = new Set<string>([
      ...Object.values(sources.DATASETS).flatMap((source) => [...source.paths]),
      ...Object.values(sources.SECTIONS).flatMap((section) => [section.path, ...(section.live ? [section.live] : [])]),
      ...Object.values(sources.LINKS),
    ])
    const queueLive = sources.SECTIONS.queue.live!.replace('operations_v2/queue.json', '')
    const dnsLive = sources.DATASETS.dns.paths[0].replace('dns_failures.json', '')
    // Bases that the domain modules still compose paths from.
    registered.add(queueLive).add(dnsLive)
    expect([...hardCoded].filter((path) => !registered.has(path))).toEqual([])
  })
})
