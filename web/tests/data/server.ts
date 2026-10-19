import { vi } from 'vitest'

/** A response body, an HTTP status, or a handler that receives the request init. */
export type Route = unknown | { status: number } | ((init: RequestInit | undefined) => Promise<Response> | Response | unknown)

const isStatus = (value: unknown): value is { status: number } =>
  !!value && typeof value === 'object' && Object.keys(value).length === 1 && typeof (value as { status: unknown }).status === 'number'

/** Normalizes a requested URL to the registry path: no data base prefix, no query. */
export function routeKey(url: string) {
  return url.replace(/\?.*$/, '').replace(/^\.?\//, '')
}

/** Serves JSON per path through a stubbed global fetch and records every request. */
export function installServer() {
  const routes = new Map<string, Route>()
  const requests: { url: string; init: RequestInit | undefined }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string, init?: RequestInit) => {
      requests.push({ url: String(input), init })
      const route = routes.get(routeKey(String(input)))
      if (route === undefined) return new Response('missing', { status: 404 })
      const result = typeof route === 'function' ? await route(init) : route
      if (result instanceof Response) return result
      if (isStatus(result)) return new Response('error', { status: result.status })
      return new Response(JSON.stringify(result), { status: 200 })
    }),
  )
  return {
    set(path: string, route: Route) {
      routes.set(path, route)
    },
    requests,
    /** Requests made for one path. */
    count(path: string) {
      return requests.filter((request) => routeKey(request.url) === path).length
    },
  }
}

/** A handler that never resolves until the request is aborted. */
export function hang(init: RequestInit | undefined): Promise<Response> {
  return new Promise((_, reject) => {
    init?.signal?.addEventListener('abort', () => reject(init.signal!.reason))
  })
}

/** Advances fake time, letting real macrotasks (WebCrypto) run between steps, until `promise` settles. */
export async function settle<T>(promise: Promise<T>, stepMs = 250, maxSteps = 100): Promise<T> {
  let done = false
  const tracked = promise.finally(() => {
    done = true
  })
  for (let step = 0; step < maxSteps && !done; step += 1) {
    await new Promise((resolve) => setImmediate(resolve))
    await vi.advanceTimersByTimeAsync(stepMs)
  }
  return tracked
}

/** Advances fake time by `ms` in `steps` equal steps, letting real macrotasks run between them. */
export async function elapse(ms: number, steps = 4) {
  for (let step = 0; step < steps; step += 1) {
    await new Promise((resolve) => setImmediate(resolve))
    await vi.advanceTimersByTimeAsync(ms / steps)
  }
}
