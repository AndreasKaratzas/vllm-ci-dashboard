// The root page is v2, served beside <site>/data/; the dev server proxies /data.
export const DATA_BASE = import.meta.env.DEV ? '/' : './'

const cache = new Map<string, Promise<unknown>>()

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export function dataUrl(path: string) {
  return /^https?:\/\//.test(path) ? path : DATA_BASE + path.replace(/^\/+/, '')
}

async function fetchWithRetry(path: string): Promise<unknown> {
  let lastError: unknown = null
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const separator = path.includes('?') ? '&' : '?'
      const response = await fetch(`${dataUrl(path)}${separator}_health=${Date.now()}-${attempt}`, {
        cache: 'no-store',
      })
      if (!response.ok) throw new Error(`${path} returned HTTP ${response.status}`)
      return await response.json()
    } catch (error) {
      lastError = error
      if (attempt < 2) await delay(attempt === 0 ? 250 : 1000)
    }
  }
  throw lastError ?? new Error(`${path} could not be fetched`)
}

export function fetchJSON<T = unknown>(path: string): Promise<T> {
  let request = cache.get(path)
  if (!request) {
    request = fetchWithRetry(path)
    cache.set(path, request)
    // A transient CDN or deployment race must not poison the page-wide cache.
    const pending = request
    pending.catch(() => {
      if (cache.get(path) === pending) cache.delete(path)
    })
  }
  return request as Promise<T>
}

export function clearDataCache() {
  cache.clear()
}
