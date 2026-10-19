// The page is served beside <site>/data/; the dev server proxies /data.
const DATA_BASE = import.meta.env.DEV ? '/' : './'

const DEFAULT_ATTEMPTS = 3
const DEFAULT_TIMEOUT_MS = 30_000
const BACKOFF_BASE_MS = 250
const BACKOFF_FACTOR = 4

export interface FetchOptions {
  /** Total attempts, including the first (default 3). */
  attempts?: number
  /** Per-attempt timeout (default 30 s). */
  timeoutMs?: number
  /** Aborting the caller's signal stops the request and every pending retry. */
  signal?: AbortSignal
  /** Key of the page-wide promise cache (default: the path). */
  cacheKey?: string
  /** Bypass the page-wide cache: always request, never store. */
  uncached?: boolean
  /** Rejects a response (by throwing) so the attempt is retried, e.g. on a generation mismatch. */
  validate?: (payload: unknown, body: ArrayBuffer) => void | Promise<void>
}

const cache = new Map<string, Promise<unknown>>()

function dataUrl(path: string) {
  return /^https?:\/\//.test(path) ? path : DATA_BASE + path.replace(/^\/+/, '')
}

function abortError(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException('The request was aborted', 'AbortError')
}

function sleep(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) return reject(abortError(signal))
    const onAbort = () => {
      clearTimeout(timer)
      reject(abortError(signal!))
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

async function attempt(path: string, index: number, options: FetchOptions): Promise<unknown> {
  const controller = new AbortController()
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS
  const timer = setTimeout(() => controller.abort(new Error(`${path} timed out after ${timeoutMs} ms`)), timeoutMs)
  const onAbort = () => controller.abort(abortError(options.signal!))
  options.signal?.addEventListener('abort', onAbort, { once: true })
  try {
    const url = dataUrl(path)
    // A unique query defeats CDN and browser caches that ignore no-store.
    const response = await fetch(`${url}${url.includes('?') ? '&' : '?'}_health=${Date.now()}-${index}`, {
      cache: 'no-store',
      signal: controller.signal,
    })
    if (!response.ok) throw new Error(`${path} returned HTTP ${response.status}`)
    const body = await response.arrayBuffer()
    const payload: unknown = JSON.parse(new TextDecoder().decode(body))
    await options.validate?.(payload, body)
    return payload
  } catch (error) {
    throw controller.signal.aborted && !options.signal?.aborted ? controller.signal.reason : error
  } finally {
    clearTimeout(timer)
    options.signal?.removeEventListener('abort', onAbort)
  }
}

async function fetchWithRetry(path: string, options: FetchOptions): Promise<unknown> {
  const attempts = Math.max(1, options.attempts ?? DEFAULT_ATTEMPTS)
  let lastError: unknown = null
  for (let index = 0; index < attempts; index += 1) {
    if (options.signal?.aborted) throw abortError(options.signal)
    try {
      return await attempt(path, index, options)
    } catch (error) {
      if (options.signal?.aborted) throw abortError(options.signal)
      lastError = error
      if (index < attempts - 1) await sleep(BACKOFF_BASE_MS * BACKOFF_FACTOR ** index, options.signal)
    }
  }
  throw lastError ?? new Error(`${path} could not be fetched`)
}

/** JSON at a data path or absolute URL: no-store, bounded retries with backoff, per-attempt timeout. */
export function fetchJSON<T = unknown>(path: string, options: FetchOptions = {}): Promise<T> {
  // A shared promise cannot honour one caller's abort, so abortable requests are never cached.
  if (options.uncached || options.signal) return fetchWithRetry(path, options) as Promise<T>
  const key = options.cacheKey ?? path
  let request = cache.get(key)
  if (!request) {
    const pending = fetchWithRetry(path, options)
    cache.set(key, pending)
    // A transient CDN or deployment race must not poison the page-wide cache.
    pending.catch(() => {
      if (cache.get(key) === pending) cache.delete(key)
    })
    request = pending
  }
  return request as Promise<T>
}

/** Forget one cached request, or all of them. */
export function clearDataCache(cacheKey?: string) {
  if (cacheKey === undefined) cache.clear()
  else cache.delete(cacheKey)
}
